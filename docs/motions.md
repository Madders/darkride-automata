# Recording motions

The walks, turns and moves in `bot/duck-cycles.json` were recorded from Pollen Robotics' Microduck policies running in
MuJoCo. You don't need this to use or design bots; it's for recording new motion, or rebuilding the skeleton.

## What you need

- Pollen's robot and policies (Apache-2.0):
  - https://github.com/pollen-robotics/microduck_rl: the MJCF robot description (and the meshes, CC BY-NC-SA 4.0,
    only needed by build_duck.py for its reference model);
  - https://huggingface.co/spaces/pollen-robotics/microduck-simulator: the trained policies (`app/public/policies/*.onnx`)
    and the scene the browser simulator uses.
- Python with `mujoco`, `onnxruntime`, `numpy`, `trimesh`, `scipy` (requirements.txt).

## The policies

Each policy is an ONNX network the simulator runs at 50 Hz: 61 inputs (gyro 3, projected gravity 3, joint positions
minus the default pose 14, joint velocities 14, the last action 14, a command 13) and 14 outputs (each joint's target,
added to the default pose). MuJoCo steps at 0.005 s with a decimation of 4. From rest, the walk policy needs a little
sensor noise (0.01) to start walking, as the real robot has.

## The scripts (motions/)

1. `record_walk.py <robot.xml, with its assets/ beside it> <walk.onnx> <walk.json>`: runs the walk policy and records the trunk and the 14 joints.
2. `build_duck.py <scene.xml> <walk.json> <out folder> --scale 4`: a reference duck.glb and .fst from the MJCF and
   Pollen's meshes (vertex clustering simplifies their CAD meshes).
3. `extract_skeleton.py <duck.glb>`: writes `tools/skeleton.json`, the skeleton without the meshes.
4. `make_cycles.py <scene.xml> <walk.onnx> <duck-cycles.json> --scale 4`: walk, turn and idle loops, each the one gait
   period whose end best matches its start, with the speed or turn rate measured; turn right is turn left mirrored.
   Also writes each joint's base rotation, limits, rest angle and parent.
5. `record_moves.py <scene.xml> <policies folder> <duck-cycles.json>`: the one-shot moves, appended as `moves`:
   - `sit`: the sit/stand policy, sitting then standing (with `sitFrame` where it can be held);
   - `roll`: the roulade policy from walking, until it's upright again;
   - `peck`: the ground-pick policy over most of its cycle;
   - `kick_left`, `kick_right`: the kick policies, then back to walking.

Motion is recorded at the robot's size and played at four times it with Froude timing: frames come `√4 = 2` times
slower, steps four times longer. The brain then scales again for each bot's size.

## Adding a move

Record it in `record_moves.py` like the others (a policy, its command, when to stop), add it to the `moves` loop, and
have the brain pick it: `startMove("name")` in `decide()` or wherever it fits.
