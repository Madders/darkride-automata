"""The bot manager: which bots are out, and taking one offline, bringing it back or restarting it.

Each bot that has its own account runs as its own assignment client in a container on the bots' host (see
docs/ "Giving a bot its own account"). This looks at those containers and starts and stops them, so that a dashboard
can call it and never touch containers or keys itself. It never reads, prints or passes on a bot's key: it doesn't
open the key files, and asks docker only for a container's state, never `docker inspect` in full (which shows the env).

Everything about the host is in a config file (JSON; paths in it are relative to it):
    "accounts":  the accounts file, "<id> <username> [off]" per line (required); "off" keeps a bot out
    "roster":    the bots.json the bots are built from (optional): their names, and its "off" kept in step
    "container": the container's name, "{id}" and "{username}" filled in (default "resident-{id}")
    "docker":    how to run docker (default ["docker"])
    "create":    how to make a bot's container when there isn't one, "{id}" and "{username}" filled in (optional),
                 e.g. ["sh", "residents/resident.sh", "start", "{id}"]
    "build":     how to rebuild one bot's files from the roster, "{id}" filled in (optional; without it, apply is
                 refused), e.g. [".venv/bin/python", "tools/build_bots.py", "--roster", "residents/bots.json",
                 "--out", "site/Duck", "{id}"]; run in the config's folder
    "push":      how to send that bot's rebuilt files (and the roster's copy, bots.json) to the asset server, "{id}"
                 filled in (optional: leave it out when the build's folder is what the asset server serves)
    "build_timeout": seconds the build and the push may take, each (default 150). An apply can take up to
                 apply_timeout(): its turn, the build and the push, both again if it puts them back, and the
                 restart; with 150 that's 740 s here, 800 s through the ssh backend. A dashboard's timeout for an
                 apply has to be longer than that, or it gives up while the manager is still working
    "backend":   "local-docker" (docker is here) or "ssh" (docker is on another host, which runs this too)
    "ssh":       for "ssh": {"host", "user", "key" (a key whose authorized_keys line forces the command),
                 "port", "options": [extra -o options], "command": how to run ssh (default ["ssh"])}

On the host, the ssh key's authorized_keys line runs this with --ssh, which takes the command from
SSH_ORIGINAL_COMMAND and allows only the commands below:
    from="<the dashboard's address>",restrict,
    command="python3 /opt/automata/tools/manage.py --config /opt/automata/manager.json --ssh" ssh-ed25519 AAAA... dashboard
It runs as the user whose authorized_keys that is, which has to be one that can write the accounts file and run
docker and the create command (root, on the bots' host).

Usage: python manage.py --config <manager.json> status [--json]
       python manage.py --config <manager.json> restart|stop|start <id>
       python manage.py --config <manager.json> settings <id>
       python manage.py --config <manager.json> apply <id> [--dry-run]   < {"base": "...", "set": {...}}
    status   each bot: id, name, username, enabled, its container's state and uptime, whether it's signed in
    restart  restarts a running or stopped bot's container (not one that's off: start it)
    stop     takes it offline: stops its container (kept, so start brings it back as it was), then marks it off
    start    brings it back: takes the off away, then starts its container (or makes it with "create"); if
             that fails, it stays marked on but isn't running, and starting all the bots brings it back
    settings the settings a dashboard may change (SETTINGS below) as the roster has them, the roster's area names,
             and "base", a fingerprint of the bot's roster entry
    apply    changes those settings: reads {"base", "set": {"<setting>": value or null, ...}} on stdin (null takes
             a setting away, back to its default), refuses if the entry no longer matches "base" (changed since it
             was read), checks every value, and answers with the "diff". Without --dry-run it then writes the
             bot's roster entry, runs "build" and "push", and restarts the bot if it's running. If the build or
             push fails, the roster is put back and built again, so the files match it.
The actions print one JSON line, {"ok": bool, "message": str, ...}, and exit 0 when ok. They take turns: each holds
.manager.lock beside the config while it runs, so two clicks at once can't write the files over each other. One
that finds another running waits up to LOCK_WAIT seconds for it, then gives up and says so: a dashboard's action
timeout has to be longer than that, so that an action it has given up on can't still happen later (over ssh,
killing the client doesn't stop the command on the host). An apply --dry-run only reads, so it doesn't wait.
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:                     # Windows, where the tests run
    fcntl = None
    import msvcrt

ID = re.compile(r"[a-z0-9][a-z0-9_-]*")  # fullmatch: an id never starts with "-", so it can't pass for an option
ACTIONS = ("restart", "stop", "start")
TIMEOUT = 60                            # seconds for any one docker or ssh call
LOCK_WAIT = 20                          # seconds an action waits for another one to finish (a dashboard allows 60)
BUILD_TIMEOUT = 150                     # seconds for the build, and for the push (config "build_timeout")
SIGN_IN_WINDOW = datetime.timedelta(minutes=10)
MAX_INPUT = 64 * 1024                   # apply's stdin

# The settings a dashboard may change, "style.<x>" inside the bot's "style": (kind, what it takes). Everything else
# in a bot's entry (its id, name, role, voice, lines, post...) is left as it is and never sent out by "settings".
SETTINGS = {
    "size": ("number", (0.25, 4.0)),
    "shell": ("colour", None),
    "accent": ("colour", None),
    "paint2": ("colour", None),
    "glow": ("number", (0.0, 1.0)),
    "metal": ("number", (0.0, 1.0)),
    "wear": ("number", (0.0, 1.0)),
    "obedience": ("number", (0.0, 1.0)),
    "rockets": ("bool", None),
    "home": ("area", None),
    "style.head": ("choice", ("loaf", "dome", "wedge")),
    "style.eyes": ("choice", ("lens", "visor", "twin")),
    "style.antennas": ("choice", (0, 1, 2)),
    "style.jaw": ("bool", None),
    "style.fins": ("bool", None),
    "style.crest": ("bool", None),
    "style.chest": ("bool", None),
}
# settings a bot can't do without: these can be changed but not taken away
NEEDED = ("shell", "accent")


class Failed(Exception):
    pass


def secret_free(text):
    """Belt and braces: whatever goes out, a key-shaped word in it doesn't."""
    return re.sub(r"tvk_\S*", "tvk_...", text)


def said(e):
    """An error's words without the host's paths in them: a file is named by its base name only."""
    if isinstance(e, OSError) and e.strerror:
        return e.strerror + (f": {pathlib.Path(e.filename).name}" if e.filename else "")
    return str(e)


def is_words(value):
    return isinstance(value, list) and value and all(isinstance(w, str) for w in value)


def fills_in(template, what, username=True):
    """Checks a template takes only {id} and {username} (or only {id}), so .format() can't fail mid-action."""
    try:
        template.format(id="x", **({"username": "x"} if username else {}))
    except (KeyError, IndexError, ValueError, AttributeError):
        names = "{id} and {username}" if username else "{id}"
        raise Failed(f"the config's {what} can fill in only {names}: {template!r}")


def check_types(config):
    """Every key the config has is the type it's used as, so a mistyped one fails here, not halfway through."""
    if not isinstance(config, dict):
        raise Failed("the config isn't a JSON object")
    for key in ("accounts", "roster", "container", "backend"):
        if key in config and not isinstance(config[key], str):
            raise Failed(f"the config's {key} isn't a string")
    for key in ("docker", "create", "build", "push"):
        if key in config and not is_words(config[key]):
            raise Failed(f"the config's {key} isn't a list of strings")
    if "container" in config:
        fills_in(config["container"], "container")
    for part in config.get("create", []):
        fills_in(part, "create")
    for key in ("build", "push"):
        for part in config.get(key, []):
            fills_in(part, key, username=False)
    if "build_timeout" in config and (isinstance(config["build_timeout"], bool)
                                      or not isinstance(config["build_timeout"], int) or config["build_timeout"] < 1):
        raise Failed("the config's build_timeout isn't a whole number of seconds")
    s = config.get("ssh", {})
    if not isinstance(s, dict):
        raise Failed("the config's ssh isn't an object")
    for key in ("host", "user", "key"):
        if key in s and not isinstance(s[key], str):
            raise Failed(f"the config's ssh {key} isn't a string")
    for key in ("port", "connect_timeout"):
        if key in s and (isinstance(s[key], bool) or not isinstance(s[key], (int, str))):
            raise Failed(f"the config's ssh {key} isn't a number")
    for key in ("command", "options"):
        if key in s and not (is_words(s[key]) or key == "options" and s[key] == []):
            raise Failed(f"the config's ssh {key} isn't a list of strings")


def load_config(path):
    path = pathlib.Path(path).resolve()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Failed(f"can't read the config {path.name}: {said(e)}")
    check_types(config)
    here = path.parent
    for key in ("accounts", "roster"):
        if config.get(key):
            config[key] = here / config[key]
    if config.get("ssh", {}).get("key"):
        config["ssh"]["key"] = here / config["ssh"]["key"]
    config.setdefault("backend", "local-docker")
    config.setdefault("container", "resident-{id}")
    config.setdefault("docker", ["docker"])
    config["here"] = here
    if config["backend"] not in ("local-docker", "ssh"):
        raise Failed(f"backend {config['backend']!r} isn't local-docker or ssh")
    if config["backend"] == "local-docker" and not config.get("accounts"):
        raise Failed("the config has no accounts file")
    if config["backend"] == "ssh" and not config.get("ssh", {}).get("host"):
        raise Failed("the config's ssh has no host")
    return config


# the accounts file and the roster

ACCOUNT_LINE = re.compile(r"^(\s*)([a-z0-9_-]+)(\s+)([A-Za-z0-9_]+)(\s+off)?(\s*(#.*)?)$")


def accounts(config):
    """[(id, username, off)] in the file's order."""
    try:
        text = config["accounts"].read_text(encoding="utf-8")
    except OSError as e:
        raise Failed(f"can't read the accounts file: {said(e)}")
    bots = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.split("#", 1)[0].strip():
            continue
        m = ACCOUNT_LINE.match(line)
        if not m:
            raise Failed(f"{config['accounts'].name} line {n} isn't \"<id> <username> [off]\"")
        bots.append((m.group(2), m.group(4), bool(m.group(5))))
    return bots


def exact_text(path):
    """A file's text with its line endings as they are (CRLF stays CRLF), so writing it back changes only what
    was meant to change."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_over(path, text):
    """Replaces a file's text in one step, keeping its permissions."""
    handle, new = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(text.encode("utf-8"))
        os.chmod(new, os.stat(path).st_mode & 0o7777)
        os.replace(new, path)
    except OSError:
        pathlib.Path(new).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def taking_turns(config):
    """Holds .manager.lock beside the config for the length of an action, waiting for one that's running."""
    with open(config["here"] / ".manager.lock", "a+b") as f:
        give_up = time.monotonic() + LOCK_WAIT
        while True:
            try:
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > give_up:
                    raise Failed("another action is still running (a build can take minutes): try again soon")
                time.sleep(0.05)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def set_off_in_accounts(config, bot_id, off):
    path = config["accounts"]
    lines = exact_text(path).splitlines(keepends=True)
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        m = ACCOUNT_LINE.match(body)
        if not m or m.group(2) != bot_id:
            continue
        if bool(m.group(5)) == off:
            return False
        flag = " off" if off else ""
        lines[i] = m.group(1) + m.group(2) + m.group(3) + m.group(4) + flag + m.group(6) + line[len(body):]
        write_over(path, "".join(lines))
        return True
    raise Failed(f"{bot_id} isn't in {path.name}")


def roster_bots(config):
    """{id: bot} from the roster, or {} without one."""
    path = config.get("roster")
    if not path or not path.exists():
        return {}
    try:
        return {b["id"]: b for b in json.loads(path.read_text(encoding="utf-8")).get("bots", []) if "id" in b}
    except (OSError, ValueError) as e:
        raise Failed(f"can't read the roster: {said(e)}")


def set_off_in_roster(config, bot_id, off):
    """Changes only the bot's own "off" in its line, so the hand-kept layout stays as it is. A bot the roster
    doesn't have is left alone."""
    path = config.get("roster")
    if not path or not path.exists():
        return False
    text = exact_text(path)
    bots = roster_bots(config)
    if bot_id not in bots or bool(bots[bot_id].get("off")) == off:
        return False
    head = re.compile(r'(\{\s*"id"\s*:\s*"' + re.escape(bot_id) + r'"\s*,\s*)')
    if off:
        new, n = head.subn(lambda m: m.group(1) + '"off": true, ', text, count=1)
    else:
        line = re.compile(r'^.*"id"\s*:\s*"' + re.escape(bot_id) + r'".*$', re.M)
        new, n = line.subn(lambda m: re.sub(r'"off"\s*:\s*true\s*,\s*|,\s*"off"\s*:\s*true', "", m.group(0), count=1),
                           text, count=1)
    try:
        ok = n == 1 and bool(next(b for b in json.loads(new)["bots"] if b.get("id") == bot_id).get("off")) == off
    except (ValueError, KeyError, StopIteration):
        ok = False
    if not ok:
        raise Failed(f"couldn't change {bot_id}'s off in {path.name} (its line isn't laid out as expected)")
    write_over(path, new)
    return True


# docker

def docker(config, *args, check=True):
    try:
        run = subprocess.run(config["docker"] + list(args), capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Failed(f"docker {args[0]}: {e}")
    if check and run.returncode:
        said = (run.stderr or run.stdout).strip().splitlines()
        raise Failed(f"docker {args[0]}: " + secret_free(said[-1] if said else f"exit {run.returncode}"))
    return run


def container_name(config, bot_id, username):
    return config["container"].format(id=bot_id, username=username)


def parse_time(stamp):
    """Docker's RFC 3339 time, nanoseconds and all."""
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)$", stamp.strip())
    if not m:
        return None
    frac = ((m.group(2) or ".") + "000000")[:7]    # before 3.11, fromisoformat takes only 3 or 6 digits
    zone = "+00:00" if m.group(3) == "Z" else m.group(3)
    return datetime.datetime.fromisoformat(m.group(1) + frac + zone)


def containers(config, names):
    """{name: (docker state, started at)} for those of the names that exist."""
    have = set(docker(config, "ps", "-a", "--format", "{{.Names}}").stdout.split())
    found = [n for n in names if n in have]
    if not found:
        return {}
    run = docker(config, "inspect", "--format", "{{.Name}}\t{{.State.Status}}\t{{.State.StartedAt}}", *found)
    states = {}
    for line in run.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            states[parts[0].lstrip("/")] = (parts[1], parse_time(parts[2]))
    return states


def signed_in(config, name, username, started):
    """True after "Signed in as <username>" in this run's log, False after a refused key, None before either.
    Only the run's first minutes are read: it signs in at the start, and a bot's log grows all day."""
    window = ["--since", started.isoformat(), "--until", (started + SIGN_IN_WINDOW).isoformat()] if started else []
    args = ["logs"] + window + [name]
    run = docker(config, *args, check=False)
    if run.returncode:
        return None
    answer = None
    me = re.compile(r"Signed in as\b.*(?<![A-Za-z0-9_])" + re.escape(username) + r"(?![A-Za-z0-9_])")
    for line in (run.stdout + run.stderr).splitlines():   # the lines are only looked at, never passed on
        if me.search(line):
            answer = True
        elif "The connection key was refused" in line:
            answer = False
    return answer


# the commands

def status(config):
    names = roster_bots(config)
    bots = accounts(config)
    states = containers(config, [container_name(config, i, u) for i, u, _ in bots])
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for bot_id, username, off in bots:
        name = container_name(config, bot_id, username)
        raw, started = states.get(name, (None, None))
        state = "missing" if raw is None else "running" if raw == "running" else "exited"
        uptime = int((now - started).total_seconds()) if state == "running" and started else None
        out.append({
            "id": bot_id,
            "name": names.get(bot_id, {}).get("name", bot_id),
            "username": username,
            "enabled": not off,
            "container": {"state": state, "uptime_s": uptime, "docker": raw},
            "signed_in": signed_in(config, name, username, started) if state == "running" else False,
        })
    return {"bots": out}


def find(config, bot_id):
    if not ID.fullmatch(bot_id or ""):
        raise Failed("a bot id is lower-case letters, digits, _ and -, as in the roster")
    for i, username, off in accounts(config):
        if i == bot_id:
            name = container_name(config, i, username)
            return username, off, name, containers(config, [name]).get(name, (None, None))[0]
    raise Failed(f"{bot_id} isn't in {config['accounts'].name}")


def restart(config, bot_id):
    username, off, name, state = find(config, bot_id)
    if off:
        raise Failed(f"{bot_id} is off: start it to bring it back")
    if state is None:
        raise Failed(f"{bot_id} has no container ({name}): start it")
    docker(config, "restart", name)
    return f"{bot_id}: restarted"


def stop(config, bot_id):
    """Stops the container first: if that fails nothing has changed, and if the marking fails it says so."""
    username, off, name, state = find(config, bot_id)
    running = state in ("running", "restarting", "paused")
    if running:
        try:
            docker(config, "stop", name)
        except Failed as e:
            raise Failed(f"{bot_id}: couldn't stop it, so it isn't marked off either ({e})")
    did = "stopped" if running else "wasn't running"
    try:
        marked = set_off_in_accounts(config, bot_id, True)
    except (Failed, OSError) as e:
        raise Failed(f"{bot_id}: {did}, but couldn't mark it off, so starting all the bots brings it back: {said(e)}")
    try:
        set_off_in_roster(config, bot_id, True)
    except (Failed, OSError) as e:
        raise Failed(f"{bot_id}: {did} and marked off in {config['accounts'].name}, but not in the roster: {said(e)}")
    return f"{bot_id}: {did}, " + ("marked off" if marked else "already off")


def start(config, bot_id):
    username, off, name, state = find(config, bot_id)
    set_off_in_accounts(config, bot_id, False)
    set_off_in_roster(config, bot_id, False)
    if state == "running":
        return f"{bot_id}: already running" + (", off taken away" if off else "")
    if state is not None:
        try:
            docker(config, "start", name)
        except Failed as e:
            raise Failed(f"{bot_id}: off taken away, but couldn't start it ({e})")
        return f"{bot_id}: started"
    if not config.get("create"):
        raise Failed(f"{bot_id} has no container ({name}) and the config has no create command")
    command = [part.format(id=bot_id, username=username) for part in config["create"]]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT, cwd=config["here"])
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Failed(f"{bot_id}: couldn't make its container: {e}")
    lines = (run.stdout + run.stderr).strip().splitlines()
    said = secret_free(lines[-1 if run.returncode else 0]) if lines else ""     # its error, or what it did
    if run.returncode:
        raise Failed(f"{bot_id}: couldn't make its container: {said or f'exit {run.returncode}'}")
    return f"{bot_id}: container made and started" + (f" ({said})" if said else "")


def act(config, action, bot_id):
    with taking_turns(config):
        return {"restart": restart, "stop": stop, "start": start}[action](config, bot_id)


# a bot's settings

def read_roster(config):
    """(path, text, doc): the roster as it is on disk."""
    path = config.get("roster")
    if not path:
        raise Failed("the config has no roster, so there are no settings to change")
    try:
        text = exact_text(path)
        doc = json.loads(text)
    except (OSError, ValueError) as e:
        raise Failed(f"can't read the roster: {said(e)}")
    if not isinstance(doc, dict) or not isinstance(doc.get("bots"), list):
        raise Failed(f"{path.name} has no \"bots\" list")
    return path, text, doc


def find_entry(path, text, doc, bot_id):
    """(start, end, entry): where the bot's object is in the roster's text, so that only its own characters change
    and the rest of the hand-kept layout stays as it is."""
    if not ID.fullmatch(bot_id or ""):
        raise Failed("a bot id is lower-case letters, digits, _ and -, as in the roster")
    same = [b for b in doc["bots"] if isinstance(b, dict) and b.get("id") == bot_id]
    if not same:
        raise Failed(f"{bot_id} isn't in {path.name}")
    if len(same) > 1:
        raise Failed(f"{bot_id} is in {path.name} twice")
    decoder = json.JSONDecoder()
    spans = []
    for m in re.finditer(r'\{\s*"id"\s*:\s*"' + re.escape(bot_id) + r'"', text):
        try:
            entry, end = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        if entry == same[0]:
            spans.append((m.start(), end, entry))
    if len(spans) != 1:
        raise Failed(f"{bot_id}'s entry in {path.name} isn't laid out as expected (its \"id\" first)")
    return spans[0]


def fingerprint(entry):
    """Changes whenever anything in the bot's entry does, so an apply can tell its settings were read from this."""
    return hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def setting(entry, key):
    if key.startswith("style."):
        style = entry.get("style")
        return style.get(key[6:]) if isinstance(style, dict) else None
    return entry.get(key)


def checked(key, value, areas):
    """The value as it goes into the roster, or Failed saying what the setting takes."""
    kind, takes = SETTINGS[key]
    if value is None:
        if key in NEEDED:
            raise Failed(f"{key} can be changed but not taken away")
        return None

    def number(v):
        return not isinstance(v, bool) and isinstance(v, (int, float)) and v == v      # v == v: not NaN

    if kind == "number":
        if not (number(value) and takes[0] <= value <= takes[1]):
            raise Failed(f"{key} is a number from {takes[0]:g} to {takes[1]:g}")
        return value if isinstance(value, int) else round(value, 3)
    if kind == "colour":
        if not (isinstance(value, list) and len(value) == 3 and all(number(c) and 0 <= c <= 1 for c in value)):
            raise Failed(f"{key} is a colour: three numbers from 0 to 1")
        return [c if isinstance(c, int) else round(c, 3) for c in value]
    if kind == "bool":
        if not isinstance(value, bool):
            raise Failed(f"{key} is true or false")
        return value
    if kind == "area":
        if not (isinstance(value, str) and value in areas):
            raise Failed(f"{key} is one of the roster's areas ({', '.join(areas) or 'it has none'})")
        return value
    if not (type(value) is type(takes[0]) and value in takes):   # type: True isn't the antenna count 1
        raise Failed(f"{key} is one of {', '.join(str(t) for t in takes)}")
    return value


def read_request(text):
    try:
        request = json.loads(text)
    except ValueError:
        request = None
    if not (isinstance(request, dict) and isinstance(request.get("base"), str) and isinstance(request.get("set"), dict)):
        raise Failed('apply reads {"base": "...", "set": {"<setting>": value, ...}} as JSON on stdin')
    unknown = sorted(str(k) for k in request["set"] if k not in SETTINGS)
    if unknown:
        listed = ", ".join(unknown)
        raise Failed("not a setting that can be changed here: " + (listed if len(listed) < 120 else listed[:120] + "..."))
    return request["base"], request["set"]


def changed(entry, changes):
    """A copy of the entry with the changes made: new keys go at the end, the rest keep their order."""
    new = json.loads(json.dumps(entry))
    for key, value in changes.items():
        where, name = new, key
        if key.startswith("style."):
            if "style" in new and not isinstance(new["style"], dict):
                raise Failed("its style isn't an object, so it can't be changed here")
            where, name = new.setdefault("style", {}), key[6:]
        if value is None:
            where.pop(name, None)
        else:
            where[name] = value
    if new.get("style") == {} and "style" not in entry:
        del new["style"]
    return new


def differences(old, new):
    return [{"setting": key, "from": setting(old, key), "to": setting(new, key)}
            for key in SETTINGS if setting(old, key) != setting(new, key)]


def describe(diff):
    def shown(v):
        return "(default)" if v is None else json.dumps(v)
    return ", ".join(f"{d['setting']} {shown(d['from'])} -> {shown(d['to'])}" for d in diff)


def apply_timeout(build_timeout):
    """The longest an apply can take: its turn, the build and the push, both again if it has to put them back,
    then looking at the bot's container and restarting it."""
    return LOCK_WAIT + 4 * build_timeout + 2 * TIMEOUT


def stop_all(run):
    """Kills a step that ran out of time and everything it started (the rsync under a push script, say), so none
    of it carries on: the step runs in its own session, so on POSIX its whole process group goes."""
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.killpg(run.pid, signal.SIGKILL)
    run.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        run.communicate(timeout=10)


def run_step(config, key, bot_id):
    """Runs the config's build or push for one bot, in the config's folder."""
    command = [part.format(id=bot_id) for part in config[key]]
    timeout = config.get("build_timeout", BUILD_TIMEOUT)
    try:
        run = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", cwd=config["here"],
                               start_new_session=os.name == "posix")
    except OSError as e:
        raise Failed(f"couldn't run the {key}: {said(e)}")
    try:
        stdout, stderr = run.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_all(run)
        raise Failed(f"the {key} took longer than {timeout} s")
    if run.returncode:
        lines = (stderr or stdout).strip().splitlines()
        raise Failed(f"the {key} failed: " + secret_free(lines[-1] if lines else f"exit {run.returncode}"))


def settings(config, bot_id):
    path, text, doc = read_roster(config)
    _, _, entry = find_entry(path, text, doc, bot_id)
    areas = list(doc.get("areas") or {}) if isinstance(doc.get("areas"), dict) else []
    return {"ok": True, "message": f"{bot_id}: its settings", "name": entry.get("name", bot_id),
            "settings": {key: setting(entry, key) for key in SETTINGS}, "areas": areas, "base": fingerprint(entry)}


def apply(config, bot_id, request, dry_run):
    base, changes = read_request(request)
    with contextlib.nullcontext() if dry_run else taking_turns(config):    # a dry run only reads
        path, text, doc = read_roster(config)
        start_at, end, entry = find_entry(path, text, doc, bot_id)
        if fingerprint(entry) != base:
            raise Failed(f"{bot_id}'s roster entry has changed since its settings were read: open them again")
        areas = list(doc["areas"]) if isinstance(doc.get("areas"), dict) else []
        new = changed(entry, {key: checked(key, value, areas) for key, value in changes.items()})
        diff = differences(entry, new)
        result = {"ok": True, "diff": diff, "base": base}
        if not diff:
            return {**result, "message": f"{bot_id}: nothing to change"}
        if dry_run:
            return {**result, "message": f"{bot_id}: would change {describe(diff)}"}
        if not config.get("build"):
            raise Failed("this host's config has no build command, so settings can't be applied here")

        new_text = text[:start_at] + json.dumps(new, ensure_ascii=False) + text[end:]
        expected = dict(doc, bots=[new if b == entry else b for b in doc["bots"]])
        try:
            same = json.loads(new_text) == expected
        except ValueError:
            same = False
        if not same:
            raise Failed(f"couldn't change {bot_id}'s entry in {path.name} without changing something else")
        write_over(path, new_text)
        steps = ["build"] + (["push"] if config.get("push") else [])
        for done, step in enumerate(steps):
            try:
                run_step(config, step, bot_id)
            except Failed as e:
                write_over(path, text)
                undo = []
                for again in steps[:done + 1]:          # the files as they were, here and on the asset server
                    try:
                        run_step(config, again, bot_id)
                        undo.append(f"{'built' if again == 'build' else 'pushed'} again as it was")
                    except Failed as e2:
                        undo.append(f"{e2} too, so its files may not match the roster: run the {again} by hand")
                        break
                raise Failed(f"{bot_id}: {e}; the roster is back as it was, " + ", ".join(undo))
        what = f"{bot_id}: {describe(diff)}; built" + (", pushed" if "push" in steps else "")
        result["base"] = fingerprint(new)

        account = next((a for a in accounts(config) if a[0] == bot_id), None) if config.get("accounts") else None
        if not account or account[2]:
            return {**result, "message": what + ("; it's off, so it shows when it's brought back" if account
                                                 else "; it has no account here, so nothing was restarted")}
        name = container_name(config, bot_id, account[1])
        if containers(config, [name]).get(name, (None,))[0] != "running":
            return {**result, "message": what + "; it isn't running, so it shows when it next starts"}
        try:
            docker(config, "restart", name)
        except Failed as e:
            raise Failed(f"{what}, but couldn't restart it ({e}): restart it to see the change")
        return {**result, "message": what + ", restarted"}


# over ssh

def over_ssh(config, words, stdin=None):
    """Runs the same command on the host, whose authorized_keys line runs this there with --ssh; apply's request
    goes on ssh's stdin."""
    s = config["ssh"]
    command = s.get("command", ["ssh"])
    command += ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={s.get('connect_timeout', 10)}"]
    if s.get("key"):
        command += ["-i", str(s["key"]), "-o", "IdentitiesOnly=yes"]
    if s.get("port"):
        command += ["-p", str(s["port"])]
    for option in s.get("options", []):
        command += ["-o", option]
    command += [(f"{s['user']}@" if s.get("user") else "") + s["host"], " ".join(words)]
    try:
        run = subprocess.run(command, capture_output=True, text=True, input=stdin or "",
                             timeout=TIMEOUT + (apply_timeout(config.get("build_timeout", BUILD_TIMEOUT))
                                                if words[0] == "apply" and "--dry-run" not in words else TIMEOUT))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Failed(f"ssh: {e}")
    out = run.stdout.strip()
    try:
        json.loads(out.splitlines()[-1] if words[0] != "status" else out)
    except (ValueError, IndexError):
        err = (run.stderr.strip().splitlines() or [f"exit {run.returncode}"])[-1]
        raise Failed("ssh: " + secret_free(err))
    return out, run.returncode


def from_ssh():
    """The words of the command an ssh client asked for, if they're one this allows."""
    try:
        words = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    except ValueError:                  # an unclosed quote, say
        words = []
    one_bot = len(words) == 2 and words[0] in ACTIONS + ("settings", "apply") and ID.fullmatch(words[1])
    if words == ["status", "--json"] or one_bot or words[2:] == ["--dry-run"] and words[0] == "apply" and \
            ID.fullmatch(words[1]):
        return words
    raise Failed("over ssh, only: " + USAGE.replace("status [--json]", "status --json"))


USAGE = "status [--json] | restart|stop|start <id> | settings <id> | apply <id> [--dry-run]"


def main(argv=None):
    parser = argparse.ArgumentParser(description="The bots' manager: status, restart, stop (take offline), start, "
                                                 "and a bot's settings.")
    parser.add_argument("--config", required=True, help="the manager's config (JSON)")
    parser.add_argument("--ssh", action="store_true", help="take the command from SSH_ORIGINAL_COMMAND (host side)")
    parser.add_argument("command", nargs="*", help=USAGE)
    parser.add_argument("--json", action="store_true", help="status as JSON")
    parser.add_argument("--dry-run", action="store_true", help="apply: only check the change and show the diff")
    args = parser.parse_args(argv)
    words = args.command + (["--json"] if args.json else []) + (["--dry-run"] if args.dry_run else [])
    as_json = True
    try:
        if args.ssh:
            words = from_ssh()
        if not words or words[0] not in ("status", "settings", "apply") + ACTIONS:
            raise Failed("a command: " + USAGE)
        as_json = words[0] != "status" or "--json" in words
        if words[0] in ACTIONS + ("settings",) and len(words) != 2:
            raise Failed(f"usage: {words[0]} <id>")
        if words[0] == "apply" and not (len(words) == 2 or len(words) == 3 and words[2] == "--dry-run"):
            raise Failed("usage: apply <id> [--dry-run], with the request on stdin")
        if words[0] == "status" and len(words) > 1 + ("--json" in words):
            raise Failed("usage: status [--json]")
        if words[0] != "status" and not ID.fullmatch(words[1]):      # before any docker or ssh
            raise Failed("a bot id is lower-case letters, digits, _ and -, as in the roster")
        request = None
        if words[0] == "apply":
            request = sys.stdin.read(MAX_INPUT + 1)
            if len(request) > MAX_INPUT:
                raise Failed(f"apply's request is longer than {MAX_INPUT} characters")
        config = load_config(args.config)
        if config["backend"] == "ssh":
            out, code = over_ssh(config, ["status", "--json"] if words[0] == "status" else words, request)
            if words[0] == "status" and not as_json:
                print_table(json.loads(out))
            else:
                print(out)
            return code
        if words[0] == "status":
            result = status(config)
            if as_json:
                print(json.dumps(result))
            else:
                print_table(result)
            return 0
        if words[0] == "settings":
            print(json.dumps(settings(config, words[1])))
        elif words[0] == "apply":
            print(json.dumps(apply(config, words[1], request, words[2:] == ["--dry-run"])))
        else:
            print(json.dumps({"ok": True, "message": act(config, words[0], words[1])}))
        return 0
    except (Failed, OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as e:
        # a file it can't write, or something the checks missed: still one JSON line
        message = said(e) if isinstance(e, (Failed, OSError, ValueError)) else f"{type(e).__name__}: {e}"
        if as_json:
            print(json.dumps({"ok": False, "message": secret_free(message)}))
        else:
            print(secret_free(message), file=sys.stderr)
        return 1


def print_table(result):
    if "bots" not in result:
        print(result.get("message", result), file=sys.stderr)
        return
    for b in result["bots"]:
        c = b["container"]
        up = f" {c['uptime_s'] // 3600}h{c['uptime_s'] % 3600 // 60:02d}m" if c["uptime_s"] is not None else ""
        signed = {True: "signed in", False: "not signed in", None: "sign-in unknown"}[b["signed_in"]]
        print(f"{b['id']:<8} {b['name']:<10} {b['username']:<16} {'on ' if b['enabled'] else 'off'} "
              f"{c['state']}{up}, {signed}")


if __name__ == "__main__":
    sys.exit(main())
