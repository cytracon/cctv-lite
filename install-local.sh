#!/usr/bin/env bash
# Install CCTV Lite for the current user on Omarchy (Arch). No root required
# unless optional packages are missing. Does not copy ~/.config/cctv-lite.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_BASE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
LIB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/cctv-lite"
APP_ID="com.bbachmann.cctv-lite"
PKG_NAME="cctv-lite"

echo "==> CCTV Lite (Omarchy / Arch, user-local)"
mkdir -p "$BIN_DIR" "$APP_DIR" "$LIB_DIR" "$LIB_DIR/icons"

rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'config.yaml' \
  "$ROOT/cctv_lite/" "$LIB_DIR/cctv_lite/"

cat > "$BIN_DIR/${PKG_NAME}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
LIB_LOCAL="$LIB_DIR"
export PYTHONPATH="\${LIB_LOCAL}\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m cctv_lite "\$@"
EOF
chmod 0755 "$BIN_DIR/${PKG_NAME}"
chmod 0755 "$ROOT/bin/${PKG_NAME}" 2>/dev/null || true

SVG_SRC="$ROOT/data/icons/cctv-lite.svg"
install -D -m 644 "$SVG_SRC" "$ICON_BASE/scalable/apps/${APP_ID}.svg"
install -D -m 644 "$SVG_SRC" "$ICON_BASE/scalable/apps/${PKG_NAME}.svg"
cp -f "$SVG_SRC" "$LIB_DIR/icons/${APP_ID}.svg"

rm -f "$APP_DIR/${PKG_NAME}.desktop"
install -m 644 "$ROOT/data/${APP_ID}.desktop" "$APP_DIR/${APP_ID}.desktop"
desk="$APP_DIR/${APP_ID}.desktop"
sed -i "s|^Exec=.*|Exec=$BIN_DIR/${PKG_NAME}|" "$desk"
sed -i "s|^Icon=.*|Icon=${APP_ID}|" "$desk"

need=()
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null \
  || need+=(python-gobject gtk4 libadwaita)
python3 -c "import yaml" 2>/dev/null || need+=(python-yaml)
python3 -c "import gi; gi.require_version('Gst','1.0')" 2>/dev/null \
  || need+=(gstreamer gst-plugins-base)
pacman -Q gst-plugins-good >/dev/null 2>&1 || need+=(gst-plugins-good)
pacman -Q gst-plugins-bad >/dev/null 2>&1 || need+=(gst-plugins-bad)
pacman -Q gst-libav >/dev/null 2>&1 || need+=(gst-libav)
pacman -Q gst-plugin-gtk4 >/dev/null 2>&1 || need+=(gst-plugin-gtk4)

if ((${#need[@]})); then
  echo "==> Missing packages: ${need[*]}"
  if command -v omarchy >/dev/null 2>&1; then
    echo "    Installing with omarchy pkg add (sudo may be requested)…"
    omarchy pkg add "${need[@]}"
  else
    echo "    Install with: sudo pacman -S --needed ${need[*]}"
  fi
fi

if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$APP_DIR" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null; then
  gtk-update-icon-cache -f -t "$ICON_BASE" 2>/dev/null || true
fi

CFG="${XDG_CONFIG_HOME:-$HOME/.config}/cctv-lite/config.yaml"
if [[ -f "$CFG" ]]; then
  echo "==> Keeping existing user config $CFG (not overwritten)"
else
  echo "==> No config yet. Copy data/config.yaml.example to $CFG and fill in locally."
fi

echo
echo "Installed:"
echo "  binary : $BIN_DIR/${PKG_NAME}"
echo "  package: $LIB_DIR"
echo "  desktop: $APP_DIR/${APP_ID}.desktop"
echo "Start with:  ${PKG_NAME}"
