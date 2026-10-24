"""Builds the folder of bot files to upload to a world's asset server (docs/adding-to-your-world.md).

  python tools/package.py --roster <bots.json> --world <world.json> [--out dist/bot] [--zip] [--skip-nav]

Into the output folder: the runtime (duck-bot.js, the persistent script; duck-brain.js; duck-cycles.json, the
recorded walks and moves; sounds/), each bot's model, textures and .fst from the roster (build_bots.py), the roster
itself as bots.json, and the world's walkable map as nav.json (build_nav.py with the world config). With --zip, also
a .zip of it next to the folder. Upload the folder's contents so they sit together at one URL, then run duck-bot.js
as the domain's persistent script with one instance per bot.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

KIT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ["duck-bot.js", "duck-brain.js", "duck-cycles.json"]


def arg(name, default=None):
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv else default


def main():
    roster = pathlib.Path(arg("--roster", KIT / "examples" / "bots.json")).resolve()
    world = arg("--world")
    out = pathlib.Path(arg("--out", KIT / "dist" / "bot")).resolve()
    if not world and "--skip-nav" not in sys.argv:
        sys.exit("give the world config with --world (or --skip-nav to leave the map out)\n" + __doc__)
    out.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME:
        shutil.copy2(KIT / "bot" / name, out / name)
    shutil.copytree(KIT / "bot" / "sounds", out / "sounds", dirs_exist_ok=True)
    print("runtime copied")
    subprocess.run([sys.executable, str(KIT / "tools" / "build_bots.py"), "--roster", str(roster), "--out", str(out)],
                   check=True)
    if "--skip-nav" not in sys.argv:
        # the world config's own "out" is replaced by this folder's nav.json
        config_path = pathlib.Path(world).resolve()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["out"] = str(out / "nav.json")
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=config_path.parent, delete=False, encoding="utf-8") as f:
            json.dump(config, f)
            temp = f.name
        try:
            subprocess.run([sys.executable, str(KIT / "tools" / "build_nav.py"), temp], check=True)
        finally:
            pathlib.Path(temp).unlink()
    files = sorted(str(p.relative_to(out)).replace("\\", "/") for p in out.rglob("*") if p.is_file())
    print("%d files in %s" % (len(files), out))
    if "--zip" in sys.argv:
        archive = shutil.make_archive(str(out), "zip", out)
        print("zipped to", archive)


if __name__ == "__main__":
    main()
