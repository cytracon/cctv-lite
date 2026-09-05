# CCTV Lite

Lightweight multi-camera RTSP viewer for **Omarchy** (Arch Linux + Hyprland). GTK4 + libadwaita + GStreamer.

The Omarchy bar plugin is a separate repository: [cytracon/omarchy-cctv-lite](https://github.com/cytracon/omarchy-cctv-lite).

## Privacy

Camera host, password, and layout live only in **`~/.config/cctv-lite/config.yaml`** (mode 600). That file is never part of this repository. `data/config.yaml.example` is an empty template.

## Install on Omarchy

```bash
omarchy install cctv-lite
```

or from a checkout:

```bash
./install-local.sh
```

Existing `~/.config/cctv-lite/config.yaml` is kept. The installer does not write credentials.

## CLI

```bash
cctv-lite --version
cctv-lite --status          # JSON for the bar plugin; no secrets
cctv-lite --sync-names      # optional ISAPI name refresh
```

## Plugin

```bash
omarchy plugin add https://github.com/cytracon/omarchy-cctv-lite.git --enable
```

## License

MIT.
