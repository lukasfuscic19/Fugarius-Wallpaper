#!/usr/bin/env python3
"""Quick check that per-monitor crops align on the virtual desktop (no GUI)."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

from app import MonitorProfile, auto_fit_zoom, detect_monitors, render_monitor_image


def apply_autofit(source: Image.Image, profiles: list[MonitorProfile], mode: str = "fill") -> None:
    zoom = auto_fit_zoom(source, profiles, mode)
    min_x = min(p.pos_x for p in profiles)
    min_y = min(p.pos_y for p in profiles)
    img_w = source.width * zoom
    img_h = source.height * zoom
    total_w = max(p.pos_x + p.width for p in profiles) - min_x
    total_h = max(p.pos_y + p.height for p in profiles) - min_y
    scale_x = img_w / total_w if total_w else 1.0
    scale_y = img_h / total_h if total_h else 1.0
    for p in profiles:
        p.zoom = zoom
        p.rotation = 0.0
        mon_cx = (p.pos_x - min_x) + p.width / 2
        mon_cy = (p.pos_y - min_y) + p.height / 2
        p.offset_x = int(mon_cx * scale_x - img_w / 2)
        p.offset_y = int(mon_cy * scale_y - img_h / 2)


def main() -> int:
    profiles = detect_monitors()
    if not profiles:
        print("No monitors detected — run from your Wayland session.", file=sys.stderr)
        return 1

    min_x = min(p.pos_x for p in profiles)
    min_y = min(p.pos_y for p in profiles)
    total_w = max(p.pos_x + p.width for p in profiles) - min_x
    total_h = max(p.pos_y + p.height for p in profiles) - min_y

    # Virtual-desktop image: each pixel encodes its (x, y) position.
    source = Image.new("RGB", (total_w, total_h))
    px = source.load()
    for y in range(total_h):
        for x in range(total_w):
            px[x, y] = (x % 256, y % 256, (x // 256) % 256)

    apply_autofit(source, profiles, "fill")

    out_dir = Path(__file__).parent / "_verify_out"
    out_dir.mkdir(exist_ok=True)

    errors = 0
    for p in profiles:
        rendered = render_monitor_image(source, p)
        out_path = out_dir / f"{p.name}.png"
        rendered.save(out_path)
        vx0 = p.pos_x - min_x
        vy0 = p.pos_y - min_y
        for sample in (
            (0, 0),
            (p.width // 2, p.height // 2),
            (p.width - 1, p.height - 1),
        ):
            lx, ly = sample
            expected = px[vx0 + lx, vy0 + ly]
            got = rendered.getpixel((lx, ly))
            if got != expected:
                errors += 1
                print(f"MISMATCH {p.name} local ({lx},{ly}) expected {expected} got {got}")

    print(f"Monitors: {len(profiles)}, desktop {total_w}×{total_h}")
    for p in profiles:
        print(f"  {p.name}: {p.width}×{p.height} @ ({p.pos_x},{p.pos_y}) zoom={p.zoom} off=({p.offset_x},{p.offset_y})")
    if errors:
        print(f"FAILED: {errors} sample pixel mismatches")
        return 1
    print(f"OK — crops match virtual desktop. Previews in {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
