# Fugarius Wallpaper

Multi-monitor panorama wallpaper tool for **Linux (Wayland)** — one source image, per-monitor crop with zoom/offset/rotation, aligned across bezels and uneven layouts (e.g. ultrawide + stacked displays).

Repository: [github.com/lukasfuscic19/Fugarius-Wallpaper](https://github.com/lukasfuscic19/Fugarius-Wallpaper)

## Features

- Single source image → separate wallpaper per monitor
- Per-monitor: zoom, X/Y offset, rotation, geometry, Wayland output name
- Auto-detect layout via `swww` / `awww` query, or KDE `kscreen-doctor`
- **Auto-fit** modes: `fill`, `fit`, `stretch`
- Live layout preview in the app
- **Apply to desktop** — push wallpapers directly through `swww` / `awww` (no export loop)
- **Live apply** — update real monitors when you release a slider
- Save/load profiles (JSON)
- Optional export: PNG per monitor + `apply_wallpaper.sh` + `manifest.json`
- Built-in image file picker (fixed window size; avoids KDE dialog resize issues)

## Requirements

| Component | Notes |
|-----------|--------|
| Python 3.10+ | Tested on Nobara / Fedora |
| [Pillow](https://pypi.org/project/pillow/) | `pip install -r requirements.txt` |
| `python3-tkinter` | System package (GUI) |
| `swww` | Recommended for **Apply to desktop** on Wayland |

### Fedora / Nobara

```bash
sudo dnf install python3 python3-tkinter swww
```

## Install & run

```bash
git clone https://github.com/lukasfuscic19/Fugarius-Wallpaper.git
cd Fugarius-Wallpaper

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

./run.sh
```

Or without the launcher:

```bash
source .venv/bin/activate
python app.py
```

## Typical workflow

1. **Open source image** — use a high-resolution file (at least as wide as your virtual desktop).
2. **Re-detect monitors** if outputs are missing.
3. Choose **fill** (default) → **Auto-fit**.
4. **Apply to desktop** — check alignment on real monitors.
5. Enable **Live apply** and fine-tune offsets/rotation per monitor.
6. **Save profile** when happy; **Export wallpapers** only if you need files on disk.

## Wayland / KDE notes

- Start `swww-daemon` once per session, or let the app start it.
- In **KDE Plasma**: disable automatic/slideshow wallpaper in *System Settings → Wallpaper*, or Plasma may draw over `swww`.
- Apply cache: `~/.cache/fugarius-wallpaper/`
- Missing output names? **Re-detect monitors** or run `swww query` and fill **Wayland output name** for each display.

## Project layout

| File | Purpose |
|------|---------|
| `app.py` | Main GUI application |
| `run.sh` | Venv + launch with Wayland env |
| `verify_panorama.py` | CLI check that crops match virtual desktop geometry |
| `requirements.txt` | Python dependencies |

## Verify alignment (optional)

```bash
source .venv/bin/activate
python verify_panorama.py
```

Prints `OK` when test crops match the detected monitor layout; writes samples to `_verify_out/` (gitignored).

## License

MIT — see [LICENSE](LICENSE).
