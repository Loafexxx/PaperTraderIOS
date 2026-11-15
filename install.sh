#!/usr/bin/env bash
# Installs Paper Trader for the current user (no sudo needed).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/papertrader"
mkdir -p "$DEST" "$HOME/.local/bin" "$HOME/.local/share/applications"

if [ ! -x "$DEST/venv/bin/python" ]; then
  python3 -m venv "$DEST/venv"
fi
"$DEST/venv/bin/pip" install -q --upgrade PySide6-Essentials pyqtgraph numpy requests

rm -rf "$DEST/app"
mkdir -p "$DEST/app"
cp -r "$SRC/papertrader" "$DEST/app/"
find "$DEST/app" -name "__pycache__" -prune -exec rm -rf {} +

# App icon: SVG + PNG sizes into the hicolor theme (the launcher entry points at the 256px PNG directly).
ICONS="$HOME/.local/share/icons/hicolor"
mkdir -p "$ICONS/scalable/apps"
cp "$SRC/papertrader/assets/papertrader.svg" "$ICONS/scalable/apps/papertrader.svg"
TMPI="$(mktemp -d)"
"$DEST/venv/bin/python" "$SRC/render_icon.py" "$SRC/papertrader/assets/papertrader.svg" "$TMPI" >/dev/null
for s in 16 24 32 48 64 128 256 512; do
  mkdir -p "$ICONS/${s}x${s}/apps"
  cp "$TMPI/$s/papertrader.png" "$ICONS/${s}x${s}/apps/papertrader.png"
done
rm -rf "$TMPI"
touch "$ICONS"
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true

cat > "$HOME/.local/bin/papertrader" <<EOF
#!/usr/bin/env bash
export QT_QPA_PLATFORM="\${QT_QPA_PLATFORM:-wayland;xcb}"
cd "$DEST/app" && exec "$DEST/venv/bin/python" -m papertrader "\$@"
EOF
chmod +x "$HOME/.local/bin/papertrader"

cat > "$HOME/.local/share/applications/papertrader.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Paper Trader
Comment=A\$ paper-trading desk with Claude
Exec=$HOME/.local/bin/papertrader
Icon=$ICONS/256x256/apps/papertrader.png
Terminal=false
Categories=Office;Finance;
StartupWMClass=papertrader
EOF
command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installed. Launch 'Paper Trader' from your app launcher or run: papertrader"
