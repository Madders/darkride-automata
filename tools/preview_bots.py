"""Quick flat-shaded previews of a roster's bots, for designing without a viewer (docs/designing-bots.md).

Draws each bot in its rest pose from the side, the front and three-quarters, in its colours (without its weathering),
one row per bot, into a PNG.

Usage: python preview_bots.py <out.png> [--roster examples/bots.json] [bot id ...]
"""
import json
import pathlib
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_bots as bb  # noqa: E402

S = 300
ZOOM = 0.8


def colour(bot, material):
    return np.array({"shell": bot["shell"], "dark": [0.1, 0.1, 0.12], "glow": bot["accent"],
                     "lens": [0.03, 0.03, 0.05]}[material])


def view(parts, bot, yaw):
    rot = bb.R.from_euler("y", yaw, degrees=True).as_matrix() @ bb.R.from_euler("x", -12, degrees=True).as_matrix().T
    light = np.array([0.4, 0.8, -0.5])
    light /= np.linalg.norm(light)
    tris, cols = [], []
    for mesh, material, bone, smooth in parts:
        v = mesh.vertices @ rot.T
        f = mesh.faces
        n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
        keep = n[:, 2] < 0
        shade = 0.35 + 0.65 * np.clip(-(n[keep] @ (rot @ light)), 0, 1)
        base = colour(bot, material)
        tris.append(v[f[keep]])
        cols.append(np.tile(base, (keep.sum(), 1)) if material == "glow" else np.outer(shade, base))
    tris, cols = np.concatenate(tris), np.concatenate(cols)
    im = Image.new("RGB", (S, S), (28, 26, 40))
    d = ImageDraw.Draw(im)
    for t, c in zip(tris[np.argsort(-tris[:, :, 2].mean(1))], cols[np.argsort(-tris[:, :, 2].mean(1))]):
        d.polygon([((p[0] * ZOOM + 0.5) * S, (0.55 - p[1] * ZOOM) * S) for p in t],
                  fill=tuple(int(255 * min(1, x)) for x in c))
    d.text((8, 6), bot["name"], fill=(220, 220, 230))
    return im


def main():
    argv = sys.argv[1:]
    out = pathlib.Path(argv[0])
    roster_path = pathlib.Path(argv[argv.index("--roster") + 1]) if "--roster" in argv else bb.KIT / "examples" / "bots.json"
    wanted = [a for i, a in enumerate(argv[1:], 1) if not a.startswith("--") and argv[i - 1] != "--roster"]
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    skeleton = json.loads((bb.HERE / "skeleton.json").read_text(encoding="utf-8"))
    cycles = json.loads((bb.KIT / "bot" / "duck-cycles.json").read_text(encoding="utf-8"))
    sk = bb.Skeleton(skeleton, cycles)
    bots = [b for b in roster["bots"] if not wanted or b["id"] in wanted]
    sheet = Image.new("RGB", (S * 3, S * len(bots)))
    for row, bot in enumerate(bots):
        parts = bb.design(sk, dict(bot.get("style", {}), rockets=bot.get("rockets", False)))
        for col, yaw in enumerate((90, 0, 35)):
            sheet.paste(view(parts, bot, yaw), (col * S, row * S))
    sheet.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
