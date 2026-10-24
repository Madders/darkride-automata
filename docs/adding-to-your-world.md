# Adding the bots to your world

## 1. Your world's entities

The map builder reads your world's entities: the `models.json.gz` your domain server saves (in its
`entities/` folder, or in a content backup from the domain's admin page), or a `models.json` you've exported. Copy it
somewhere next to your world config.

It also needs your models (glTF/GLB, OBJ, STL; FBX through FBX2glTF). For each model URL it tries, in order:
- a local folder you map the URL's start to (`assets`): fastest, and works offline;
- downloading it (`download`, on by default) into a cache;
and leaves out any model it can't load (it says so). Boxes, shapes and flat images (used as floors) need no files.

## 2. The world config

Copy `examples/world.json` and set:

| field | what |
|---|---|
| `entities` | your models.json(.gz) |
| `assets` | URL starts mapped to local folders: `{"https://host/": "local/folder/"}` |
| `download` | fetch models it has no local copy of (default true) |
| `fbx2gltf` | the FBX2glTF executable, if you have FBX models (`npm install fbx2gltf` has one) |
| `cache` | where downloads and conversions go |
| `region` | the box to map, `[min, max]` on each axis: the walkable part of your world, not the whole skybox |
| `skip` | entity name starts to leave out (sky domes, city backdrops, invisible helpers) |
| `bot` | the bot's sizes: `cell` (grid, 0.25 m), `headroom` (1.15 m), `step` (0.32 m: the highest step it takes; your stairs' rise must be lower), `drop` (0.6 m: what counts as a drop) |
| `pois` | rules for points of interest: `{"kind", "type": entity type, "match": [text in its model/image URL or name]}`; the first rule that fits names it |
| `out` | where to write nav.json (package.py overrides it) |

Then `python tools/build_nav.py my-world.json` prints what it found: the walkable surfaces, the connected areas (it
keeps the largest), the edges and walls, the rocket hops and where they are. If the bots should reach somewhere the
map leaves out, it's usually a gap a bot can't step (a threshold higher than `step`), glass or a railing in the way,
or a ledge narrower than 75 cm.

**After changing your world** (moving furniture, adding models), rebuild the map and reupload nav.json; the bots load
it when they start, so restart them.

## 3. The roster

Copy `examples/bots.json` and design your crew (docs/designing-bots.md). Set the areas (home areas, and `hatch`
where they start) in your world's coordinates: walk there in Interface and read your position.

## 4. Package and upload

```
python tools/package.py --roster my-bots.json --world my-world.json --out dist/bot --zip
```

Upload the contents of `dist/bot` so they sit together at one URL (an ATP folder works too). Every bot file is
referenced relative to `duck-bot.js`.

## 5. The domain

In the domain's admin pages, **Content > Scripts > Persistent Scripts**: add the URL of `duck-bot.js`, with **one
instance per wandering bot**, and for each bot with a role (a bouncer, a guide) its own `bot-<id>.js` with one instance; save
(the domain restarts). Speaking bots' lines are made on Windows by package.py (tools/make_voice.py). Then:

- **Assignment clients**: each bot needs one of its own, beside the mixers. Start the domain's assignment-client
  monitor with room: `assignment-client --min 6 --max 20` (or `-n`), or run extra agent-only clients
  (`assignment-client -t 2 -n 6 -a <domain address>`).
- **Entity rights**: bots with rockets create a short-lived particle entity for their flame, so agents need rez rights.
  Agents on the same machine as the domain have them as localhost; agents elsewhere need the domain's permissions to
  allow it (or leave rockets off).
- **Avatar view radius**: on Darkride, the brain sets `Agent.avatarViewRadius` so a bot sees and hears avatars all
  round it, not only ahead. Engines without it give bots a narrower view: they'll still wander, but notice less.

## Voice commands (optional)

For spoken commands, run the speech service on the machine the bots run on, and put its URL in the roster's
`hearing`:

```
pip install vosk
# the small English model (40 MB) from https://alphacephei.com/vosk/models, unzipped into .models/
python tools/hearing.py --roster my-world/bots.json
```

It listens on 127.0.0.1:40195 (`--port`, `--host`), answers only the bots, and needs about 150 MB of memory. `--dump
last.wav` keeps the last clip it was sent, to listen to when it mishears. It needs Darkride's agent hearing
(`Agent.takeHeardAudio`); on other engines bots just won't take commands.

## Running the bots from another machine

The bots needn't run on your domain's server: any machine with the server build can run their assignment clients and
join the domain over the internet (its connections all go out, so no ports to open at home). On the domain, add that
machine's public IP to **Security > Assignment Client IP address Whitelist**, and give the persistent script a pool,
say `bots`; on the machine, run `assignment-client -t 2 --pool bots -a <domain address> -n 10`. If that machine or
its internet goes down, the bots leave; and with a changing IP, the whitelist needs updating.

## What they cost

Each bot is one assignment client: about **110 MB of memory and 5% of one CPU core** (30 updates a second), plus a
little avatar-mixer traffic per visitor. Ten bots: about 1.1 GB and half a core.

## Starting fresh

If agents pile up after many restarts (each settings change restarts the domain, and dropped agents can leave their
assignment clients behind), stop the domain and its assignment clients and start them again.
