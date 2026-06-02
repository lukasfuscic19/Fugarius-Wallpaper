#!/usr/bin/env bash
# Install launcher and .desktop entry for a git checkout (not AppImage).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
BIN_DIR="${HOME}/.local/bin"

mkdir -p "${DESKTOP_DIR}" "${BIN_DIR}"

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
