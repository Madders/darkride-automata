"""The bots' walkable map of a world (nav.json), from the world's entities and their models.

Builds the world from its entities as Darkride places them (each model's bounds fitted to the entity's dimensions
about its registration point, rotated and moved, following parents), then on a grid of cells:
- casts a ray down each column and keeps every upward-facing surface with headroom of clear space above it as a
  walkable cell (several per column: ground floor, mezzanines, stair treads...);
- links neighbouring cells (8 ways) when the step between them is at most a step's height and short horizontal rays
  between them, at knee, mid, chest and handrail height, hit nothing: so thin walls, glass and railings still block;
- tags an EDGE where a cell's neighbour drops away with nothing there (a ledge to peer over), and a WALL where a
  neighbour is blocked; leaves off ledges too narrow to stand on beside a drop;
- keeps the largest connected area, so bots never start somewhere they can't leave;
- finds rocket hops (an upper floor's open edge above a floor reachable through clear air), for bots with rockets;
- adds points of interest from the world config's rules (things for the bots to go and look at).
Images laid flat count as floors.

Everything about the world is in a config file (docs/adding-to-your-world.md, examples/world.json): where the
entities are (a models.json, or the models.json.gz a domain server saves), where its models can be read from (local
folders for URL prefixes; anything else is downloaded and cached), the region to map, names to leave out, the points
of interest, and where to write nav.json. FBX models need FBX2glTF (its path in the config); glTF, OBJ and STL load
directly.

Usage (Python with trimesh, embreex, shapely, networkx): python build_nav.py <world.json> [--cell 0.25]
"""
import gzip
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import urllib.parse
import urllib.request

import numpy as np
import trimesh

# set from the world config (configure())
CONFIG = {}
WORLD = OUT = CACHE = FBX2GLTF = None
REGION = {"x": (-50.0, 50.0), "y": (-20.0, 30.0), "z": (-50.0, 50.0)}
CELL = 0.25
HEADROOM = 1.15      # the standard bot is about a metre tall
STEP = 0.32          # stairs rise about 0.2-0.25
DROP = 0.6           # a fall worth peering over
HOP = (1.2, 7.0)     # a rocket hop: how far up or down it goes (m)
HOP_REACH = (0.75, 1.75)  # how far out from the edge the floor below may be (m)
HOP_SHAFT = 0.3      # the open air above the landing must be this wide all round (m): no dropping down a slit
HOP_SPACING = 2.5    # hops on the same edge at least this far apart (m)
# knee and chest, plus mid and handrail height: a railing's rails stop the ducks too (they'd fit under, but
# shouldn't go out onto the ledge beyond)
BLOCK_HEIGHTS = (0.3, 0.55, 0.8, 1.05)
NARROW = 3                           # cells (0.75 m): a ledge this narrow beside a drop is left off the map
TOP = 30.0


def quat(q):
    x, y, z, w = q.get("x", 0), q.get("y", 0), q.get("z", 0), q.get("w", 1)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def vec(v, default=0.0):
    return np.array([v.get("x", default), v.get("y", default), v.get("z", default)], float)


def world_pose(e, by_id):
    pos, rot = vec(e.get("position", {})), quat(e.get("rotation", {}))
    parent = by_id.get(e.get("parentID"))
    if parent is None:
        return pos, rot
    ppos, prot = world_pose(parent, by_id)
    return ppos + prot @ pos, prot @ rot


def configure(path):
    """Reads the world config (see examples/world.json); paths in it are relative to it."""
    global CONFIG, WORLD, OUT, CACHE, FBX2GLTF, REGION, CELL, HEADROOM, STEP, DROP
    path = pathlib.Path(path).resolve()
    CONFIG = json.loads(path.read_text(encoding="utf-8"))
    here = path.parent
    WORLD = here / CONFIG["entities"]
    OUT = here / CONFIG.get("out", "nav.json")
    CACHE = here / CONFIG.get("cache", ".navcache")
    FBX2GLTF = here / CONFIG["fbx2gltf"] if CONFIG.get("fbx2gltf") else None
    REGION = {a: tuple(CONFIG["region"][a]) for a in "xyz"} if "region" in CONFIG else REGION
    sizes = CONFIG.get("bot", {})
    CELL, HEADROOM = sizes.get("cell", CELL), sizes.get("headroom", HEADROOM)
    STEP, DROP = sizes.get("step", STEP), sizes.get("drop", DROP)
    CONFIG["_assets"] = {prefix: here / folder for prefix, folder in CONFIG.get("assets", {}).items()}


def read_entities():
    raw = WORLD.read_bytes()
    text = gzip.decompress(raw).decode("utf-8") if raw[:2] == b"\x1f\x8b" else raw.decode("utf-8")
    return json.loads(text)["Entities"]


def local_file(url):
    """Where a model is on disk: under a local folder its URL prefix maps to, or downloaded into the cache."""
    for prefix, folder in CONFIG["_assets"].items():
        if url.startswith(prefix):
            return folder / urllib.parse.unquote(url[len(prefix):]).lstrip("/")
    if url.startswith(("http://", "https://")) and CONFIG.get("download", True):
        name = urllib.parse.unquote(urllib.parse.urlsplit(url).path).rsplit("/", 1)[-1]
        target = CACHE / "download" / (hashlib.sha1(url.encode()).hexdigest()[:10] + "-" + name)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "darkride-bots build_nav"})
                target.write_bytes(urllib.request.urlopen(request, timeout=60).read())
            except Exception as error:
                print("  couldn't download %s: %s" % (url, str(error)[:80]))
                return pathlib.Path("missing")
        return target
    return pathlib.Path("missing")


def load_mesh(url, cache):
    if url in cache:
        return cache[url]
    path = local_file(url)
    mesh = None
    try:
        if path.suffix.lower() == ".fbx":
            if not FBX2GLTF:
                print("  skipped %s: an FBX model needs fbx2gltf in the config" % path.name)
                cache[url] = None
                return None
            CACHE.mkdir(parents=True, exist_ok=True)
            glb = CACHE / (path.stem + ".glb")
            if not glb.exists():
                subprocess.run([str(FBX2GLTF), "--binary", "-i", str(path), "-o", str(CACHE / path.stem)],
                               check=True, capture_output=True)
            path = glb
        if path.exists():
            loaded = trimesh.load(str(path), force="scene")
            mesh = loaded.to_geometry() if isinstance(loaded, trimesh.Scene) else loaded
    except Exception as error:   # a model that won't load is just left out of the map
        print("  skipped %s: %s" % (path.name, str(error)[:80]))
    cache[url] = mesh
    return mesh


def scene_triangles():
    entities = read_entities()
    by_id = {e["id"]: e for e in entities}
    cache, parts = {}, []
    skip = tuple(CONFIG.get("skip", []))
    for e in entities:
        if e["type"] not in ("Model", "Box", "Shape", "Image"):
            continue
        name = e.get("name", "")
        if (skip and name.startswith(skip)) or e.get("visible") is False:
            continue
        pos, rot = world_pose(e, by_id)
        if not all(REGION[a][0] < pos[i] < REGION[a][1] for i, a in enumerate("xyz")):
            continue
        dims = vec(e.get("dimensions", {}), 0.1)
        reg = vec(e.get("registrationPoint", {}), 0.5)
        if e["type"] == "Model":
            mesh = load_mesh(e.get("modelURL", ""), cache)
            if mesh is None or len(mesh.faces) == 0:
                continue
            lo, hi = mesh.bounds
            size = np.maximum(hi - lo, 1e-6)
            local = (mesh.vertices - lo) / size * dims - reg * dims
            faces = mesh.faces
        elif e["type"] == "Image":
            # some floors are images laid flat (the office deck's model has no top): a quad in its local x-y
            w, h = dims[0] / 2, dims[1] / 2
            local = np.array([[-w, -h, 0], [w, -h, 0], [w, h, 0], [-w, h, 0]])
            faces = np.array([[0, 1, 2], [0, 2, 3], [0, 2, 1], [0, 3, 2]])     # both sides
        else:
            box = trimesh.creation.box(extents=dims)
            local = box.vertices - (reg - 0.5) * dims
            faces = box.faces
        parts.append((local @ rot.T + pos, faces))
    vs, fs, base = [], [], 0
    for v, f in parts:
        vs.append(v); fs.append(f + base); base += len(v)
    scene = trimesh.Trimesh(np.vstack(vs), np.vstack(fs), process=False)
    print("scene: %d models, %d triangles" % (len(parts), len(scene.faces)))
    return scene


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    configure(sys.argv[1])
    cell = float(dict(zip(sys.argv[2:], sys.argv[3:])).get("--cell", CELL))
    scene = scene_triangles()
    ray = scene.ray
    xs = np.arange(REGION["x"][0], REGION["x"][1], cell) + cell / 2
    zs = np.arange(REGION["z"][0], REGION["z"][1], cell) + cell / 2
    gx, gz = np.meshgrid(xs, zs, indexing="ij")
    origins = np.column_stack([gx.ravel(), np.full(gx.size, TOP), gz.ravel()])
    down = np.tile([0, -1.0, 0], (len(origins), 1))
    locs, ray_ids, tri_ids = ray.intersects_location(origins, down, multiple_hits=True)
    normals = scene.face_normals[tri_ids]
    # every surface a column's ray passes, top down; a floor is an upward face with headroom up to the next surface
    columns = {}
    for (x, y, z), r, n in zip(locs, ray_ids, normals[:, 1]):
        columns.setdefault(r, []).append((y, n))
    cells = []                       # (ix, iz, height)
    for r, hits in columns.items():
        hits.sort(key=lambda h: -h[0])
        ix, iz = divmod(r, len(zs))
        for k, (y, ny) in enumerate(hits):
            if ny < 0.7 or not (REGION["y"][0] < y < REGION["y"][1]):
                continue
            above = [h[0] for h in hits[:k] if h[0] > y + 0.02]
            if above and min(above) - y < HEADROOM:
                continue
            if any(abs(c[2] - y) < 0.05 for c in cells[-3:] if c[0] == ix and c[1] == iz):
                continue
            cells.append((int(ix), int(iz), round(float(y), 3)))
    # one-cell holes (a doorway's threshold with no floor of its own, between the bridge and the office): bridge
    # them where floors on opposite sides line up and nothing in the column blocks the way
    occupied = {}
    for i, (ix, iz, y) in enumerate(cells):
        occupied.setdefault((ix, iz), []).append(y)
    filled = 0
    for (ix, iz), ys in list(occupied.items()):
        for y in ys:
            for dx, dz in ((1, 0), (0, 1)):
                gap, far = (ix + dx, iz + dz), (ix + 2 * dx, iz + 2 * dz)
                if any(abs(g - y) <= STEP for g in occupied.get(gap, [])):
                    continue
                match = [g for g in occupied.get(far, []) if abs(g - y) <= STEP]
                if not match:
                    continue
                h = (y + match[0]) / 2
                hits = columns.get(gap[0] * len(zs) + gap[1], [])
                if any(h + 0.05 < hy < h + HEADROOM for hy, _ in hits):
                    continue
                cells.append((gap[0], gap[1], round(h, 3)))
                occupied.setdefault(gap, []).append(h)
                filled += 1
    print("walkable surfaces: %d (%d one-cell gaps bridged)" % (len(cells), filled))

    # neighbours: by column, then the nearest surface in the neighbouring column within STEP
    by_column = {}
    for i, (ix, iz, y) in enumerate(cells):
        by_column.setdefault((ix, iz), []).append(i)
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    pairs, edge, wall = [], np.zeros(len(cells), bool), np.zeros(len(cells), bool)
    drop_dirs = [[] for _ in cells]              # which way each edge cell's drop is
    for i, (ix, iz, y) in enumerate(cells):
        for d, (dx, dz) in enumerate(dirs):
            others = by_column.get((ix + dx, iz + dz), [])
            near = [j for j in others if abs(cells[j][2] - y) <= STEP]
            if not near:
                # anything at all in that column from a fall's depth up to head height is an obstacle (a plant,
                # a rock, a wall); only an empty column there is a drop to peer over
                hits = columns.get((ix + dx) * len(zs) + (iz + dz), [])
                if any(y - DROP < h < y + HEADROOM for h, _ in hits):
                    wall[i] = True
                else:
                    edge[i] = True
                    if dx == 0 or dz == 0:
                        drop_dirs[i].append((dx, dz))
                continue
            j = min(near, key=lambda j: abs(cells[j][2] - y))
            if j > i:
                pairs.append((i, j, d))
    # thin walls and glass: short rays between linked cells at knee, mid, chest and handrail height
    ok = np.ones(len(pairs), bool)
    for height in BLOCK_HEIGHTS:
        o = np.array([[xs[cells[i][0]], cells[i][2] + height, zs[cells[i][1]]] for i, j, d in pairs])
        t = np.array([[xs[cells[j][0]], cells[j][2] + height, zs[cells[j][1]]] for i, j, d in pairs])
        v = t - o
        length = np.linalg.norm(v, axis=1)
        hit_locs, hit_rays, _ = ray.intersects_location(o, v / length[:, None], multiple_hits=False)
        for loc, r in zip(hit_locs, hit_rays):
            if np.linalg.norm(loc - o[r]) < length[r]:
                ok[r] = False
    links = [[] for _ in cells]
    step_to = [{} for _ in cells]                # direction index -> the linked neighbour that way
    opposite = {0: 1, 1: 0, 2: 3, 3: 2, 4: 7, 7: 4, 5: 6, 6: 5}
    for (i, j, d), good in zip(pairs, ok):
        if good:
            links[i].append(j); links[j].append(i)
            step_to[i][d] = j; step_to[j][opposite[d]] = i
        else:
            wall[i] = wall[j] = True
    # narrow ledges beside a drop (the strip outside a railing, the top of a wall): no place for a duck. A cell goes
    # if, across x or across z, the floor it's on is at most NARROW cells wide and one of those cells is at a drop
    narrow = np.zeros(len(cells), bool)
    for i in range(len(cells)):
        for a, b in ((0, 1), (2, 3)):
            span = [i]
            for d in (a, b):
                u = i
                while len(span) <= NARROW and d in step_to[u]:
                    u = step_to[u][d]; span.append(u)
            if len(span) <= NARROW and any(edge[u] for u in span):
                narrow[i] = True
    for i in np.nonzero(narrow)[0]:
        for j in links[i]:
            links[j].remove(i)
        links[i] = []
    print("narrow ledge cells dropped: %d" % narrow.sum())
    # keep the largest connected area
    seen, best = np.full(len(cells), -1), None
    comp = 0
    for s in range(len(cells)):
        if seen[s] >= 0:
            continue
        stack, members = [s], []
        seen[s] = comp
        while stack:
            u = stack.pop(); members.append(u)
            for w in links[u]:
                if seen[w] < 0:
                    seen[w] = comp; stack.append(w)
        if best is None or len(members) > len(best):
            best = members
        if len(members) > 150:
            hs = [cells[m][2] for m in members]
            mx = [xs[cells[m][0]] for m in members]; mz = [zs[cells[m][1]] for m in members]
            print("  area %d: %d cells, x %.1f..%.1f z %.1f..%.1f, heights %.2f..%.2f"
                  % (comp, len(members), min(mx), max(mx), min(mz), max(mz), min(hs), max(hs)))
        comp += 1
    keep = sorted(best)
    remap = {old: new for new, old in enumerate(keep)}
    out_cells = [[cells[i][0], cells[i][1], cells[i][2], int(edge[i]) | (int(wall[i]) << 1)] for i in keep]
    out_links = [[remap[j] for j in links[i] if j in remap] for i in keep]
    hops = find_hops(ray, cells, keep, edge, wall, drop_dirs, xs, zs)
    out_hops = [[remap[i], remap[j]] for i, j in hops]
    heights = [c[2] for c in out_cells]
    print("kept %d cells (largest of %d areas), heights %.2f to %.2f, %d edge and %d wall cells"
          % (len(keep), comp, min(heights), max(heights), sum(c[3] & 1 for c in out_cells),
             sum(c[3] >> 1 & 1 for c in out_cells)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"cell": cell, "origin": [REGION["x"][0], REGION["z"][0]],
                               "cells": out_cells, "links": out_links, "hops": out_hops, "pois": pois()},
                              separators=(",", ":")), encoding="utf-8")
    print("wrote %s (%.0f KB)" % (OUT, OUT.stat().st_size / 1024))


def find_hops(ray, cells, keep, edge, wall, drop_dirs, xs, zs):
    """Rocket hops for the small bots that can't climb stairs: from a floor up onto an upper floor's open edge (and
    back down). A hop is an edge cell with a drop (no railing: that's what makes it an edge), and the highest floor
    cell a little way out beyond it and HOP lower, with clear air from just above that floor up past the upper floor
    (nothing overhead to hit) and nothing between the two at the upper floor's height. Hops along the same edge are
    thinned to one every HOP_SPACING metres. Each is (upper cell, lower cell)."""
    kept = set(keep)
    column = {}
    for i in keep:
        column.setdefault((cells[i][0], cells[i][1]), []).append(i)
    cell = xs[1] - xs[0]
    pos = lambda i: np.array([xs[cells[i][0]], cells[i][2], zs[cells[i][1]]])
    found = []
    for i in keep:
        if not edge[i] or wall[i] or not drop_dirs[i]:
            continue
        out = np.mean(drop_dirs[i], axis=0)
        if not np.any(out):
            continue
        out = np.array([out[0], 0, out[1]], float) / np.linalg.norm(out)
        p = pos(i)
        for reach in np.arange(HOP_REACH[0], HOP_REACH[1] + 0.01, cell):
            q = p + out * reach
            ix, iz = int(round((q[0] - xs[0]) / cell)), int(round((q[2] - zs[0]) / cell))
            below = [j for j in column.get((ix, iz), []) if HOP[0] <= p[1] - cells[j][2] <= HOP[1] and not wall[j]]
            if not below:
                continue
            j = max(below, key=lambda j: cells[j][2])
            landing = pos(j)
            # open air from just above the landing to above the upper floor, across a shaft the bot fits through
            starts = [landing + [dx, 0.3, dz] for dx, dz in ((0, 0), (HOP_SHAFT, 0), (-HOP_SHAFT, 0), (0, HOP_SHAFT), (0, -HOP_SHAFT))]
            rise = p[1] + 1.2 - (landing[1] + 0.3)
            hits, idx, _ = ray.intersects_location(starts, [[0, 1.0, 0]] * len(starts), multiple_hits=False)
            if any(h[1] - starts[k][1] < rise for h, k in zip(hits, idx)):
                break                                    # something overhead or too close: no hop here
            a, b = p + [0, 0.5, 0], np.array([landing[0], p[1] + 0.5, landing[2]])
            d = np.linalg.norm(b - a)
            hits, _, _ = ray.intersects_location([a], [(b - a) / d], multiple_hits=False)
            if len(hits) and np.linalg.norm(hits[0] - a) < d:
                break
            found.append((i, j, p[1] - landing[1]))
            break
    chosen = []
    for i, j, rise in sorted(found, key=lambda h: -h[2]):
        if all(np.linalg.norm(pos(i) - pos(k)) >= HOP_SPACING for k, _ in chosen):
            chosen.append((i, j))
    print("rocket hops: %d (of %d places)" % (len(chosen), len(found)))
    for i, j in chosen[:12]:
        print("  up %.1f m onto %s from %s" % (cells[i][2] - cells[j][2], pos(i).round(1), pos(j).round(1)))
    return chosen


def pois():
    """Things worth a bot's attention, by the config's rules: each {"kind", "type" (optional entity type), "match"
    (optional: any of these in the entity's model or image URL or its name)}. The first rule that fits names it."""
    entities = read_entities()
    by_id = {e["id"]: e for e in entities}
    rules = CONFIG.get("pois", [])
    out = []
    for e in entities:
        if e.get("visible") is False:
            continue
        text = e.get("modelURL", "") + " " + e.get("imageURL", "") + " " + e.get("name", "")
        for rule in rules:
            if rule.get("type") and e["type"] != rule["type"]:
                continue
            if rule.get("match") and not any(m in text for m in rule["match"]):
                continue
            p, _ = world_pose(e, by_id)
            if all(REGION[a][0] < p[i] < REGION[a][1] for i, a in enumerate("xyz")):
                out.append({"kind": rule["kind"], "p": [round(float(c), 2) for c in p]})
            break
    return out


if __name__ == "__main__":
    main()
