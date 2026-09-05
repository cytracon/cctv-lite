"""YAML config load/save for CCTV Lite."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

APP_NAME = "cctv-lite"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.yaml"


DEFAULT_CAMERAS = [
    # Natural channel order 1..16 (Hikvision N01/N02).
    {"id": "cam01", "name": "Cam 1", "channel_main": 101, "channel_sub": 102},
    {"id": "cam02", "name": "Cam 2", "channel_main": 201, "channel_sub": 202},
    {"id": "cam03", "name": "Cam 3", "channel_main": 301, "channel_sub": 302},
    {"id": "cam04", "name": "Cam 4", "channel_main": 401, "channel_sub": 402},
    {"id": "cam05", "name": "Cam 5", "channel_main": 501, "channel_sub": 502},
    {"id": "cam06", "name": "Cam 6", "channel_main": 601, "channel_sub": 602},
    {"id": "cam07", "name": "Cam 7", "channel_main": 701, "channel_sub": 702},
    {"id": "cam08", "name": "Cam 8", "channel_main": 801, "channel_sub": 802},
    {"id": "cam09", "name": "Cam 9", "channel_main": 901, "channel_sub": 902},
    {"id": "cam10", "name": "Cam 10", "channel_main": 1001, "channel_sub": 1002},
    {"id": "cam11", "name": "Cam 11", "channel_main": 1101, "channel_sub": 1102},
    {"id": "cam12", "name": "Cam 12", "channel_main": 1201, "channel_sub": 1202},
    {"id": "cam13", "name": "Cam 13", "channel_main": 1301, "channel_sub": 1302},
    {"id": "cam14", "name": "Cam 14", "channel_main": 1401, "channel_sub": 1402},
    {"id": "cam15", "name": "Cam 15", "channel_main": 1501, "channel_sub": 1502},
    {"id": "cam16", "name": "Cam 16", "channel_main": 1601, "channel_sub": 1602},
]


def default_config() -> dict[str, Any]:
    """Safe empty defaults — never ship a live public NVR as template."""
    return {
        "host": "",
        "port": 554,
        # Optional separate host for ISAPI names (LAN IP while RTSP uses public IP)
        "http_host": "",
        "http_port": 80,
        "http_scheme": "http",
        "user": "",
        "password": "",
        "path_template": "/ISAPI/Streaming/channels/{channel}",
        "rtsp": {
            "latency_ms": 120,
            "protocols": "tcp",
            "drop_on_latency": True,
            "timeout_us": 5_000_000,
            "reconnect_delay_s": 3.0,
            "stagger_ms": 250,
            "detail_switch_delay_ms": 500,
            "use_hardware_decode": True,
            "mute_audio": True,
        },
        "ui": {
            "window_width": 1600,
            "window_height": 900,
            "show_labels": True,
            "pause_when_unmapped": True,
            "dark": True,
        },
        "cameras": [],
        "layouts": [
            {
                "name": "All 4×4 (sub)",
                "cols": 4,
                "rows": 4,
                "quality": "sub",
                "cameras": [],
            },
        ],
        "active_layout": 0,
    }


def site_template_config() -> dict[str, Any]:
    """Generic empty template. Never embed a real NVR host or password."""
    return default_config()


@dataclass
class Camera:
    id: str
    name: str
    channel_main: int
    channel_sub: int
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Camera":
        return cls(
            id=str(d["id"]),
            name=str(d.get("name") or d["id"]),
            channel_main=int(d["channel_main"]),
            channel_sub=int(d.get("channel_sub") or d["channel_main"]),
            enabled=bool(d.get("enabled", True)),
        )


@dataclass
class Layout:
    name: str
    cols: int
    rows: int
    quality: str  # "sub" | "main"
    cameras: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Layout":
        return cls(
            name=str(d.get("name") or "Layout"),
            cols=max(1, int(d.get("cols") or 1)),
            rows=max(1, int(d.get("rows") or 1)),
            quality="main" if str(d.get("quality", "sub")).lower() == "main" else "sub",
            cameras=[str(x) for x in (d.get("cameras") or [])],
        )


class Config:
    def __init__(self, data: dict[str, Any] | None = None, path: Path | None = None):
        self.data = data if data is not None else default_config()
        self._path = path or CONFIG_PATH

    @property
    def path(self) -> Path:
        return self._path

    def cameras(self) -> list[Camera]:
        return [Camera.from_dict(c) for c in self.data.get("cameras") or []]

    def camera_map(self) -> dict[str, Camera]:
        return {c.id: c for c in self.cameras()}

    def layouts(self) -> list[Layout]:
        return [Layout.from_dict(l) for l in self.data.get("layouts") or []]

    def active_layout(self) -> Layout:
        layouts = self.layouts()
        if not layouts:
            return Layout(name="Empty", cols=1, rows=1, quality="sub", cameras=[])
        idx = int(self.data.get("active_layout") or 0)
        idx = max(0, min(idx, len(layouts) - 1))
        return layouts[idx]

    def rtsp(self) -> dict[str, Any]:
        return dict(self.data.get("rtsp") or {})

    def ui(self) -> dict[str, Any]:
        return dict(self.data.get("ui") or {})

    def needs_setup(self) -> bool:
        """True if host/credentials or cameras are missing."""
        if not (self.data.get("host") or "").strip():
            return True
        if not (self.data.get("cameras") or []):
            return True
        return False

    def status_payload(self) -> dict[str, Any]:
        """Compact JSON for the Omarchy bar plugin. No host, user, or password."""
        from . import __version__

        layout = self.active_layout()
        cams = self.cameras()
        return {
            "version": __version__,
            "configured": not self.needs_setup(),
            "cameras": len(cams),
            "enabled_cameras": sum(1 for c in cams if c.enabled),
            "layout": layout.name,
            "grid": f"{layout.cols}×{layout.rows}",
            "quality": layout.quality,
        }

    def build_url(self, channel: int) -> str:
        host = self.data.get("host") or "127.0.0.1"
        port = int(self.data.get("port") or 554)
        user = self.data.get("user") or ""
        password = self.data.get("password") or ""
        tmpl = self.data.get("path_template") or "/ISAPI/Streaming/channels/{channel}"
        path = tmpl.format(channel=channel)
        if not path.startswith("/"):
            path = "/" + path
        if user:
            u = quote(str(user), safe="")
            p = quote(str(password), safe="")
            return f"rtsp://{u}:{p}@{host}:{port}{path}"
        return f"rtsp://{host}:{port}{path}"

    def url_for_camera(self, cam: Camera, quality: str) -> str:
        ch = cam.channel_main if quality == "main" else cam.channel_sub
        return self.build_url(ch)

    def ensure_dirs(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(CONFIG_DIR, 0o700)
        except OSError:
            pass

    def save(self, path: Path | None = None) -> Path:
        if yaml is None:
            raise RuntimeError("PyYAML is required. Install with: pip install --user pyyaml")
        self.ensure_dirs()
        target = path or self._path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, sort_keys=False, allow_unicode=True)
        os.chmod(tmp, 0o600)
        tmp.replace(target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        self._path = target
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        target = path or CONFIG_PATH
        if not target.exists():
            return cls(default_config(), path=target)
        if yaml is None:
            raise RuntimeError("PyYAML is required. Install with: pip install --user pyyaml")
        with open(target, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # Merge shallow defaults for missing keys (safe empty defaults)
        base = default_config()
        for k, v in base.items():
            if k not in data:
                data[k] = v
            elif isinstance(v, dict) and isinstance(data.get(k), dict):
                merged = dict(v)
                merged.update(data[k])
                data[k] = merged
        return cls(data, path=target)

    def set_active_layout(self, index: int) -> None:
        layouts = self.layouts()
        if not layouts:
            self.data["active_layout"] = 0
            return
        self.data["active_layout"] = max(0, min(int(index), len(layouts) - 1))


def ensure_config(import_if_missing: bool = True) -> Config:
    """Load config; if missing, try snap import then write empty defaults (not live NVR)."""
    if CONFIG_PATH.exists():
        return Config.load()
    cfg = Config(default_config(), path=CONFIG_PATH)
    if import_if_missing:
        try:
            from .import_snap import import_from_snap

            imported = import_from_snap()
            if imported is not None:
                cfg = imported
                cfg._path = CONFIG_PATH
        except Exception:
            pass
    cfg.save()
    return cfg
