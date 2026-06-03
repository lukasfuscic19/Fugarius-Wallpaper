#!/usr/bin/env bash
# Install launcher and .desktop entry for a git checkout (not AppImage).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
BIN_DIR="${HOME}/.local/bin"

ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
mkdir -p "${DESKTOP_DIR}" "${BIN_DIR}"

if [[ -d "${ROOT}/assets/icons/hicolor" ]]; then
  echo "==> Installing icons to ${ICON_DIR}"
  cp -a "${ROOT}/assets/icons/hicolor/"* "${ICON_DIR}/"
  gtk-update-icon-cache -f -t "${ICON_DIR}" 2>/dev/null || true
else
  echo "Warning: run ./packaging/generate-icons.sh first (missing icon tree)." >&2
fi

cat > "${BIN_DIR}/fugarius-wallpaper" <<EOF
#!/usr/bin/env bash
exec "${ROOT}/run.sh" "\$@"
EOF
chmod +x "${BIN_DIR}/fugarius-wallpaper"

sed "s|Exec=.*|Exec=${BIN_DIR}/fugarius-wallpaper|" \
  "${ROOT}/packaging/fugarius-wallpaper.desktop" > "${DESKTOP_DIR}/fugarius-wallpaper.desktop"

echo "Installed:"
echo "  ${BIN_DIR}/fugarius-wallpaper"
echo "  ${DESKTOP_DIR}/fugarius-wallpaper.desktop"
echo "Run once: ${ROOT}/install.sh"
