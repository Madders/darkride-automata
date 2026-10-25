"""Weathered paint for the bots, in a used-future style (2026-10-24): textures per bot for its shell, its dark metal, its
bare metal and its trim (second paint), from its colours and its wear (0 factory-fresh to 1 beaten up); and with each
a normal map, so panel lines, rivets, chips and scratches catch the light as relief rather than being painted flat.

The shell: its colour with a little fade across it, panels with dark panel lines and grime gathered along them, some
panels in a second paint (like R2-D2's blue), a stencilled unit number and, on the worn ones, hazard stripes; then
paint chips down to bare metal, scratches, grime, scorch marks and rust streaks running down from the chips, all more
so the higher the wear. The dark metal: gunmetal with scratches and grime. Textures are box-projected onto the bots
(build_bots.py), with v running down the world, so the streaks run down.
"""
import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

N = 1024
BARE = np.array([0.5, 0.5, 0.52])
PRIMER = np.array([0.33, 0.3, 0.27])
RUST = np.array([0.42, 0.22, 0.09])


def noise(rng, scale, octaves=4):
    """Smooth value noise in 0..1, from random grids blown up and summed."""
    out = np.zeros((N, N))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = max(2, int(scale * 2 ** o))
        grid = Image.fromarray((rng.random((cells, cells)) * 255).astype(np.uint8))
        out += amp * np.asarray(grid.resize((N, N), Image.BICUBIC), float) / 255
        total += amp
        amp *= 0.5
    return out / total


def panels(rng, depth=4):
    """Rectangles splitting the texture into panels (x0, y0, x1, y1), like plating."""
    out = []

    def split(x0, y0, x1, y1, d):
        if d == 0 or (x1 - x0 < 90 and y1 - y0 < 90) or (d < 3 and rng.random() < 0.25):
            out.append((x0, y0, x1, y1))
            return
        if (x1 - x0) > (y1 - y0):
            c = int(x0 + (x1 - x0) * rng.uniform(0.3, 0.7))
            split(x0, y0, c, y1, d - 1); split(c, y0, x1, y1, d - 1)
        else:
            c = int(y0 + (y1 - y0) * rng.uniform(0.3, 0.7))
            split(x0, y0, x1, c, d - 1); split(x0, c, x1, y1, d - 1)
    split(0, 0, N, N, depth)
    return out


def font(size):
    for name in ("C:/Windows/Fonts/bahnschrift.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        try:
            f = ImageFont.truetype(name, size)
            if name.endswith("bahnschrift.ttf"):
                f.set_variation_by_name("Bold Condensed")
            return f
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def shell_texture(bot, seed, colour=None, markings=True, second_panels=True):
    """The painted shell (or, with colour, another painted or bare part): (texture PNG, normal map PNG)."""
    rng = np.random.default_rng(seed)
    wear = bot.get("wear", 0.3)
    colour = np.array(bot["shell"] if colour is None else colour, float)
    second = np.array(bot.get("paint2", list(np.array(bot["accent"]) * 0.55 + colour * 0.15)), float)
    img = np.ones((N, N, 3)) * colour
    img *= (1 + (noise(rng, 3) - 0.5) * (0.08 + 0.15 * wear))[..., None]          # uneven paint, fading

    # panels: a few in the second paint; then the lines between them, with grime gathered along them
    plates = panels(rng)
    for x0, y0, x1, y1 in plates:
        if rng.random() < 0.18 and second_panels:
            img[y0 + 3:y1 - 3, x0 + 3:x1 - 3] = second * (0.95 + 0.1 * rng.random())
    lines = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(lines)
    for x0, y0, x1, y1 in plates:
        d.rectangle((x0, y0, x1 - 1, y1 - 1), outline=255, width=3)
        for cx, cy in ((x0 + 9, y0 + 9), (x1 - 10, y0 + 9), (x0 + 9, y1 - 10), (x1 - 10, y1 - 10)):   # rivets
            if x1 - x0 > 60 and y1 - y0 > 60:
                d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=200)
    line = np.asarray(lines, float) / 255
    height = 1 - 0.9 * np.asarray(lines.filter(ImageFilter.GaussianBlur(1.2)), float) / 255    # grooves, rivets as bumps
    height += 0.6 * (np.asarray(lines, float) == 200)
    near = np.asarray(lines.filter(ImageFilter.GaussianBlur(10 + 14 * wear)), float) / 255
    img *= (1 - 0.55 * line)[..., None]
    img *= (1 - np.clip(near * (0.6 + 1.6 * wear), 0, 0.55))[..., None]

    # markings: the unit number stencilled on, and hazard stripes on the working bots
    marks = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    dm = ImageDraw.Draw(marks)
    ink = (25, 25, 30, 230) if colour.mean() > 0.45 else (225, 225, 215, 220)
    label = bot.get("unit", "MP-%02d" % (seed % 97))
    for k in range(2 if markings else 0):
        x, y = rng.integers(80, N - 260), rng.integers(80, N - 120)
        dm.text((x, y), label, font=font(int(rng.integers(36, 64))), fill=ink)
    if wear > 0.35 and markings:
        x, y = rng.integers(60, N - 300), rng.integers(60, N - 140)
        for k in range(8):
            c = (230, 180, 20, 235) if k % 2 == 0 else (20, 20, 20, 235)
            dm.polygon([(x + k * 32, y), (x + k * 32 + 32, y), (x + k * 32 + 72, y + 80), (x + k * 32 + 40, y + 80)], fill=c)
    m = np.asarray(marks, float) / 255
    img = img * (1 - m[..., 3:]) + m[..., :3] * m[..., 3:]

    # wear: chips, scratches, grime, scorching, rust running down from the chips
    chips = Image.new("L", (N, N), 0)
    dc = ImageDraw.Draw(chips)
    spots = []
    # chips gather where things get knocked: along panel edges and at corners, small, many only on worn bots
    edges = np.argwhere(line > 0.5)
    for _ in range(int(700 * wear ** 2)):
        if rng.random() < 0.75 and len(edges):
            y, x = edges[rng.integers(len(edges))] + rng.normal(0, 4 + 10 * wear, 2)
        else:
            x, y = rng.integers(0, N), rng.integers(0, N)
        r = rng.uniform(1.5, 3 + 9 * wear)
        pts = [(x + r * rng.uniform(0.5, 1.2) * np.cos(a), y + r * rng.uniform(0.5, 1.2) * np.sin(a))
               for a in np.linspace(0, 2 * np.pi, 9, endpoint=False)]
        dc.polygon(pts, fill=255)
        spots.append((x, y, r))
    chip = np.asarray(chips, float) / 255
    rim = np.clip(np.asarray(chips.filter(ImageFilter.MaxFilter(5)), float) / 255 - chip, 0, 1)
    under = np.where((noise(rng, 20, 2) > 0.5)[..., None], BARE, PRIMER)     # bare metal or the primer under
    img = img * (1 - chip[..., None]) + under * chip[..., None]
    height -= 0.35 * np.asarray(chips.filter(ImageFilter.GaussianBlur(1)), float) / 255         # chipped-off paint
    img *= (1 - 0.45 * rim)[..., None]
    scratches = Image.new("L", (N, N), 0)
    ds = ImageDraw.Draw(scratches)
    for _ in range(int(10 + 300 * wear)):
        x, y = rng.integers(0, N), rng.integers(0, N)
        a, ln = rng.uniform(0, np.pi), rng.uniform(10, 30 + 90 * wear)
        ds.line((x, y, x + ln * np.cos(a), y + ln * np.sin(a)), fill=int(rng.integers(60, 160)), width=1)
    s = np.asarray(scratches, float)[..., None] / 255
    img = img * (1 - 0.6 * s) + BARE * 0.6 * s
    height -= 0.25 * s[..., 0]
    height += (noise(rng, 40, 2) - 0.5) * 0.12 * (0.3 + wear)                                  # dents and pitting
    grime = noise(rng, 6) ** 2
    img *= (1 - np.clip(grime * (0.15 + 0.9 * wear), 0, 0.6))[..., None]
    if wear > 0.5:
        scorch = np.zeros((N, N))
        yy, xx = np.mgrid[0:N, 0:N]
        for _ in range(int(2 + 5 * (wear - 0.5) * 2)):
            cx, cy, r = rng.integers(0, N), rng.integers(0, N), rng.uniform(40, 130)
            scorch = np.maximum(scorch, np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
        scorch *= 0.4 + 0.6 * noise(rng, 10, 3)
        img *= (1 - 0.8 * np.clip(scorch, 0, 1))[..., None]
    if wear > 0.55:
        streaks = Image.new("L", (N, N), 0)
        dr = ImageDraw.Draw(streaks)
        for x, y, r in spots[:int(60 * wear)]:
            ln = rng.uniform(40, 220) * wear
            for k in range(int(r)):
                dr.line((x - r / 2 + k, y, x - r / 2 + k + rng.uniform(-3, 3), y + ln * rng.uniform(0.5, 1)),
                        fill=int(60 + 80 * rng.random()), width=1)
        st = np.asarray(streaks.filter(ImageFilter.GaussianBlur(2)), float)[..., None] / 255
        img = img * (1 - st) + RUST * st
    return to_png(img), normal_map(height)


def dark_texture(bot, seed):
    """The dark metal: gunmetal plates with faint panel lines and rivets, scratches and grime: (PNG, normal map PNG)."""
    rng = np.random.default_rng(seed + 1)
    wear = bot.get("wear", 0.3)
    img = np.ones((N, N, 3)) * np.array([0.085, 0.09, 0.105])
    img *= (1 + (noise(rng, 4) - 0.5) * 0.4)[..., None]
    lines = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(lines)
    for x0, y0, x1, y1 in panels(rng, 3):
        d.rectangle((x0, y0, x1 - 1, y1 - 1), outline=255, width=2)
        if x1 - x0 > 60 and y1 - y0 > 60:
            for cx, cy in ((x0 + 8, y0 + 8), (x1 - 9, y0 + 8), (x0 + 8, y1 - 9), (x1 - 9, y1 - 9)):
                d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=200)
    line = np.asarray(lines, float) / 255
    img *= (1 - 0.5 * line)[..., None]
    height = 1 - 0.9 * np.asarray(lines.filter(ImageFilter.GaussianBlur(1.2)), float) / 255
    height += 0.6 * (np.asarray(lines, float) == 200)
    scratches = Image.new("L", (N, N), 0)
    ds = ImageDraw.Draw(scratches)
    for _ in range(int(80 + 600 * wear)):
        x, y = rng.integers(0, N), rng.integers(0, N)
        a, ln = rng.uniform(0, np.pi), rng.uniform(8, 60)
        ds.line((x, y, x + ln * np.cos(a), y + ln * np.sin(a)), fill=int(rng.integers(60, 160)), width=1)
    s = np.asarray(scratches, float)[..., None] / 255
    img = img * (1 - 0.6 * s) + np.array([0.45, 0.45, 0.47]) * 0.6 * s
    height -= 0.25 * s[..., 0]
    img *= (1 - np.clip(noise(rng, 7) ** 2 * (0.2 + 0.7 * wear), 0, 0.6))[..., None]
    return to_png(img), normal_map(height)


def normal_map(height, strength=3.0):
    """A tangent-space normal map (glTF's: +x along u, +y up the image) from a height field."""
    dy, dx = np.gradient(height)
    n = np.dstack([-dx * strength, dy * strength, np.ones_like(height)])     # (image rows run down v; +y is up)
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return to_png(n * 0.5 + 0.5)


def spark(size=64):
    """A soft round glow, white in the middle, for the rockets' flame and smoke particles."""
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - size / 2 + 0.5, yy - size / 2 + 0.5) / (size / 2)
    a = np.clip(1 - r, 0, 1) ** 1.6
    img = np.dstack([np.ones_like(a), np.ones_like(a), np.ones_like(a), a])
    buf = io.BytesIO()
    Image.fromarray((img * 255).astype(np.uint8), "RGBA").save(buf, "PNG")
    return buf.getvalue()


def to_png(img):
    buf = io.BytesIO()
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(buf, "PNG", optimize=True)
    return buf.getvalue()
