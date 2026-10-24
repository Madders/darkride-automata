"""Records the Microduck walking under its real policy, for the Madders Place duck (spike, 2026-10-24).

Runs Pollen's walk policy (BEST_alpha_walking.onnx from the microduck-simulator Space, Apache-2.0) on the robot's
MJCF in MuJoCo, exactly as the Space's game.js does: 50 Hz policy steps of four 5 ms physics steps, the 61-value
observation [gyro, projected gravity, joint positions and velocities, last action, 13-value command], and joint
targets = default pose + action. A command script (forward speed, turn rate, head pose over time) steers it, and every
policy step's trunk pose and 14 joint angles are written to a JSON clip the Darkride agent script plays back.

Usage: <venv>/python record_walk.py <robot.xml with assets/ beside it> <walk.onnx> <out.json>
"""
import json
import sys

import mujoco
import numpy as np
import onnxruntime as ort

JOINT_NAMES = ["left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee", "left_ankle",
               "neck_pitch", "head_pitch", "head_yaw", "head_roll",
               "right_hip_yaw", "right_hip_roll", "right_hip_pitch", "right_knee", "right_ankle"]
DEFAULT_POSE = np.array([0, -0.08726646259971647, -0.457924, -0.004940, 0.452984,
                         0.3490658503988659, 0.3490658503988659, 0, 0,
                         0, 0.08726646259971647, 0.457924, 0.004940, -0.452984])
TIMESTEP, DECIMATION = 0.005, 4

# (seconds, forward m/s, turn rad/s, head [neck_pitch, head_pitch, head_yaw, head_roll] offsets): a little tour
SCRIPT = [
    (2.0, 0.0, 0.0, [0, 0, 0, 0]),          # settle
    (2.0, 0.0, 0.0, [0, 0, 0.6, 0]),        # look left
    (2.0, 0.0, 0.0, [0, 0, -0.6, 0]),       # look right
    (6.0, 0.22, 0.0, [0, 0, 0, 0]),         # walk
    (3.0, 0.15, 0.8, [0, 0, 0.3, 0]),       # curve left
    (5.0, 0.22, 0.0, [0, 0, 0, 0]),         # walk
    (3.0, 0.0, -1.0, [0, 0, -0.3, 0]),      # turn on the spot
    (3.0, 0.0, 0.0, [0, -0.3, 0, 0.2]),     # curious head tilt
    (2.0, 0.0, 0.0, [0, 0, 0, 0]),
]


def scene(path):
    """The robot with a floor, written beside it (its meshes are found relative to the file)."""
    import os
    xml = open(path, encoding="utf-8").read()
    floor = '<geom name="floor" type="plane" size="20 20 0.1" friction="1 0.005 0.0001"/>'
    xml = xml.replace("<worldbody>", "<worldbody>" + floor, 1)
    tmp = os.path.join(os.path.dirname(os.path.abspath(path)), "_scene.xml")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(xml)
    return mujoco.MjModel.from_xml_path(tmp)


def main():
    robot, policy, out = sys.argv[1:4]
    model = scene(robot)
    model.opt.timestep = TIMESTEP
    data = mujoco.MjData(model)
    session = ort.InferenceSession(policy)
    jid = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n) for n in JOINT_NAMES]
    qadr = [model.jnt_qposadr[j] for j in jid]
    vadr = [model.jnt_dofadr[j] for j in jid]
    act_index = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, n) for n in JOINT_NAMES]
    if min(act_index) < 0:   # actuators named differently: fall back to their order (the Space does the same)
        act_index = list(range(14))
    gyro = model.sensor_adr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, "imu_ang_vel")]
    trunk = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "trunk_base")

    data.qpos[:7] = [0, 0, 0.12, 1, 0, 0, 0]
    for i, a in enumerate(qadr):
        data.qpos[a] = DEFAULT_POSE[i]
    for i, a in enumerate(act_index):
        data.ctrl[a] = DEFAULT_POSE[i]
    mujoco.mj_forward(model, data)

    last = np.zeros(14, dtype=np.float32)
    head = np.zeros(4)
    frames = []
    for seconds, vx, wz, head_target in SCRIPT:
        for _ in range(int(seconds * 50)):
            head += 0.1 * (np.array(head_target) - head)            # the runtime's head smoothing
            q = data.xquat[trunk]                                    # w, x, y, z
            rot = np.zeros(9)
            mujoco.mju_quat2Mat(rot, q)
            gravity = rot.reshape(3, 3).T @ np.array([0, 0, -1.0])
            cmd = np.zeros(13)
            cmd[0], cmd[2] = vx, wz
            cmd[3:7] = head
            obs = np.concatenate([data.sensordata[gyro:gyro + 3], gravity,
                                  [data.qpos[a] - DEFAULT_POSE[i] for i, a in enumerate(qadr)],
                                  [data.qvel[a] for a in vadr], last, cmd]).astype(np.float32)[None]
            act = session.run(None, {session.get_inputs()[0].name: obs})[0][0]
            last = act.astype(np.float32)
            for i, a in enumerate(act_index):
                data.ctrl[a] = DEFAULT_POSE[i] + act[i]
            for _ in range(DECIMATION):
                mujoco.mj_step(model, data)
            frames.append([round(float(x), 4) for x in list(data.qpos[:7]) + [data.qpos[a] for a in qadr]])
    json.dump({"fps": 50, "joints": JOINT_NAMES, "layout": "x y z qw qx qy qz then the joints (rad)",
               "source": "Pollen Robotics microduck walk policy (Apache-2.0), recorded in MuJoCo",
               "frames": frames}, open(out, "w"))
    f = np.array(frames)
    print("%d frames, trunk height %.3f-%.3f m, travelled %.2f m, ended at (%.2f, %.2f)"
          % (len(f), f[:, 2].min(), f[:, 2].max(), np.abs(np.diff(f[:, :2], axis=0)).sum(), f[-1, 0], f[-1, 1]))


if __name__ == "__main__":
    main()
