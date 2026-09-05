"""CLI entry: python -m cctv_lite | cctv-lite"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cctv-lite",
        description="Lightweight multi-camera RTSP viewer (GStreamer + GTK4)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: ~/.config/cctv-lite/config.yaml)",
    )
    parser.add_argument(
        "--import-snap",
        action="store_true",
        help="Import cameras from cctv-viewer snap config and save, then exit",
    )
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="Write config (default or imported) and exit",
    )
    parser.add_argument(
        "--sync-names",
        action="store_true",
        help="Fetch camera names from NVR via ISAPI (HTTP) and save config",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print a compact JSON snapshot for the Omarchy bar plugin (no secrets)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from . import __version__

    if args.version:
        print(__version__)
        return 0

    if args.status:
        import json

        from .config import Config

        cfg = Config.load(args.config) if args.config else Config.load()
        print(json.dumps(cfg.status_payload(), indent=2))
        return 0

    from .config import CONFIG_PATH, Config, ensure_config
    from .import_snap import find_snap_config, import_from_snap

    if args.import_snap:
        snap = find_snap_config()
        if snap is None:
            print("No cctv-viewer snap config found.", file=sys.stderr)
            return 1
        cfg = import_from_snap(snap)
        if cfg is None:
            print(f"Could not parse snap config: {snap}", file=sys.stderr)
            return 1
        path = args.config or CONFIG_PATH
        saved = cfg.save(path)
        # Never print password
        n = len(cfg.cameras())
        print(f"Imported {n} cameras from {snap}")
        print(f"Wrote {saved} (mode 600)")
        return 0

    if args.write_config:
        if args.config and args.config.exists():
            cfg = Config.load(args.config)
        else:
            cfg = ensure_config(import_if_missing=True)
        path = args.config or CONFIG_PATH
        saved = cfg.save(path)
        print(f"Wrote {saved}")
        return 0

    if args.sync_names:
        from .nvr import sync_names

        cfg = Config.load(args.config) if args.config else Config.load()
        n, msg = sync_names(cfg.data)
        # save when names changed, or always persist http discovery fields if present
        if n > 0 or "nicht erreichbar" not in msg:
            cfg.save(args.config or cfg.path)
        print(msg)
        for c in cfg.cameras():
            print(f"  {c.id}: {c.name} (main={c.channel_main})")
        return 0 if "nicht erreichbar" not in msg else 1

    if args.config:
        cfg = Config.load(args.config)
    else:
        cfg = ensure_config(import_if_missing=True)
        if not CONFIG_PATH.exists():
            cfg.save()

    if not (cfg.data.get("password") or cfg.data.get("user")):
        logging.getLogger("cctv_lite").warning(
            "No credentials in config — edit %s",
            args.config or CONFIG_PATH,
        )

    from .app import run_app

    return int(run_app(cfg) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
