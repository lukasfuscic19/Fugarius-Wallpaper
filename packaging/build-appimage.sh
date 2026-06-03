#!/usr/bin/env bash
# Build a portable AppImage for Fugarius Wallpaper (KDE Plasma / Wayland).
#
# The AppImage bundles Python deps (Pillow + venv). Tkinter and KDE tools
# (qdbus, kscreen-doctor, plasmashell D-Bus) come from the host — same as ./run.sh.
#
# Requirements: python3, python3-venv, python3-tkinter, appimagetool (optional: linuxdeploy)
#   Fedora: sudo dnf install python3 python3-tkinter fuse appimage-cli
#   appimagetool: https://github.com/AppImage/AppImageKit/releases
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${ROOT}/build"
APPDIR="${BUILD}/Fugarius-Wallpaper.AppDir"

echo "==> Preparing AppDir"
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/share/fugarius-wallpaper"

echo "==> Copying application"
for f in app.py run.sh install.sh restore_panels.sh verify_panorama.py requirements.txt README.md LICENSE; do
  cp -a "${ROOT}/${f}" "${APPDIR}/usr/share/fugarius-wallpaper/"
done
chmod +x "${APPDIR}/usr/share/fugarius-wallpaper/run.sh"
chmod +x "${APPDIR}/usr/share/fugarius-wallpaper/install.sh"
chmod +x "${APPDIR}/usr/share/fugarius-wallpaper/restore_panels.sh"

echo "==> Bundling Python venv (Pillow only)"
python3 -m venv "${APPDIR}/usr/share/fugarius-wallpaper/.venv"
"${APPDIR}/usr/share/fugarius-wallpaper/.venv/bin/pip" install -q -r \
  "${APPDIR}/usr/share/fugarius-wallpaper/requirements.txt"

cat > "${APPDIR}/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
APP="${HERE}/usr/share/fugarius-wallpaper"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "${XDG_RUNTIME_DIR}/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi
if ! "${APP}/.venv/bin/python" -c "import tkinter" 2>/dev/null; then
  echo "Fugarius Wallpaper needs host python3-tkinter (system package)." >&2
  exit 1
fi
cd "${APP}"
exec ./run.sh
EOF
chmod +x "${APPDIR}/AppRun"

if [[ -d "${ROOT}/assets/icons/hicolor" ]]; then
  mkdir -p "${APPDIR}/usr/share/icons/hicolor"
  cp -a "${ROOT}/assets/icons/hicolor/"* "${APPDIR}/usr/share/icons/hicolor/"
fi

cp "${ROOT}/packaging/fugarius-wallpaper.desktop" "${APPDIR}/fugarius-wallpaper.desktop"
sed -i 's|Exec=.*|Exec=AppRun|' "${APPDIR}/fugarius-wallpaper.desktop"
mkdir -p "${APPDIR}/usr/share/applications" "${APPDIR}/usr/share/metainfo"
cp "${APPDIR}/fugarius-wallpaper.desktop" "${APPDIR}/usr/share/applications/"
cp "${ROOT}/packaging/com.fugarius.Wallpaper.appdata.xml" "${APPDIR}/usr/share/metainfo/"

for size in 256 128 64; do
  icon="${ROOT}/assets/icons/hicolor/${size}x${size}/apps/fugarius-wallpaper.png"
  if [[ -f "${icon}" ]]; then
    cp "${icon}" "${APPDIR}/fugarius-wallpaper.png"
    cp "${icon}" "${APPDIR}/.DirIcon"
    break
  fi
done

if command -v appimagetool >/dev/null 2>&1; then
  echo "==> Building AppImage"
  ARCH=x86_64 appimagetool "${APPDIR}" "${BUILD}/Fugarius-Wallpaper-x86_64.AppImage"
  echo "Done: ${BUILD}/Fugarius-Wallpaper-x86_64.AppImage"
else
  echo "AppDir ready: ${APPDIR}"
  echo "Install appimagetool and re-run to produce a single .AppImage file."
fi
