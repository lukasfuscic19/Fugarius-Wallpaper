#!/usr/bin/env bash
# Build assets/icons/hicolor from assets/icon-source.png (requires Pillow in .venv).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

if [[ ! -f assets/icon-source.png ]]; then
  echo "Missing assets/icon-source.png" >&2
  exit 1
fi

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON=python3
fi

"${PYTHON}" <<'PY'
from pathlib import Path
from PIL import Image

root = Path(".")
img = Image.open(root / "assets/icon-source.png").convert("RGBA")
base = root / "assets/icons/hicolor"
for size in (16, 22, 24, 32, 48, 64, 128, 256, 512):
    out = base / f"{size}x{size}" / "apps"
    out.mkdir(parents=True, exist_ok=True)
    img.resize((size, size), Image.Resampling.LANCZOS).save(out / "fugarius-wallpaper.png")
img.resize((512, 512), Image.Resampling.LANCZOS).save(root / "assets/fugarius-wallpaper-512.png")
print("Icons written under assets/icons/hicolor/")
PY
