import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

# Max pixel delta when matching kscreen positions to KWin layout entries.
POSITION_MATCH_TOLERANCE = 48


@dataclass
class MonitorProfile:
    name: str
    width: int
    height: int
    pos_x: int
    pos_y: int
    output_name: str = ""
    screen_id: int = -1  # kscreen output id (informational; ≠ Plasma screen index)
    plasma_screen: int = -1  # Plasma/KWin screen id (desktops()[i].screen)
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
    current_screen_id = -1
    current_geo: tuple[int, int, int, int] | None = None

    for line in raw.splitlines():
        m_out = re.match(r"\s*Output:\s*(\d+)\s+(\S+)", line)
        if m_out:
            if current_name and current_geo:
                profiles.append(MonitorProfile(
                    name=current_name,
                    width=current_geo[2],
                    height=current_geo[3],
                    pos_x=current_geo[0],
                    pos_y=current_geo[1],
                    output_name=current_name,
                    screen_id=current_screen_id,
                ))
            candidate = m_out.group(2)
            if re.match(r"[0-9a-f]{8}-", candidate):
                continue
            current_name = candidate
            try:
                current_screen_id = int(m_out.group(1))
            except ValueError:
                current_screen_id = -1
            current_geo = None
            continue

        m_geo = re.search(r"Geometry:\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)", line)
        if m_geo and current_name:
            current_geo = (int(m_geo.group(1)), int(m_geo.group(2)), int(m_geo.group(3)), int(m_geo.group(4)))

    if current_name and current_geo:
        profiles.append(MonitorProfile(
            name=current_name,
            width=current_geo[2],
            height=current_geo[3],
            pos_x=current_geo[0],
            pos_y=current_geo[1],
            output_name=current_name,
            screen_id=current_screen_id,
        ))
    return profiles


def _parse_kscreen_json(raw: str) -> list[MonitorProfile]:
    profiles: list[MonitorProfile] = []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return profiles
    for output in data.get("outputs", []):
        if not output.get("connected") or not output.get("enabled", True):
            continue
        name = (output.get("name") or "").strip()
        if not name or re.match(r"[0-9a-f]{8}-", name):
            continue
        pos = output.get("pos") or {}
        size = output.get("size") or {}
        try:
            px = int(pos.get("x", 0))
            py = int(pos.get("y", 0))
            w = int(size.get("width", 0))
            h = int(size.get("height", 0))
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        profiles.append(MonitorProfile(
            name=name,
            width=w,
            height=h,
            pos_x=px,
            pos_y=py,
            output_name=name,
            screen_id=int(output.get("id", -1)),
        ))
    return profiles


def detect_monitors() -> list[MonitorProfile]:
    raw_json = _run_as_user(["kscreen-doctor", "-j"])
    if raw_json:
        profiles = _parse_kscreen_json(raw_json)
        if profiles:
            return profiles

    if _plasma_available():
        raw = _run_as_user(["kscreen-doctor", "-o"])
        if raw:
            profiles = _parse_kscreen(raw)
            if profiles:
                return profiles

    profiles = _try_awww_query()
    if profiles:
        return profiles

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


def _dbus_from_plasma_process() -> str | None:
    """Read session bus address from running plasmashell (works when IDE omits env)."""
    for proc_name in ("plasmashell", "plasmashell6", "kwin_wayland"):
        try:
            result = subprocess.run(
                ["pgrep", "-x", proc_name],
                capture_output=True,
                text=True,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        for pid_s in result.stdout.split():
            try:
                pid = int(pid_s)
                raw = Path(f"/proc/{pid}/environ").read_bytes()
            except (OSError, ValueError):
                continue
            for entry in raw.split(b"\0"):
                if entry.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
                    return entry.decode(errors="replace").split("=", 1)[1]
    return None


def _user_env() -> dict[str, str]:
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        dbus = _dbus_from_plasma_process()
        if dbus:
            env["DBUS_SESSION_BUS_ADDRESS"] = dbus
        else:
            bus = Path(env["XDG_RUNTIME_DIR"]) / "bus"
            if bus.exists():
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return env


def _qdbus_binaries() -> list[str]:
    bins: list[str] = []
    for name in ("qdbus", "qdbus-qt6"):
        if shutil.which(name) and name not in bins:
            bins.append(name)
    return bins


def _plasma_eval(script: str, timeout: int = 15) -> tuple[int, str, str]:
    """Run org.kde.PlasmaShell.evaluateScript; returns (returncode, stdout, stderr)."""
    env = _user_env()
    last_rc, last_out, last_err = 1, "", "qdbus not found"
    for qdbus in _qdbus_binaries():
        result = subprocess.run(
            [
                qdbus,
                "org.kde.plasmashell",
                "/PlasmaShell",
                "org.kde.PlasmaShell.evaluateScript",
                script,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = f"{out}\n{err}".lower()
        if result.returncode == 0 and "error" not in combined and "referenceerror" not in combined:
            return 0, out, err
        last_rc, last_out, last_err = result.returncode, out, err
    return last_rc, last_out, last_err


def wallpaper_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(base) / "fugarius-wallpaper"


def wallpaper_pictures_dir() -> Path:
    base = os.environ.get("XDG_PICTURES_DIR", str(Path.home() / "Pictures"))
    return Path(base) / "FugariusWallpaper"


def _safe_output_slug(profile: MonitorProfile) -> str:
    return re.sub(r"[^\w\-.]+", "_", profile.output_name or profile.name)


def _prune_wallpaper_cache(safe: str, keep: Path) -> None:
    for old in wallpaper_cache_dir().glob(f"{safe}-*.png"):
        if old.resolve() != keep.resolve():
            try:
                old.unlink()
            except OSError:
                pass


def _plasma_available() -> bool:
    if not _qdbus_binaries():
        return False
    rc, out, err = _plasma_eval("print(desktops().length)", timeout=10)
    if rc != 0 or not out.isdigit() or int(out) <= 0:
        return False
    combined = (out + err).lower()
    return "cannot find" not in combined and "unknownobject" not in combined


def _plasma_desktop_count() -> int:
    rc, out, _ = _plasma_eval("print(desktops().length)", timeout=10)
    if rc == 0 and out.isdigit():
        return int(out)
    return 0


def _positions_match(
    ax: int,
    ay: int,
    bx: int,
    by: int,
    tolerance: int = POSITION_MATCH_TOLERANCE,
) -> bool:
    return abs(ax - bx) <= tolerance and abs(ay - by) <= tolerance


def _plasma_desktop_for_screen() -> dict[int, int]:
    """Map KWin/Plasma screen index → desktop index (first desktop on that screen)."""
    mapping: dict[int, int] = {}
    rc, raw, err = _plasma_eval(
        'var ds=desktops(),r=[];for(var i=0;i<ds.length;i++){r.push(i+"|"+ds[i].screen)}print(r.join(","))',
        timeout=15,
    )
    if rc == 0 and raw:
        for part in raw.split(","):
            if "|" not in part:
                continue
            desktop_idx_s, screen_id_s = part.split("|", 1)
            try:
                desktop_idx = int(desktop_idx_s)
                screen_id = int(screen_id_s)
            except ValueError:
                continue
            if screen_id not in mapping:
                mapping[screen_id] = desktop_idx

    if mapping:
        return mapping

    # Fallback: one qdbus call per desktop (more reliable on some setups).
    count = _plasma_desktop_count()
    for desktop_idx in range(count):
        rc, screen_s, _ = _plasma_eval(f"print(desktops()[{desktop_idx}].screen)", timeout=10)
        if rc == 0 and screen_s.lstrip("-").isdigit():
            screen_id = int(screen_s)
            if screen_id not in mapping:
                mapping[screen_id] = desktop_idx

    if not mapping and err:
        print(f"[fugarius-wallpaper] Plasma layout: {err}", file=sys.stderr)
    return mapping


def _kwin_output_layout_entries(
    kscreen_positions: set[tuple[int, int]] | None = None,
) -> list[dict[str, int]]:
    """KWin outputIndex + position from the active layout in kwinoutputconfig.json."""
    path = Path.home() / ".config/kwinoutputconfig.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    setups: list[dict] = []
    for block in doc:
        if block.get("name") == "setups":
            setups = block.get("data") or []
            break
    if not setups:
        return []

    if kscreen_positions is None:
        kscreen_positions = {
            (p.pos_x, p.pos_y)
            for p in _parse_kscreen_json(_run_as_user(["kscreen-doctor", "-j"]) or "")
        }
    if not kscreen_positions:
        kscreen_positions = None

    best_outputs: list[dict] = []
    best_score = -1
    for setup in setups:
        outputs = [o for o in setup.get("outputs", []) if o.get("enabled", True)]
        if not outputs:
            continue
        if kscreen_positions is not None:
            score = 0
            for o in outputs:
                ox = int(o.get("position", {}).get("x", 0))
                oy = int(o.get("position", {}).get("y", 0))
                for px, py in kscreen_positions:
                    if _positions_match(px, py, ox, oy):
                        score += 1
                        break
            if score > best_score:
                best_score = score
                best_outputs = outputs
        elif not best_outputs:
            best_outputs = outputs

    entries: list[dict[str, int]] = []
    for output in best_outputs:
        pos = output.get("position") or {}
        try:
            entries.append({
                "screen_id": int(output["outputIndex"]),
                "pos_x": int(pos.get("x", 0)),
                "pos_y": int(pos.get("y", 0)),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return entries


def _profile_center(profile: MonitorProfile) -> tuple[float, float]:
    return (profile.pos_x + profile.width / 2, profile.pos_y + profile.height / 2)


def _layout_entry_for_profile(
    profile: MonitorProfile,
    layout: list[dict[str, int]],
) -> dict[str, int] | None:
    for entry in layout:
        if _positions_match(profile.pos_x, profile.pos_y, entry["pos_x"], entry["pos_y"]):
            return entry
    best: dict[str, int] | None = None
    best_dist = math.inf
    pcx, pcy = _profile_center(profile)
    for entry in layout:
        dist = math.hypot(pcx - entry["pos_x"], pcy - entry["pos_y"])
        if dist < best_dist:
            best_dist = dist
            best = entry
    return best


def _containment_screen_by_resolution(cfg_text: str) -> dict[tuple[int, int], int]:
    """Map (width, height) → Plasma lastScreen when each resolution is unique in config."""
    buckets: dict[tuple[int, int], list[int]] = {}
    for match in re.finditer(
        r"\[Containments\]\[(\d+)\]\n(.*?)(?=\n\[Containments\]\[|\Z)",
        cfg_text,
        re.DOTALL,
    ):
        body = match.group(2)
        if "plugin=org.kde.plasma.folder" not in body or "formfactor=0" not in body:
            continue
        screen_m = re.search(r"^lastScreen=(\d+)", body, re.MULTILINE)
        geo_m = re.search(r"ItemGeometries-(\d+)x(\d+)", body)
        if not screen_m or not geo_m:
            continue
        key = (int(geo_m.group(1)), int(geo_m.group(2)))
        buckets.setdefault(key, []).append(int(screen_m.group(1)))
    return {size: ids[0] for size, ids in buckets.items() if len(ids) == 1}


def _match_profiles_to_plasma_screens(
    profiles: list[MonitorProfile],
    screen_to_desktop: dict[int, int],
) -> None:
    """Assign profile.plasma_screen using KWin layout positions (any monitor count/layout).

    KWin outputIndex can differ from Plasma screen id (e.g. index 3 on one output);
    positions from kwinoutputconfig.json are authoritative, then ids are reconciled.
    """
    kscreen_positions = {(p.pos_x, p.pos_y) for p in profiles}
    layout = _kwin_output_layout_entries(kscreen_positions)
    known_screens = set(screen_to_desktop.keys())

    if layout:
        paired: list[tuple[MonitorProfile, int]] = []
        for profile in profiles:
            entry = _layout_entry_for_profile(profile, layout)
            if entry is not None:
                paired.append((profile, entry["screen_id"]))

        for profile, kwin_id in paired:
            if kwin_id in known_screens:
                profile.plasma_screen = kwin_id

        used = {p.plasma_screen for p in profiles if p.plasma_screen >= 0}
        need_map = [p for p, _ in paired if p.plasma_screen < 0]
        remaining_screens = sorted(sid for sid in known_screens if sid not in used)
        if len(need_map) == len(remaining_screens):
            for profile, sid in zip(
                sorted(need_map, key=lambda p: (p.pos_y, p.pos_x, p.name)),
                remaining_screens,
            ):
                profile.plasma_screen = sid

    unmatched = [p for p in profiles if p.plasma_screen < 0]
    if not unmatched:
        return

    cfg_path = plasma_applets_config_path()
    if cfg_path.is_file():
        by_res = _containment_screen_by_resolution(cfg_path.read_text(encoding="utf-8"))
        used = {p.plasma_screen for p in profiles if p.plasma_screen >= 0}
        for profile in unmatched:
            sid = by_res.get((profile.width, profile.height))
            if sid is not None and sid in known_screens and sid not in used:
                profile.plasma_screen = sid
                used.add(sid)

    still = [p.name for p in profiles if p.plasma_screen < 0]
    if still:
        print(
            "[fugarius-wallpaper] Could not map Plasma screen for: "
            + ", ".join(still)
            + " (check kscreen-doctor and ~/.config/kwinoutputconfig.json).",
            file=sys.stderr,
        )


def _plasma_screen_for_profile(
    profile: MonitorProfile,
    profiles: list[MonitorProfile],
    screen_to_desktop: dict[int, int],
) -> int | None:
    """Resolve monitor → Plasma/KWin screen id."""
    if profile.plasma_screen >= 0 and profile.plasma_screen in screen_to_desktop:
        return profile.plasma_screen
    _match_profiles_to_plasma_screens(profiles, screen_to_desktop)
    if profile.plasma_screen >= 0:
        return profile.plasma_screen
    return None


def assign_plasma_screen_ids(profiles: list[MonitorProfile]) -> None:
    screen_to_desktop = _plasma_desktop_for_screen()
    if not screen_to_desktop:
        return
    for profile in profiles:
        profile.plasma_screen = -1
    _match_profiles_to_plasma_screens(profiles, screen_to_desktop)


# org.kde.image FillMode → Qt Quick Image.* (see plasma-wallpaper org.kde.image config.qml)
PLASMA_FILL_MODE_CROP = 2  # PreserveAspectCrop — ořízne na plochu, nepřetéká přes panely


def _plasma_set_screen_wallpaper(screen_num: int, image_path: Path) -> tuple[bool, str]:
    uri = image_path.resolve().as_uri().replace("\\", "/").replace("'", "\\'")
    script = (
        f"var t={screen_num},u='{uri}';"
        "for(var i=0;i<desktops().length;i++){"
        "var d=desktops()[i];if(d.screen!==t)continue;"
        'd.wallpaperPlugin="org.kde.image";'
        'd.currentConfigGroup=Array("Wallpaper","org.kde.image","General");'
        f"d.writeConfig('FillMode',{PLASMA_FILL_MODE_CROP});"
        'd.writeConfig("Image",u);'
        "d.reloadConfig();break;}"
    )
    rc, out, err = _plasma_eval(script, timeout=30)
    combined = "\n".join(x for x in (out, err) if x).strip().lower()
    if rc != 0 or "error" in combined or "referenceerror" in combined:
        return False, combined or "Plasma wallpaper script failed"
    return True, ""


def _plasma_refresh_all_wallpapers() -> None:
    """Reload every desktop containment so the image plugin repaints."""
    _plasma_eval(
        "for(var i=0;i<desktops().length;i++){desktops()[i].reloadConfig();}",
        timeout=20,
    )


def plasma_applets_config_path() -> Path:
    return Path.home() / ".config/plasma-org.kde.plasma.desktop-appletsrc"


def _desktop_containment_by_screen(cfg_text: str) -> dict[int, str]:
    """Map Plasma lastScreen id → containment id (desktop folder only)."""
    mapping: dict[int, str] = {}
    for match in re.finditer(
        r"\[Containments\]\[(\d+)\]\n(.*?)(?=\n\[Containments\]\[|\Z)",
        cfg_text,
        re.DOTALL,
    ):
        cid, body = match.group(1), match.group(2)
        if "plugin=org.kde.plasma.folder" not in body or "formfactor=0" not in body:
            continue
        screen_m = re.search(r"^lastScreen=(\d+)", body, re.MULTILINE)
        if screen_m:
            mapping[int(screen_m.group(1))] = cid
    return mapping


def _patch_containment_wallpaper(cfg_text: str, containment_id: str, image_uri: str) -> str:
    header = f"[Containments][{containment_id}][Wallpaper][org.kde.image][General]"
    if header not in cfg_text:
        raise ValueError(f"Wallpaper section missing for containment {containment_id}")

    pattern = (
        rf"(\[Containments\]\[{containment_id}\]\[Wallpaper\]\[org\.kde\.image\]\[General\]\n)"
        rf"(.*?)(?=\n\[)"
    )

    def _rewrite_block(match: re.Match[str]) -> str:
        block = match.group(2)
        lines: list[str] = []
        has_fill = has_image = False
        for line in block.splitlines():
            if line.startswith("Image="):
                lines.append(f"Image={image_uri}")
                has_image = True
            elif line.startswith("FillMode="):
                lines.append(f"FillMode={PLASMA_FILL_MODE_CROP}")
                has_fill = True
            else:
                lines.append(line)
        if not has_fill:
            lines.insert(0, f"FillMode={PLASMA_FILL_MODE_CROP}")
        if not has_image:
            lines.append(f"Image={image_uri}")
        body = "\n".join(lines)
        if body:
            body += "\n"
        return match.group(1) + body

    return re.sub(pattern, _rewrite_block, cfg_text, count=1, flags=re.DOTALL)


def _restart_plasmashell() -> tuple[bool, str]:
    env = _user_env()
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", "plasma-plasmashell.service"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        if result.returncode == 0:
            time.sleep(2)
            return True, ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    for binary in ("kquitapp6", "kquitapp5"):
        if not shutil.which(binary):
            continue
        subprocess.run([binary, "plasmashell"], env=env, timeout=15)
        time.sleep(2)
        for starter in ("kstart6", "kstart", "kstart5"):
            if shutil.which(starter):
                subprocess.Popen(
                    [starter, "plasmashell"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(2)
                return True, ""
    return False, "Could not restart plasmashell (systemctl / kstart failed)."


def apply_plasma_wallpapers_via_config(
    items: list[tuple[MonitorProfile, Path]],
    screen_to_desktop: dict[int, int],
) -> tuple[bool, str]:
    cfg_path = plasma_applets_config_path()
    if not cfg_path.exists():
        return False, f"Plasma config not found:\n{cfg_path}"

    profiles = [p for p, _ in items]
    text = cfg_path.read_text(encoding="utf-8")
    containments = _desktop_containment_by_screen(text)
    if not containments:
        return False, "No desktop containments found in Plasma config."

    backup = cfg_path.with_name(
        cfg_path.name + f".bak-fugarius-{int(time.time())}"
    )
    shutil.copy2(cfg_path, backup)

    used_screens: set[int] = set()
    for profile, image_path in items:
        screen_num = (
            profile.plasma_screen
            if profile.plasma_screen >= 0
            else _plasma_screen_for_profile(profile, profiles, screen_to_desktop)
        )
        if screen_num is None or screen_num < 0:
            return False, f"{profile.name}: no Plasma screen matched"
        if screen_num in used_screens:
            return False, f"{profile.name}: screen {screen_num} already used"
        cid = containments.get(screen_num)
        if cid is None:
            return (
                False,
                f"{profile.name}: no containment for screen {screen_num} "
                f"(known: {sorted(containments.keys())})",
            )
        uri = image_path.resolve().as_uri().replace("\\", "/")
        try:
            text = _patch_containment_wallpaper(text, cid, uri)
        except ValueError as exc:
            return False, str(exc)
        used_screens.add(screen_num)

    cfg_path.write_text(text, encoding="utf-8")
    ok, err = _restart_plasmashell()
    if not ok:
        shutil.copy2(backup, cfg_path)
        return False, f"{err}\nConfig restored from backup."
    return True, ""


def _validate_plasma_screen_mapping(profiles: list[MonitorProfile]) -> str | None:
    """Return error message if any profile lacks a Plasma screen assignment."""
    missing = [p.name for p in profiles if p.plasma_screen < 0]
    if not missing:
        return None
    return (
        "Could not map these monitors to Plasma screens:\n"
        + ", ".join(missing)
        + "\n\nTry Re-detect monitors. Requires kscreen-doctor and "
        "~/.config/kwinoutputconfig.json (KDE/KWin)."
    )


def apply_plasma_wallpapers(
    items: list[tuple[MonitorProfile, Path]],
) -> tuple[bool, str]:
    screen_to_desktop = _plasma_desktop_for_screen()
    profiles = [p for p, _ in items]
    assign_plasma_screen_ids(profiles)
    mapping_err = _validate_plasma_screen_mapping(profiles)
    if mapping_err:
        return False, mapping_err

    if not screen_to_desktop:
        if plasma_applets_config_path().exists():
            return apply_plasma_wallpapers_via_config(items, screen_to_desktop or {})
        rc, out, err = _plasma_eval("print(desktops().length)", timeout=10)
        detail = err or out or f"exit {rc}"
        return (
            False,
            "Could not reach KDE Plasma.\n\n"
            f"Detail: {detail}\n\n"
            "Run ./restore_panels.sh from a TTY or Konsole.\n"
            "Start the app via ./run.sh in a KDE session.",
        )

    if not _plasma_available():
        return apply_plasma_wallpapers_via_config(items, screen_to_desktop)

    errors: list[str] = []
    used_screens: set[int] = set()

    for profile, image_path in items:
        screen_num = _plasma_screen_for_profile(profile, profiles, screen_to_desktop)
        if screen_num is None:
            errors.append(f"{profile.name}: no Plasma screen matched")
            continue
        if screen_num in used_screens:
            errors.append(f"{profile.name}: screen {screen_num} already used")
            continue

        ok, err = _plasma_set_screen_wallpaper(screen_num, image_path)
        if ok:
            used_screens.add(screen_num)
        else:
            errors.append(f"{profile.name}: {err}")

    if errors:
        return apply_plasma_wallpapers_via_config(items, screen_to_desktop)

    _plasma_refresh_all_wallpapers()
    return True, ""


def _is_kde_session() -> bool:
    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP", "")
        + os.environ.get("XDG_SESSION_DESKTOP", "")
    ).lower()
    return "kde" in desktop or "plasma" in desktop


def resolve_wallpaper_backend() -> str | None:
    if _plasma_available():
        return "plasma"
    if _is_kde_session() and _qdbus_binaries() and shutil.which("kscreen-doctor"):
        return "plasma"
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


def apply_wallpaper_images(
    binary: str,
    items: list[tuple[MonitorProfile, Path]],
) -> tuple[bool, str]:
    if binary == "plasma":
        return apply_plasma_wallpapers(items)

    env = _user_env()
    errors: list[str] = []
    for profile, image_path in items:
        result = subprocess.run(
            [binary, "img", "--outputs", profile.output_name, image_path.as_posix()],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "unknown error").strip()
            errors.append(f"{profile.output_name}: {msg}")
    if errors:
        return False, "\n".join(errors)
    return True, ""


# ──────────────────────────────────────────────
# Fixed-size image file picker (replaces native askopenfilename)
# ──────────────────────────────────────────────

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"})

# Mount types / paths to hide from the volume list (pseudo-fs and system dirs).
_IGNORE_FSTYPES = frozenset({
    "proc", "sysfs", "devtmpfs", "tmpfs", "devpts", "cgroup", "cgroup2",
    "pstore", "bpf", "tracefs", "debugfs", "securityfs", "configfs",
    "fusectl", "mqueue", "hugetlbfs", "squashfs", "autofs", "binfmt_misc",
    "rpc_pipefs", "efivarfs", "none",
})
_IGNORE_MOUNT_PREFIXES = (
    "/proc", "/sys", "/dev", "/run/user", "/run/credentials", "/snap",
    "/var/lib/docker", "/var/lib/nfs", "/boot/efi",
)


def list_mounted_volumes() -> list[tuple[str, Path]]:
    """User-visible volumes: USB disks, secondary drives, GVfs, etc."""
    seen: set[str] = set()
    volumes: list[tuple[str, Path]] = []
    home = Path.home().resolve()

    def add(path: Path, label: str | None = None) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if not resolved.is_dir():
            return
        key = str(resolved)
        if key in seen or resolved == home:
            return
        seen.add(key)
        name = label or resolved.name or str(resolved)
        volumes.append((name, resolved))

    username = home.name
    scan_roots = [
        Path("/run/media") / username,
        Path("/run/media"),
        Path("/media") / username,
        Path("/media"),
        Path("/mnt"),
    ]
    for base in scan_roots:
        if not base.is_dir():
            continue
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                add(child)

    gvfs = Path(f"/run/user/{os.getuid()}/gvfs")
    if gvfs.is_dir():
        try:
            for child in sorted(gvfs.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir():
                    label = child.name.split("=", 1)[-1].replace("%20", " ")
                    add(child, label or child.name)
        except OSError:
            pass

    try:
        for line in Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mountpoint = Path(parts[1])
            fstype = parts[2]
            if fstype in _IGNORE_FSTYPES:
                continue
            mp = str(mountpoint)
            if mountpoint == Path("/") or any(mp.startswith(p) for p in _IGNORE_MOUNT_PREFIXES):
                continue
            if fstype == "tmpfs" and mountpoint != Path("/tmp"):
                continue
            if mp.startswith("/tmp/.mount_"):
                continue
            add(mountpoint)
    except OSError:
        pass

    volumes.sort(key=lambda item: item[0].lower())
    return volumes


class ImageFileDialog(tk.Toplevel):
    """Non-resizable file picker with folder tree, file list, and image preview."""

    WIDTH = 1260
    HEIGHT = 560
    TREE_WIDTH = 248
    PREVIEW_WIDTH = 300
    _DUMMY = "\x00dummy"
    _SKIP_DIR_NAMES = frozenset({".git", ".venv", "__pycache__", "node_modules"})

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
        self._syncing_tree = False
        self._top_mounts: dict[str, str] = {}
        self._preview_photo: ImageTk.PhotoImage | None = None
        self._preview_after: str | None = None

        start = initial_dir if initial_dir and initial_dir.is_dir() else Path.home() / "Pictures"
        if not start.is_dir():
            start = Path.home()
        self.current_dir = start.resolve()

        self._build_ui()
        self._init_tree_roots()
        self._navigate_to(self.current_dir, sync_tree=True)
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

        browser = ttk.Frame(self, padding=(10, 0, 10, 6))
        browser.pack(fill=tk.BOTH, expand=True)

        tree_outer = ttk.LabelFrame(browser, text="Folders", width=self.TREE_WIDTH)
        tree_outer.pack(side=tk.LEFT, fill=tk.Y)
        tree_outer.pack_propagate(False)

        tree_scroll = ttk.Scrollbar(tree_outer)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(tree_outer, show="tree", yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        tree_scroll.config(command=self.tree.yview)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        list_outer = ttk.LabelFrame(browser, text="Files")
        list_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        list_scroll = ttk.Scrollbar(list_outer)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(
            list_outer,
            yscrollcommand=list_scroll.set,
            font=("Sans", 11),
            activestyle="dotbox",
            selectmode=tk.SINGLE,
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        list_scroll.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self._on_activate)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        preview_outer = ttk.LabelFrame(browser, text="Preview", width=self.PREVIEW_WIDTH)
        preview_outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        preview_outer.pack_propagate(False)
        self.preview_label = tk.Label(
            preview_outer,
            bg="#2b2b2b",
            fg="#aaaaaa",
            text="Select an image",
            anchor=tk.CENTER,
            justify=tk.CENTER,
            wraplength=self.PREVIEW_WIDTH - 24,
        )
        self.preview_label.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        btn_row = ttk.Frame(self, padding=10)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Cancel", width=12, command=self._cancel).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_row, text="Open", width=12, command=self._open_selected).pack(side=tk.RIGHT)

    def _dir_iid(self, path: Path) -> str:
        return str(path.resolve())

    def _tree_dummy_iid(self, path: Path) -> str:
        return self._dir_iid(path) + self._DUMMY

    def _list_dirs(self, folder: Path) -> list[Path]:
        try:
            entries = list(folder.iterdir())
        except OSError:
            return []
        dirs = [p for p in entries if p.is_dir()]
        visible = [
            p
            for p in dirs
            if not p.name.startswith(".") and p.name not in self._SKIP_DIR_NAMES
        ]
        return sorted(visible, key=lambda p: p.name.lower())

    def _tree_label(self, folder: Path) -> str:
        iid = self._dir_iid(folder)
        if iid in self._top_mounts:
            return self._top_mounts[iid]
        if folder == Path.home().resolve():
            return "Home"
        if folder == Path("/"):
            return "Root (/)"
        return folder.name or str(folder)

    def _init_tree_roots(self) -> None:
        home = Path.home().resolve()
        self._top_mounts[self._dir_iid(home)] = "Home"
        self.tree.insert("", tk.END, iid=self._dir_iid(home), text="Home", open=True)
        self._tree_add_dummy(home)
        self._tree_load_children(home)

        for label, mount in list_mounted_volumes():
            iid = self._dir_iid(mount)
            self._top_mounts[iid] = label
            self.tree.insert("", tk.END, iid=iid, text=label, open=False)
            self._tree_add_dummy(mount)

        root = Path("/")
        self._top_mounts[self._dir_iid(root)] = "Root (/)"
        self.tree.insert("", tk.END, iid=self._dir_iid(root), text="Root (/)", open=False)
        self._tree_add_dummy(root)

    def _tree_add_dummy(self, path: Path) -> None:
        dummy = self._tree_dummy_iid(path)
        if not self.tree.exists(dummy):
            self.tree.insert(self._dir_iid(path), tk.END, iid=dummy, text="…")

    def _tree_load_children(self, path: Path) -> None:
        iid = self._dir_iid(path)
        dummy = self._tree_dummy_iid(path)
        if self.tree.exists(dummy):
            self.tree.delete(dummy)
        home = Path.home().resolve()
        for child in self._list_dirs(path):
            child_iid = self._dir_iid(child)
            if child_iid in self._top_mounts:
                continue  # top-level volume — already in tree
            if path == Path("/") and child.resolve() == home:
                continue
            if not self.tree.exists(child_iid):
                self.tree.insert(iid, tk.END, iid=child_iid, text=child.name)
                self._tree_add_dummy(child)

    def _on_tree_open(self, _event: tk.Event | None = None) -> None:
        iid = self.tree.focus()
        if not iid or iid.endswith(self._DUMMY):
            return
        self._tree_load_children(Path(iid))

    def _on_tree_select(self, _event: tk.Event | None = None) -> None:
        if self._syncing_tree:
            return
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.endswith(self._DUMMY):
            return
        path = Path(iid)
        if path.is_dir():
            self._navigate_to(path, sync_tree=False)

    def _tree_reveal(self, target: Path) -> None:
        target = target.resolve()
        chain: list[Path] = []
        node = target
        while True:
            chain.append(node)
            if node.parent == node:
                break
            node = node.parent
        chain.reverse()

        self._syncing_tree = True
        try:
            for folder in chain:
                iid = self._dir_iid(folder)
                parent_iid = "" if folder.parent == folder else self._dir_iid(folder.parent)
                if not self.tree.exists(iid):
                    label = self._tree_label(folder)
                    if parent_iid:
                        if self.tree.exists(parent_iid):
                            self._tree_load_children(Path(parent_iid))
                            self.tree.item(parent_iid, open=True)
                    self.tree.insert(parent_iid, tk.END, iid=iid, text=label or str(folder))
                    self._tree_add_dummy(folder)
                self._tree_load_children(folder)
                self.tree.item(iid, open=True)
            if self.tree.exists(self._dir_iid(target)):
                self.tree.selection_set(self._dir_iid(target))
                self.tree.see(self._dir_iid(target))
        finally:
            self._syncing_tree = False

    def _navigate_to(self, path: Path, *, sync_tree: bool) -> None:
        path = path.resolve()
        if not path.is_dir():
            return
        self.current_dir = path
        self._refresh_listing()
        if sync_tree:
            self._tree_reveal(path)

    def _clear_preview(self, message: str = "Select an image") -> None:
        self._preview_photo = None
        self.preview_label.config(image="", text=message)

    def _schedule_preview(self, path: Path) -> None:
        if self._preview_after:
            try:
                self.after_cancel(self._preview_after)
            except tk.TclError:
                pass
        self._preview_after = self.after(120, lambda: self._load_preview(path))

    def _load_preview(self, path: Path) -> None:
        self._preview_after = None
        sel = self._selected_path()
        if sel != path:
            return
        try:
            with Image.open(path) as img:
                img = img.convert("RGB")
                max_w = self.PREVIEW_WIDTH - 20
                max_h = self.HEIGHT - 120
                img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
        except OSError:
            self._clear_preview("Cannot preview")
            return
        self._preview_photo = photo
        self.preview_label.config(image=photo, text="")
        self.preview_label.image = photo

    def _on_list_select(self, _event: tk.Event | None = None) -> None:
        path = self._selected_path()
        if path is None or path.is_dir():
            self._clear_preview("Select an image" if path is None else "Folder")
            return
        self._schedule_preview(path)

    def _refresh_listing(self) -> None:
        self.listbox.delete(0, tk.END)
        self._items.clear()
        self._clear_preview()
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
            self._navigate_to(parent, sync_tree=True)

    def _go_to_path(self) -> None:
        target = Path(self.path_var.get().strip()).expanduser()
        if target.is_file():
            self.result = str(target.resolve())
            self._close()
            return
        if target.is_dir():
            self._navigate_to(target.resolve(), sync_tree=True)
            return
        messagebox.showerror("Invalid path", "Folder does not exist.", parent=self)

    def _on_activate(self, _event: tk.Event | None = None) -> None:
        path = self._selected_path()
        if path is None:
            return
        if path.is_dir():
            self._navigate_to(path, sync_tree=True)
        else:
            self.result = str(path)
            self._close()

    def _open_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("Select file", "Choose an image from the list.", parent=self)
            return
        if path.is_dir():
            self._navigate_to(path, sync_tree=True)
            return
        self.result = str(path)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        if self._preview_after:
            try:
                self.after_cancel(self._preview_after)
            except tk.TclError:
                pass
            self._preview_after = None
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

def virtual_desktop_bounds(
    profiles: list[MonitorProfile],
) -> tuple[int, int, int, int]:
    """Return min_x, min_y, total_w, total_h of the combined monitor layout."""
    min_x = min(p.pos_x for p in profiles)
    min_y = min(p.pos_y for p in profiles)
    total_w = max(p.pos_x + p.width for p in profiles) - min_x
    total_h = max(p.pos_y + p.height for p in profiles) - min_y
    return min_x, min_y, total_w, total_h


def render_monitor_image(
    source: Image.Image,
    profile: MonitorProfile,
    profiles: list[MonitorProfile] | None = None,
    fit_mode: str = "fill",
) -> Image.Image:
    """Crop one monitor's view from a single panorama on the virtual desktop.

    Each profile has its own zoom, rotation, and offset (fine-tune per monitor).
    VD coords map 1:1 to image pixels after scaling; offset shifts that monitor's
    viewport on the shared plane (auto-fit starts from a common baseline).
    """
    if profiles is None:
        profiles = [profile]
    min_x, min_y, total_w, total_h = virtual_desktop_bounds(profiles)
    out = Image.new("RGB", (profile.width, profile.height), color=(0, 0, 0))
    if total_w <= 0 or total_h <= 0:
        return out

    src = source.convert("RGB")
    scaled = src.resize(
        (
            max(1, int(src.width * profile.zoom)),
            max(1, int(src.height * profile.zoom)),
        ),
        Image.Resampling.LANCZOS,
    )
    rotated = scaled.rotate(
        profile.rotation,
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )
    if fit_mode == "stretch":
        rotated = rotated.resize(
            (max(1, total_w), max(1, total_h)),
            Image.Resampling.LANCZOS,
        )
    rw, rh = rotated.size

    # VD coords map 1:1 to image pixels; image center sits on VD center (via offset).
    # Do NOT scale by rw/total_w — that stretches the VD bbox onto the full image and
    # pins the top/bottom monitors to the image edges instead of the layout center.
    vd_cx = total_w / 2.0
    vd_cy = total_h / 2.0
    icx = rw / 2.0 + profile.offset_x
    icy = rh / 2.0 + profile.offset_y

    mx = profile.pos_x - min_x
    my = profile.pos_y - min_y
    w, h = profile.width, profile.height

    left = int(icx + (mx - vd_cx))
    top = int(icy + (my - vd_cy))
    right = int(icx + (mx + w - vd_cx))
    bottom = int(icy + (my + h - vd_cy))

    sl = max(0, left)
    su = max(0, top)
    sr = min(rw, right)
    sb = min(rh, bottom)
    if sr > sl and sb > su:
        out.paste(rotated.crop((sl, su, sr, sb)), (sl - left, su - top))
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
    else:  # stretch — non-uniform resize in render_monitor_image
        return 1.0


def autofit_transform(
    source: Image.Image,
    profiles: list[MonitorProfile],
    mode: str = "fill",
) -> float:
    """Baseline for all monitors: same zoom, zero offset, centered on virtual desktop.

    Adjust zoom/rotation/offset per monitor afterward in the UI.
    """
    if not profiles:
        return 1.0
    zoom = round(auto_fit_zoom(source, profiles, mode), 3)
    for p in profiles:
        p.zoom = zoom
        p.rotation = 0.0
        p.offset_x = 0
        p.offset_y = 0
    return zoom


# ──────────────────────────────────────────────
# Main App
# ──────────────────────────────────────────────

class WallpaperPanoramaApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Fugarius Wallpaper")
        self.root.geometry("1520x1020")
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
        self._preview_hit_regions: list[tuple[int, int, int, int, int]] = []
        self._preview_layout: dict[str, float] = {}
        self._preview_drag: tuple[int, int, int, int, int] | None = None
        self._drag_preview_after: str | None = None
        self._arrow_hold_since: dict[str, float] = {}
        self._arrow_repeat_after: str | None = None
        self._arrow_active_key: str | None = None
        self._live_apply_warned = False
        self._value_labels: dict[str, tk.StringVar] = {}
        self._scales: dict[str, ttk.Scale] = {}
        self._build_ui()
        self._bind_arrow_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)
        self._detect_and_load_monitors()
        self._load_profile_into_controls()
        self._invalidate_preview()
        self.root.after_idle(self._schedule_preview_update)
        self.root.after(300, self._show_backend_status)

    def _show_backend_status(self) -> None:
        binary = resolve_wallpaper_backend()
        if binary == "plasma":
            rc, n, _ = _plasma_eval("print(desktops().length)", timeout=5)
            if rc == 0 and n.isdigit():
                self.status_var.set(f"Wallpaper backend: KDE Plasma ({n} desktop(s))")
            else:
                self.status_var.set("Wallpaper backend: KDE Plasma (D-Bus check failed — use ./run.sh)")
        elif binary:
            self.status_var.set(f"Wallpaper backend: {binary}")
        else:
            self.status_var.set("Wallpaper backend: none — run ./install.sh")

    def _detect_and_load_monitors(self) -> None:
        profiles = detect_monitors()
        if profiles:
            self.profiles = profiles
            assign_plasma_screen_ids(self.profiles)
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

        # Sidebar: scrollable controls + status bar always visible at the bottom.
        left_outer = ttk.Frame(outer, width=300)
        left_outer.pack(side=tk.LEFT, fill=tk.Y)
        left_outer.pack_propagate(False)

        self._left_canvas = tk.Canvas(left_outer, width=300, highlightthickness=0, borderwidth=0)
        left_scroll = ttk.Scrollbar(left_outer, orient=tk.VERTICAL, command=self._left_canvas.yview)
        left = ttk.Frame(self._left_canvas)
        self._left_canvas_window = self._left_canvas.create_window((0, 0), window=left, anchor=tk.NW, width=300)

        def _sync_left_scroll(_event: tk.Event | None = None) -> None:
            self._left_canvas.configure(scrollregion=self._left_canvas.bbox("all"))

        left.bind("<Configure>", _sync_left_scroll)
        self._left_canvas.configure(yscrollcommand=left_scroll.set)
        self._left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def _left_mousewheel(event: tk.Event) -> None:
            if event.delta:
                self._left_canvas.yview_scroll(int(-event.delta / 120), "units")
            elif getattr(event, "num", None) == 4:
                self._left_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                self._left_canvas.yview_scroll(1, "units")

        for w in (self._left_canvas, left):
            w.bind("<MouseWheel>", _left_mousewheel)
            w.bind("<Button-4>", _left_mousewheel)
            w.bind("<Button-5>", _left_mousewheel)

        status_frame = ttk.Frame(left_outer)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="Detecting monitors…")
        ttk.Label(
            status_frame,
            textvariable=self.status_var,
            wraplength=280,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=4, pady=8)

        right = ttk.Frame(outer)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        ttk.Label(
            right,
            text="Preview: click monitor · drag pan · wheel zoom · arrows nudge (1px, 5px after 5s hold)",
            font=("Sans", 9),
        ).pack(anchor=tk.W, pady=(0, 4))

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
            "fill / fit: jeden zoom, střed obrázku = střed virtuální plochy\n"
            "fit = celý obrázek viditelný (černé okraje na monitorech)\n"
            "stretch = roztáhne na obdélník virtuální plochy (bez zachování poměru)",
        )
        apply_frame = ttk.LabelFrame(left, text="Desktop")
        apply_frame.pack(fill=tk.X, pady=(10, 4))
        apply_btn = ttk.Button(apply_frame, text="Apply to desktop", command=self.apply_to_desktop)
        apply_btn.pack(**btn_opts, pady=4)
        self.live_apply_var = tk.BooleanVar(value=False)
        live_cb = ttk.Checkbutton(
            apply_frame,
            text="Live apply (sliders)",
            variable=self.live_apply_var,
            command=self._on_live_apply_toggle,
        )
        live_cb.pack(anchor=tk.W, padx=4, pady=(0, 4))
        self._add_tooltip(
            apply_btn,
            "Nastaví tapetu na plochu.\n"
            "KDE Plasma: nativně přes Plasma (doporučeno).\n"
            "Jinak swww/awww. Vyžaduje Wayland output name u každého monitoru.",
        )

        ttk.Button(left, text="Save profile", command=self.save_profile).pack(**btn_opts, pady=2)
        ttk.Button(left, text="Load profile", command=self.load_profile).pack(**btn_opts, pady=2)
        ttk.Button(left, text="Export wallpapers…", command=self.export_wallpapers).pack(**btn_opts, pady=2)

        ttk.Label(left, text="Monitor").pack(anchor=tk.W, pady=(14, 2))
        self.monitor_var   = tk.StringVar()
        self.monitor_combo = ttk.Combobox(left, textvariable=self.monitor_var, state="readonly")
        self.monitor_combo.pack(fill=tk.X, pady=(0, 10))
        self.monitor_combo.bind("<<ComboboxSelected>>", self.on_monitor_selected)

        controls = ttk.LabelFrame(left, text="Transform (selected monitor)"); controls.pack(fill=tk.X, pady=6)
        self.zoom_var     = tk.DoubleVar(value=1.0)
        self.rotation_var = tk.DoubleVar(value=0.0)
        self.offset_x_var = tk.IntVar(value=0)
        self.offset_y_var = tk.IntVar(value=0)
        self.width_var    = tk.IntVar(value=1920)
        self.height_var   = tk.IntVar(value=1080)
        self.pos_x_var    = tk.IntVar(value=0)
        self.pos_y_var    = tk.IntVar(value=0)
        self.output_var   = tk.StringVar(value="")

        self._add_slider(controls, "zoom", "Zoom", self.zoom_var, 0.1, 5.0, step=0.01, is_float=True)
        self._add_slider(controls, "rotation", "Rotation", self.rotation_var, -35.0, 35.0, step=0.1, is_float=True)
        self._add_slider(controls, "offset_x", "Offset X", self.offset_x_var, -8000, 8000, step=1)
        self._add_slider(controls, "offset_y", "Offset Y", self.offset_y_var, -8000, 8000, step=1)

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

        self.preview_canvas = tk.Canvas(right, bg="#1E1E1E", highlightthickness=0, cursor="arrow")
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.configure(takefocus=True)
        self.preview_canvas.bind("<Configure>", self._schedule_preview_update)
        self.preview_canvas.bind("<Button-1>", self._on_preview_press)
        self.preview_canvas.bind("<B1-Motion>", self._on_preview_drag)
        self.preview_canvas.bind("<ButtonRelease-1>", self._on_preview_release)
        self.preview_canvas.bind("<Motion>", self._on_preview_hover)
        for wheel in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.preview_canvas.bind(wheel, self._on_preview_wheel)

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
        step: float = 1.0,
        is_float: bool = False,
    ) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row, text=text, width=10).pack(side=tk.LEFT)
        shown = tk.StringVar(value=self._format_control_value(key, var))
        self._value_labels[key] = shown

        entry = ttk.Entry(row, textvariable=shown, width=9, font=("TkFixedFont", 10), justify="right")
        entry.pack(side=tk.RIGHT)

        def clamp_and_set(value: float) -> None:
            clamped = max(mn, min(mx, value))
            if is_float:
                var.set(float(clamped))
                shown.set(f"{float(clamped):.2f}")
            else:
                var.set(int(round(clamped)))
                shown.set(str(int(var.get())))

        def commit_entry(_event: tk.Event | None = None) -> None:
            if self._ui_busy:
                return
            try:
                raw = shown.get().strip().replace(",", ".")
                clamp_and_set(float(raw) if is_float else float(raw))
                self.apply_controls()
                self._schedule_live_apply()
            except ValueError:
                shown.set(self._format_control_value(key, var))

        def nudge(delta: float) -> None:
            if self._ui_busy:
                return
            current = float(var.get())
            clamp_and_set(current + delta)
            self.apply_controls()
            self._schedule_live_apply()

        def on_entry_arrow(event: tk.Event) -> str:
            if event.keysym == "Up":
                nudge(step)
            elif event.keysym == "Down":
                nudge(-step)
            return "break"

        entry.bind("<Return>", commit_entry)
        entry.bind("<FocusOut>", commit_entry)
        entry.bind("<Up>", on_entry_arrow)
        entry.bind("<Down>", on_entry_arrow)

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
        zoom = autofit_transform(self.source_image, self.profiles, mode)

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
        sel = self.monitor_var.get()
        names = [p.name for p in self.profiles]
        if sel in names:
            self._set_monitor_index(names.index(sel))
        else:
            idx = self.monitor_combo.current()
            if idx >= 0:
                self._set_monitor_index(idx)

    def _set_monitor_index(self, index: int) -> None:
        if index < 0 or index >= len(self.profiles):
            return
        if index != self.current_index:
            self.apply_controls()
            self.current_index = index
            self._sync_controls_from_profile()
            self._invalidate_preview()
            self._schedule_preview_update()
        names = [p.name for p in self.profiles]
        if names:
            self.monitor_var.set(names[index])
            self.monitor_combo.current(index)
        p = self.profiles[index]
        self.status_var.set(
            f"Selected: {p.name} — drag pan · wheel zoom · arrows 1px (5px after 5s hold)"
        )

    def _select_monitor(self, index: int) -> None:
        self._set_monitor_index(index)

    _ARROW_KEYS = frozenset({"Up", "Down", "Left", "Right"})
    _ARROW_FAST_AFTER_SEC = 5.0
    _ARROW_REPEAT_INITIAL_MS = 400
    _ARROW_REPEAT_MS = 45

    def _bind_arrow_keys(self) -> None:
        for key in self._ARROW_KEYS:
            self.root.bind(f"<{key}>", self._on_arrow_press, add="+")
            self.root.bind(f"<KeyRelease-{key}>", self._on_arrow_release, add="+")

    def _arrow_input_allowed(self) -> bool:
        if not self.source_image or not self.profiles:
            return False
        focus = self.root.focus_get()
        if focus is None:
            return True
        return focus.winfo_class() not in ("Entry", "TEntry", "Spinbox", "TSpinbox")

    def _arrow_step_size(self, keysym: str) -> int:
        since = self._arrow_hold_since.get(keysym)
        if since is None:
            return 1
        if time.monotonic() - since >= self._ARROW_FAST_AFTER_SEC:
            return 5
        return 1

    def _arrow_nudge(self, keysym: str, step: int) -> None:
        self.apply_controls()
        p = self.profiles[self.current_index]
        if keysym == "Left":
            p.offset_x += step
        elif keysym == "Right":
            p.offset_x -= step
        elif keysym == "Up":
            p.offset_y += step
        elif keysym == "Down":
            p.offset_y -= step
        self._sync_controls_from_profile()
        self._invalidate_preview()
        self._schedule_preview_update()
        self._schedule_live_apply()

    def _cancel_arrow_repeat(self) -> None:
        if self._arrow_repeat_after:
            try:
                self.root.after_cancel(self._arrow_repeat_after)
            except tk.TclError:
                pass
            self._arrow_repeat_after = None

    def _schedule_arrow_repeat(self, *, initial: bool) -> None:
        self._cancel_arrow_repeat()
        delay = self._ARROW_REPEAT_INITIAL_MS if initial else self._ARROW_REPEAT_MS
        self._arrow_repeat_after = self.root.after(delay, self._arrow_repeat_tick)

    def _arrow_repeat_tick(self) -> None:
        self._arrow_repeat_after = None
        keysym = self._arrow_active_key
        if keysym is None or keysym not in self._arrow_hold_since:
            return
        if not self._arrow_input_allowed():
            return
        self._arrow_nudge(keysym, self._arrow_step_size(keysym))
        self._schedule_arrow_repeat(initial=False)

    def _on_arrow_press(self, event: tk.Event) -> str | None:
        keysym = event.keysym
        if keysym not in self._ARROW_KEYS or not self._arrow_input_allowed():
            return None
        if keysym in self._arrow_hold_since:
            return "break"
        self._arrow_hold_since[keysym] = time.monotonic()
        self._arrow_active_key = keysym
        self._arrow_nudge(keysym, 1)
        self._schedule_arrow_repeat(initial=True)
        return "break"

    def _on_arrow_release(self, event: tk.Event) -> str | None:
        keysym = event.keysym
        self._arrow_hold_since.pop(keysym, None)
        if self._arrow_active_key == keysym:
            self._arrow_active_key = None
            self._cancel_arrow_repeat()
        return "break"

    def _preview_hit_test(self, cx: int, cy: int) -> int | None:
        hits: list[tuple[int, int]] = []
        for x, y, w, h, idx in self._preview_hit_regions:
            if x <= cx <= x + w and y <= cy <= y + h:
                hits.append((w * h, idx))
        if not hits:
            return None
        hits.sort(key=lambda t: t[0])
        return hits[0][1]

    def _on_preview_press(self, event: tk.Event) -> None:
        if not self.source_image or not self.profiles:
            return
        self.preview_canvas.focus_set()
        idx = self._preview_hit_test(event.x, event.y)
        if idx is None:
            return
        self._select_monitor(idx)
        p = self.profiles[idx]
        self._preview_drag = (event.x, event.y, p.offset_x, p.offset_y, idx)

    def _on_preview_drag(self, event: tk.Event) -> None:
        if not self._preview_drag or not self.source_image:
            return
        sx, sy, ox, oy, idx = self._preview_drag
        layout_scale = max(self._preview_layout.get("scale", 1.0), 0.05)
        p = self.profiles[idx]
        sens = layout_scale / max(p.zoom, 0.1)
        p.offset_x = ox - int((event.x - sx) / sens)
        p.offset_y = oy - int((event.y - sy) / sens)
        self._sync_controls_from_profile()
        if self._drag_preview_after:
            try:
                self.root.after_cancel(self._drag_preview_after)
            except tk.TclError:
                pass
        self._drag_preview_after = self.root.after(40, self._finish_drag_preview)

    def _finish_drag_preview(self) -> None:
        self._drag_preview_after = None
        self._invalidate_preview()
        self._schedule_preview_update()

    def _on_preview_release(self, _event: tk.Event) -> None:
        if self._preview_drag:
            self.apply_controls()
            self._schedule_live_apply()
        self._preview_drag = None
        if self._drag_preview_after:
            try:
                self.root.after_cancel(self._drag_preview_after)
            except tk.TclError:
                pass
            self._drag_preview_after = None
            self._finish_drag_preview()

    def _on_preview_hover(self, event: tk.Event) -> None:
        if self._preview_drag:
            return
        if self._preview_hit_test(event.x, event.y) is not None:
            self.preview_canvas.config(cursor="hand2")
        else:
            self.preview_canvas.config(cursor="arrow")

    def _on_preview_wheel(self, event: tk.Event) -> None:
        if not self.source_image or not self.profiles:
            return
        idx = self._preview_hit_test(event.x, event.y)
        if idx is not None:
            self._select_monitor(idx)
        self.apply_controls()
        step = 0.04
        if getattr(event, "num", None) == 4:
            delta = step
        elif getattr(event, "num", None) == 5:
            delta = -step
        elif getattr(event, "delta", 0) > 0:
            delta = step
        else:
            delta = -step
        p = self.profiles[self.current_index]
        p.zoom = max(0.1, min(5.0, round(p.zoom + delta, 3)))
        self._sync_controls_from_profile()
        self._invalidate_preview()
        self._schedule_preview_update()
        self._schedule_live_apply()

    def apply_controls(self) -> None:
        if not self.profiles:
            return
        p = self.profiles[self.current_index]
        p.zoom = float(self.zoom_var.get())
        p.rotation = float(self.rotation_var.get())
        p.offset_x = int(self.offset_x_var.get())
        p.offset_y = int(self.offset_y_var.get())
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
        p.zoom = float(self.zoom_var.get())
        p.rotation = float(self.rotation_var.get())
        p.offset_x = int(self.offset_x_var.get())
        p.offset_y = int(self.offset_y_var.get())
        p.width, p.height = int(self.width_var.get()), int(self.height_var.get())
        p.pos_x, p.pos_y = int(self.pos_x_var.get()), int(self.pos_y_var.get())
        p.output_name = self.output_var.get().strip()

    def _refresh_monitor_list(self) -> None:
        names = [p.name for p in self.profiles]
        self.monitor_combo["values"] = names
        if names:
            idx = min(self.current_index, len(names) - 1)
            self.monitor_combo.current(idx)
            self.monitor_var.set(names[idx])

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
        if self._drag_preview_after:
            try:
                self.root.after_cancel(self._drag_preview_after)
            except tk.TclError:
                pass
        self._cancel_arrow_repeat()
        self._arrow_hold_since.clear()
        self._arrow_active_key = None
        try:
            self.preview_canvas.unbind("<Configure>")
            self.preview_canvas.unbind("<Button-1>")
            self.preview_canvas.unbind("<B1-Motion>")
            self.preview_canvas.unbind("<ButtonRelease-1>")
            self.preview_canvas.unbind("<Motion>")
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
            self._preview_layout = {"scale": scale}
            self._preview_hit_regions = []

            for i, p in enumerate(self.profiles):
                img = render_monitor_image(
                    self.source_image,
                    p,
                    self.profiles,
                    self.fit_mode_var.get(),
                )
                tw = max(10, int(p.width * scale))
                th = max(10, int(p.height * scale))
                thumb = img.resize((tw, th), Image.Resampling.BILINEAR)
                x = int(base_x + (p.pos_x - min_x) * scale)
                y = int(base_y + (p.pos_y - min_y) * scale)
                self._preview_hit_regions.append((x, y, tw, th, i))
                photo = ImageTk.PhotoImage(thumb)
                self.preview_refs.append(photo)
                self.preview_canvas.create_image(x, y, image=photo, anchor=tk.NW)
                color = "#5ED8FF" if i == self.current_index else "#888888"
                width = 3 if i == self.current_index else 2
                self.preview_canvas.create_rectangle(x, y, x + tw, y + th, outline=color, width=width)
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

    def _on_live_apply_toggle(self) -> None:
        if self.live_apply_var.get() and self.source_image:
            self._live_apply_warned = False
            self.apply_to_desktop(silent=False)

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
            msg = "No wallpaper backend. Run ./install.sh from the project folder."
            self.status_var.set(msg)
            if not silent:
                messagebox.showerror(
                    "Cannot apply wallpaper",
                    msg + "\n\nOn KDE Plasma, qdbus + kscreen-doctor are used.\n"
                    "Optional: sudo dnf install swww",
                )
            return False

        if binary != "plasma":
            ok, err = ensure_wallpaper_daemon(binary)
            if not ok:
                if not silent:
                    messagebox.showerror("Wallpaper daemon", err)
                self.status_var.set(err)
                return False

        if resolve_wallpaper_backend() == "plasma":
            assign_plasma_screen_ids(self.profiles)

        cache = wallpaper_cache_dir()
        pictures = wallpaper_pictures_dir()
        cache.mkdir(parents=True, exist_ok=True)
        pictures.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        items: list[tuple[MonitorProfile, Path]] = []
        for profile in self.profiles:
            safe = _safe_output_slug(profile)
            path = cache / f"{safe}-{stamp}.png"
            render_monitor_image(
                self.source_image,
                profile,
                self.profiles,
                self.fit_mode_var.get(),
            ).save(path)
            _prune_wallpaper_cache(safe, path)
            try:
                shutil.copy2(path, pictures / f"{safe}.png")
            except OSError:
                pass
            items.append((profile, path))

        backend_label = "KDE Plasma" if binary == "plasma" else binary
        self.status_var.set(f"Applying via {backend_label}…")
        self.root.update_idletasks()

        ok, err = apply_wallpaper_images(binary, items)
        if not ok:
            self.status_var.set(f"Apply failed: {err[:100]}")
            if not silent:
                messagebox.showerror("Apply failed", err)
            elif self.live_apply_var.get() and not self._live_apply_warned:
                self._live_apply_warned = True
                messagebox.showwarning(
                    "Live apply failed",
                    f"{err}\n\nTapety se neaplikovaly. Zkontroluj ./install.sh\n"
                    "nebo klikni Apply to desktop pro detail.",
                )
            return False

        self._live_apply_warned = False
        self.status_var.set(f"Applied to {len(items)} monitor(s) via {backend_label}")
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
            render_monitor_image(
                self.source_image,
                profile,
                self.profiles,
                self.fit_mode_var.get(),
            ).save(output)
            generated.append((profile, output))
        backend = resolve_wallpaper_backend()
        script_lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "# Generated by Fugarius Wallpaper — per-monitor crops are in this folder.",
            "",
        ]
        if backend == "plasma":
            script_lines.extend([
                "# KDE Plasma: prefer Apply to desktop in the app (D-Bus + correct FillMode).",
                "# Or set each screen's wallpaper to the matching PNG in System Settings.",
                "echo \"Plasma: run ./run.sh in a KDE session and use Apply to desktop.\"",
                "",
            ])
        else:
            binary = backend or "swww"
            script_lines.extend([
                f'BINARY="{binary}"',
                "command -v \"$BINARY\" >/dev/null 2>&1 || { echo \"Install swww or use KDE Plasma.\"; exit 1; }",
                "",
                "\"${BINARY}-daemon\" >/dev/null 2>&1 || true",
                "sleep 1",
                "",
            ])
            for profile, img_path in generated:
                if profile.output_name:
                    script_lines.append(
                        f"\"$BINARY\" img --outputs \"{profile.output_name}\" \"{img_path.as_posix()}\""
                    )
            if not any(p.output_name for p, _ in generated):
                script_lines.append(
                    "echo 'No output names set. Fill output_name in app and export again.'"
                )
        script_path = out_dir / "apply_wallpaper.sh"
        script_path.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
        script_path.chmod(0o755)
        (out_dir / "manifest.json").write_text(json.dumps({"source": str(self.source_path) if self.source_path else "", "profiles": [asdict(p) for p in self.profiles], "generated": [str(p) for _, p in generated], "script": str(script_path)}, indent=2), encoding="utf-8")
        self.status_var.set(f"Export complete → {out_dir}")
        messagebox.showinfo("Done", f"Wallpapers exported.\nRun:\n{script_path}")


def main() -> None:
    root = tk.Tk()
    app = WallpaperPanoramaApp(root)
    root.minsize(1280, 960)
    root.mainloop()


if __name__ == "__main__":
    main()
