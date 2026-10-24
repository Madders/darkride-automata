"""Builds the Madders Place duck's avatar and its walk clip from the Microduck's MJCF (spike, 2026-10-24).

- Avatar (duck.glb + duck.fst): a rigid-skinned glTF straight from MuJoCo's compiled model. Each body is a bone
  named after its joint (the trunk is "Hips"); every visual mesh is simplified (vertex clustering) and bound wholly
  to its body's bone;
  colours come from the MJCF materials. MuJoCo is Z-up with the duck facing +X; Darkride is Y-up, so everything is
  turned to face -Z (Darkride's forward) and scaled by --scale.
- Clip (duck-walk.json): the recording from record_walk.py as Darkride values per frame: the avatar's position
  (metres, from the clip's start) and yaw, the Hips' tilt, and each joint's local rotation, so the agent script only
  copies numbers. Played at 50 fps / sqrt(scale), the way a bigger body moves (Froude scaling).

The meshes are Pollen Robotics' (CC BY-NC-SA 4.0): fine for a personal, non-commercial world with credit, and
anything made from them stays under that licence. They stay in the git-ignored assets folder.

Usage: <venv>/python build_duck.py <scene.xml> <walk recording.json> <out dir> [--scale 4] [--cell 0.0015]
"""
import json
import math
import pathlib
import struct
import sys

import mujoco
import numpy as np

# MuJoCo (x forward, y left, z up) -> Darkride (x right, y up, -z forward)
M = np.array([[0, -1, 0], [0, 0, 1], [-1, 0, 0]], float)


CELL = 0.0015   # vertex clustering grid on the real 25 cm robot, metres


def cluster(verts, faces, cell):
    """Simplifies a CAD mesh by snapping vertices to a grid and merging them (edge-collapse simplifiers refuse
    these meshes: they're long thin slivers). Degenerate and duplicate triangles are dropped."""
    keys = np.floor(verts / cell).astype(np.int64)
    uniq, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.ravel()
    sums = np.zeros((len(uniq), 3))
    np.add.at(sums, inverse, verts)
    counts = np.bincount(inverse, minlength=len(uniq))[:, None]
    f = inverse[faces]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    _, first = np.unique(np.sort(f, axis=1), axis=0, return_index=True)
    return sums / counts, f[np.sort(first)]


def quat_to_mat(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def mat_to_quat(r):
    """(x, y, z, w), glTF and Darkride order."""
    t = np.trace(r)
    if t > 0:
        s = math.sqrt(t + 1) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, s / 4]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [s / 4, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, s / 4, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = math.sqrt(1 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, s / 4, (r[1, 0] - r[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def to_dr(r):
    """A MuJoCo-frame rotation as the same rotation in Darkride's frame."""
    return M @ r @ M.T


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = dict(zip(sys.argv[1::1], sys.argv[2::1]))
    scale = float(opts.get("--scale", 4))
    cell = float(opts.get("--cell", CELL))
    scene, recording, out = args[0], args[1], pathlib.Path(args[2])
    out.mkdir(parents=True, exist_ok=True)
    m = mujoco.MjModel.from_xml_path(scene)

    bodies = list(range(1, m.nbody))
    name = {b: ("Hips" if b == 1 else mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, m.body_jntadr[b])) for b in bodies}
    # bind pose = every joint at 0: a body's local frame is its MJCF pos/quat in its parent
    local_r = {b: quat_to_mat(m.body_quat[b]) for b in bodies}
    local_p = {b: m.body_pos[b].copy() for b in bodies}
    local_r[1], local_p[1] = np.eye(3), np.zeros(3)      # the trunk is the avatar's origin
    world = {}
    for b in bodies:
        parent = m.body_parentid[b]
        if parent in world:
            pr, pp = world[parent]
            world[b] = (pr @ local_r[b], pp + pr @ local_p[b])
        else:
            world[b] = (local_r[b], local_p[b])

    # visual meshes in the trunk frame, grouped by colour, each vertex bound to its body's bone
    visual = [g for g in range(m.ngeom) if m.geom_group[g] == 2 and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH]
    total = sum(m.mesh_facenum[m.geom_dataid[g]] for g in visual)
    groups, simplified = {}, {}
    for g in visual:
        mesh = m.geom_dataid[g]
        if mesh not in simplified:          # a part used several times (the servos) is simplified once
            v0, nv = m.mesh_vertadr[mesh], m.mesh_vertnum[mesh]
            f0, nf = m.mesh_faceadr[mesh], m.mesh_facenum[mesh]
            verts = m.mesh_vert[v0:v0 + nv].astype(np.float64)
            faces = m.mesh_face[f0:f0 + nf].astype(np.int64)
            verts, faces = cluster(verts, faces, cell)
            simplified[mesh] = (verts, faces)
        verts, faces = simplified[mesh]
        b = m.geom_bodyid[g]
        br, bp = world[b]
        gr, gp = quat_to_mat(m.geom_quat[g]), m.geom_pos[g]
        pts = (br @ (gr @ verts.T + gp[:, None]) + bp[:, None]).T
        rgba = tuple(np.round(m.mat_rgba[m.geom_matid[g]], 3)) if m.geom_matid[g] >= 0 else (0.8, 0.8, 0.8, 1)
        groups.setdefault(rgba, []).append((pts, faces, bodies.index(b)))

    primitives, materials = [], []
    for rgba, parts in groups.items():
        vs, fs, js, base = [], [], [], 0
        for pts, faces, joint in parts:
            vs.append(pts); fs.append(faces + base); js += [joint] * len(pts); base += len(pts)
        v = (M @ np.vstack(vs).T).T * scale
        f = np.vstack(fs)
        # flat normals per face would split vertices; smooth per part is enough for a robot shell
        n = np.zeros_like(v)
        tri = v[f]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        for k in range(3):
            np.add.at(n, f[:, k], fn)
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        primitives.append({"v": v, "n": n, "f": f, "j": np.array(js)})
        materials.append({"pbrMetallicRoughness": {"baseColorFactor": [float(c) for c in rgba],
                                                   "metallicFactor": 0.1, "roughnessFactor": 0.5}})

    joints_order = [name[b] for b in bodies]
    write_skinned_glb(out / "duck.glb", primitives, materials, bodies, m.body_parentid, local_r, local_p, world,
                      name, scale)
    # a scripted avatar knows its joints only from the .fst's jointIndex lines (ScriptableAvatar::getJointNames), and
    # sends rotations by those indices; a viewer applies them by index to the skeleton its glTF loader builds, which
    # has a ROOT joint first (the scene has two roots: the skeleton and the mesh) and the mesh node last. So the .fst
    # must list exactly that, or every rotation lands a joint off and the duck never moves its legs (2026-10-24)
    viewer_joints = ["ROOT"] + joints_order + ["duck_mesh"]
    (out / "duck.fst").write_text("\n".join([
        "name = MaddersDuck", "type = body+head", "scale = 1", "filename = duck.glb",
        "joint = jointRoot = Hips", "joint = jointHead = head_roll"]
        + ["jointIndex = %s = %d" % (n, i) for i, n in enumerate(viewer_joints)] + [""]), encoding="utf-8")

    # the clip in Darkride's terms
    rec = json.load(open(recording))
    jidx = {n: i for i, n in enumerate(rec["joints"])}
    start = np.array(rec["frames"][0][:3])
    frames = []
    for fr in rec["frames"]:
        pos, quat, q = np.array(fr[:3]), fr[3:7], fr[7:]
        trunk = quat_to_mat(quat)
        yaw = math.atan2(trunk[1, 0], trunk[0, 0])
        tilt = rot_z(-yaw) @ trunk                       # what's left once the heading is taken out
        p = M @ ((pos - start) * scale)
        row = [round(float(x), 4) for x in p] + [round(yaw, 4)]
        row += [round(float(x), 4) for x in mat_to_quat(to_dr(tilt))]
        for b in bodies[1:]:
            r = local_r[b] @ rot_z(q[jidx[name[b]]])
            row += [round(float(x), 4) for x in mat_to_quat(to_dr(r))]
        frames.append(row)
    json.dump({"fps": rec["fps"] / math.sqrt(scale), "scale": scale, "joints": joints_order[1:],
               "layout": "x y z (m, Darkride frame) yaw (rad) then Hips quat (x y z w) then each joint's local quat",
               "frames": frames}, open(out / "duck-walk.json", "w"), separators=(",", ":"))
    print("avatar: %d bones, %d triangles (from %d), %d colours; clip: %d frames at %.1f fps"
          % (len(bodies), sum(len(p["f"]) for p in primitives), total, len(materials), len(frames),
             rec["fps"] / math.sqrt(scale)))


def write_skinned_glb(path, primitives, materials, bodies, parent_of, local_r, local_p, world, name, scale):
    blobs, views, accessors = [], [], []

    def add(data, target=None):
        offset = sum(len(b) for b in blobs)
        blobs.append(data + b"\0" * (-len(data) % 4))
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        views.append(view)
        return len(views) - 1

    def accessor(array, component, kind, target=None, minmax=False):
        acc = {"bufferView": add(np.ascontiguousarray(array).tobytes(), target), "componentType": component,
               "count": int(len(array)), "type": kind}
        if minmax:
            acc["min"], acc["max"] = array.min(0).tolist(), array.max(0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    nodes = []
    for b in bodies:
        r = to_dr(local_r[b])
        t = M @ local_p[b] * scale
        node = {"name": name[b], "rotation": [float(x) for x in mat_to_quat(r)], "translation": [float(x) for x in t]}
        kids = [bodies.index(c) for c in bodies if parent_of[c] == b]
        if kids:
            node["children"] = kids
        nodes.append(node)
    mesh_node = len(nodes)
    nodes.append({"name": "duck_mesh", "mesh": 0, "skin": 0})
    inverse = []
    for b in bodies:
        wr, wp = world[b]
        g = np.eye(4)
        g[:3, :3] = to_dr(wr)
        g[:3, 3] = M @ wp * scale
        inverse.append(np.linalg.inv(g).T.astype(np.float32).ravel())     # column-major
    ibm = accessor(np.array(inverse, np.float32), 5126, "MAT4")
    prims = []
    for i, p in enumerate(primitives):
        joints = np.zeros((len(p["v"]), 4), np.uint16)
        joints[:, 0] = p["j"]
        weights = np.zeros((len(p["v"]), 4), np.float32)
        weights[:, 0] = 1
        prims.append({"attributes": {
            "POSITION": accessor(p["v"].astype(np.float32), 5126, "VEC3", 34962, True),
            "NORMAL": accessor(p["n"].astype(np.float32), 5126, "VEC3", 34962),
            "JOINTS_0": accessor(joints, 5123, "VEC4", 34962),
            "WEIGHTS_0": accessor(weights, 5126, "VEC4", 34962)},
            "indices": accessor(p["f"].astype(np.uint32).ravel(), 5125, "SCALAR", 34963), "material": i})
    gltf = {"asset": {"version": "2.0", "generator": "maddersplace tools/duck/build_duck.py",
                      "copyright": "Microduck meshes (c) Pollen Robotics, CC BY-NC-SA 4.0"},
            "scene": 0, "scenes": [{"nodes": [0, mesh_node]}], "nodes": nodes,
            "skins": [{"joints": list(range(len(bodies))), "inverseBindMatrices": ibm, "skeleton": 0}],
            "meshes": [{"primitives": prims}], "materials": materials, "accessors": accessors, "bufferViews": views,
            "buffers": [{"byteLength": sum(len(b) for b in blobs)}]}
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    body = b"".join(blobs)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(body)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(body), 0x004E4942) + body)


if __name__ == "__main__":
    main()
