#!/usr/bin/env bash
# Obnoví KDE panely a tapetu po „překrytí“ Fugarius tapetou (FillMode Centered / D-Bus výpadek).
# Spusť z TTY (Ctrl+Alt+F3), SSH, nebo Konsole — nepotřebuješ vidět panel.

set -euo pipefail

uid="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${uid}}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

CFG="${HOME}/.config/plasma-org.kde.plasma.desktop-appletsrc"
if [[ ! -f "${CFG}" ]]; then
  echo "Config not found: ${CFG}" >&2
  exit 1
fi

BACKUP="${CFG}.bak-restore-$(date +%Y%m%d-%H%M%S)"
cp -a "${CFG}" "${BACKUP}"
echo "Backup: ${BACKUP}"

# Bezpečný režim vyplnění (Scaled and cropped)
sed -i 's/^FillMode=6$/FillMode=2/' "${CFG}"

# Volitelně: vrátit původní tapetu místo fugarius cache (první argument = file:// URI)
if [[ -n "${1:-}" ]]; then
  ORIG="$1"
  sed -i "s|file://${HOME}/.cache/fugarius-wallpaper/[^[:space:]]*|${ORIG}|g" "${CFG}"
  echo "Wallpaper paths set to: ${ORIG}"
else
  echo "Fugarius cache paths kept; only FillMode fixed. Pass a file:// URI to restore old wallpaper."
fi

echo "Restarting plasmashell…"
systemctl --user restart plasma-plasmashell.service 2>/dev/null || {
  kquitapp6 plasmashell 2>/dev/null || true
  sleep 2
  kstart plasmashell 2>/dev/null || true
}

echo "Done. Panels should return in a few seconds. If not, log out and back in."
