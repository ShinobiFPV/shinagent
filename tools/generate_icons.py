#!/usr/bin/env python3
"""Generate Q2 PWA icons — H9000 Terminal HAL-eye aesthetic.

Renders concentric-ring "eye" icons (housing, rings, iris, glowing core)
onto a terminal-black background at the three sizes the PWA manifest and
apple-touch-icon meta tag reference. Re-run this any time the design
constants below change; it fully regenerates all three PNGs.

    py -3 tools/generate_icons.py
"""
import math
import os

try:
    import cairo  # noqa: F401
    USE_CAIRO = True
except ImportError:
    USE_CAIRO = False

from PIL import Image, ImageDraw

ICON_DIR = os.path.join(os.path.dirname(__file__), "..", "webapp", "static", "icons")
SIZES = (180, 192, 512)
SUPERSAMPLE = 4  # render this many times larger, then downsample for antialiasing

BG = (0, 8, 10, 255)
EYE_COLOR = (255, 60, 60)  # #ff3c3c — matches current UI red theme
WARM_WHITE = (255, 255, 230)


def _lerp_color(c0, c1, t):
    return tuple(int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(len(c0)))


def _color_at(stops, t):
    """stops: ascending list of (t, rgba) tuples. Clamp + linearly interpolate."""
    if t <= stops[0][0]:
        return stops[0][1]
    if t >= stops[-1][0]:
        return stops[-1][1]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return _lerp_color(c0, c1, local_t)
    return stops[-1][1]


def radial_gradient_layer(size, cx, cy, r_outer, stops):
    """A transparent RGBA layer with a radial gradient filled circle,
    built as concentric filled circles from the outside in (PIL has no
    native radial gradient), one pixel-radius step at a time."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    r_outer_px = max(1, int(round(r_outer)))
    for r in range(r_outer_px, 0, -1):
        t = r / r_outer_px
        color = _color_at(stops, t)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    return layer


def draw_housing(size, cx, cy, housing_r):
    stops = [
        (0.0, (20, 30, 25, 255)),
        (1.0, (5, 10, 8, 255)),
    ]
    return radial_gradient_layer(size, cx, cy, housing_r, stops)


def draw_rings(size, cx, cy, housing_r, tick_mode):
    """tick_mode: None (no ticks), "outer3" (outer 3 rings only), "all" (every ring)."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    n_rings = 7
    for r in range(n_rings):
        radius = housing_r * (0.75 - r * 0.07)
        alpha = 0.15 + r * 0.04
        width = 1.5 + r * 0.3
        color = (*EYE_COLOR, int(round(alpha * 255)))
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=color, width=max(1, int(round(width))),
        )

        draw_ticks = tick_mode == "all" or (tick_mode == "outer3" and r < 3)
        if draw_ticks:
            n_ticks = 12 + r * 4
            tick_len = width * 2.2
            for i in range(n_ticks):
                angle = 2 * math.pi * i / n_ticks
                x0 = cx + math.cos(angle) * (radius - tick_len / 2)
                y0 = cy + math.sin(angle) * (radius - tick_len / 2)
                x1 = cx + math.cos(angle) * (radius + tick_len / 2)
                y1 = cy + math.sin(angle) * (radius + tick_len / 2)
                draw.line([x0, y0, x1, y1], fill=color, width=max(1, int(round(width * 0.6))))
    return layer


def draw_iris(size, cx, cy, housing_r):
    iris_r = housing_r * 0.38
    stops = [
        (0.0, (*EYE_COLOR, int(round(0.15 * 255)))),
        (1.0, (*EYE_COLOR, int(round(0.55 * 255)))),
    ]
    return radial_gradient_layer(size, cx, cy, iris_r, stops)


def draw_core(size, cx, cy, icon_size):
    core_r = icon_size * 0.13
    stops = [
        (0.0, (*WARM_WHITE, 255)),
        (0.2, (*EYE_COLOR, 255)),
        (0.7, (*EYE_COLOR, 128)),
        (1.0, (*EYE_COLOR, 0)),
    ]
    layer = radial_gradient_layer(size, cx, cy, core_r * 2.5, stops)

    # specular highlight, offset up-left
    draw = ImageDraw.Draw(layer)
    hx = cx - core_r * 0.25
    hy = cy - core_r * 0.25
    hr = core_r * 0.18
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255, int(round(0.85 * 255))))
    return layer


def draw_halo(size, cx, cy, housing_r):
    # Soft bloom that peaks right at the housing edge and fades both
    # inward and outward, rather than a flat fill — a flat inner fill
    # (the literal reading of "inner stop = 0.35 solid") washes out the
    # rings/iris/core drawn on top of it.
    r_outer = housing_r * 1.4
    stops = [
        (0.0, (*EYE_COLOR, 0)),
        (housing_r * 0.9 / r_outer, (*EYE_COLOR, int(round(0.35 * 255)))),
        (1.0, (*EYE_COLOR, 0)),
    ]
    return radial_gradient_layer(size, cx, cy, r_outer, stops)


def apply_scanlines(img):
    """Faint horizontal scanline texture — only used on the 512px icon,
    too fine to read at smaller sizes."""
    size = img.width
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for y in range(0, size, 3):
        draw.line([0, y, size, y], fill=(0, 0, 0, int(round(0.15 * 255))), width=1)
    return Image.alpha_composite(img, layer)


def round_corners(img, corner_radius):
    size = img.width
    mask = Image.new("L", (size, size), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([0, 0, size - 1, size - 1], radius=corner_radius, fill=255)
    img.putalpha(mask)
    return img


def generate_icon(icon_size):
    ss = SUPERSAMPLE
    size = icon_size * ss
    cx = cy = size / 2
    housing_r = size * 0.44

    base = Image.new("RGBA", (size, size), BG)

    # Halo is composited behind the rings/iris/core as an ambient glow —
    # drawing it last (as literally listed) would wash a flat fill
    # over the whole eye, since its inner stop is a solid, not transparent.
    for layer in (
        draw_housing(size, cx, cy, housing_r),
        draw_halo(size, cx, cy, housing_r),
        draw_rings(size, cx, cy, housing_r, tick_mode={
            180: None, 192: "outer3", 512: "all",
        }[icon_size]),
        draw_iris(size, cx, cy, housing_r),
        draw_core(size, cx, cy, size),
    ):
        base = Image.alpha_composite(base, layer)

    icon = base.resize((icon_size, icon_size), Image.LANCZOS)

    if icon_size == 512:
        icon = apply_scanlines(icon)

    if icon_size == 180:
        icon = round_corners(icon, int(round(icon_size * 0.22)))

    return icon


def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    for icon_size in SIZES:
        icon = generate_icon(icon_size)
        out_path = os.path.join(ICON_DIR, f"icon-{icon_size}.png")
        icon.save(out_path, "PNG")
        print(f"wrote {out_path} ({icon.width}x{icon.height})")


if __name__ == "__main__":
    if USE_CAIRO:
        print("pycairo available but not implemented in this script; using Pillow path")
    main()
