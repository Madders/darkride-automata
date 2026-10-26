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
    "backend":   "local-docker" (docker is here) or "ssh" (docker is on another host, which runs this too)
    "ssh":       for "ssh": {"host", "user", "key" (a key whose authorized_keys line forces the command),
                 "port", "options": [extra -o options], "command": how to run ssh (default ["ssh"])}

On the host, the ssh key's authorized_keys line runs this with --ssh, which takes the command from
SSH_ORIGINAL_COMMAND and allows only the four below:
    from="<the dashboard's address>",restrict,
    command="python3 /opt/automata/tools/manage.py --config /opt/automata/manager.json --ssh" ssh-ed25519 AAAA... dashboard
It runs as the user whose authorized_keys that is, which has to be one that can write the accounts file and run
docker and the create command (root, on the bots' host).

Usage: python manage.py --config <manager.json> status [--json]
       python manage.py --config <manager.json> restart|stop|start <id>
    status   each bot: id, name, username, enabled, its container's state and uptime, whether it's signed in
    restart  restarts a running or stopped bot's container (not one that's off: start it)
    stop     takes it offline: stops its container (kept, so start brings it back as it was), then marks it off
    start    brings it back: takes the off away, then starts its container (or makes it with "create"); if
             that fails, it stays marked on but isn't running, and starting all the bots brings it back
The actions print one JSON line, {"ok": bool, "message": str}, and exit 0 when ok. They take turns: each holds
.manager.lock beside the config while it runs, so two clicks at once can't write the files over each other.
"""
import argparse
import contextlib
import datetime
import json
import os
import pathlib
import re
import shlex
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
SIGN_IN_WINDOW = datetime.timedelta(minutes=10)


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


def fills_in(template, what):
    """Checks a template takes only {id} and {username}, so .format() can't fail mid-action."""
    try:
        template.format(id="x", username="x")
    except (KeyError, IndexError, ValueError, AttributeError):
        raise Failed(f"the config's {what} can fill in only {{id}} and {{username}}: {template!r}")


def check_types(config):
    """Every key the config has is the type it's used as, so a mistyped one fails here, not halfway through."""
    if not isinstance(config, dict):
        raise Failed("the config isn't a JSON object")
    for key in ("accounts", "roster", "container", "backend"):
        if key in config and not isinstance(config[key], str):
            raise Failed(f"the config's {key} isn't a string")
    for key in ("docker", "create"):
        if key in config and not is_words(config[key]):
            raise Failed(f"the config's {key} isn't a list of strings")
    if "container" in config:
        fills_in(config["container"], "container")
    for part in config.get("create", []):
        fills_in(part, "create")
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
        give_up = time.monotonic() + TIMEOUT * 3
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
                    raise Failed("another action is still running: try again in a minute")
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
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
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
    text = path.read_text(encoding="utf-8")
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


# over ssh

def over_ssh(config, words):
    """Runs the same command on the host, whose authorized_keys line runs this there with --ssh."""
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
        run = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT * 2)
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
    if words == ["status", "--json"] or len(words) == 2 and words[0] in ACTIONS and ID.fullmatch(words[1]):
        return words
    raise Failed("over ssh, only: status --json | restart|stop|start <id>")


def main(argv=None):
    parser = argparse.ArgumentParser(description="The bots' manager: status, restart, stop (take offline), start.")
    parser.add_argument("--config", required=True, help="the manager's config (JSON)")
    parser.add_argument("--ssh", action="store_true", help="take the command from SSH_ORIGINAL_COMMAND (host side)")
    parser.add_argument("command", nargs="*", help="status [--json] | restart|stop|start <id>")
    parser.add_argument("--json", action="store_true", help="status as JSON")
    args = parser.parse_args(argv)
    words = args.command + (["--json"] if args.json else [])
    as_json = True
    try:
        if args.ssh:
            words = from_ssh()
        if not words or words[0] not in ("status",) + ACTIONS:
            raise Failed("a command: status [--json] | restart|stop|start <id>")
        as_json = words[0] in ACTIONS or "--json" in words
        if words[0] in ACTIONS and len(words) != 2:
            raise Failed(f"usage: {words[0]} <id>")
        if words[0] == "status" and len(words) > 1 + ("--json" in words):
            raise Failed("usage: status [--json]")
        if words[0] in ACTIONS and not ID.fullmatch(words[1]):      # before any docker or ssh
            raise Failed("a bot id is lower-case letters, digits, _ and -, as in the roster")
        config = load_config(args.config)
        if config["backend"] == "ssh":
            out, code = over_ssh(config, ["status", "--json"] if words[0] == "status" else words)
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
