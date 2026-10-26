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
(the domain restarts). Every bot has a `bot-<id>.js`, so a wanderer can run from its own too (it must, with an account
of its own: "Giving a bot its own account" below). Speaking bots' lines are made on Windows by package.py (tools/make_voice.py). Then:

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

## Giving a bot its own account

On Darkride, a bot can sign in to an account of its own on the metaverse (vr.darkride.ai). Register it as a **bot
account**: the bots here run on scripted logic, not AI. It then has a name in the People list, and its nametag shows
that name marked as a bot and "hosted by" you, instead of "anonymous". The bot itself doesn't change; only how its
body's assignment client is started does.

1. **The account.** On vr.darkride.ai, register the bot (its username, 4-16 letters, digits or `_`, is the name
   everyone sees) and make its **connection key** (`tvk_...`). Treat the key like a password.
2. **Its own assignment client**, in a pool named after its username (spelt the same in both places, capitals
   included: the domain matches pool names exactly), with the account in its environment:
   ```
   HIFI_AC_USERNAME=<username>  HIFI_AC_KEY=<key>  HIFI_METAVERSE_URL=https://vr.darkride.ai
   assignment-client -t 2 --pool <username> -a <domain address> --server-port <domain port, 40102 by default>
   ```
   - **One process per bot, no `-n` or `--min`/`--max`**: every child would sign in as the same account.
   - Keep the key in an env file that only this process reads (Docker `--env-file`, owned by root, mode 600). Never
     put it in a script, in git, in a compose file's shared `environment:`, on a command line or in a log. It is
     still readable by root on that machine: `docker inspect` shows a container's environment, and some engine builds
     leave it in the running process's environment too, so keep root on the bots' host to yourself.
   - The pool keeps every other script off this client: without it, the domain could hand it another bot's script,
     which would then run under this bot's name.
3. **Its script.** In the domain's **Settings > Scripts > Persistent Scripts**, the bot needs exactly one row: its
   loader `bot-<id>.js` (the builder writes one for every bot on the roster; it sets `BOT_ID`), one instance, in the
   pool `<username>`. Move rows, don't add them:
   - If the bot already has a row of its own (its loader in another pool), change that row's pool to `<username>`.
     A second row would start a second copy of the same bot, and nothing settles which one stays.
   - If it ran as one of the plain `duck-bot.js` instances, add its loader row and take one instance off the
     `duck-bot.js` row. A spare `duck-bot.js` copy would keep picking a bot whose loader then sends it away.
4. **Check** the client's log: "Signing in to https://vr.darkride.ai as …", then "Signed in as …", and the brain's
   "signed in: shown as "<username>" (still <bot name>)". A refused key logs the metaverse's reason (the account
   paused by its host, and so on), and the bot then runs as before, anonymous.

What the brain does about it: nothing you need to set. Who the bot is comes from its loader (`BOT_ID`), never from the
account, so the username can be anything ("Bot_Scout" for Scout), and it's what the nametag, the People list and chat
show, not the roster's name. Visitors still call it by its roster name in voice commands ("Scout, follow me"),
because the speech service listens for the roster's names. If a plain
`duck-bot.js` copy had already taken that bot (the domain started it first), the copy gives way to the loader and
becomes another bot. While it waits for what it heard to be recognised, it sends `{busy: true}`, then `{done: true}`,
on the Messages channel `tivoli.ai-resident`, which a nametag that has a mark uses to animate it. Without an account (a
plain client in the pool, or no key) it runs exactly as before.

A bot with an account costs what it did as a child of a shared client (below): the shared client's children are
separate processes already, so moving a bot to its own client adds only that client's few MB of start-up.

## Managing them from a dashboard

`tools/manage.py` looks at the bots that have their own accounts and starts and stops them, so that a dashboard can
call it and never touch a container or a key (Python 3.8+, nothing to install). It reads a config file
(`examples/manager.json`): the accounts file (`<id> <username> [off]` per line), the roster for the bots' names, the
container's name (`resident-{id}`), and a command that makes a bot's container when there isn't one (`resident.sh
start {id}`).

```
python tools/manage.py --config manager.json status --json
python tools/manage.py --config manager.json restart|stop|start <id>
```

- `status --json`: `{"bots": [{"id", "name", "username", "enabled", "container": {"state": "running|exited|missing",
  "uptime_s", "docker"}, "signed_in"}]}`, where `docker` is docker's own word for the state (restarting, paused...).
  `signed_in` comes from the first 10 minutes of this run's log: "Signed in as" is true, a refused key false, neither
  null; a bot that isn't running is false. Without `--json`, a table.
- `stop <id>` takes a bot offline: stops its container, then marks it `off` in the accounts file (and the roster, if
  the config names one and has the bot). `start <id>` takes the `off` away and starts it again, or makes it. `restart <id>` restarts a
  bot that isn't off. Each prints one line, `{"ok": true|false, "message": "..."}`, and exits 0 when ok. If docker
  can't stop it, nothing is marked; if the marking fails afterwards, the message says what was done. `start` takes the
  `off` away first, so a start that fails leaves the bot marked on but not running: starting all the bots brings it
  back.
- A config with a key of the wrong type (a string where a list goes, a `{placeholder}` other than `{id}` and
  `{username}`) is refused with the same one JSON line before anything runs.
- The actions take turns (a lock, `.manager.lock` beside the config), so two clicks at once can't write the accounts
  file or the roster over each other.
- It never opens a key file or prints a key, and asks docker only for a container's state, never its full
  `docker inspect` (which shows the environment).
- The roster's `off` is the copy on that machine: the copy on your asset server only changes when you upload it.

On the bots' host it's laid out like this (the paths in `examples/manager.json` are relative to the config):
```
/opt/automata/tools/manage.py          the manager
/opt/automata/manager.json             its config (examples/manager.json)
/opt/automata/residents/               resident.sh, accounts, the key files, bots.json (for the names)

python3 /opt/automata/tools/manage.py --config /opt/automata/manager.json status --json
python3 /opt/automata/tools/manage.py --config /opt/automata/manager.json restart|stop|start <id>
```
The commands are only those: `status [--json]` and `restart`, `stop` or `start <id>`, where an id is lower-case
letters, digits, `_` and `-`, starting with a letter or digit. Anything else is refused before docker or ssh is
touched. (`resident.sh` itself takes only lower-case letters and digits in an id, so keep the ids to those.)

**From another machine** (a dashboard on the LAN): give the dashboard an ssh key of its own whose `authorized_keys`
line on the bots' host runs only the manager, only from the dashboard's machine:
```
from="<the dashboard's address>",restrict,command="python3 /opt/automata/tools/manage.py --config /opt/automata/manager.json --ssh" ssh-ed25519 AAAA... dashboard
```
`restrict` (OpenSSH 7.2+) turns off forwarding, the pty and anything added later. The command runs as the user whose
`authorized_keys` it is, which must be able to write the accounts file and run docker and `resident.sh start`: on
the bots' host, root's.
With `--ssh` it takes the command from `SSH_ORIGINAL_COMMAND` and allows only `status --json` and `restart`, `stop`
or `start <id>`. The dashboard's own config is `{"backend": "ssh", "ssh": {"host": "...", "user": "...", "key":
"dashboard-key"}}`, and the same command lines then run there. `python tests/manage-test.py` checks the manager with a
stand-in docker and ssh.

## What they cost

Each bot is one assignment client: about **110 MB of memory and 5% of one CPU core** (30 updates a second), plus a
little avatar-mixer traffic per visitor. Ten bots: about 1.1 GB and half a core.

## Starting fresh

If agents pile up after many restarts (each settings change restarts the domain, and dropped agents can leave their
assignment clients behind), stop the domain and its assignment clients and start them again.
