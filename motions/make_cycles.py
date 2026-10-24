"""The ducks' motion cycles: walk, turn left, turn right and idle, cut as seamless loops from the real policy.

Runs the walk policy in MuJoCo (record_walk.py's loop) once per behaviour with a steady command, drops the first
seconds while it settles, finds the gait's period from the left knee (autocorrelation), and keeps the one period whose
end best matches its start. Each frame keeps what the duck's body does in place: the trunk's bob (height) and tilt and
the 14 joints, in Darkride's frame (build_duck.py's conversion); the brain script moves and turns the duck itself,
using each cycle's measured speed or turn rate. It also writes each joint's base rotation and axis, so the brain can
turn the head itself (looking at visitors, peering over edges).

Usage: <venv>/python make_cycles.py <scene.xml> <walk.onnx> <out cycles.json> [--scale 4]
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

BEHAVIOURS = {   # forward m/s, turn rad/s, seconds
    "walk": (0.25, 0.0, 16.0),           # the policy's top forward speed
    "turn_left": (0.04, 1.0, 12.0),      # a touch of forward breaks the standing duck's symmetry
    "idle": (0.0, 0.0, 8.0),
}
SETTLE = 5.0


def run(model, session, vx, wz, seconds):
    data = mujoco.MjData(model)
    jid = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n) for n in JOINT_NAMES]
    qadr = [model.jnt_qposadr[j] for j in jid]
    vadr = [model.jnt_dofadr[j] for j in jid]
    gyro = model.sensor_adr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, "imu_ang_vel")]
    trunk = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "trunk_base")
    data.qpos[:7] = [0, 0, 0.12, 1, 0, 0, 0]
    for i, a in enumerate(qadr):
        data.qpos[a] = DEFAULT_POSE[i]
    # actuators by joint name (their order in the MJCF isn't the policy's), as record_walk.py does
    act_index = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, n) for n in JOINT_NAMES]
    if min(act_index) < 0:
        act_index = list(range(14))
    for i, a in enumerate(act_index):
        data.ctrl[a] = DEFAULT_POSE[i]
    mujoco.mj_forward(model, data)
    last = np.zeros(14, dtype=np.float32)
    rng = np.random.default_rng(1)
    frames = []
    for step in range(int(seconds * 50)):
        # stand for two seconds, then ease the command in over one (from a cold start it just stands)
        ramp = 0.0 if step < 100 else min(1.0, (step - 100) / 50.0)
        rot = np.zeros(9)
        mujoco.mju_quat2Mat(rot, data.xquat[trunk])
        gravity = rot.reshape(3, 3).T @ np.array([0, 0, -1.0])
        cmd = np.zeros(13)
        cmd[0], cmd[2] = vx * ramp, wz * ramp
        obs = np.concatenate([data.sensordata[gyro:gyro + 3], gravity,
                              [data.qpos[a] - DEFAULT_POSE[i] for i, a in enumerate(qadr)],
                              [data.qvel[a] for a in vadr], last, cmd]).astype(np.float32)[None]
        # sensor noise like the real robot's: a perfectly symmetric duck at rest just shuffles in place
        obs[0, :48] += rng.normal(0, 0.01, 48).astype(np.float32)
        act = session.run(None, {session.get_inputs()[0].name: obs})[0][0]
        last = act.astype(np.float32)
        for i, a in enumerate(act_index):
            data.ctrl[a] = DEFAULT_POSE[i] + act[i]
        for _ in range(DECIMATION):
            mujoco.mj_step(model, data)
        frames.append(np.concatenate([data.qpos[:7], [data.qpos[a] for a in qadr]]))
    return np.array(frames)


def mirror(frames):
    """Left-right mirror image of a recording: the legs swap sides with their angles negated (the default pose and
    limits show the right leg's joints are the left's mirrored), head yaw and roll flip, and the trunk mirrors
    across its sagittal plane (y -> -y). A standing duck is too symmetric to start turning right by itself."""
    out = frames.copy()
    j = {n: 7 + i for i, n in enumerate(JOINT_NAMES)}
    for part in ("hip_yaw", "hip_roll", "hip_pitch", "knee", "ankle"):
        out[:, j["left_" + part]] = -frames[:, j["right_" + part]]
        out[:, j["right_" + part]] = -frames[:, j["left_" + part]]
    for n in ("head_yaw", "head_roll"):
        out[:, j[n]] = -frames[:, j[n]]
    out[:, 1] = -frames[:, 1]                    # y
    out[:, 4] = -frames[:, 4]                    # quaternion (w x y z): x and z flip
    out[:, 6] = -frames[:, 6]
    return out


def best_loop(frames):
    """(start, length) of the most seamless single gait period after settling."""
    steady = frames[int(SETTLE * 50):]
    knee = steady[:, 7 + JOINT_NAMES.index("left_knee")]
    k = knee - knee.mean()
    if np.abs(k).max() < 0.02:                  # standing: any short stretch loops
        return int(SETTLE * 50), 50
    ac = np.correlate(k, k, "full")[len(k) - 1:]
    period = int(np.argmax(ac[15:60]) + 15)     # 0.3-1.2 s
    joints = steady[:, 7:]
    best = min(range(0, len(steady) - period - 1),
               key=lambda s: np.abs(joints[s] - joints[s + period]).sum())
    return int(SETTLE * 50) + best, period


def main():
    argv = sys.argv[1:]
    scale = float(argv[argv.index("--scale") + 1]) if "--scale" in argv else 4.0
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or not argv[i - 1].startswith("--"))]
    scene, policy, out = args
    model = mujoco.MjModel.from_xml_path(scene)
    model.opt.timestep = TIMESTEP
    session = ort.InferenceSession(policy)
    bodies = list(range(1, model.nbody))
    names = ["Hips"] + [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, model.body_jntadr[b]) for b in bodies[1:]]
    local_r = {b: quat_to_mat(model.body_quat[b]) for b in bodies}
    slow = math.sqrt(scale)
    cycles = {}
    recorded = {}
    for name in list(BEHAVIOURS) + ["turn_right"]:
        if name == "turn_right":
            frames = mirror(recorded["turn_left"])
        else:
            vx, wz, seconds = BEHAVIOURS[name]
            frames = recorded[name] = run(model, session, vx, wz, seconds)
        start, length = best_loop(frames)
        clip = frames[start:start + length]
        # how far and how much it turns over the loop, in the world (scaled), per second of slowed playback
        trunk0, trunk1 = quat_to_mat(clip[0][3:7]), quat_to_mat(frames[start + length][3:7])
        yaw0 = math.atan2(trunk0[1, 0], trunk0[0, 0])
        yaw1 = math.atan2(trunk1[1, 0], trunk1[0, 0])
        turned = (yaw1 - yaw0 + math.pi) % (2 * math.pi) - math.pi
        moved = np.linalg.norm(frames[start + length][:2] - clip[0][:2])
        duration = length / 50.0 * slow
        rows = []
        for fr in clip:
            trunk = quat_to_mat(fr[3:7])
            yaw = math.atan2(trunk[1, 0], trunk[0, 0])
            tilt = rot_z(-yaw) @ trunk
            row = [round(float((fr[2] - 0.12) * scale), 4)]
            row += [round(float(x), 4) for x in mat_to_quat(to_dr(tilt))]
            for b in bodies[1:]:
                angle = fr[7 + JOINT_NAMES.index(names[bodies.index(b)])]
                row += [round(float(x), 4) for x in mat_to_quat(to_dr(local_r[b] @ rot_z(angle)))]
            rows.append(row)
        cycles[name] = {"frames": rows, "speed": round(moved * scale / duration, 4),
                        "turn": round(turned / duration, 4), "seconds": round(duration, 3)}
        print("%-10s loop of %d frames (%.2f s played), %.2f m/s, %.1f deg/s"
              % (name, length, duration, moved * scale / duration, math.degrees(turned / duration)))
    # each joint's parent (Hips for the top ones): a viewer wants every joint's rotation in the model's frame, so the
    # brain composes them down the chain
    parents = {names[bodies.index(b)]: names[bodies.index(model.body_parentid[b])] for b in bodies[1:]}
    base = {names[bodies.index(b)]: [round(float(x), 5) for x in mat_to_quat(to_dr(local_r[b]))] for b in bodies[1:]}
    limits = {n: [round(float(model.jnt_range[model.body_jntadr[b]][0]), 3), round(float(model.jnt_range[model.body_jntadr[b]][1]), 3)]
              for n, b in zip(names[1:], bodies[1:])}
    json.dump({"fps": 50 / slow, "scale": scale, "trunkHeight": 0.12 * scale, "joints": names[1:],
               "layout": "dy (m) then Hips quat (x y z w) then each joint's local quat; joint local = base * rotY(angle)",
               "base": base, "parents": parents, "limits": limits, "rest": dict(zip(JOINT_NAMES, [round(float(x), 4) for x in DEFAULT_POSE])),
               "cycles": cycles}, open(out, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
