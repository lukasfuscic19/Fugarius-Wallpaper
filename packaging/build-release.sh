#!/usr/bin/env bash
# Build AppImage + RPM for GitHub release (outputs in dist/)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-0.2.0}"
DIST="${ROOT}/dist"
BUILD="${ROOT}/build"
APPIMAGE_TOOL="${BUILD}/appimagetool"

mkdir -p "${DIST}"

echo "==> AppImage"
if ! command -v appimagetool >/dev/null 2>&1; then
  if [[ ! -x "${APPIMAGE_TOOL}" ]]; then
    echo "    Downloading appimagetool…"
    mkdir -p "${BUILD}"
    curl -fsSL -o "${APPIMAGE_TOOL}" \
      "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "${APPIMAGE_TOOL}"
  fi
  export PATH="${BUILD}:${PATH}"
fi

"${ROOT}/packaging/build-appimage.sh"

APPIMAGE=$(find "${BUILD}" -maxdepth 1 -name 'Fugarius-Wallpaper*.AppImage' | head -1)
if [[ -n "${APPIMAGE}" && -f "${APPIMAGE}" ]]; then
  cp -a "${APPIMAGE}" "${DIST}/Fugarius-Wallpaper-${VERSION}-x86_64.AppImage"
  echo "    ${DIST}/Fugarius-Wallpaper-${VERSION}-x86_64.AppImage"
else
  echo "Warning: AppImage not produced (install appimagetool or FUSE)." >&2
fi

echo "==> RPM"
"${ROOT}/packaging/build-rpm.sh" "${VERSION}"

echo ""
echo "Release artifacts in ${DIST}/:"
ls -la "${DIST}/"
