# Fugarius Wallpaper

Multi-monitor **panorama** wallpaper tool for **Linux Wayland**. One source image is placed on a virtual desktop (all monitors combined); each screen shows the matching viewport with its own zoom, offset, and rotation.

**Primary target:** [KDE Plasma](https://kde.org/plasma-desktop/) (Wayland). Optional: `swww` / `awww` on other compositors.

Repository: [github.com/lukasfuscic19/Fugarius-Wallpaper](https://github.com/lukasfuscic19/Fugarius-Wallpaper)

## Supported platforms

| Environment | Apply wallpapers | Detect layout | Notes |
|-------------|------------------|---------------|--------|
| **KDE Plasma (Wayland)** | Native (D-Bus + config fallback) | `kscreen-doctor`, `kwinoutputconfig.json` | **Recommended** — full feature set |
| Hyprland / Sway / etc. | `swww` or `awww` | `swww query` / `awww query` | Export + manual output names |
| GNOME, X11-only, macOS, Windows | — | — | **Not supported** (no port planned here) |

There is **no hardcoded monitor layout** (no fixed resolution lists or output-name swaps). Any count and arrangement is driven by detected geometry and KWin/Plasma screen mapping.

## Features

- Single source image → one panorama → per-monitor PNG crops
- **Per-monitor transform:** zoom, X/Y offset, rotation for the selected screen (auto-fit sets a common baseline, then fine-tune each)
- **Auto-detect layout** via `kscreen-doctor -j`, with fallbacks (`kscreen-doctor -o`, `swww`/`awww query`)
- **Plasma screen mapping** from monitor positions (`kwinoutputconfig.json` + kscreen), reconciled with Plasma desktop IDs
- **Auto-fit:** `fill` (default), `fit`, `stretch` — image center aligned to the **center of the virtual desktop** bounding box (1:1 VD↔image pixel mapping after scale)
- Live layout preview — click monitor, drag to pan, wheel to zoom, arrow keys to nudge
- **Apply to desktop** — Plasma per screen, or `swww` / `awww`
- **Live apply** (optional)
- Save/load profiles (JSON) with per-monitor transform values
- Export: PNG per monitor + `apply_wallpaper.sh` + `manifest.json`
- Image picker with folder tree and thumbnail preview

## Requirements (KDE Plasma)

| Component | Purpose |
|-----------|---------|
| Python 3.10+ | Runtime |
| [Pillow](https://pypi.org/project/pillow/) | Image processing (`requirements.txt`) |
| `python3-tkinter` | GUI |
| `kscreen-doctor` | Monitor layout (package `kscreen`) |
| `qdbus` or `qdbus-qt6` + `qt6-dbus` | Plasma wallpaper via D-Bus |
| Active **Plasma Wayland session** | D-Bus bus, `plasmashell` |

Optional for non-KDE: `swww` (or `awww`).

## Quick start

```bash
git clone https://github.com/lukasfuscic19/Fugarius-Wallpaper.git
cd Fugarius-Wallpaper
./install.sh
./run.sh
```

Run from **Konsole** (or another terminal in your graphical KDE session), not from an isolated IDE terminal — session D-Bus must be available.

### Optional: application menu entry + icon

```bash
./packaging/generate-icons.sh    # optional if assets/icons already present
./packaging/install-desktop.sh   # after ./install.sh
```

### Fedora / Nobara RPM (KDE)

```bash
./packaging/build-rpm.sh 0.2.0
sudo dnf install dist/fugarius-wallpaper-0.2.0-*.noarch.rpm
```

Installs `/usr/bin/fugarius-wallpaper`, desktop entry, icons, and AppStream metadata (`dnf search fugarius` / Discover).

### AppImage

```bash
./packaging/build-release.sh 0.2.0   # RPM + AppImage → dist/
chmod +x dist/Fugarius-Wallpaper-0.2.0-x86_64.AppImage
./dist/Fugarius-Wallpaper-0.2.0-x86_64.AppImage
```

The AppImage bundles Pillow in a venv; **tkinter**, **qdbus**, and **kscreen-doctor** still come from the host system. See [packaging/build-appimage.sh](packaging/build-appimage.sh).

### Manual packages

**Fedora / Nobara:**

```bash
sudo dnf install python3 python3-pip python3-tkinter kscreen qt6-dbus
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

**Debian / Ubuntu:**

```bash
sudo apt install python3 python3-pip python3-tk python3-venv kscreen
# qdbus from qtchooser / qt6 tools as available on your release
```

## Typical workflow

1. **Open source image** — use enough resolution for your combined desktop size.
2. **Re-detect monitors** after changing layout or if outputs are missing.
3. **Auto-fit** (`fill` is default; re-run after each new image).
4. Fine-tune **per monitor** (select monitor, then sliders / preview drag / wheel).
5. **Apply to desktop** — verify on real monitors.
6. **Save profile** when satisfied.

## How the panorama works

1. All monitors form one rectangle: min/max of `pos_x`, `pos_y`, `width`, `height`.
2. Auto-fit picks one **zoom** (and `stretch` resizes to that rectangle).
3. Auto-fit aligns image **center** to virtual desktop **center**; per-monitor offsets/zoom fine-tune each viewport.
4. Each monitor crops the region that matches its rectangle on that plane (black where the image does not reach, e.g. in `fit` mode).

## KDE Plasma apply

- Uses `org.kde.image` with fill mode **Scaled and cropped** (`FillMode=2`) so panels are not covered.
- Crops: `~/.cache/fugarius-wallpaper/` (timestamped); copies in `~/Pictures/FugariusWallpaper/`.
- Use wallpaper type **Image** in Plasma settings (not Slideshow) if something reverts.
- Screen assignment uses **positions** from kscreen + `~/.config/kwinoutputconfig.json`, not output-name guesses.

### Panels hidden or apply failed?

```bash
./restore_panels.sh
# optional: ./restore_panels.sh 'file:///path/to/previous/wallpaper.png'
```

Then `./run.sh` → **Re-detect monitors** → **Apply** again.

## Other compositors (`swww` / `awww`)

- Install and start `swww-daemon` (or `awww-daemon`).
- Fill **Wayland output name** per monitor (`swww query` or **Re-detect monitors**).
- **Apply to desktop** or use exported `apply_wallpaper.sh`.

## Project layout

| Path | Purpose |
|------|---------|
| `app.py` | Main GUI and rendering |
| `install.sh` | System deps + Python venv |
| `run.sh` | Launch with session env (D-Bus, Wayland) |
| `restore_panels.sh` | Fix Plasma fill mode / panels |
| `verify_panorama.py` | CLI alignment check |
| `assets/` | App icon (`icon-source.png`, Freedesktop hicolor tree) |
| `packaging/` | `.desktop`, icons, AppImage build, menu install |
| `CHANGELOG.md` | Release notes |
| `requirements.txt` | Pillow |

## Verify alignment (optional)

```bash
source .venv/bin/activate
python verify_panorama.py
```

Expect `OK` when synthetic crops match the detected layout; samples in `_verify_out/` (gitignored).

## For porters and other setups

- **Adjust Plasma mapping:** `assign_plasma_screen_ids()` / `_match_profiles_to_plasma_screens()` in `app.py` — requires `kwinoutputconfig.json` and D-Bus desktop list.
- **Adjust detection:** `detect_monitors()` — prefer `kscreen-doctor -j`.
- **New compositor:** implement in `resolve_wallpaper_backend()` and `apply_wallpaper_images()`.
- **AppImage / Flatpak:** bundle app + venv; declare host deps (`tkinter`, KDE tools) or ship a Plasma-only wrapper. This repo ships a minimal AppImage build script, not a full Flatpak manifest.

## License

MIT — see [LICENSE](LICENSE).
