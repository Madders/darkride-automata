# The skeleton

Every bot shares one skeleton: the Microduck's, from Pollen Robotics' MJCF robot description (Apache-2.0), scaled
up four times. It's in `tools/skeleton.json` (no meshes), and every bot's model is built on it, which is why one set
of recorded walks and moves fits them all.

## Frames and units

- **y is up**, the bot **faces -z**, its **left is -x** (so its right is +x).
- Metres, at **four times the real robot's size**: the standard bot (size 1) is about 1.05 m to the top of its
  head, its hips 0.48 m off the floor (`trunkHeight` in duck-cycles.json). A bot's `size` scales all of it.
- The model's origin is the hips: the mesh is in the Hips bone's frame, and the floor is at y = -0.48 in the rest
  pose.

(MuJoCo is z up with the robot facing +x; the conversion is `M = [[0,-1,0],[0,0,1],[-1,0,0]]`, MuJoCo to ours.)

## The bones

Sixteen: the fifteen moving parts and the jaw. Each turns about **its own local y axis**: a joint at angle *a* has
the local rotation `base × rotY(a)`, where `base` is its rotation at angle 0 (`base` in duck-cycles.json).

| bone | parent | at rest (m) | turns about (rest, world) |
|---|---|---|---|
| Hips | (root) | 0, 0, 0 | (the trunk: position and tilt) |
| left_hip_yaw | Hips | -0.07, -0.02, -0.024 | up-down: the leg swings in and out |
| left_hip_roll | left_hip_yaw | -0.07, -0.07, -0.09 | front-back: the leg rolls sideways |
| left_hip_pitch | left_hip_roll | -0.17, -0.079, -0.016 | left-right: the thigh swings fore and aft |
| left_knee | left_hip_pitch | -0.141, -0.219, 0.073 | left-right (bends backwards, like a bird's) |
| left_ankle | left_knee | -0.232, -0.379, 0 | left-right: the foot tilts |
| neck_pitch | Hips | -0.058, 0.13, -0.104 | left-right: the neck tips forward |
| head_pitch | neck_pitch | -0.058, 0.318, -0.036 | left-right: the head nods |
| head_yaw | head_pitch | 0, 0.392, -0.036 | up-down: the head turns |
| head_roll | head_yaw | 0, 0.45, 0.036 | front-back: the head tilts |
| jaw | head_roll | 0, 0.445, 0.09 | left-right: the lower jaw opens (added by build_bots.py) |
| right_… | as left | x mirrored | as left |

Joint limits (radians, from the MJCF) are in duck-cycles.json's `limits`, the rest angles in `rest`, and each bone's
parent in `parents`.

## Bind pose and rest pose

- The **bind pose** is the MJCF's zero pose: every joint at angle 0, legs splayed. It's what the model's bones and
  inverse bind matrices are in (skeleton.json), so it's what a model is skinned to.
- The **rest pose** is the walk policy's default stance: knees bent, feet under the body, head up. It's what the bot
  looks like standing still, and what build_bots.py designs parts in: each part is made in the rest pose and carried
  onto its bone's bind pose (`bind × rest⁻¹` for its bone), so it sits right when the bones are posed.

## What a viewer expects (the important bit)

Three things about how Darkride's viewer applies a scripted avatar's joints, each of which cost us a day:

1. **The joint list.** A viewer applies an avatar's joint data by *index* into the skeleton its glTF loader builds:
   **ROOT first** (the loader adds it, because the scene has two roots: the skeleton and the mesh node), then the
   nodes **depth first** from each root: Hips, the left leg, the neck and head, **the jaw straight after head_roll**,
   the right leg, then `duck_mesh`. A scripted avatar only knows its joints from its .fst's `jointIndex` lines, so
   the .fst must list exactly that order. If the count differs from the viewer's skeleton, the viewer ignores the
   joint data altogether (the legs never move); if the order differs, every rotation lands on the wrong joint.
   build_bots.py writes the list by walking the node tree the same way.
2. **Model-frame rotations.** The viewer takes each joint's rotation **in the model's frame** (the product of its
   parents' rotations and its own), not relative to its parent. The brain composes them down the chain.
3. **The half turn.** A viewer turns every avatar model half round about y (avatar models normally face +z; ours faces
   -z). The brain sends ROOT (and the mesh node) a half turn to cancel it, so the bot faces the way it walks.

## The recorded data (duck-cycles.json)

- `fps`, `scale`, `trunkHeight`, `joints` (the 14 joint bones in order), `base`, `parents`, `limits`, `rest`, `jaw`.
- `cycles`: `walk`, `turn_left`, `turn_right`, `idle`, each `{frames, speed (m/s), turn (rad/s), seconds}`; a frame is
  `[dy, Hips x y z w, then each joint's local quaternion x y z w]`: the trunk's bob and tilt, and the joints.
- `moves`: `sit`, `roll`, `peck`, `kick_left`, `kick_right`, each `{frames, turn, seconds, sitFrame?}`; a frame is
  `[offset x y z, Hips x y z w, then each joint's local quaternion]`, the offset and the trunk's rotation relative to
  where the move started; `sitFrame` is where a sit can be held.

## Making a skeleton.json yourself

From Pollen's MJCF: build a duck.glb with `motions/build_duck.py` (it also needs their meshes, which are only used
for that model), then `python motions/extract_skeleton.py duck.glb` writes `tools/skeleton.json`. See
docs/motions.md.
