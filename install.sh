#!/usr/bin/env bash
# Install Fugarius Wallpaper — system packages + Python venv.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Fugarius Wallpaper — install"

if command -v dnf >/dev/null 2>&1; then
  echo "==> Installing system packages (dnf)…"
  sudo dnf install -y \
    python3 \
    python3-pip \
    python3-tkinter \
    kscreen \
    qt6-dbus \
    || true
  # Optional: swww for non-KDE Wayland setups
  if ! command -v swww >/dev/null 2>&1; then
    echo "    (optional) swww for Hyprland/Sway — not installed by default on KDE"
    sudo dnf install -y swww 2>/dev/null || true
  fi
elif command -v apt-get >/dev/null 2>&1; then
  echo "==> Installing system packages (apt)…"
  sudo apt-get update
  sudo apt-get install -y python3 python3-pip python3-tk python3-venv
  sudo apt-get install -y swww 2>/dev/null || true
else
  echo "Install manually: python3, python3-tkinter, pip, kscreen-doctor, qdbus (KDE)"
fi

echo "==> Python virtualenv + Pillow…"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo ""
echo "Done. Run: ./run.sh"
echo ""
if command -v qdbus >/dev/null 2>&1; then
  echo "KDE Plasma detected — wallpapers apply natively (no swww required)."
else
  echo "For Wayland tiling compositors, install swww: sudo dnf install swww"
fi
