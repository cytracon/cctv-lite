"""Import camera layout/credentials from the installed cctv-viewer snap config."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import Config, default_config

SNAP_CANDIDATES = [
    Path.home() / "snap/cctv-viewer/current/.config/CCTV Viewer/CCTV Viewer.conf",
    Path.home() / "snap/cctv-viewer/1635/.config/CCTV Viewer/CCTV Viewer.conf",
    Path.home() / "snap/cctv-viewer/1629/.config/CCTV Viewer/CCTV Viewer.conf",
    Path.home() / "snap/cctv-viewer/1557/.config/CCTV Viewer/CCTV Viewer.conf",
]


def find_snap_config() -> Path | None:
    for p in SNAP_CANDIDATES:
        if p.exists():
            return p
    # fallback: any version
    root = Path.home() / "snap/cctv-viewer"
    if not root.exists():
        return None
    found = sorted(root.glob("*/.config/CCTV Viewer/CCTV Viewer.conf"), reverse=True)
    return found[0] if found else None


def _parse_urls(text: str) -> list[str]:
    urls = re.findall(r"rtsp://[^\"\\\s]+", text)
    cleaned: list[str] = []
    for u in urls:
        u = u.strip().replace("\\n", "").rstrip("\\")
        if u and u not in cleaned:
            cleaned.append(u)
    return cleaned


def _parse_rtsp(url: str) -> dict[str, Any] | None:
    """Parse RTSP URL; unquote userinfo so passwords are not double-encoded later."""
    from urllib.parse import unquote, urlsplit

    raw = (url or "").strip()
    if not raw.lower().startswith("rtsp://"):
        return None
    # urlsplit handles encoded userinfo
    parts = urlsplit(raw)
    if parts.scheme.lower() != "rtsp":
        return None
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    host = parts.hostname or ""
    if not host:
        return None
    port = int(parts.port or 554)
    path = parts.path or ""
    if parts.query:
        path = f"{path}?{parts.query}"
    channel = None
    cm = re.search(r"channels/(\d+)", path)
    if cm:
        channel = int(cm.group(1))
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "path": path,
        "channel": channel,
    }


def import_from_snap(path: Path | None = None) -> Config | None:
    conf_path = path or find_snap_config()
    if conf_path is None or not conf_path.exists():
        return None

    text = conf_path.read_text(encoding="utf-8", errors="replace")
    urls = _parse_urls(text)
    if not urls:
        return None

    data = default_config()
    first = _parse_rtsp(urls[0])
    if first:
        data["host"] = first["host"]
        data["port"] = first["port"]
        data["user"] = first["user"]
        data["password"] = first["password"]
        if first["path"] and "{channel}" not in (data.get("path_template") or ""):
            # keep ISAPI template if channels present
            if "channels/" in first["path"]:
                data["path_template"] = "/ISAPI/Streaming/channels/{channel}"

    # Group by camera number (Hikvision: N01 main, N02 sub)
    by_cam: dict[int, dict[str, int]] = {}
    order_sub: list[int] = []
    order_main: list[int] = []

    for url in urls:
        info = _parse_rtsp(url)
        if not info or info["channel"] is None:
            continue
        ch = info["channel"]
        cam_num = ch // 100
        stream = ch % 100  # 1=main, 2=sub typically
        if cam_num <= 0:
            continue
        slot = by_cam.setdefault(cam_num, {})
        if stream == 1:
            slot["main"] = ch
            if cam_num not in order_main:
                order_main.append(cam_num)
        else:
            slot["sub"] = ch
            if cam_num not in order_sub:
                order_sub.append(cam_num)
        # fill missing
        slot.setdefault("main", cam_num * 100 + 1)
        slot.setdefault("sub", cam_num * 100 + 2)

    # Prefer natural channel order 1..N (not raw snap viewport order)
    cam_order = sorted(by_cam.keys())
    cameras = []
    for n in cam_order:
        slot = by_cam[n]
        cameras.append(
            {
                "id": f"cam{n:02d}",
                "name": f"Cam {n}",
                "channel_main": int(slot.get("main", n * 100 + 1)),
                "channel_sub": int(slot.get("sub", n * 100 + 2)),
                "enabled": True,
            }
        )
    # include any cameras only seen as main
    for n in sorted(by_cam.keys()):
        if n not in cam_order:
            slot = by_cam[n]
            cameras.append(
                {
                    "id": f"cam{n:02d}",
                    "name": f"Cam {n}",
                    "channel_main": int(slot.get("main", n * 100 + 1)),
                    "channel_sub": int(slot.get("sub", n * 100 + 2)),
                    "enabled": True,
                }
            )

    if cameras:
        data["cameras"] = cameras
        ids = [c["id"] for c in cameras]
        n = len(ids)
        # 4x4 if 13-16, else fit
        if n >= 13:
            cols, rows = 4, 4
        elif n >= 10:
            cols, rows = 4, 3
        elif n >= 7:
            cols, rows = 3, 3
        elif n >= 5:
            cols, rows = 3, 2
        elif n >= 3:
            cols, rows = 2, 2
        else:
            cols, rows = n, 1
        data["layouts"] = [
            {
                "name": f"All {cols}×{rows} (sub)",
                "cols": cols,
                "rows": rows,
                "quality": "sub",
                "cameras": ids,
            }
        ]
        # chunked main-quality groups of 6
        groups = []
        chunk = 6
        for i in range(0, n, chunk):
            part = ids[i : i + chunk]
            c = min(3, len(part))
            r = (len(part) + c - 1) // c
            groups.append(
                {
                    "name": f"Group {chr(65 + len(groups))} (main)",
                    "cols": c,
                    "rows": max(1, r),
                    "quality": "main",
                    "cameras": part,
                }
            )
        data["layouts"].extend(groups)
        data["active_layout"] = 0

    return Config(data)
