"""The ducks' one-shot moves, recorded from Pollen's own policies: sit down and stand up, the roll (roulade), the
ground peck and the two kicks (2026-10-24).

Each move runs in MuJoCo the way the Microduck simulator triggers it (game.js), handing between policies without
resetting the action history, with a little sensor noise as on the real robot:
- sit: the sit/stand policy holds the stand for 0.8 s, then its sit flag (command slot 0) goes to 1; clearing it
  stands the duck up again;
- roll: from walking, the roulade policy (all-zero command) until the trunk has tipped and is upright again;
- peck: the ground-pick policy with its phase as [cos, sin] in command slots 0-1, for 0.7 of a 4 s period;
- kick_left / kick_right: the kick policy for 0.5 s from standing, then back to the walk policy.

Every frame keeps the trunk's motion relative to where the move started (offset forward/left and up, in metres at
the duck's scale, and its full rotation), and the 14 joints, all in Darkride's frame, so the brain plays the move
from the duck's position and heading and takes its final heading afterwards.

Usage: <venv>/python record_moves.py <scene.xml> <policies dir> <duck-cycles.json to add to> [--scale 4]
"""
import json
import math
import pathlib
import sys

import mujoco
import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_duck import M, mat_to_quat, quat_to_mat, rot_z, to_dr   # noqa: E402
from record_walk import DECIMATION, DEFAULT_POSE, JOINT_NAMES, TIMESTEP   # noqa: E402


class Duck:
    """The robot in MuJoCo with the policy loop of the Microduck simulator."""

    def __init__(self, model, seed=1):
        self.m, self.d = model, mujoco.MjData(model)
        jid = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n) for n in JOINT_NAMES]
        self.qadr = [model.jnt_qposadr[j] for j in jid]
        self.vadr = [model.jnt_dofadr[j] for j in jid]
        self.act = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, n) for n in JOINT_NAMES]
        self.gyro = model.sensor_adr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, "imu_ang_vel")]
        self.trunk = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "trunk_base")
        self.d.qpos[:7] = [0, 0, 0.12, 1, 0, 0, 0]
        for i, a in enumerate(self.qadr):
            self.d.qpos[a] = DEFAULT_POSE[i]
        for i, a in enumerate(self.act):
            self.d.ctrl[a] = DEFAULT_POSE[i]
        mujoco.mj_forward(model, self.d)
        self.last = np.zeros(14, dtype=np.float32)
        self.rng = np.random.default_rng(seed)
        self.frames = []

    def gravity(self):
        rot = np.zeros(9)
        mujoco.mju_quat2Mat(rot, self.d.xquat[self.trunk])
        return rot.reshape(3, 3).T @ np.array([0, 0, -1.0])

    def step(self, session, cmd):
        d = self.d
        obs = np.concatenate([d.sensordata[self.gyro:self.gyro + 3], self.gravity(),
                              [d.qpos[a] - DEFAULT_POSE[i] for i, a in enumerate(self.qadr)],
                              [d.qvel[a] for a in self.vadr], self.last, cmd]).astype(np.float32)[None]
        obs[0, :48] += self.rng.normal(0, 0.01, 48).astype(np.float32)
        act = session.run(None, {session.get_inputs()[0].name: obs})[0][0]
        self.last = act.astype(np.float32)
        for i, a in enumerate(self.act):
            d.ctrl[a] = DEFAULT_POSE[i] + act[i]
        for _ in range(DECIMATION):
            mujoco.mj_step(self.m, d)
        self.frames.append(np.concatenate([d.qpos[:7], [d.qpos[a] for a in self.qadr]]))

    def hold(self, session, seconds, vx=0.0, wz=0.0):
        for _ in range(int(seconds * 50)):
            cmd = np.zeros(13)
            cmd[0], cmd[2] = vx, wz
            self.step(session, cmd)


def record(model, sessions, move):
    """(frames of the whole run, index where the move starts, index where it ends)."""
    duck = Duck(model)
    walk = sessions["walk"]
    duck.hold(walk, 2.0)
    if move == "sit":
        start = len(duck.frames)
        duck.last[:] = 0
        duck.hold(sessions["sitstand"], 0.8)                          # the hand-over: hold the stand first
        for _ in range(200):                                          # sit, and stay sat a while
            cmd = np.zeros(13); cmd[0] = 1.0
            duck.step(sessions["sitstand"], cmd)
        sat = len(duck.frames)
        for _ in range(180):                                          # stand back up
            duck.step(sessions["sitstand"], np.zeros(13))
        end = len(duck.frames)
        duck.last[:] = 0
        duck.hold(walk, 1.0)
        return duck.frames, start, end, sat
    if move == "roll":
        duck.hold(walk, 3.0, vx=0.15)                                 # it initiates best mid-gait
        start, tipped = len(duck.frames), False
        for steps in range(150):
            duck.step(sessions["roll"], np.zeros(13))
            g = duck.gravity()
            tipped = tipped or g[2] > -0.3
            if tipped and g[2] < -0.85 and steps >= 40:
                break
        end = len(duck.frames)
        duck.last[:] = 0
        duck.hold(walk, 1.5)
        return duck.frames, start, end, None
    if move == "peck":
        start = len(duck.frames)
        phase = 0.0
        while phase < 0.7:
            cmd = np.zeros(13); cmd[0], cmd[1] = math.cos(2 * math.pi * phase), math.sin(2 * math.pi * phase)
            duck.step(sessions["peck"], cmd)
            phase += 0.02 / 4.0
        end = len(duck.frames)
        duck.hold(walk, 1.0)
        return duck.frames, start, end, None
    if move in ("kick_left", "kick_right"):
        start = len(duck.frames)
        for _ in range(25):
            duck.step(sessions[move], np.zeros(13))
        duck.hold(walk, 1.2)                                          # the follow-through back to standing
        end = len(duck.frames)
        return duck.frames, start, end, None
    raise ValueError(move)


def to_rows(model, frames, start, end, scale):
    """Frames from start to end relative to the start pose, in Darkride's frame (see the module docstring)."""
    bodies = list(range(1, model.nbody))
    names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, model.body_jntadr[b]) for b in bodies[1:]]
    local_r = {b: quat_to_mat(model.body_quat[b]) for b in bodies}
    f0 = frames[start]
    t0 = quat_to_mat(f0[3:7])
    yaw0 = math.atan2(t0[1, 0], t0[0, 0])
    heading = rot_z(-yaw0)
    rows = []
    for fr in frames[start:end]:
        off = heading @ (np.array(fr[:3]) - np.array(f0[:3]))
        off[2] = fr[2] - 0.12                                         # height above the standing trunk height
        p = M @ off * scale
        rel = heading @ quat_to_mat(fr[3:7])                          # the trunk's whole rotation, from the start heading
        row = [round(float(x), 4) for x in p] + [round(float(x), 4) for x in mat_to_quat(to_dr(rel))]
        for b, name in zip(bodies[1:], names):
            row += [round(float(x), 4) for x in mat_to_quat(to_dr(local_r[b] @ rot_z(fr[7 + JOINT_NAMES.index(name)])))]
        rows.append(row)
    tl = quat_to_mat(frames[end - 1][3:7])
    turned = (math.atan2(tl[1, 0], tl[0, 0]) - yaw0 + math.pi) % (2 * math.pi) - math.pi
    return rows, turned


def main():
    argv = sys.argv[1:]
    scale = float(argv[argv.index("--scale") + 1]) if "--scale" in argv else 4.0
    scene, policies, out = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or not argv[i - 1].startswith("--"))]
    model = mujoco.MjModel.from_xml_path(scene)
    model.opt.timestep = TIMESTEP
    p = pathlib.Path(policies)
    sessions = {"walk": "BEST_alpha_walking", "sitstand": "BEST_alpha_sitstand", "roll": "roulade",
                "peck": "alpha_ground_pick", "kick_left": "ball_kick_left", "kick_right": "ball_kick_right"}
    sessions = {k: ort.InferenceSession(str(p / (v + ".onnx"))) for k, v in sessions.items()}
    data = json.load(open(out))
    moves = {}
    for move in ("sit", "roll", "peck", "kick_left", "kick_right"):
        frames, start, end, sat = record(model, sessions, move)
        rows, turned = to_rows(model, frames, start, end, scale)
        heights = [r[1] for r in rows]
        entry = {"frames": rows, "turn": round(turned, 3), "seconds": round(len(rows) / 50 * math.sqrt(scale), 2)}
        if sat:
            entry["sitFrame"] = sat - start                           # where the sit is held (until told to stand)
        moves[move] = entry
        print("%-10s %3d frames (%.1f s played), height %.2f..%.2f m, ends %.2f m forward, turned %.0f deg"
              % (move, len(rows), entry["seconds"], min(heights), max(heights), -rows[-1][2], math.degrees(turned)))
    data["moves"] = moves
    data["layout_moves"] = ("offset x y z (m, Darkride frame, from the start pose) then the trunk's whole rotation "
                            "(Hips, x y z w, relative to the start heading) then each joint's local quat")
    json.dump(data, open(out, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
