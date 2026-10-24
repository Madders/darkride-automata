"""Builds the bots' models: one per bot in a roster (bots.json), on the Microduck's skeleton (tools/skeleton.json).

Each model is built from simple rounded parts: a torso shell, servo hubs with glowing rings at the joints, thighs,
shins and flat feet, a neck column and a head, with a choice per bot of head (loaf, dome or wedge), eyes (lens, visor
slit or twin lenses), antennas (0 to 2), back fins, a crest, a chest light, rocket pods, colours, weathering
(weathering.py: Star Wars droid style, from factory-fresh to beaten up, by the bot's wear), and a jaw that opens
while the bot makes a sound. Other bodies on the same skeleton: a bouncer (an armoured sentry), a mouse droid, a
power droid and an astromech (style "build"). See docs/designing-bots.md.

The parts are designed with the robot standing in its rest pose (the walk policy's default pose: from the skeleton
and bot/duck-cycles.json) and carried onto each bone's bind pose, so every bot shares the skeleton, and the recorded
walks and moves fit them all. The jaw is a bone added to the skeleton here (a child of head_roll, hinged at the back
of the head); every bot has it, and only those with the jaw style have a separate lower jaw on it. Each bot's .fst
lists the joints in the order a viewer builds them (docs/skeleton.md).

Writes, into the output folder: duck-<id>.glb and duck-<id>.fst for each bot, their weathered textures and
normal maps (bot-<id>-shell.png, bot-<id>-shell-n.png, and the same for dark, metal and trim, those its body uses), for a bot with a role its own persistent script (bot-<id>.js), fx-spark.png and a copy of the
roster as bots.json.

Usage: python build_bots.py [--roster examples/bots.json] [--out dist/bot] [bot id ...]
"""
import json
import pathlib
import struct
import sys

import numpy as np
import trimesh
from scipy.spatial.transform import Rotation as R

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import weathering  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parent
FLOOR = -0.48                   # the floor below the hips in the rest pose (the trunk's height)
JAW_HINGE = [0, 0.445, 0.09]    # where the jaw hinges, under the back of the head, in the rest pose
TILE = 0.5                      # metres of bot one texture covers (box-projected)
TEXTURED = ("shell", "dark", "metal", "trim")    # the materials with weathering and relief (weathering.py)


# ── the skeleton ───────────────────────────────────────────────────────────
def transform(q, t):
    m = np.eye(4)
    m[:3, :3] = R.from_quat(q).as_matrix()
    m[:3, 3] = t
    return m


class Skeleton:
    """The duck's bones: bind pose (as in duck.glb) and rest pose (the policy's default stance), in the mesh's frame."""

    def __init__(self, skeleton, cycles):
        self.data = skeleton
        skin = skeleton["skin"]
        self.names = [skeleton["nodes"][i]["name"] for i in skin["joints"]]
        self.ibm = np.array(skin["inverseBindMatrices"], np.float32).reshape(-1, 4, 4)     # column-major, as glTF
        ibm = self.ibm.transpose(0, 2, 1)
        self.bind = {n: np.linalg.inv(ibm[k]) for k, n in enumerate(self.names)}
        nodes = {nd["name"]: nd for nd in skeleton["nodes"]}
        self.rest = {"Hips": np.eye(4)}
        for n in cycles["joints"]:
            q = (R.from_quat(cycles["base"][n]) * R.from_rotvec([0, cycles["rest"][n], 0])).as_quat()
            self.rest[n] = self.rest[cycles["parents"][n]] @ transform(q, nodes[n]["translation"])

        # the jaw: hinged at the back of the head's underside, turning about the head's sideways axis (its local y,
        # like every other joint here, so the brain turns it the same way)
        self.jaw_parent = "head_roll"
        head = self.rest[self.jaw_parent]
        jaw_world = np.eye(4)
        jaw_world[:3, :3] = R.from_euler("z", -90, degrees=True).as_matrix()   # local y along the duck's x
        jaw_world[:3, 3] = JAW_HINGE
        self.jaw_local = np.linalg.inv(head) @ jaw_world
        self.rest["jaw"] = jaw_world
        self.bind["jaw"] = self.bind[self.jaw_parent] @ self.jaw_local
        self.names.append("jaw")

    def pos(self, bone):
        return self.rest[bone][:3, 3].copy()

    def axis(self, bone):
        return self.rest[bone][:3, :3] @ np.array([0, 1.0, 0])

    def to_bind(self, bone):
        """Rest-pose geometry on this bone, carried to where the bone is in the bind pose."""
        return self.bind[bone] @ np.linalg.inv(self.rest[bone])


# ── shapes (all in the rest pose's frame: y up, the duck facing -z, its left at -x) ────────────────────────────
def rounded_box(lo, hi, r, detail=2):
    """A box from lo to hi with its edges rounded to radius r (the hull of eight spheres)."""
    lo, hi = np.array(lo, float), np.array(hi, float)
    r = min(r, *(0.49 * (hi - lo)))
    ball = trimesh.creation.icosphere(subdivisions=detail, radius=r).vertices
    corners = [[x, y, z] for x in (lo[0] + r, hi[0] - r) for y in (lo[1] + r, hi[1] - r) for z in (lo[2] + r, hi[2] - r)]
    return trimesh.convex.convex_hull(np.vstack([ball + c for c in corners]))


def ellipsoid(centre, half, detail=3):
    m = trimesh.creation.icosphere(subdivisions=detail)
    m.apply_scale(half)
    m.apply_translation(centre)
    return m


def along(mesh, a, b):
    """A mesh built along +z from 0 to its length, turned and moved to run from a to b."""
    a, b = np.array(a, float), np.array(b, float)
    m = trimesh.geometry.align_vectors([0, 0, 1], (b - a) / np.linalg.norm(b - a))
    m[:3, 3] = a
    mesh.apply_transform(m)
    return mesh


def capsule(a, b, radius):
    length = np.linalg.norm(np.array(b) - np.array(a))
    m = trimesh.creation.capsule(height=length, radius=radius, count=[16, 12])
    m.apply_translation([0, 0, length / 2])
    return along(m, a, b)


def tapered(a, b, r0, r1, sections=20):
    """A smooth tapered rod from a (radius r0) to b (radius r1), with round ends."""
    a, b = np.array(a, float), np.array(b, float)
    pts = []
    for c, r in ((a, r0), (b, r1)):
        pts.append(trimesh.creation.icosphere(subdivisions=2, radius=r).vertices + c)
    return trimesh.convex.convex_hull(np.vstack(pts))


def disc(centre, axis, radius, thickness, sections=32):
    m = trimesh.creation.cylinder(radius=radius, height=thickness, sections=sections)
    axis = np.array(axis, float) / np.linalg.norm(axis)
    t = trimesh.geometry.align_vectors([0, 0, 1], axis)
    t[:3, 3] = centre
    m.apply_transform(t)
    return m


def ring(centre, axis, r_in, r_out, thickness, sections=32):
    m = trimesh.creation.annulus(r_min=r_in, r_max=r_out, height=thickness, sections=sections)
    axis = np.array(axis, float) / np.linalg.norm(axis)
    t = trimesh.geometry.align_vectors([0, 0, 1], axis)
    t[:3, 3] = centre
    m.apply_transform(t)
    return m


def top_at(mesh, x, z):
    """The height of a mesh's top surface at (x, z)."""
    hits, _, _ = mesh.ray.intersects_location([[x, 5.0, z]], [[0, -1.0, 0]])
    return float(hits[:, 1].max()) if len(hits) else float(mesh.bounds[1][1])


def band(shell, y0, y1, grow=1.03):
    """A glowing band hugging a shell between two heights: the shell itself, a touch larger, cut to that slice."""
    m = shell.copy()
    c = m.bounds.mean(0)
    v = m.vertices.copy()
    v[:, [0, 2]] = c[[0, 2]] + (v[:, [0, 2]] - c[[0, 2]]) * grow
    m.vertices = v
    m = trimesh.intersections.slice_mesh_plane(m, [0, 1.0, 0], [0, y0, 0])
    return trimesh.intersections.slice_mesh_plane(m, [0, -1.0, 0], [0, y1, 0])


def ridge(shell, x, z0, z1, width, height, steps=28):
    """A ridge running front to back along a shell's top, following its curve."""
    verts, faces = [], []
    for k, z in enumerate(np.linspace(z0, z1, steps)):
        y = top_at(shell, x, z)
        verts += [[x - width, y - 0.01, z], [x + width, y - 0.01, z], [x + width, y + height, z], [x - width, y + height, z]]
        if k:
            a, b = 4 * (k - 1), 4 * k
            for i in range(4):
                j = (i + 1) % 4
                faces += [[a + i, b + i, b + j], [a + i, b + j, a + j]]
    n = len(verts)
    faces += [[0, 2, 1], [0, 3, 2], [n - 4, n - 3, n - 2], [n - 4, n - 2, n - 1]]
    m = trimesh.Trimesh(verts, faces, process=True)
    m.fix_normals()
    return m


def box(lo, hi):
    lo, hi = np.array(lo, float), np.array(hi, float)
    m = trimesh.creation.box(extents=hi - lo)
    m.apply_translation((lo + hi) / 2)
    return m


# ── the bot ─────────────────────────────────────────────────────────────────
def design(sk, style):
    """[(mesh, material, bone, smooth)] for a bot of this style, in the rest pose."""
    if style.get("build") == "bouncer":
        return design_bouncer(sk, style)
    if style.get("build") == "mouse":
        return design_mouse(sk, style)
    if style.get("build") == "power":
        return design_power(sk, style)
    if style.get("build") == "astromech":
        return design_astromech(sk, style)
    parts = []

    def add(mesh, material, bone, smooth=True):
        parts.append((mesh, material, bone, smooth))

    # torso: a rounded shell, a dark belly plate under it, a chest light and, if wanted, fins on the back
    add(rounded_box([-0.118, -0.095, -0.135], [0.118, 0.165, 0.165], 0.06), "shell", "Hips")
    add(rounded_box([-0.095, -0.125, -0.105], [0.095, -0.06, 0.135], 0.025), "dark", "Hips")
    if style.get("chest", True):
        add(rounded_box([-0.06, 0.03, -0.142], [0.06, 0.055, -0.12], 0.008), "glow", "Hips")
        add(rounded_box([-0.06, -0.005, -0.142], [0.06, 0.01, -0.12], 0.006), "glow", "Hips")
    for x in ((-0.123, -0.112), (0.112, 0.123)):                    # side vents
        add(box([x[0], 0.0, -0.05], [x[1], 0.09, 0.08]), "dark", "Hips", False)
    if style.get("pennant"):
        # a tour guide's pennant: a pole up from its back, a little flag in its glowing colour streaming behind
        foot, top = np.array([0.075, 0.1, 0.16]), np.array([0.085, 1.02, 0.2])
        add(capsule(foot, top, 0.006), "dark", "Hips")
        add(ellipsoid(top, [0.014, 0.014, 0.014], 1), "glow", "Hips")
        y0, y1 = top[1] - 0.17, top[1] - 0.02
        flag = trimesh.Trimesh([[top[0] - 0.004, y0, top[2]], [top[0] - 0.004, y1, top[2]], [top[0] - 0.004, (y0 + y1) / 2, top[2] + 0.24],
                                [top[0] + 0.004, y0, top[2]], [top[0] + 0.004, y1, top[2]], [top[0] + 0.004, (y0 + y1) / 2, top[2] + 0.24]],
                               [[0, 1, 2], [3, 5, 4], [0, 3, 4], [0, 4, 1], [1, 4, 5], [1, 5, 2], [2, 5, 3], [2, 3, 0]])
        flag.fix_normals()
        add(flag, "glow", "Hips", False)
    if style.get("fins"):
        for x in (-0.045, 0.045):
            add(rounded_box([x - 0.008, 0.02, 0.15], [x + 0.008, 0.2, 0.23], 0.007), "dark", "Hips")
            add(box([x - 0.009, 0.15, 0.2], [x + 0.009, 0.19, 0.225]), "glow", "Hips", False)

    # legs: servo blocks at the hip, a hub with a glowing ring at the hip pitch and the knee, thigh and shin, a foot
    for side in ("left", "right"):
        hy, hr, hp, kn, an = (sk.pos("%s_%s" % (side, n)) for n in ("hip_yaw", "hip_roll", "hip_pitch", "knee", "ankle"))
        add(capsule(hy, hr, 0.032), "dark", side + "_hip_yaw")
        add(rounded_box(np.minimum(hr, hp) - 0.035, np.maximum(hr, hp) + 0.035, 0.02), "dark", side + "_hip_roll")
        for joint, bone, r in ((hp, side + "_hip_pitch", 0.052), (kn, side + "_knee", 0.045), (an, side + "_ankle", 0.04)):
            ax = sk.axis(bone)
            add(disc(joint, ax, r, 0.05), "dark", bone, False)
            add(ring(joint, ax, r * 0.62, r * 0.82, 0.056), "glow", bone, False)
            add(disc(joint, ax, r * 0.55, 0.054), "shell", bone, False)
        add(tapered(hp, kn, 0.042, 0.034), "shell", side + "_hip_pitch")
        add(tapered(kn, an, 0.03, 0.026), "shell", side + "_knee")
        toe, heel = -0.15, 0.1
        add(rounded_box([an[0] - 0.055, FLOOR, toe], [an[0] + 0.055, FLOOR + 0.05, heel], 0.022), "shell", side + "_ankle")
        add(rounded_box([an[0] - 0.05, FLOOR - 0.003, toe + 0.01], [an[0] + 0.05, FLOOR + 0.012, heel - 0.01], 0.006),
            "glow", side + "_ankle")
        add(tapered(an, [an[0], FLOOR + 0.045, -0.02], 0.028, 0.03), "dark", side + "_ankle")
        if style.get("rockets"):
            # rocket pods either side of the heel, nozzles down, glowing rings round their mouths
            for dx in (-0.068, 0.068):
                c = np.array([an[0] + dx, FLOOR + 0.07, heel - 0.04])
                add(capsule(c + [0, 0.05, 0], c, 0.022), "dark", side + "_ankle")
                add(disc(c + [0, -0.012, 0], [0, 1, 0], 0.026, 0.03), "dark", side + "_ankle", False)
                add(ring(c + [0, -0.03, 0], [0, 1, 0], 0.012, 0.022, 0.008), "glow", side + "_ankle", False)

    # neck: a column from the neck servo up to the head turret
    npc, hpc, hyc, hrc = sk.pos("neck_pitch"), sk.pos("head_pitch"), sk.pos("head_yaw"), sk.pos("head_roll")
    centre = lambda p: np.array([0, p[1], p[2]])
    add(disc(centre(npc), [1, 0, 0], 0.045, 0.16), "dark", "neck_pitch", False)
    add(ring(centre(npc), [1, 0, 0], 0.028, 0.038, 0.165), "glow", "neck_pitch", False)
    add(capsule(centre(npc), centre(hpc), 0.03), "shell", "neck_pitch")
    add(disc(centre(hpc), [1, 0, 0], 0.04, 0.12), "dark", "head_pitch", False)
    add(capsule(centre(hpc), centre(hyc), 0.028), "dark", "head_pitch")
    add(disc(hyc + [0, 0.02, 0], [0, 1, 0], 0.055, 0.04), "dark", "head_yaw", False)
    add(ring(hyc + [0, 0.02, 0], [0, 1, 0], 0.056, 0.064, 0.012), "glow", "head_yaw", False)

    # head: its shell, the eyes on the front, a glowing band round its base, antennas
    head, eyes = style.get("head", "loaf"), style.get("eyes", "lens")
    lo, hi = np.array([-0.16, 0.42, -0.31]), np.array([0.16, 0.6, 0.15])
    if style.get("jaw"):
        # the lower jaw: a rounded plate under the front of the head, on its own bone, and a glowing mouth above it
        # that shows when it opens
        add(rounded_box([-0.13, 0.4, -0.3], [0.13, 0.455, 0.07], 0.03), "shell", "jaw")
        add(rounded_box([-0.11, 0.44, -0.27], [0.11, 0.46, 0.04], 0.01), "glow", "jaw")
        add(rounded_box([-0.12, 0.455, -0.28], [0.12, 0.47, 0.06], 0.008), "glow", "head_roll")
        lo = lo + [0, 0.045, 0]                                         # the head's shell sits over the jaw
    if head == "dome":
        shell = ellipsoid((lo + hi) / 2 + [0, 0.01, 0], (hi - lo) / 2 * [1.0, 1.15, 1.0])
    elif head == "wedge":
        shell = rounded_box(lo, hi, 0.025)
        v = shell.vertices.copy()                                       # slope the front down, like a visor
        front = (v[:, 2] - lo[2]) / (hi[2] - lo[2])
        v[:, 1] = np.where(v[:, 1] > 0.5, v[:, 1] - (1 - front) * 0.06, v[:, 1])
        shell.vertices = v
    else:
        shell = rounded_box(lo, hi, 0.085)
    add(shell, "shell", "head_roll")
    # the glowing band hugs the head's own shape, a little way up from its underside
    y0 = shell.bounds[0][1] + 0.18 * (shell.bounds[1][1] - shell.bounds[0][1])
    add(band(shell, y0, y0 + 0.028), "glow", "head_roll")
    front_z, eye_y = lo[2] - 0.002, 0.51
    if eyes == "visor":
        add(rounded_box([-0.12, eye_y - 0.025, front_z - 0.01], [0.12, eye_y + 0.025, front_z + 0.03], 0.012), "lens",
            "head_roll")
        add(rounded_box([-0.1, eye_y - 0.008, front_z - 0.016], [0.1, eye_y + 0.008, front_z], 0.006), "glow", "head_roll")
    else:
        spots = [(-0.06, 0.045), (0.06, 0.045)] if eyes == "twin" else [(0, 0.065)]
        for x, r in spots:
            c = np.array([x, eye_y, front_z])
            add(disc(c, [0, 0, 1], r * 1.25, 0.04), "dark", "head_roll", False)
            add(disc(c + [0, 0, -0.012], [0, 0, 1], r, 0.03), "lens", "head_roll", False)
            add(ring(c + [0, 0, -0.02], [0, 0, 1], r * 0.45, r * 0.6, 0.024), "glow", "head_roll", False)
    for k in range(style.get("antennas", 1)):
        x = 0.07 if style.get("antennas", 1) == 1 else (-0.08 if k == 0 else 0.08)
        base = np.array([x, top_at(shell, x, 0.06) - 0.008, 0.06])
        tip = base + [0, 0.13 + 0.03 * k, 0.04]
        add(disc(base, [0, 1, 0], 0.022, 0.03), "dark", "head_roll", False)
        add(capsule(base, tip, 0.007), "dark", "head_roll")
        add(ellipsoid(tip, [0.016, 0.016, 0.016], 2), "glow", "head_roll")
    if style.get("crest"):
        zmin, zmax = shell.bounds[0][2], shell.bounds[1][2]
        add(ridge(shell, 0, zmin + 0.09, zmax - 0.05, 0.012, 0.022), "glow", "head_roll")
    return parts


HULL_DROP = -0.22               # the bouncer's hull sits this much lower than a duck's head


def rivets(add, bone, points, r=0.008):
    for p in points:
        add(ellipsoid(p, [r, r, r], 1), "dark", bone)


def design_bouncer(sk, style):
    """A big armoured sentry on the same skeleton, ED-209 crossed with an ABC Warrior: no lights, all plate, rivets,
    hydraulics and guns. Its main hull rides on head_roll, the way ED-209's body carries its guns, so it can swivel
    to watch people; the pelvis is on Hips; the legs are the duck's bird legs, armoured; big clawed feet."""
    parts = []

    def add(mesh, material, bone, smooth=True):
        parts.append((mesh, material, bone, smooth))

    # pelvis: a heavy armoured block, skirt plates front and back, hip drums, exhaust stacks on its back
    add(rounded_box([-0.15, -0.13, -0.15], [0.15, 0.1, 0.15], 0.03), "shell", "Hips")
    add(rounded_box([-0.13, -0.17, -0.165], [0.13, -0.04, -0.14], 0.01), "shell", "Hips")          # front skirt
    add(rounded_box([-0.13, -0.17, 0.14], [0.13, -0.04, 0.165], 0.01), "shell", "Hips")            # back skirt
    add(rounded_box([-0.11, 0.1, -0.12], [0.11, 0.13, 0.12], 0.015), "dark", "Hips")                # top plate
    rivets(add, "Hips", [[x, y, -0.168] for x in (-0.11, -0.04, 0.04, 0.11) for y in (-0.15, -0.06)])
    rivets(add, "Hips", [[x, -0.02, z] for x in (-0.152, 0.152) for z in (-0.11, 0.0, 0.11)])
    for x in (-0.07, 0.07):
        add(capsule([x, 0.0, 0.16], [x, 0.09, 0.19], 0.022), "dark", "Hips")                      # exhaust stubs
        add(disc([x, 0.1, 0.193], [0, 0.3, 1], 0.03, 0.02), "dark", "Hips", False)

    # legs: actuators at the hip, armoured thighs and shins with hydraulic rams beside them, knee caps, clawed feet
    for side, sx in (("left", -1), ("right", 1)):
        hy, hr, hp, kn, an = (sk.pos("%s_%s" % (side, n)) for n in ("hip_yaw", "hip_roll", "hip_pitch", "knee", "ankle"))
        add(capsule(hy, hr, 0.05), "dark", side + "_hip_yaw")
        add(rounded_box(np.minimum(hr, hp) - 0.055, np.maximum(hr, hp) + 0.055, 0.02), "dark", side + "_hip_roll")
        for joint, bone, r in ((hp, side + "_hip_pitch", 0.085), (kn, side + "_knee", 0.07), (an, side + "_ankle", 0.06)):
            ax = sk.axis(bone)
            add(disc(joint, ax, r, 0.09), "dark", bone, False)
            add(disc(joint, ax, r * 0.6, 0.1), "shell", bone, False)
            add(disc(joint, ax, r * 0.25, 0.105), "dark", bone, False)
        # thigh: a thick armour shell, a plate on the outside, a ram behind
        add(tapered(hp, kn, 0.07, 0.058), "shell", side + "_hip_pitch")
        plate_out = np.array([sx * 0.05, 0, 0])
        add(tapered(hp + plate_out + [0, -0.01, 0], kn + plate_out + [0, 0.03, 0], 0.035, 0.03), "shell", side + "_hip_pitch")
        add(capsule(hp + [0, -0.02, 0.07], kn + [0, 0.03, 0.06], 0.016), "dark", side + "_hip_pitch")
        add(ellipsoid(kn + [0, 0, -0.01], [0.075, 0.07, 0.07]), "shell", side + "_knee")              # knee cap
        # shin
        add(tapered(kn, an, 0.055, 0.045), "shell", side + "_knee")
        add(capsule(kn + [0, -0.03, -0.065], an + [0, 0.04, -0.06], 0.014), "dark", side + "_knee")
        add(capsule(kn + [sx * 0.045, -0.04, -0.04], an + [sx * 0.04, 0.05, -0.04], 0.01), "dark", side + "_knee")
        # foot: a wide armoured slab, three claws forward, a spur behind, an armoured ankle
        add(rounded_box([an[0] - 0.1, FLOOR, -0.17], [an[0] + 0.1, FLOOR + 0.075, 0.13], 0.025), "shell", side + "_ankle")
        add(rounded_box([an[0] - 0.09, FLOOR + 0.07, -0.12], [an[0] + 0.09, FLOOR + 0.1, 0.09], 0.02), "dark", side + "_ankle")
        for dx in (-0.065, 0, 0.065):
            add(tapered([an[0] + dx, FLOOR + 0.035, -0.17], [an[0] + dx, FLOOR + 0.015, -0.27], 0.03, 0.012), "dark",
                side + "_ankle")
        add(tapered([an[0], FLOOR + 0.04, 0.13], [an[0], FLOOR + 0.02, 0.21], 0.028, 0.01), "dark", side + "_ankle")
        add(tapered(an, [an[0], FLOOR + 0.09, -0.02], 0.045, 0.06), "dark", side + "_ankle")
        rivets(add, side + "_ankle", [[an[0] + x, FLOOR + 0.077, z] for x in (-0.075, 0.075) for z in (-0.13, 0.09)])

    # the spine: an armoured column with rams either side, up to a heavy turret ring
    npc, hpc, hyc = sk.pos("neck_pitch"), sk.pos("head_pitch"), sk.pos("head_yaw")
    centre = lambda p: np.array([0, p[1], p[2]])
    add(disc(centre(npc), [1, 0, 0], 0.07, 0.2), "dark", "neck_pitch", False)
    add(disc(centre(npc), [1, 0, 0], 0.045, 0.21), "shell", "neck_pitch", False)
    add(tapered(centre(npc), centre(hpc), 0.07, 0.06), "shell", "neck_pitch")
    for k in np.linspace(0.25, 0.75, 3):                                 # armour collars
        add(disc(centre(npc) + (centre(hpc) - centre(npc)) * k, centre(hpc) - centre(npc), 0.078, 0.025), "dark",
            "neck_pitch", False)
    for x in (-0.085, 0.085):
        add(capsule(centre(npc) + [x, 0.02, 0.03], centre(hpc) + [x, -0.03, 0.02], 0.014), "dark", "neck_pitch")
    add(disc(centre(hpc), [1, 0, 0], 0.06, 0.16), "dark", "head_pitch", False)
    add(capsule(centre(hpc), centre(hyc), 0.06), "dark", "head_pitch")
    add(disc(hyc + [0, 0.02, 0], [0, 1, 0], 0.14, 0.05), "dark", "head_yaw", False)
    rivets(add, "head_yaw", [hyc + [0.12 * np.cos(a), 0.047, 0.12 * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 10, endpoint=False)])

    # the hull: wide and angular, its chin and brow sloping back, a dark visor slit, armour plates and rivets
    lo, hi = np.array([-0.27, 0.43, -0.36]), np.array([0.27, 0.68, 0.22])
    hull = rounded_box(lo, hi, 0.03)
    v = hull.vertices.copy()
    front = np.clip((v[:, 2] - lo[2]) / 0.22, 0, 1)                     # 0 at the very front, 1 from 22 cm back
    mid = 0.5 * (lo[1] + hi[1])
    v[:, 1] = np.where(v[:, 1] < mid, v[:, 1] + (1 - front) * 0.07, v[:, 1] - (1 - front) * 0.09)   # chin and brow
    v[:, 0] *= np.where(v[:, 2] < lo[2] + 0.1, 0.86, 1.0)               # a narrower nose
    hull.vertices = v
    add(hull, "shell", "head_roll")
    add(rounded_box([-0.2, 0.555, lo[2] + 0.005], [0.2, 0.585, lo[2] + 0.06], 0.008), "lens", "head_roll")
    add(rounded_box([-0.22, 0.64, -0.2], [0.22, 0.685, 0.16], 0.015), "shell", "head_roll")        # top armour
    add(rounded_box([-0.18, 0.68, -0.12], [0.18, 0.7, 0.1], 0.008), "dark", "head_roll")
    rivets(add, "head_roll", [[x, 0.688, z] for x in (-0.2, -0.07, 0.07, 0.2) for z in (-0.18, 0.14)])
    rivets(add, "head_roll", [[x, y, hi[2] + 0.002] for x in (-0.22, 0, 0.22) for y in (0.47, 0.62)])
    # cooling fins and a power pack on its back
    add(rounded_box([-0.18, 0.46, hi[2] - 0.01], [0.18, 0.64, hi[2] + 0.06], 0.012), "dark", "head_roll")
    for x in np.linspace(-0.15, 0.15, 7):
        add(box([x - 0.006, 0.47, hi[2] + 0.06], [x + 0.006, 0.63, hi[2] + 0.1]), "dark", "head_roll", False)
    # a sensor mast with a small dish
    add(capsule([0.17, hi[1], 0.12], [0.19, hi[1] + 0.16, 0.14], 0.012), "dark", "head_roll")
    add(disc([0.19, hi[1] + 0.15, 0.115], [0.2, 0.3, -1], 0.04, 0.01), "shell", "head_roll", False)

    # left arm: a twin cannon on a heavy mount; right arm: a six-tube rocket pod
    add(rounded_box([-0.39, 0.45, -0.16], [-0.26, 0.63, 0.14], 0.025), "shell", "head_roll")       # left mount
    add(disc([-0.27, 0.54, -0.01], [1, 0, 0], 0.08, 0.05), "dark", "head_roll", False)             # shoulder bearing
    for y in (0.5, 0.58):
        b0, b1 = np.array([-0.325, y, -0.16]), np.array([-0.325, y, -0.58])
        add(capsule(b0, b1, 0.024), "dark", "head_roll")
        add(disc(b1 + [0, 0, 0.03], [0, 0, 1], 0.034, 0.05), "dark", "head_roll", False)           # muzzle brake
        add(disc(b1 + [0, 0, -0.002], [0, 0, 1], 0.014, 0.06), "lens", "head_roll", False)          # the bore
    add(rounded_box([-0.37, 0.47, -0.32], [-0.28, 0.61, -0.15], 0.015), "shell", "head_roll")     # barrel shroud
    add(rounded_box([0.26, 0.46, -0.24], [0.42, 0.64, 0.12], 0.025), "shell", "head_roll")         # rocket pod
    add(disc([0.27, 0.55, -0.01], [1, 0, 0], 0.08, 0.05), "dark", "head_roll", False)
    for x in (0.3, 0.38):
        for y in (0.49, 0.55, 0.61):
            add(disc([x, y, -0.244], [0, 0, 1], 0.022, 0.012), "lens", "head_roll", False)
    rivets(add, "head_roll", [[x, 0.643, z] for x in (0.28, 0.4) for z in (-0.2, 0.08)])
    rivets(add, "head_roll", [[x, 0.633, z] for x in (-0.37, -0.28) for z in (-0.12, 0.1)])
    # the hull and guns hang low, squat over the pelvis like ED-209's body, with the spine up inside them (it still
    # swivels on the head's joints to watch people)
    for mesh, material, bone, smooth in parts:
        if bone == "head_roll":
            mesh.apply_translation([0, HULL_DROP, 0])
    return parts


MOUSE_TOP = FLOOR + 0.25        # the mouse droid's height


def design_mouse(sk, style):
    """A mouse droid (an MSE-6 sort): a low boxy body skimming the floor, with the legs hidden in its skirt (they
    still walk, under there, which makes it jiggle along). Everything rides on Hips: no head to turn. A wedge nose
    with a bumper and a sensor eye, wheel housings, hinged top panels, a grille, stub antennas and a tail light."""
    parts = []

    def add(mesh, material, smooth=True):
        parts.append((mesh, material, "Hips", smooth))

    lo, hi = np.array([-0.17, FLOOR + 0.035, -0.29]), np.array([0.17, MOUSE_TOP, 0.25])
    body = rounded_box(lo, hi, 0.03)
    v = body.vertices.copy()
    nose = np.clip((v[:, 2] - lo[2]) / 0.16, 0, 1)                      # slope the top down to the nose
    v[:, 1] = np.where(v[:, 1] > FLOOR + 0.12, v[:, 1] - (1 - nose) * 0.08, v[:, 1])
    body.vertices = v
    add(body, "shell")
    add(rounded_box([-0.175, FLOOR + 0.015, -0.27], [0.175, FLOOR + 0.07, 0.235], 0.02), "dark")      # skirt
    add(rounded_box([-0.15, FLOOR + 0.03, -0.31], [0.15, FLOOR + 0.08, -0.27], 0.015), "dark")        # bumper
    add(disc([0, FLOOR + 0.13, -0.285], [0, 0.35, -1], 0.03, 0.03), "dark", False)                   # the eye
    add(disc([0, FLOOR + 0.131, -0.297], [0, 0.35, -1], 0.018, 0.012), "lens", False)
    add(ring([0, FLOOR + 0.132, -0.302], [0, 0.35, -1], 0.008, 0.013, 0.008), "glow", False)
    for x in (-0.18, 0.18):                                              # wheel housings, hubs showing
        for z in (-0.17, 0.14):
            add(rounded_box([x - 0.012, FLOOR + 0.02, z - 0.06], [x + 0.012, FLOOR + 0.1, z + 0.06], 0.01), "dark")
            add(disc([x * 1.08, FLOOR + 0.055, z], [1, 0, 0], 0.03, 0.012), "shell", False)
    for z0, z1 in ((-0.12, 0.04), (0.06, 0.22)):                        # hinged top panels with handles
        add(rounded_box([-0.14, MOUSE_TOP - 0.004, z0], [0.14, MOUSE_TOP + 0.014, z1], 0.006), "shell")
        add(capsule([-0.04, MOUSE_TOP + 0.022, (z0 + z1) / 2], [0.04, MOUSE_TOP + 0.022, (z0 + z1) / 2], 0.006), "dark")
    for x in np.linspace(-0.12, 0.12, 9):                               # a cooling grille on the back
        add(box([x - 0.006, FLOOR + 0.09, 0.25], [x + 0.006, MOUSE_TOP - 0.03, 0.262]), "dark", False)
    add(box([-0.06, MOUSE_TOP - 0.025, 0.252], [0.06, MOUSE_TOP - 0.012, 0.264]), "glow", False)     # tail light
    for x in (-0.1, 0.1):                                               # stub antennas
        base = np.array([x, MOUSE_TOP + 0.01, 0.18])
        add(capsule(base, base + [0, 0.07, 0.015], 0.005), "dark")
        add(ellipsoid(base + [0, 0.075, 0.016], [0.009, 0.009, 0.009], 1), "glow")
    if style.get("antennas", 0) >= 1:                                   # a whip aerial
        add(capsule([0.12, MOUSE_TOP, 0.2], [0.13, MOUSE_TOP + 0.28, 0.24], 0.003), "dark")
    rivets_on = [[x, FLOOR + 0.2, z] for x in (-0.172, 0.172) for z in (-0.05, 0.05, 0.15)]
    for p in rivets_on:
        add(ellipsoid(p, [0.007, 0.007, 0.007], 1), "dark")
    return parts


def cylinder(centre, axis, radius, height, sections=40):
    return disc(centre, axis, radius, height, sections)


def design_power(sk, style):
    """A power droid (the EG-6 "gonk" sort, like DN-LD): a tall box of a battery on two stubby legs. No lights to
    speak of: a small status lamp, vents, a control panel, side drums, a top hatch with handles, a power coupling. The
    box rides on Hips (it waddles as it walks); the legs below it are the duck's, made short and thick."""
    parts = []

    def add(mesh, material, bone="Hips", smooth=True):
        parts.append((mesh, material, bone, smooth))

    lo, hi = np.array([-0.2, -0.21, -0.18]), np.array([0.2, 0.5, 0.18])
    add(rounded_box(lo, hi, 0.05), "shell")
    add(rounded_box([-0.18, hi[1] - 0.02, -0.15], [0.18, hi[1] + 0.05, 0.15], 0.04), "shell")       # the rounded top
    add(rounded_box([-0.12, hi[1] + 0.04, -0.08], [0.12, hi[1] + 0.065, 0.08], 0.01), "dark")        # top hatch
    for z in (-0.04, 0.04):
        add(capsule([-0.05, hi[1] + 0.08, z], [0.05, hi[1] + 0.08, z], 0.008), "dark")              # its handles
    add(rounded_box([-0.205, -0.19, -0.16], [0.205, -0.14, 0.16], 0.015), "dark")                   # a skirt band
    add(rounded_box([-0.205, 0.38, -0.16], [0.205, 0.41, 0.16], 0.01), "dark")                      # a band near the top
    # the front: a control panel with knobs and a little status lamp, a grille below
    add(rounded_box([-0.12, 0.12, lo[2] - 0.015], [0.12, 0.3, lo[2] + 0.01], 0.012), "dark")
    for x in (-0.07, 0.0, 0.07):
        add(disc([x, 0.24, lo[2] - 0.02], [0, 0, 1], 0.018, 0.02), "metal", smooth=False)
    add(disc([0.08, 0.16, lo[2] - 0.02], [0, 0, 1], 0.012, 0.015), "glow", smooth=False)
    for y in np.linspace(-0.08, 0.06, 6):
        add(box([-0.11, y - 0.008, lo[2] - 0.012], [0.11, y + 0.008, lo[2] + 0.005]), "dark", smooth=False)
    # the sides: power drums and a coupling socket
    for sx in (-1, 1):
        add(cylinder([sx * 0.205, 0.17, 0.0], [1, 0, 0], 0.09, 0.03), "dark", smooth=False)
        add(cylinder([sx * 0.22, 0.17, 0.0], [1, 0, 0], 0.06, 0.02), "metal", smooth=False)
        add(cylinder([sx * 0.232, 0.17, 0.0], [1, 0, 0], 0.025, 0.02), "dark", smooth=False)
    add(cylinder([0.0, 0.3, hi[2] + 0.01], [0, 0, 1], 0.05, 0.03), "dark", smooth=False)         # coupling, back
    add(rounded_box([-0.14, -0.1, hi[2] - 0.005], [0.14, 0.2, hi[2] + 0.02], 0.01), "dark")        # back plate
    rivets(add, "Hips", [[x, y, lo[2] - 0.004] for x in (-0.17, 0.17) for y in (-0.12, 0.45)])
    # legs: short thick shins and big flat feet below the box; the thighs are inside it
    for side in ("left", "right"):
        kn, an = sk.pos(side + "_knee"), sk.pos(side + "_ankle")
        ax = sk.axis(side + "_knee")
        add(cylinder(kn, ax, 0.05, 0.06), "dark", side + "_knee", False)
        add(tapered(kn, an, 0.045, 0.04), "shell", side + "_knee")
        add(cylinder(an, sk.axis(side + "_ankle"), 0.045, 0.06), "dark", side + "_ankle", False)
        add(rounded_box([an[0] - 0.065, FLOOR, -0.16], [an[0] + 0.065, FLOOR + 0.06, 0.1], 0.025), "shell", side + "_ankle")
        add(rounded_box([an[0] - 0.06, FLOOR - 0.003, -0.15], [an[0] + 0.06, FLOOR + 0.015, 0.09], 0.006), "dark", side + "_ankle")
    return parts


ASTRO_AXIS = [0, -0.036]        # the astromech's body and dome share the head's swivel axis (x, z)


def design_astromech(sk, style):
    """An astromech (the R-series sort): a barrel of a body on two side legs with a third in the middle, a domed head
    that swivels (on head_yaw) to look about. No neon: a radar eye, a holo-projector, two small logic panels, and
    its second paint on the dome and body panels. As R2 does when it drives, it stands as a tripod: the side legs
    angle back, the body tilts back on its shoulders and its middle leg's wheel comes forward under it. So the body
    (with its middle leg) rides on neck_pitch, whose joint is where the shoulders are; the side legs ride on Hips (the
    brain turns them about their ankle hinges); the feet on the ankle bones (kept level); the dome on head_yaw. The
    duck's own legs are hidden inside (the brain glides it along)."""
    parts = []

    def add(mesh, material, bone="Hips", smooth=True):
        parts.append((mesh, material, bone, smooth))

    def body(mesh, material, smooth=True):
        add(mesh, material, "neck_pitch", smooth)

    cx, cz = ASTRO_AXIS
    top = sk.pos("head_yaw")[1] - 0.012                                  # the body's top, just under the dome
    r, y0 = 0.2, FLOOR + 0.2
    pivot = sk.pos("neck_pitch")                                         # the shoulders: the body tilts about here
    sy, lz = pivot[1], pivot[2]
    body(cylinder([cx, (top + y0) / 2, cz], [0, 1, 0], r, top - y0, 48), "shell")
    body(cylinder([cx, y0 - 0.03, cz], [0, 1, 0], r * 0.82, 0.06), "dark", False)                 # the lower skirt
    body(cylinder([cx, top - 0.005, cz], [0, 1, 0], r + 0.004, 0.012), "metal", False)            # the dome ring
    # panels on the body, in its second paint: two tall ones in front, a band, side panels
    fz = cz - r - 0.004
    for x0, x1 in ((-0.13, -0.05), (0.05, 0.13)):
        body(box([x0, y0 + 0.12, fz - 0.006], [x1, top - 0.08, fz + 0.03]), "trim", False)
    body(box([-0.04, top - 0.12, fz - 0.006], [0.04, top - 0.07, fz + 0.03]), "dark", False)       # a vent
    for y in (y0 + 0.05, y0 + 0.08):                                     # coin-slot vents low down
        body(box([-0.035, y - 0.006, fz - 0.008], [0.035, y + 0.006, fz + 0.03]), "dark", False)
    body(box([-0.17, top - 0.05, cz - 0.1], [0.17, top - 0.035, cz + 0.1]), "trim", False)
    for sx in (-1, 1):
        body(box([sx * (r - 0.02) - 0.02, y0 + 0.05, cz - 0.06], [sx * (r - 0.02) + 0.02, sy - 0.08, cz + 0.06]), "trim", False)
        body(cylinder([sx * (r + 0.005), sy, lz], [1, 0, 0], 0.075, 0.03), "dark", False)        # the shoulder socket
    # the middle leg: down from the body's underside to a caster wheel; it tilts with the body, so as the body leans
    # back while driving, the wheel comes forward under it (a wheel looks right at any angle)
    wheel_r, wy = 0.06, FLOOR + 0.06
    body(rounded_box([cx - 0.03, wy + 0.02, cz - 0.04], [cx + 0.03, y0, cz + 0.03], 0.012), "metal")
    for side in (-1, 1):                                                 # the caster's fork
        body(rounded_box([cx + side * 0.04 - 0.008, wy - 0.01, cz - 0.05], [cx + side * 0.04 + 0.008, wy + 0.05, cz + 0.04], 0.006), "dark")
    body(cylinder([cx, wy, cz], [1, 0, 0], wheel_r, 0.06), "dark", False)
    body(cylinder([cx, wy, cz], [1, 0, 0], wheel_r * 0.45, 0.065), "metal", False)
    # the side legs: from the shoulder (the pivot) down to an ankle hinge on the skeleton's own ankle joint, so they're
    # raked back a little as built (the brain turns them upright when standing, and further back when driving); the
    # feet ride on the ankle bones, which the brain keeps level, so they stay flat on the floor
    def strut(a, b, half_x, half_z):
        ends = [rounded_box(np.array(p) - [half_x, 0.02, half_z], np.array(p) + [half_x, 0.02, half_z], 0.012).vertices
                for p in (a, b)]
        return trimesh.convex.convex_hull(np.vstack(ends))

    for sx in (-1, 1):
        side = "left" if sx < 0 else "right"
        x = sx * (r + 0.045)
        hinge = np.array([x, sk.pos(side + "_ankle")[1], sk.pos(side + "_ankle")[2]])
        shoulder = np.array([x, sy, lz])
        add(cylinder([sx * (r + 0.025), sy, lz], [1, 0, 0], 0.065, 0.04), "metal", smooth=False)
        add(cylinder([sx * (r + 0.048), sy, lz], [1, 0, 0], 0.04, 0.012), "trim", smooth=False)
        add(cylinder([sx * (r + 0.056), sy, lz], [1, 0, 0], 0.014, 0.012), "dark", smooth=False)
        add(strut(shoulder + [0, 0.05, 0], hinge + [0, 0.02, 0], 0.03, 0.045), "shell")
        add(strut(shoulder - [0, 0.07, 0], hinge + [0, 0.1, 0], 0.033, 0.02), "trim", smooth=False)
        ankle = side + "_ankle"
        add(cylinder(hinge, [1, 0, 0], 0.035, 0.11), "metal", ankle, False)                       # the ankle hinge
        add(rounded_box([x - 0.05, FLOOR, hinge[2] - 0.11], [x + 0.05, FLOOR + 0.11, hinge[2] + 0.11], 0.025), "shell", ankle)
        add(rounded_box([x - 0.045, FLOOR - 0.003, hinge[2] - 0.1], [x + 0.045, FLOOR + 0.02, hinge[2] + 0.1], 0.008), "dark", ankle)
        if style.get("rockets"):                                         # booster pods on the outside of each leg
            c = shoulder + 0.55 * (hinge - shoulder) + [sx * 0.045, -0.05, 0.02]
            add(capsule(c + [0, 0.1, 0], c, 0.026), "metal")
            add(disc(c + [0, -0.02, 0], [0, 1, 0], 0.03, 0.03), "dark", smooth=False)
    # the dome: silver, panels in its second paint, a radar eye, a holo-projector, two little logic panels
    hy = sk.pos("head_yaw")
    centre = np.array([cx, hy[1], cz])
    ball = trimesh.creation.icosphere(subdivisions=4, radius=r).vertices
    rim = [[r * np.cos(a), 0, r * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 64, endpoint=False)]
    dome = trimesh.convex.convex_hull(np.vstack([ball[ball[:, 1] > 0], rim]))   # the top half of a ball
    dome.apply_scale([1, 0.92, 1])
    dome.apply_translation(centre)
    add(dome, "metal", "head_yaw")
    for k, a in enumerate(np.linspace(0, 2 * np.pi, 8, endpoint=False)):  # a ring of panels round its lower half
        if k == 0:
            continue                                                     # (the eye goes there)
        d = np.array([np.sin(a), 0, -np.cos(a)])
        p = centre + d * (r * 0.97) + [0, 0.05, 0]
        panel = box([-0.035, -0.035, -0.006], [0.035, 0.035, 0.006])
        panel.apply_transform(trimesh.geometry.align_vectors([0, 0, -1], d))
        panel.apply_translation(p)
        add(panel, "trim", "head_yaw", False)
    add(cylinder(centre + [0, 0.16, 0], [0, 1, 0], 0.07, 0.03), "trim", "head_yaw", False)         # the top panel
    eye = centre + np.array([0, 0.09, -r * 0.88])
    add(cylinder(eye, [0, 0.4, -1], 0.04, 0.03), "dark", "head_yaw", False)
    add(cylinder(eye + [0, 0.005, -0.012], [0, 0.4, -1], 0.025, 0.02), "lens", "head_yaw", False)
    add(cylinder(centre + [0.1, 0.13, -0.12], [0.5, 0.6, -1], 0.022, 0.035), "metal", "head_yaw", False)   # holo-projector
    for x, w in ((-0.09, 0.05), (0.05, 0.035)):                         # logic panels, a dim glow
        p = centre + np.array([x, 0.035, -r * 0.985])
        add(box(p + [-w / 2, -0.018, -0.005], p + [w / 2, 0.018, 0.004]), "glow", "head_yaw", False)
    return parts


def materials(bot):
    """The bot's materials; shell and dark take their weathered textures (their image files, by name, in write())."""
    accent = bot["accent"]
    glow, wear = bot.get("glow", 0.8), bot.get("wear", 0.3)
    return {
        "shell": {"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": bot.get("metal", 0.4) * (1 - 0.5 * wear),
                                           "roughnessFactor": 0.3 + 0.4 * wear}},
        "dark": {"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.85,
                                          "roughnessFactor": 0.4 + 0.3 * wear}},
        "glow": {"pbrMetallicRoughness": {"baseColorFactor": list(accent) + [1], "metallicFactor": 0,
                                          "roughnessFactor": 0.3},
                 "emissiveFactor": [c * glow for c in accent]},
        "metal": {"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.9,
                                           "roughnessFactor": 0.3 + 0.35 * wear}},
        "trim": {"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.2,
                                          "roughnessFactor": 0.45 + 0.3 * wear}},
        "lens": {"pbrMetallicRoughness": {"baseColorFactor": [0.02, 0.02, 0.03, 1], "metallicFactor": 0.2,
                                          "roughnessFactor": 0.05},
                 "emissiveFactor": [c * glow * 0.15 for c in accent]},
    }


# ── writing the skinned GLB ─────────────────────────────────────────────────
def skeleton_nodes(sk):
    """The skeleton's nodes as glTF nodes, with the jaw added (as the last node, a child of its parent)."""
    nodes = []
    for nd in sk.data["nodes"]:
        node = {k: v for k, v in nd.items() if k != "mesh"}
        if nd.get("mesh"):
            node.update(mesh=0, skin=0)
        nodes.append(node)
    q = R.from_matrix(sk.jaw_local[:3, :3]).as_quat()
    nodes.append({"name": "jaw", "rotation": [float(x) for x in q], "translation": [float(x) for x in sk.jaw_local[:3, 3]]})
    parent = next(nd for nd in nodes if nd.get("name") == sk.jaw_parent)
    parent["children"] = parent.get("children", []) + [len(nodes) - 1]
    return nodes


def viewer_joints(sk, nodes):
    """The joints in the order a viewer's glTF loader makes them: ROOT, then the nodes depth first from each scene
    root (so the jaw comes straight after head_roll). An avatar's .fst must list them in exactly this order."""
    order = ["ROOT"]

    def visit(i):
        order.append(nodes[i]["name"])
        for c in nodes[i].get("children", []):
            visit(c)
    for root in sk.data["roots"]:
        visit(root)
    return order


def box_uv(mesh, offset):
    """Texture coordinates projected from the side each face looks to (box mapping), reading left to right as seen
    from outside, with v running down the world (so streaks run down)."""
    v, n = mesh.vertices, mesh.vertex_normals
    k = np.abs(n).argmax(1)
    u = np.where(k == 0, -np.sign(n[:, 0]) * v[:, 2], np.where(k == 2, np.sign(n[:, 2]) * v[:, 0], v[:, 0]))
    w = np.where(k == 1, v[:, 2], -v[:, 1])
    return np.c_[u, w] / TILE + offset


def tangents(pos, nrm, uv, faces):
    """Per-vertex tangents (xyz along u, w the handedness) from each triangle's positions and UVs, for normal maps."""
    t = np.zeros_like(pos, dtype=np.float64)
    b = np.zeros_like(pos, dtype=np.float64)
    p0, p1, p2 = pos[faces[:, 0]], pos[faces[:, 1]], pos[faces[:, 2]]
    w0, w1, w2 = uv[faces[:, 0]], uv[faces[:, 1]], uv[faces[:, 2]]
    e1, e2, d1, d2 = p1 - p0, p2 - p0, w1 - w0, w2 - w0
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1e-12, r)
    sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
    tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / r[:, None]
    for k in range(3):
        np.add.at(t, faces[:, k], sdir)
        np.add.at(b, faces[:, k], tdir)
    t = t - nrm * np.sum(nrm * t, axis=1, keepdims=True)              # Gram-Schmidt against the normal
    length = np.linalg.norm(t, axis=1, keepdims=True)
    fallback = np.cross(nrm, [0, 1, 0]) + np.cross(nrm, [1, 0, 0]) * 0.01
    t = np.where(length > 1e-9, t / np.maximum(length, 1e-9), fallback / np.linalg.norm(fallback, axis=1, keepdims=True))
    w = np.where(np.sum(np.cross(nrm, t) * b, axis=1) < 0, -1.0, 1.0)
    return np.hstack([t, w[:, None]]).astype(np.float32)


def write(path, sk, parts, mats, textures):
    nodes = skeleton_nodes(sk)
    jaw_node = len(nodes) - 1
    out = {"asset": {"version": "2.0", "generator": "darkride-automata build_bots.py"}, "scene": 0,
           "scenes": [{"nodes": sk.data["roots"]}], "nodes": nodes, "skins": [], "meshes": [], "materials": [],
           "accessors": [], "bufferViews": [], "buffers": []}
    data = bytearray()

    def put(arr, ctype, kind, target=None, minmax=False):
        while len(data) % 4:
            data.append(0)
        view = {"buffer": 0, "byteOffset": len(data), "byteLength": arr.nbytes}
        if target:
            view["target"] = target
        data.extend(arr.tobytes())
        out["bufferViews"].append(view)
        a = {"bufferView": len(out["bufferViews"]) - 1, "componentType": ctype, "count": len(arr), "type": kind}
        if minmax:
            a["min"], a["max"] = arr.min(0).tolist(), arr.max(0).tolist()
        out["accessors"].append(a)
        return len(out["accessors"]) - 1

    skin = {"joints": sk.data["skin"]["joints"] + [jaw_node]}
    ibm = sk.ibm
    jaw_ibm = np.linalg.inv(sk.bind["jaw"]).T.reshape(1, 4, 4)       # column-major, like the rest
    ibm = np.vstack([ibm, jaw_ibm]).reshape(-1, 16).astype(np.float32)
    skin["inverseBindMatrices"] = put(ibm, 5126, "MAT4")
    out["skins"].append(skin)

    for name, (uri, normal_uri) in textures.items():                    # the weathering, as files beside the model
        out.setdefault("samplers", [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}])
        for key, u in (("baseColorTexture", uri), ("normalTexture", normal_uri)):
            out.setdefault("images", []).append({"uri": u})
            out.setdefault("textures", []).append({"source": len(out["images"]) - 1, "sampler": 0})
            if key == "normalTexture":
                mats[name]["normalTexture"] = {"index": len(out["textures"]) - 1, "scale": 1.0}
            else:
                mats[name]["pbrMetallicRoughness"][key] = {"index": len(out["textures"]) - 1}

    prims, tris = [], 0
    rng = np.random.default_rng(len(parts))
    for name, mat in mats.items():
        pos, nrm, jnt, idx, uvs = [], [], [], [], []
        base = 0
        for mesh, material, bone, smooth in parts:
            if material != name:
                continue
            m = mesh.copy()
            if not smooth:
                m.unmerge_vertices()                                    # flat shading for discs and boxes
            if name in textures:
                uvs.append(box_uv(m, rng.random(2)))                    # each part a different patch of the paint
            m.apply_transform(sk.to_bind(bone))
            pos.append(m.vertices); nrm.append(m.vertex_normals); idx.append(m.faces + base)
            jnt.append(np.full(len(m.vertices), sk.names.index(bone)))
            base += len(m.vertices)
        if not pos:
            continue
        pos = np.vstack(pos).astype(np.float32)
        joints = np.zeros((len(pos), 4), np.uint8)
        joints[:, 0] = np.concatenate(jnt)
        weights = np.zeros((len(pos), 4), np.float32)
        weights[:, 0] = 1
        faces = np.vstack(idx).astype(np.uint32)
        tris += len(faces)
        out["materials"].append(dict(mats[name], name=name))
        attributes = {"POSITION": put(pos, 5126, "VEC3", 34962, True),
                      "NORMAL": put(np.vstack(nrm).astype(np.float32), 5126, "VEC3", 34962),
                      "JOINTS_0": put(joints, 5121, "VEC4", 34962),
                      "WEIGHTS_0": put(weights, 5126, "VEC4", 34962)}
        if uvs:
            uv = np.vstack(uvs).astype(np.float32)
            attributes["TEXCOORD_0"] = put(uv, 5126, "VEC2", 34962)
            attributes["TANGENT"] = put(tangents(pos, np.vstack(nrm), uv, faces), 5126, "VEC4", 34962)
        prims.append({"attributes": attributes, "indices": put(faces.ravel(), 5125, "SCALAR", 34963),
                      "material": len(out["materials"]) - 1})
    out["meshes"].append({"name": "bot", "primitives": prims})
    while len(data) % 4:
        data.append(0)
    out["buffers"].append({"byteLength": len(data)})
    text = json.dumps(out, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(data), 0x004E4942) + bytes(data)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return tris


def write_fst(out, bot, joints):
    """The bot's .fst: its model, its size, and its joints in the order a viewer makes them (docs/skeleton.md)."""
    lines = ["name = DarkrideBot-" + bot["id"], "type = body+head", "scale = %g" % bot.get("size", 1),
             "filename = duck-%s.glb" % bot["id"], "joint = jointRoot = Hips", "joint = jointHead = head_roll"]
    lines += ["jointIndex = %s = %d" % (n, i) for i, n in enumerate(joints)]
    (out / ("duck-%s.fst" % bot["id"])).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    argv = sys.argv[1:]
    roster_path = pathlib.Path(argv[argv.index("--roster") + 1]) if "--roster" in argv else KIT / "examples" / "bots.json"
    out = pathlib.Path(argv[argv.index("--out") + 1]) if "--out" in argv else KIT / "dist" / "bot"
    wanted = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] not in ("--roster", "--out"))]
    out.mkdir(parents=True, exist_ok=True)
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    skeleton = json.loads((HERE / "skeleton.json").read_text(encoding="utf-8"))
    cycles = json.loads((KIT / "bot" / "duck-cycles.json").read_text(encoding="utf-8"))
    sk = Skeleton(skeleton, cycles)
    # the brain turns the jaw with what's in duck-cycles.json: it must be this skeleton's jaw
    jaw = [round(float(x), 5) for x in R.from_matrix(sk.jaw_local[:3, :3]).as_quat()]
    if cycles.get("jaw", {}).get("base") != jaw or cycles["jaw"].get("parent") != sk.jaw_parent:
        cycles["jaw"] = {"parent": sk.jaw_parent, "base": jaw, "open": -1}
        (KIT / "bot" / "duck-cycles.json").write_text(json.dumps(cycles, separators=(",", ":")), encoding="utf-8")
        print("updated the jaw in bot/duck-cycles.json")
    joints = viewer_joints(sk, skeleton_nodes(sk))
    for bot in roster["bots"]:
        if wanted and bot["id"] not in wanted:
            continue
        seed = sum(ord(c) * 31 ** k for k, c in enumerate(bot["id"])) % 100000
        bot = dict(bot)
        bot.setdefault("unit", "%s-%02d" % (roster.get("unit_prefix", "DR"), seed % 97))
        style = dict(bot.get("style", {}), rockets=bot.get("rockets", False))
        parts = design(sk, style)
        used = {material for _, material, _, _ in parts}
        trim = bot.get("paint2", list(np.array(bot["accent"]) * 0.55 + np.array(bot["shell"]) * 0.15))
        paints = {"shell": lambda: weathering.shell_texture(bot, seed),
                  "dark": lambda: weathering.dark_texture(bot, seed),
                  "metal": lambda: weathering.shell_texture(bot, seed + 7, [0.66, 0.67, 0.7], False, False),
                  "trim": lambda: weathering.shell_texture(bot, seed + 13, trim, False, False)}
        textures = {}
        for name in TEXTURED:
            if name not in used:
                continue
            textures[name] = ("bot-%s-%s.png" % (bot["id"], name), "bot-%s-%s-n.png" % (bot["id"], name))
            colour, normal = paints[name]()
            (out / textures[name][0]).write_bytes(colour)
            (out / textures[name][1]).write_bytes(normal)
        tris = write(out / ("duck-%s.glb" % bot["id"]), sk, parts, materials(bot), textures)
        write_fst(out, bot, joints)
        if bot.get("role"):
            # a bot with a role (a bouncer) runs from its own persistent script, which names it
            loader = ["//", "//  bot-%s.js: %s's own persistent script (darkride-automata): the brain, as this bot (%s)."
                      % (bot["id"], bot["name"], bot["role"]), "//", "var BOT_ID = %s;" % json.dumps(bot["id"]),
                      'Script.include(Script.resolvePath("duck-brain.js"));', ""]
            (out / ("bot-%s.js" % bot["id"])).write_text("\n".join(loader), encoding="utf-8")
        print("%-8s %-8s size %.2f wear %.2f  %s  %d triangles" % (bot["id"], bot["name"], bot.get("size", 1),
              bot.get("wear", 0.3), json.dumps(bot.get("style", {})), tris))
    (out / "bots.json").write_text(json.dumps(roster, indent=1), encoding="utf-8")       # as given, without the defaults
    (out / "fx-spark.png").write_bytes(weathering.spark())


if __name__ == "__main__":
    main()
