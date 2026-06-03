#!/usr/bin/env bash
# System launcher (RPM install → /usr/bin/fugarius-wallpaper). Uses distro Python + Pillow.
set -euo pipefail

if [[ -f /usr/share/fugarius-wallpaper/app.py ]]; then
  APP_ROOT=/usr/share/fugarius-wallpaper
else
  APP_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../share/fugarius-wallpaper" && pwd)"
fi

missing=()
command -v python3 >/dev/null || missing+=(python3)
python3 -c "import tkinter" 2>/dev/null || missing+=(python3-tkinter)
python3 -c "import PIL" 2>/dev/null || missing+=(python3-pillow)

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Missing: ${missing[*]}" >&2
  echo "On Fedora: sudo dnf install python3-tkinter python3-pillow kscreen qt6-qttools" >&2
  exit 1
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  if [[ -S "${XDG_RUNTIME_DIR}/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
  elif pid=$(pgrep -x plasmashell 2>/dev/null | head -1) && [[ -n "${pid}" ]]; then
    dbus_line=$(tr '\0' '\n' < "/proc/${pid}/environ" | grep '^DBUS_SESSION_BUS_ADDRESS=' | head -1)
    if [[ -n "${dbus_line}" ]]; then
      export "${dbus_line?}"
    fi
  fi
fi

cd "${APP_ROOT}"
exec python3 app.py
