#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv — run: ./install.sh"
  exit 1
fi

missing=()
command -v python3 >/dev/null || missing+=("python3")
.venv/bin/python -c "import tkinter" 2>/dev/null || missing+=("python3-tkinter")
.venv/bin/python -c "import PIL" 2>/dev/null || missing+=("pip install Pillow (run ./install.sh)")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Missing dependencies: ${missing[*]}"
  echo "Run: ./install.sh"
  exit 1
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  if [[ -S "$XDG_RUNTIME_DIR/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  elif pid=$(pgrep -x plasmashell 2>/dev/null | head -1) && [[ -n "$pid" ]]; then
    dbus_line=$(tr '\0' '\n' < "/proc/$pid/environ" | grep '^DBUS_SESSION_BUS_ADDRESS=' | head -1)
    if [[ -n "$dbus_line" ]]; then
      export "${dbus_line?}"
    fi
  fi
fi

exec .venv/bin/python app.py
