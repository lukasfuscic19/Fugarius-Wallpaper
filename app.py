import json
import os
import re
import shutil
import subprocess
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk


@dataclass
class MonitorProfile:
    name: str
    width: int
    height: int
    pos_x: int
    pos_y: int
    output_name: str = ""
    zoom: float = 1.0
    offset_x: int = 0
    offset_y: int = 0
    rotation: float = 0.0


# ──────────────────────────────────────────────
# Monitor detection
# ──────────────────────────────────────────────

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def _run_as_user(cmd: list[str], timeout: int = 5) -> str:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _is_process_running(name: str) -> bool:
    try:
        result = subprocess.run(["pgrep", "-x", name], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def _try_awww_query() -> list["MonitorProfile"]:
    """Start awww-daemon if not running, query, then kill it if we started it."""
    import time

    daemon_was_running = _is_process_running("awww-daemon")
    daemon_proc = None

    if not daemon_was_running:
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        try:
            daemon_proc = subprocess.Popen(
                ["awww-daemon"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            time.sleep(1.5)  # Give daemon time to start
        except FileNotFoundError:
            return []

    # Try query
    profiles = []
    for binary in ("awww", "swww"):
        raw = _run_as_user([binary, "query"])
        if raw:
            profiles = _parse_awww_query(raw)
            if profiles:
                break

    # Kill daemon only if we started it
    if daemon_proc and not daemon_was_running:
        try:
            daemon_proc.terminate()
            daemon_proc.wait(timeout=3)
        except Exception:
            pass

    return profiles


def _parse_awww_query(raw: str) -> list[MonitorProfile]:
    profiles: list[MonitorProfile] = []
    for line in _strip_ansi(raw).splitlines():
        line = line.strip()
        if not line:
            continue
        m_name = re.match(r"^([A-Za-z0-9_\-]+)\s*:", line)
        if not m_name:
            continue
        output_name = m_name.group(1)
        m_geo = re.search(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", line)
        if not m_geo:
            continue
        w, h, px, py = int(m_geo.group(1)), int(m_geo.group(2)), int(m_geo.group(3)), int(m_geo.group(4))
        profiles.append(MonitorProfile(name=output_name, width=w, height=h, pos_x=px, pos_y=py, output_name=output_name))
    return profiles


def _parse_kscreen(raw: str) -> list[MonitorProfile]:
    profiles: list[MonitorProfile] = []
    raw = _strip_ansi(raw)
    current_name: str | None = None
    current_geo:  tuple[int, int, int, int] | None = None

    for line in raw.splitlines():
        m_out = re.match(r"\s*Output:\s*\d+\s+(\S+)", line)
        if m_out:
            if current_name and current_geo:
                profiles.append(MonitorProfile(
                    name=current_name, width=current_geo[2], height=current_geo[3],
                    pos_x=current_geo[0], pos_y=current_geo[1], output_name=current_name,
                ))
            candidate = m_out.group(1)
            if re.match(r"[0-9a-f]{8}-", candidate):
                continue
            current_name = candidate
            current_geo  = None
            continue

        m_geo = re.search(r"Geometry:\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)", line)
        if m_geo and current_name:
            current_geo = (int(m_geo.group(1)), int(m_geo.group(2)), int(m_geo.group(3)), int(m_geo.group(4)))

    if current_name and current_geo:
        profiles.append(MonitorProfile(
            name=current_name, width=current_geo[2], height=current_geo[3],
            pos_x=current_geo[0], pos_y=current_geo[1], output_name=current_name,
        ))
    return profiles


def detect_monitors() -> list[MonitorProfile]:
    # 1. awww/swww (start daemon if needed, kill after if we started it)
    profiles = _try_awww_query()
    if profiles:
        return profiles

    # 2. kscreen-doctor
    raw = _run_as_user(["kscreen-doctor", "-o"])
    if raw:
        profiles = _parse_kscreen(raw)
        if profiles:
            return profiles

    return []


def query_outputs() -> list[str]:
    """Get output names from awww/swww (daemon must be running)."""
    for binary in ("awww", "swww"):
        raw = _run_as_user([binary, "query"])
        if raw:
            names = [
                m.group(1)
                for line in raw.splitlines()
                if (m := re.match(r"^([A-Za-z0-9_\-]+)\s*:", line.strip()))
            ]
            if names:
                return names
    return []


def _user_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    return env


def wallpaper_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(base) / "fugarius-wallpaper"


def resolve_wallpaper_backend() -> str | None:
    for binary in ("swww", "awww"):
        if shutil.which(binary):
            return binary
    return None


def ensure_wallpaper_daemon(binary: str) -> tuple[bool, str]:
    daemon = f"{binary}-daemon"
    if _is_process_running(daemon):
        return True, ""
    try:
        subprocess.Popen(
            [daemon],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_user_env(),
        )
        time.sleep(0.8)
    except FileNotFoundError:
        return False, f"{daemon} not found."
    if _is_process_running(daemon):
        return True, ""
    return False, f"{daemon} did not start. Try: {daemon}"


def apply_wallpaper_images(binary: str, outputs: list[tuple[str, Path]]) -> tuple[bool, str]:
    env = _user_env()
    errors: list[str] = []
    for output_name, image_path in outputs:
        result = subprocess.run(
            [binary, "img", "--outputs", output_name, image_path.as_posix()],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "unknown error").strip()
            errors.append(f"{output_name}: {msg}")
    if errors:
        return False, "\n".join(errors)
    return True, ""


# ──────────────────────────────────────────────
# Fixed-size image file picker (replaces native askopenfilename)
# ──────────────────────────────────────────────

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"})


class ImageFileDialog(tk.Toplevel):
    """Non-resizable file picker — avoids KDE/native dialog width jumping."""

    WIDTH = 820
    HEIGHT = 520

    def __init__(self, parent: tk.Tk, initial_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.title("Select source image")
        self.resizable(False, False)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(self.WIDTH, self.HEIGHT)
        self.maxsize(self.WIDTH, self.HEIGHT)
        self.transient(parent)
        self.update_idletasks()
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.WIDTH) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.HEIGHT) // 2)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{px}+{py}")
        self.grab_set()
        self.result: str | None = None
        self._items: list[Path] = []

        start = initial_dir if initial_dir and initial_dir.is_dir() else Path.home() / "Pictures"
        if not start.is_dir():
            start = Path.home()
        self.current_dir = start.resolve()

        self._build_ui()
        self._refresh_listing()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.wait_window()

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        path_row = ttk.Frame(self)
        path_row.pack(fill=tk.X, **pad)
        ttk.Button(path_row, text="Up", width=5, command=self._go_up).pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=str(self.current_dir))
        entry = ttk.Entry(path_row, textvariable=self.path_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        entry.bind("<Return>", lambda _e: self._go_to_path())
        ttk.Button(path_row, text="Go", width=5, command=self._go_to_path).pack(side=tk.LEFT)

        list_outer = ttk.Frame(self, padding=(10, 0, 10, 6))
        list_outer.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_outer)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(
            list_outer,
            yscrollcommand=scroll.set,
            font=("Sans", 11),
            activestyle="dotbox",
            selectmode=tk.SINGLE,
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self._on_activate)

        btn_row = ttk.Frame(self, padding=10)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Cancel", width=12, command=self._cancel).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_row, text="Open", width=12, command=self._open_selected).pack(side=tk.RIGHT)

    def _refresh_listing(self) -> None:
        self.listbox.delete(0, tk.END)
        self._items.clear()
        self.path_var.set(str(self.current_dir))

        try:
            children = list(self.current_dir.iterdir())
        except OSError as exc:
            messagebox.showerror("Cannot read folder", str(exc), parent=self)
            return

        parent_dir = self.current_dir.parent
        if parent_dir != self.current_dir:
            self._items.append(parent_dir)
            self.listbox.insert(tk.END, "[ .. ]  parent folder")

        dirs = sorted((p for p in children if p.is_dir()), key=lambda p: p.name.lower())
        files = sorted(
            (p for p in children if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS),
            key=lambda p: p.name.lower(),
        )
        for folder in dirs:
            self._items.append(folder)
            self.listbox.insert(tk.END, f"[ {folder.name} ]")
        for file in files:
            self._items.append(file)
            self.listbox.insert(tk.END, file.name)

    def _selected_path(self) -> Path | None:
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self._items[sel[0]]

    def _go_up(self) -> None:
        parent = self.current_dir.parent
        if parent != self.current_dir:
            self.current_dir = parent
            self._refresh_listing()

    def _go_to_path(self) -> None:
        target = Path(self.path_var.get().strip()).expanduser()
        if target.is_file():
            self.result = str(target.resolve())
            self._close()
            return
        if target.is_dir():
            self.current_dir = target.resolve()
            self._refresh_listing()
            return
        messagebox.showerror("Invalid path", "Folder does not exist.", parent=self)

    def _on_activate(self, _event: tk.Event | None = None) -> None:
        path = self._selected_path()
        if path is None:
            return
        if path.is_dir():
            self.current_dir = path
            self._refresh_listing()
        else:
            self.result = str(path)
            self._close()

    def _open_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("Select file", "Choose an image from the list.", parent=self)
            return
        if path.is_dir():
            self.current_dir = path
            self._refresh_listing()
            return
        self.result = str(path)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# ──────────────────────────────────────────────
# First-run setup dialog (fallback only)
# ──────────────────────────────────────────────

class MonitorSetupDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("Monitor Setup — Fugarius Wallpaper")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.result: list[MonitorProfile] = []

        ttk.Label(self, text="Monitors could not be auto-detected.\nPlease configure your displays below.", padding=12).grid(row=0, column=0, columnspan=2)
        ttk.Label(self, text="Number of monitors:").grid(row=1, column=0, sticky=tk.W, padx=12, pady=4)
        self.count_var = tk.IntVar(value=2)
        ttk.Spinbox(self, textvariable=self.count_var, from_=1, to=6, increment=1, width=5).grid(row=1, column=1, sticky=tk.W, padx=12)
        ttk.Button(self, text="Set monitor count", command=self._rebuild_rows).grid(row=2, column=0, columnspan=2, pady=6)

        self.rows_frame = ttk.Frame(self)
        self.rows_frame.grid(row=3, column=0, columnspan=2, padx=12, pady=4)

        ttk.Button(self, text="OK",     command=self._ok,     width=12).grid(row=4, column=0, pady=12)
        ttk.Button(self, text="Cancel", command=self._cancel, width=12).grid(row=4, column=1, pady=12)

        self.row_widgets: list[dict] = []
        self._rebuild_rows()
        self.wait_window()

    def _rebuild_rows(self) -> None:
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self.row_widgets.clear()
        count = self.count_var.get()
        for col, h in enumerate(["Name", "Width", "Height", "Pos X", "Pos Y", "Output name"]):
            ttk.Label(self.rows_frame, text=h, font=("Sans", 9, "bold")).grid(row=0, column=col, padx=6, pady=2)
        for i in range(count):
            row = dict(
                name=tk.StringVar(value=f"Monitor {i+1}"),
                width=tk.IntVar(value=1920), height=tk.IntVar(value=1080),
                pos_x=tk.IntVar(value=i * 1920), pos_y=tk.IntVar(value=0),
                output=tk.StringVar(value=""),
            )
            for col, key in enumerate(["name", "width", "height", "pos_x", "pos_y", "output"]):
                ttk.Entry(self.rows_frame, textvariable=row[key], width=10).grid(row=i+1, column=col, padx=4, pady=2)
            self.row_widgets.append(row)

    def _ok(self) -> None:
        profiles = []
        for row in self.row_widgets:
            try:
                profiles.append(MonitorProfile(
                    name=row["name"].get() or "Monitor",
                    width=int(row["width"].get()), height=int(row["height"].get()),
                    pos_x=int(row["pos_x"].get()), pos_y=int(row["pos_y"].get()),
                    output_name=row["output"].get().strip(),
                ))
            except ValueError:
                messagebox.showerror("Invalid input", "Please enter valid numbers.")
                return
        self.result = profiles
        self._close()

    def _cancel(self) -> None:
        self.result = []
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# ──────────────────────────────────────────────
# Image rendering
# ──────────────────────────────────────────────

def render_monitor_image(source: Image.Image, profile: MonitorProfile) -> Image.Image:
    src     = source.convert("RGB")
    scaled  = src.resize((max(1, int(src.width * profile.zoom)), max(1, int(src.height * profile.zoom))), Image.Resampling.LANCZOS)
    rotated = scaled.rotate(profile.rotation, resample=Image.Resampling.BICUBIC, expand=True)
    cx, cy  = rotated.width // 2 + profile.offset_x, rotated.height // 2 + profile.offset_y
    left, upper = cx - profile.width // 2, cy - profile.height // 2
    right, lower = left + profile.width, upper + profile.height
    out = Image.new("RGB", (profile.width, profile.height), color=(0, 0, 0))
    sl, su = max(0, left), max(0, upper)
    sr, sb = min(rotated.width, right), min(rotated.height, lower)
    if sr > sl and sb > su:
        out.paste(rotated.crop((sl, su, sr, sb)), (sl - left, su - upper))
    return out


def auto_fit_zoom(source: Image.Image, profiles: list[MonitorProfile], mode: str = "fill") -> float:
    if not profiles:
        return 1.0
    total_w = max(p.pos_x + p.width  for p in profiles) - min(p.pos_x for p in profiles)
    total_h = max(p.pos_y + p.height for p in profiles) - min(p.pos_y for p in profiles)
    ratio_w = total_w / max(1, source.width)
    ratio_h = total_h / max(1, source.height)
    if mode == "fill":
        return max(ratio_w, ratio_h)   # pokryj vše, ořízni přesah
    elif mode == "fit":
        return min(ratio_w, ratio_h)   # vleze celý, padding
    else:  # stretch
        return max(ratio_w, ratio_h)   # stejné jako fill, stretch řeší renderer


# ──────────────────────────────────────────────
# Main App
# ──────────────────────────────────────────────

class WallpaperPanoramaApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Fugarius Wallpaper")
        self.root.geometry("1450x880")
        self.source_path:  Path | None  = None
        self.source_image: Image.Image | None = None
        self.profiles:     list[MonitorProfile] = []
        self.current_index = 0
        self.preview_refs: list[ImageTk.PhotoImage] = []
        self._ui_busy = False
        self._closing = False
        self._preview_updating = False
        self._preview_after: str | None = None
        self._last_preview_key: object | None = None
        self._last_image_dir: Path | None = None
        self._apply_after: str | None = None
        self._value_labels: dict[str, tk.StringVar] = {}
        self._scales: dict[str, ttk.Scale] = {}
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)
        self._detect_and_load_monitors()
        self._load_profile_into_controls()
        self._invalidate_preview()
        self.root.after_idle(self._schedule_preview_update)

    def _detect_and_load_monitors(self) -> None:
        profiles = detect_monitors()
        if profiles:
            self.profiles = profiles
            outputs = query_outputs()
            for i, p in enumerate(self.profiles):
                if not p.output_name and i < len(outputs):
                    p.output_name = outputs[i]
            self.status_var.set(f"Auto-detected {len(self.profiles)} monitor(s).")
            self._invalidate_preview()
        else:
            dlg = MonitorSetupDialog(self.root)
            if dlg.result:
                self.profiles = dlg.result
                self.status_var.set(f"Configured {len(self.profiles)} monitor(s) manually.")
            else:
                self.profiles = [MonitorProfile("Monitor 1", 1920, 1080, 0, 0)]
                self.status_var.set("No monitors configured. Add monitors manually.")
            self._invalidate_preview()
        self._refresh_monitor_list()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        # Fixed-width sidebar — prevents buttons/labels resizing when values change.
        left = ttk.Frame(outer, width=300)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        right = ttk.Frame(outer)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        btn_opts = dict(fill=tk.X, padx=2)
        ttk.Button(left, text="Open source image", command=self.open_source).pack(**btn_opts, pady=(0, 4))
        ttk.Button(left, text="Re-detect monitors", command=self._detect_and_load_monitors).pack(**btn_opts, pady=2)

        fit_frame = ttk.Frame(left)
        fit_frame.pack(fill=tk.X, pady=2)
        self.fit_mode_var = tk.StringVar(value="fill")
        combo = ttk.Combobox(
            fit_frame,
            textvariable=self.fit_mode_var,
            values=["fill", "fit", "stretch"],
            state="readonly",
            width=12,
        )
        combo.pack(fill=tk.X)
        ttk.Button(left, text="Auto-fit", command=self.auto_fit).pack(**btn_opts, pady=(4, 2))
        self._add_tooltip(
            combo,
            "fill = pokryje celý desktop, přebytečné ořízne\n"
            "fit = vleze celý obrázek, okraje zůstanou černé\n"
            "stretch = roztáhne bez zachování poměru stran",
        )
        apply_frame = ttk.LabelFrame(left, text="Desktop")
        apply_frame.pack(fill=tk.X, pady=(10, 4))
        apply_btn = ttk.Button(apply_frame, text="Apply to desktop", command=self.apply_to_desktop)
        apply_btn.pack(**btn_opts, pady=4)
        self.live_apply_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            apply_frame,
            text="Live apply (sliders)",
            variable=self.live_apply_var,
        ).pack(anchor=tk.W, padx=4, pady=(0, 4))
        self._add_tooltip(
            apply_btn,
            "Nastaví pozadí přímo přes swww/awww (Wayland).\n"
            "Vyžaduje vyplněné Wayland output name u každého monitoru.",
        )

        ttk.Button(left, text="Save profile", command=self.save_profile).pack(**btn_opts, pady=2)
        ttk.Button(left, text="Load profile", command=self.load_profile).pack(**btn_opts, pady=2)
        ttk.Button(left, text="Export wallpapers…", command=self.export_wallpapers).pack(**btn_opts, pady=2)

        ttk.Label(left, text="Monitor").pack(anchor=tk.W, pady=(14, 2))
        self.monitor_var   = tk.StringVar()
        self.monitor_combo = ttk.Combobox(left, textvariable=self.monitor_var, state="readonly")
        self.monitor_combo.pack(fill=tk.X, pady=(0, 10))
        self.monitor_combo.bind("<<ComboboxSelected>>", self.on_monitor_selected)

        controls = ttk.LabelFrame(left, text="Transform"); controls.pack(fill=tk.X, pady=6)
        self.zoom_var     = tk.DoubleVar(value=1.0)
        self.rotation_var = tk.DoubleVar(value=0.0)
        self.offset_x_var = tk.IntVar(value=0)
        self.offset_y_var = tk.IntVar(value=0)
        self.width_var    = tk.IntVar(value=1920)
        self.height_var   = tk.IntVar(value=1080)
        self.pos_x_var    = tk.IntVar(value=0)
        self.pos_y_var    = tk.IntVar(value=0)
        self.output_var   = tk.StringVar(value="")

        self._add_slider(controls, "zoom", "Zoom", self.zoom_var, 0.1, 5.0, float_fmt=True)
        self._add_slider(controls, "rotation", "Rotation", self.rotation_var, -35.0, 35.0, float_fmt=True)
        self._add_slider(controls, "offset_x", "Offset X", self.offset_x_var, -8000, 8000)
        self._add_slider(controls, "offset_y", "Offset Y", self.offset_y_var, -8000, 8000)

        geo = ttk.LabelFrame(left, text="Monitor geometry"); geo.pack(fill=tk.X, pady=(10, 6))
        self._add_spinbox(geo, "Width",  self.width_var,  320,   8000)
        self._add_spinbox(geo, "Height", self.height_var, 200,   6000)
        self._add_spinbox(geo, "Pos X",  self.pos_x_var,  -8000, 8000)
        self._add_spinbox(geo, "Pos Y",  self.pos_y_var,  -8000, 8000)

        out_frame = ttk.Frame(left); out_frame.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(out_frame, text="Wayland output name").pack(anchor=tk.W)
        out_entry = ttk.Entry(out_frame, textvariable=self.output_var); out_entry.pack(fill=tk.X)
        out_entry.bind("<FocusOut>", lambda _: self.apply_controls())

        ttk.Button(left, text="Add monitor",    command=self.add_monitor).pack(fill=tk.X, pady=(12, 2))
        ttk.Button(left, text="Remove monitor", command=self.remove_monitor).pack(fill=tk.X, pady=2)

        self.status_var = tk.StringVar(value="Detecting monitors…")
        ttk.Label(left, textvariable=self.status_var, wraplength=280, justify=tk.LEFT).pack(
            fill=tk.X, side=tk.BOTTOM, pady=(14, 0)
        )

        self.preview_canvas = tk.Canvas(right, bg="#1E1E1E", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", self._schedule_preview_update)

    def _add_tooltip(self, widget, text: str) -> None:
        tip = None
        def show(e):
            nonlocal tip
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{e.x_root+12}+{e.y_root+6}")
            ttk.Label(tip, text=text, background="#FFFFE0", relief=tk.SOLID, borderwidth=1, padding=4).pack()
        def hide(e):
            nonlocal tip
            if tip:
                tip.destroy()
                tip = None
        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _format_control_value(self, key: str, var: tk.Variable) -> str:
        if key in ("zoom", "rotation"):
            return f"{float(var.get()):.2f}"
        return f"{int(var.get())}"

    def _add_slider(
        self,
        parent: ttk.LabelFrame,
        key: str,
        text: str,
        var: tk.Variable,
        mn: float,
        mx: float,
        *,
        float_fmt: bool = False,
    ) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row, text=text, width=10).pack(side=tk.LEFT)
        shown = tk.StringVar(value=self._format_control_value(key, var))
        self._value_labels[key] = shown
        ttk.Label(row, textvariable=shown, width=8, anchor=tk.E, font=("TkFixedFont", 10)).pack(side=tk.RIGHT)

        scale = ttk.Scale(parent, variable=var, from_=mn, to=mx, orient=tk.HORIZONTAL)
        scale.pack(fill=tk.X, pady=(2, 0))
        self._scales[key] = scale

        def on_drag(_value: str) -> None:
            if self._ui_busy:
                return
            shown.set(self._format_control_value(key, var))

        def on_release(_event: tk.Event) -> None:
            if not self._ui_busy:
                self.apply_controls()
                self._schedule_live_apply()

        scale.configure(command=on_drag)
        scale.bind("<ButtonRelease-1>", on_release)
        _ = float_fmt

    def _add_spinbox(self, parent, text, var, mn, mx) -> None:
        row = ttk.Frame(parent); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=text, width=8).pack(side=tk.LEFT)
        sp = ttk.Spinbox(row, textvariable=var, from_=mn, to=mx, increment=1)
        sp.pack(side=tk.LEFT, fill=tk.X, expand=True)
        sp.bind("<FocusOut>", lambda _: self.apply_controls())
        sp.bind("<Return>",   lambda _: self.apply_controls())

    def open_source(self) -> None:
        dlg = ImageFileDialog(self.root, self._last_image_dir)
        if not dlg.result:
            return
        path = dlg.result
        self._last_image_dir = Path(path).parent
        self.source_path = Path(path)
        self.source_image = Image.open(path)
        self.status_var.set(f"Loaded: {self.source_path.name}  ({self.source_image.width}×{self.source_image.height})")
        self._invalidate_preview()
        self.auto_fit(silent=True)
        if self.live_apply_var.get():
            self._schedule_live_apply()

    def auto_fit(self, silent: bool = False) -> None:
        if not self.source_image:
            if not silent:
                messagebox.showinfo("Auto-fit", "Load an image first.")
            return
        mode = self.fit_mode_var.get()
        zoom = round(auto_fit_zoom(self.source_image, self.profiles, mode), 3)

        # Virtual desktop bounds
        min_x = min(p.pos_x for p in self.profiles)
        min_y = min(p.pos_y for p in self.profiles)

        # Scaled image size
        img_w = self.source_image.width  * zoom
        img_h = self.source_image.height * zoom

        # Total virtual desktop size
        total_w = max(p.pos_x + p.width  for p in self.profiles) - min_x
        total_h = max(p.pos_y + p.height for p in self.profiles) - min_y

        # Center of virtual desktop in image coords
        center_x = img_w / 2
        center_y = img_h / 2

        # Map virtual-desktop pixels → scaled image pixels (fill/fit can scale past desktop size).
        scale_x = img_w / total_w if total_w else 1.0
        scale_y = img_h / total_h if total_h else 1.0

        for p in self.profiles:
            p.zoom = zoom
            p.rotation = 0.0
            mon_cx = (p.pos_x - min_x) + p.width / 2
            mon_cy = (p.pos_y - min_y) + p.height / 2
            p.offset_x = int(mon_cx * scale_x - img_w / 2)
            p.offset_y = int(mon_cy * scale_y - img_h / 2)

        self._invalidate_preview()
        self._sync_controls_from_profile()
        self._schedule_preview_update()
        if not silent:
            self.status_var.set(f"Auto-fit ({mode}), zoom {zoom:.3f}")

    def save_profile(self) -> None:
        self.apply_controls()
        path = filedialog.asksaveasfilename(title="Save profile", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        Path(path).write_text(json.dumps([asdict(p) for p in self.profiles], indent=2), encoding="utf-8")
        self.status_var.set(f"Profile saved: {path}")

    def load_profile(self) -> None:
        path = filedialog.askopenfilename(title="Load profile", filetypes=[("JSON", "*.json")])
        if not path:
            return
        self.profiles = [MonitorProfile(**item) for item in json.loads(Path(path).read_text(encoding="utf-8"))]
        self.current_index = 0
        self._refresh_monitor_list()
        self._load_profile_into_controls()
        self._invalidate_preview()
        self._schedule_preview_update()
        self.status_var.set(f"Profile loaded: {path}")

    def add_monitor(self) -> None:
        n = len(self.profiles) + 1
        self.profiles.append(MonitorProfile(f"Monitor {n}", 1920, 1080, n * 100, n * 100))
        self.current_index = len(self.profiles) - 1
        self._refresh_monitor_list()
        self._load_profile_into_controls()
        self._schedule_preview_update()

    def remove_monitor(self) -> None:
        if len(self.profiles) <= 1:
            messagebox.showwarning("Cannot remove", "At least one monitor must remain.")
            return
        del self.profiles[self.current_index]
        self.current_index = max(0, self.current_index - 1)
        self._refresh_monitor_list()
        self._load_profile_into_controls()
        self._schedule_preview_update()

    def on_monitor_selected(self, _event) -> None:
        self.apply_controls()
        self.current_index = self.monitor_combo.current()
        self._load_profile_into_controls()
        self._invalidate_preview()
        self._schedule_preview_update()

    def apply_controls(self) -> None:
        if not self.profiles:
            return
        p = self.profiles[self.current_index]
        p.zoom, p.rotation = float(self.zoom_var.get()), float(self.rotation_var.get())
        p.offset_x, p.offset_y = int(self.offset_x_var.get()), int(self.offset_y_var.get())
        p.width, p.height = int(self.width_var.get()), int(self.height_var.get())
        p.pos_x, p.pos_y = int(self.pos_x_var.get()), int(self.pos_y_var.get())
        p.output_name = self.output_var.get().strip()
        for key, var in (
            ("zoom", self.zoom_var),
            ("rotation", self.rotation_var),
            ("offset_x", self.offset_x_var),
            ("offset_y", self.offset_y_var),
        ):
            if key in self._value_labels:
                self._value_labels[key].set(self._format_control_value(key, var))
        self._invalidate_preview()
        self._schedule_preview_update()

    def apply_controls_without_loop(self) -> None:
        if not self.profiles:
            return
        p = self.profiles[self.current_index]
        p.zoom, p.rotation = float(self.zoom_var.get()), float(self.rotation_var.get())
        p.offset_x, p.offset_y = int(self.offset_x_var.get()), int(self.offset_y_var.get())
        p.width, p.height = int(self.width_var.get()), int(self.height_var.get())
        p.pos_x, p.pos_y = int(self.pos_x_var.get()), int(self.pos_y_var.get())
        p.output_name = self.output_var.get().strip()

    def _refresh_monitor_list(self) -> None:
        names = [p.name for p in self.profiles]
        self.monitor_combo["values"] = names
        if names:
            self.monitor_combo.current(min(self.current_index, len(names) - 1))

    def _sync_controls_from_profile(self) -> None:
        """Push profile → widgets without triggering preview/slider feedback."""
        self._refresh_monitor_list()
        if not self.profiles:
            return
        p = self.profiles[self.current_index]
        self._ui_busy = True
        try:
            self.zoom_var.set(p.zoom)
            self.rotation_var.set(p.rotation)
            self.offset_x_var.set(p.offset_x)
            self.offset_y_var.set(p.offset_y)
            self.width_var.set(p.width)
            self.height_var.set(p.height)
            self.pos_x_var.set(p.pos_x)
            self.pos_y_var.set(p.pos_y)
            self.output_var.set(p.output_name)
            for key, var in (
                ("zoom", self.zoom_var),
                ("rotation", self.rotation_var),
                ("offset_x", self.offset_x_var),
                ("offset_y", self.offset_y_var),
            ):
                if key in self._value_labels:
                    self._value_labels[key].set(self._format_control_value(key, var))
        finally:
            self._ui_busy = False

    def _load_profile_into_controls(self) -> None:
        self._sync_controls_from_profile()

    def _invalidate_preview(self) -> None:
        self._last_preview_key = None

    def _preview_key(self) -> tuple:
        cw = max(self.preview_canvas.winfo_width(), 1)
        ch = max(self.preview_canvas.winfo_height(), 1)
        if not self.source_image or not self.profiles:
            return (cw, ch, 0, ())
        state = tuple(
            (
                p.zoom,
                p.rotation,
                p.offset_x,
                p.offset_y,
                p.width,
                p.height,
                p.pos_x,
                p.pos_y,
            )
            for p in self.profiles
        )
        return (cw, ch, self.current_index, state)

    def _cancel_preview_timer(self) -> None:
        if self._preview_after:
            try:
                self.root.after_cancel(self._preview_after)
            except tk.TclError:
                pass
            self._preview_after = None

    def _schedule_preview_update(self, event: tk.Event | None = None) -> None:
        if self._closing or self._preview_updating:
            return
        if event is not None and event.widget != self.preview_canvas:
            return
        self._cancel_preview_timer()
        self._preview_after = self.root.after(80, self._run_preview_update)

    def _run_preview_update(self) -> None:
        self._preview_after = None
        if self._closing:
            return
        self.update_preview()

    def on_quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._cancel_preview_timer()
        self._cancel_live_apply_timer()
        try:
            self.preview_canvas.unbind("<Configure>")
        except tk.TclError:
            pass
        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel):
                try:
                    child.grab_release()
                except tk.TclError:
                    pass
                child.destroy()
        self.preview_refs.clear()
        self.root.quit()
        self.root.destroy()

    def update_preview(self) -> None:
        if self._closing:
            return

        key = self._preview_key()
        if key == self._last_preview_key:
            return

        self._preview_updating = True
        try:
            self._last_preview_key = key
            self.preview_canvas.delete("all")
            self.preview_refs.clear()

            canvas_w = max(self.preview_canvas.winfo_width(), 200)
            canvas_h = max(self.preview_canvas.winfo_height(), 200)
            margin = 40

            if not self.source_image:
                self.preview_canvas.create_text(
                    canvas_w // 2,
                    canvas_h // 2,
                    text="Open source image to preview / export",
                    fill="#CCCCCC",
                    font=("Sans", 16),
                )
                return

            if not self._ui_busy:
                self.apply_controls_without_loop()

            min_x = min(p.pos_x for p in self.profiles)
            min_y = min(p.pos_y for p in self.profiles)
            total_w = max(p.pos_x + p.width for p in self.profiles) - min_x
            total_h = max(p.pos_y + p.height for p in self.profiles) - min_y

            avail_w = max(1, canvas_w - margin * 2)
            avail_h = max(1, canvas_h - margin * 2)
            scale = min(avail_w / max(1, total_w), avail_h / max(1, total_h))
            scale = max(0.05, scale)

            layout_w = total_w * scale
            layout_h = total_h * scale
            base_x = margin + (avail_w - layout_w) / 2
            base_y = margin + (avail_h - layout_h) / 2

            for i, p in enumerate(self.profiles):
                img = render_monitor_image(self.source_image, p)
                tw = max(10, int(p.width * scale))
                th = max(10, int(p.height * scale))
                thumb = img.resize((tw, th), Image.Resampling.BILINEAR)
                x = int(base_x + (p.pos_x - min_x) * scale)
                y = int(base_y + (p.pos_y - min_y) * scale)
                photo = ImageTk.PhotoImage(thumb)
                self.preview_refs.append(photo)
                self.preview_canvas.create_image(x, y, image=photo, anchor=tk.NW)
                color = "#5ED8FF" if i == self.current_index else "#E0E0E0"
                self.preview_canvas.create_rectangle(x, y, x + tw, y + th, outline=color, width=2)
                self.preview_canvas.create_text(
                    x + 8,
                    min(y + 6, y + th - 4),
                    anchor=tk.NW,
                    fill=color,
                    text=f"{p.name}  {p.width}×{p.height}",
                    font=("Sans", 10, "bold"),
                )

            self.preview_canvas.create_rectangle(
                base_x,
                base_y,
                base_x + layout_w,
                base_y + layout_h,
                outline="#666666",
                dash=(4, 4),
            )
        finally:
            self._preview_updating = False

    def _cancel_live_apply_timer(self) -> None:
        if self._apply_after:
            try:
                self.root.after_cancel(self._apply_after)
            except tk.TclError:
                pass
            self._apply_after = None

    def _schedule_live_apply(self) -> None:
        if self._closing or not self.live_apply_var.get() or not self.source_image:
            return
        self._cancel_live_apply_timer()
        self._apply_after = self.root.after(450, self._run_live_apply)

    def _run_live_apply(self) -> None:
        self._apply_after = None
        if self._closing:
            return
        self.apply_to_desktop(silent=True)

    def apply_to_desktop(self, silent: bool = False) -> bool:
        if not self.source_image:
            if not silent:
                messagebox.showinfo("Apply to desktop", "Load an image first.")
            return False

        self.apply_controls()
        missing = [p.name for p in self.profiles if not p.output_name.strip()]
        if missing:
            if not silent:
                messagebox.showerror(
                    "Missing output names",
                    "Each monitor needs a Wayland output name (e.g. DP-1).\n\n"
                    f"Missing: {', '.join(missing)}\n\n"
                    "Use Re-detect monitors or run: swww query",
                )
            return False

        binary = resolve_wallpaper_backend()
        if not binary:
            if not silent:
                messagebox.showerror(
                    "swww not found",
                    "Install a Wayland wallpaper daemon:\n\n  sudo dnf install swww\n\n"
                    "Then log out/in or run: swww-daemon",
                )
            return False

        ok, err = ensure_wallpaper_daemon(binary)
        if not ok:
            if not silent:
                messagebox.showerror("Wallpaper daemon", err)
            self.status_var.set(err)
            return False

        cache = wallpaper_cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        outputs: list[tuple[str, Path]] = []
        for profile in self.profiles:
            safe = re.sub(r"[^\w\-.]+", "_", profile.output_name)
            path = cache / f"{safe}.png"
            render_monitor_image(self.source_image, profile).save(path)
            outputs.append((profile.output_name, path))

        self.status_var.set(f"Applying via {binary}…")
        self.root.update_idletasks()

        ok, err = apply_wallpaper_images(binary, outputs)
        if not ok:
            if not silent:
                messagebox.showerror("Apply failed", err)
            self.status_var.set("Apply failed")
            return False

        self.status_var.set(f"Applied to {len(outputs)} monitor(s) via {binary}")
        return True

    def export_wallpapers(self) -> None:
        if not self.source_image:
            messagebox.showerror("No image", "Load source image first.")
            return
        self.apply_controls()
        target = filedialog.askdirectory(title="Select export directory")
        if not target:
            return
        out_dir = Path(target)
        generated: list[tuple[MonitorProfile, Path]] = []
        for i, profile in enumerate(self.profiles, start=1):
            output = out_dir / f"{i:02d}_{profile.name.lower().replace(' ', '_')}.png"
            render_monitor_image(self.source_image, profile).save(output)
            generated.append((profile, output))
        binary = resolve_wallpaper_backend() or "swww"
        script_lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'BINARY="{binary}"',
            "command -v \"$BINARY\" >/dev/null 2>&1 || { echo \"Install swww: sudo dnf install swww\"; exit 1; }",
            "",
            "\"${BINARY}-daemon\" >/dev/null 2>&1 || true",
            "sleep 1",
            "",
        ]
        for profile, img_path in generated:
            if profile.output_name:
                script_lines.append(
                    f"\"$BINARY\" img --outputs \"{profile.output_name}\" \"{img_path.as_posix()}\""
                )
        if not any(p.output_name for p, _ in generated):
            script_lines.append("echo 'No output names set. Fill output_name in app and export again.'")
        script_path = out_dir / "apply_wallpaper.sh"
        script_path.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
        script_path.chmod(0o755)
        (out_dir / "manifest.json").write_text(json.dumps({"source": str(self.source_path) if self.source_path else "", "profiles": [asdict(p) for p in self.profiles], "generated": [str(p) for _, p in generated], "script": str(script_path)}, indent=2), encoding="utf-8")
        self.status_var.set(f"Export complete → {out_dir}")
        messagebox.showinfo("Done", f"Wallpapers exported.\nRun:\n{script_path}")


def main() -> None:
    root = tk.Tk()
    app = WallpaperPanoramaApp(root)
    root.minsize(1100, 700)
    root.mainloop()


if __name__ == "__main__":
    main()
