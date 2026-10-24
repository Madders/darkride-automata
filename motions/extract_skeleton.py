"""Writes tools/skeleton.json: the bots' skeleton on its own, from a duck.glb built by build_duck.py.

The skeleton is the Microduck's, from Pollen Robotics' MJCF robot description (Apache-2.0): one bone per moving part
(Hips, then each leg's hip yaw, roll and pitch, knee and ankle, and the neck and head's pitch, yaw, roll), in the
mesh frame duck.glb uses (y up, the duck facing -z, its left at -x; metres at four times the robot's size). It keeps
the node tree (names, translations, rotations, children), the scene's roots and each bone's inverse bind matrix:
everything build_bots.py needs, and none of the meshes (Pollen's meshes are CC BY-NC-SA and stay out of the kit).

Usage: python extract_skeleton.py <duck.glb> [out skeleton.json]
"""
import json
import pathlib
import struct
import sys

import numpy as np


def main():
    src = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(__file__).resolve().parents[1] / "tools" / "skeleton.json"
    data = src.read_bytes()
    n = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + n])
    binary = data[20 + n + 8:]
    skin = gltf["skins"][0]
    a = gltf["accessors"][skin["inverseBindMatrices"]]
    view = gltf["bufferViews"][a["bufferView"]]
    ibm = np.frombuffer(binary, np.float32, a["count"] * 16, view.get("byteOffset", 0) + a.get("byteOffset", 0))
    nodes = []
    for nd in gltf["nodes"]:
        node = {"name": nd["name"]}
        for key in ("translation", "rotation", "children"):
            if key in nd:
                node[key] = nd[key]
        if "mesh" in nd:
            node["mesh"] = True                      # the node the model's mesh hangs from (no geometry kept)
        nodes.append(node)
    skeleton = {
        "about": "The bots' skeleton: the Microduck's (Pollen Robotics' MJCF robot description, Apache-2.0), at four "
                 "times the robot's size, y up, facing -z. nodes as in glTF; skin: the bone nodes in skin order with "
                 "their inverse bind matrices (column-major, as glTF). No meshes.",
        "nodes": nodes,
        "roots": gltf["scenes"][gltf.get("scene", 0)]["nodes"],
        "skin": {"joints": skin["joints"], "inverseBindMatrices": [round(float(x), 6) for x in ibm]},
    }
    out.write_text(json.dumps(skeleton, indent=1), encoding="utf-8")
    print("wrote %s: %d nodes, %d bones" % (out, len(nodes), len(skin["joints"])))


if __name__ == "__main__":
    main()
