"""Fetch camera / channel names from a Hikvision NVR via ISAPI (HTTP)."""

from __future__ import annotations

import logging
import re
import ssl
import xml.etree.ElementTree as ET
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    Request,
    build_opener,
)

log = logging.getLogger("cctv_lite.nvr")

ISAPI_PATHS = (
    "/ISAPI/ContentMgmt/InputProxy/channels",
    "/ISAPI/System/Video/inputs/channels",
    "/ISAPI/ContentMgmt/InputProxy/channels/status",
    "/ISAPI/Streaming/channels",
)

DEFAULT_HTTP_PORTS = (80, 8000, 8080, 443)


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def parse_channel_names(xml_text: str) -> dict[int, str]:
    """Parse ISAPI XML into {channel_index: name}."""
    names: dict[int, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("ISAPI XML parse error: %s", e)
        return names

    for el in root.iter():
        tag = _local(el.tag).lower()
        if tag not in (
            "inputproxychannel",
            "videoinputchannel",
            "streamingchannel",
            "inputproxychannelstatus",
            "channel",
        ):
            continue
        cid = None
        name = ""
        for child in el:
            ct = _local(child.tag).lower()
            if ct in ("id", "channelid", "videoid"):
                raw = _text(child)
                if raw.isdigit():
                    n = int(raw)
                    cid = n // 100 if n >= 100 else n
            elif ct in ("name", "channelname", "srcname"):
                name = _text(child)
        if cid and name:
            prev = names.get(cid, "")
            if not prev or (len(name) > len(prev) and not name.lower().startswith("cam")):
                names[cid] = name
            elif not prev:
                names[cid] = name
    if not names:
        ids: list[int] = []
        for el in root.iter():
            if _local(el.tag).lower() == "id" and _text(el).isdigit():
                n = int(_text(el))
                ids.append(n // 100 if n >= 100 else n)
            if _local(el.tag).lower() in ("name", "channelname") and ids:
                names[ids[-1]] = _text(el)
    return names


def _http_get(url: str, user: str, password: str, timeout: float = 6.0) -> tuple[int, str]:
    """
    HTTP GET with digest then basic auth.
    Credentials stay in Python (not process argv / ps).
    """
    ctx = ssl.create_default_context()
    # NVRs often use self-signed HTTPS
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    passman = HTTPPasswordMgrWithDefaultRealm()
    passman.add_password(None, url, user or "", password or "")
    opener = build_opener(
        HTTPDigestAuthHandler(passman),
        HTTPBasicAuthHandler(passman),
        # HTTPS handler with our context
    )
    # Install SSL context for https
    import urllib.request

    https_handler = urllib.request.HTTPSHandler(context=ctx)
    opener = build_opener(
        HTTPDigestAuthHandler(passman),
        HTTPBasicAuthHandler(passman),
        https_handler,
    )

    req = Request(url, method="GET", headers={"User-Agent": "cctv-lite/0.1.5"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return int(getattr(resp, "status", 200) or 200), body
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return int(e.code or 0), body
    except (URLError, TimeoutError, OSError) as e:
        log.debug("HTTP get failed %s: %s", url, e)
        return 0, ""


def fetch_channel_names(
    host: str,
    user: str,
    password: str,
    http_port: int | None = None,
    http_scheme: str | None = None,
    ports: tuple[int, ...] | None = None,
    timeout: float = 5.0,
) -> tuple[dict[int, str], str]:
    """Try ISAPI endpoints; return (names, source_url_or_error)."""
    host = (host or "").strip()
    if not host:
        return {}, "no host"

    port_list: list[int] = []
    if http_port:
        port_list.append(int(http_port))
    for p in ports or DEFAULT_HTTP_PORTS:
        if p not in port_list:
            port_list.append(p)

    schemes = [http_scheme] if http_scheme else ["http", "https"]
    last_err = "unreachable"

    for scheme in schemes:
        for port in port_list:
            if scheme == "https" and port in (80, 8000, 8080) and not http_scheme:
                continue
            if scheme == "http" and port == 443 and not http_scheme:
                continue
            for path in ISAPI_PATHS:
                url = f"{scheme}://{host}:{port}{path}"
                code, body = _http_get(url, user, password, timeout=timeout)
                if code == 200 and body.strip().startswith("<"):
                    names = parse_channel_names(body)
                    if names:
                        log.info("NVR names from %s (%d channels)", url, len(names))
                        return names, url
                    last_err = f"{url} -> HTTP {code} but no names parsed"
                elif code:
                    last_err = f"{url} -> HTTP {code}"
                else:
                    last_err = f"{url} -> no response"
    return {}, last_err


def apply_names_to_config(data: dict[str, Any], names: dict[int, str]) -> int:
    """Update cameras[].name from NVR names. Returns count updated."""
    updated = 0
    cameras = data.get("cameras") or []
    for cam in cameras:
        if not isinstance(cam, dict):
            continue
        num = None
        ch = cam.get("channel_main")
        if ch is not None:
            try:
                n = int(ch)
                num = n // 100 if n >= 100 else n
            except (TypeError, ValueError):
                pass
        if num is None:
            m = re.search(r"(\d+)", str(cam.get("id") or ""))
            if m:
                num = int(m.group(1))
        if num is None:
            continue
        name = names.get(num)
        if not name:
            continue
        if cam.get("name") != name:
            cam["name"] = name
            updated += 1
    return updated


def sync_names(config_data: dict[str, Any]) -> tuple[int, str]:
    """Fetch NVR names and apply to config_data in place."""
    rtsp_host = str(config_data.get("host") or "")
    http_host = str(config_data.get("http_host") or "").strip() or rtsp_host
    user = str(config_data.get("user") or "")
    password = str(config_data.get("password") or "")
    http_port = config_data.get("http_port")
    http_scheme = config_data.get("http_scheme") or None
    try:
        http_port_i = int(http_port) if http_port not in (None, "", 0) else None
    except (TypeError, ValueError):
        http_port_i = None

    hosts: list[str] = []
    for h in (http_host, rtsp_host):
        if h and h not in hosts:
            hosts.append(h)

    last_err = "unreachable"
    names: dict[int, str] = {}
    source = ""
    for host in hosts:
        names, source = fetch_channel_names(
            host=host,
            user=user,
            password=password,
            http_port=http_port_i,
            http_scheme=str(http_scheme) if http_scheme else None,
        )
        if names:
            break
        last_err = source

    if not names:
        return (
            0,
            f"NVR-Namen nicht erreichbar ({last_err}). "
            "HTTP/ISAPI muss offen sein (nicht nur RTSP:554). "
            "In Settings: http_host (NVR LAN IP) + http_port 80 setzen.",
        )
    n = apply_names_to_config(config_data, names)
    return n, f"{n} Name(n) von {source}"
