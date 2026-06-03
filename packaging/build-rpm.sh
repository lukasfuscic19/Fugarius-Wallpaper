#!/usr/bin/env bash
# Build Fedora/RHEL RPM (noarch) into dist/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-0.2.0}"
DIST="${ROOT}/dist"
RPMTOP="${ROOT}/build/rpm"

command -v rpmbuild >/dev/null || { echo "Install: sudo dnf install rpm-build" >&2; exit 1; }

mkdir -p "${DIST}" "${RPMTOP}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS,BUILDROOT}
TARBALL="${RPMTOP}/SOURCES/fugarius-wallpaper-${VERSION}.tar.gz"

echo "==> Source tarball (HEAD)"
git -C "${ROOT}" archive --format=tar.gz --prefix="Fugarius-Wallpaper-${VERSION}/" \
  -o "${TARBALL}" HEAD

cp "${ROOT}/packaging/rpm/fugarius-wallpaper.spec" "${RPMTOP}/SPECS/"
export QA_RPATHS=$((0x0003 | 0x0002))

echo "==> rpmbuild"
rpmbuild -bb \
  --define "_topdir ${RPMTOP}" \
  --define "version ${VERSION}" \
  "${RPMTOP}/SPECS/fugarius-wallpaper.spec"

RPM=$(find "${RPMTOP}/RPMS" -name "fugarius-wallpaper-${VERSION}*.rpm" | head -1)
cp -a "${RPM}" "${DIST}/"
echo "Done: ${DIST}/$(basename "${RPM}")"
