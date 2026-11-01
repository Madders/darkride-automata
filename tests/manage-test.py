"""tools/manage.py against a stand-in docker (and ssh): no containers, no keys, no host needed.

Runs status, restart, stop and start on a copy of an accounts file and roster in a temporary folder, with a fake
docker that keeps its containers in a JSON file and logs every call, then checks the JSON that comes out, what the
files say afterwards, what docker was asked, and that a key beside the accounts never shows up anywhere.

Usage: python tests/manage-test.py
"""
import datetime
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

MANAGE = pathlib.Path(__file__).resolve().parent.parent / "tools" / "manage.py"
KEY = "tvk_s3cretS3cretS3cret"

# docker, from a JSON file of {name: {"status", "started", "log"}}; every call is appended to calls.txt
FAKE_DOCKER = r'''
import json, pathlib, sys
here = pathlib.Path(__file__).parent
state = json.loads((here / "containers.json").read_text())
args = sys.argv[1:]
with open(here / "calls.txt", "a") as f:
    f.write(" ".join(args) + "\n")
def save():
    (here / "containers.json").write_text(json.dumps(state))
if args[:2] == ["ps", "-a"]:
    print("\n".join(state))
elif args[0] == "inspect":
    if args[1] != "--format":
        sys.exit("inspect without --format would show the env")
    for name in args[3:]:
        c = state[name]
        print(f"/{name}\t{c['status']}\t{c['started']}")
elif args[0] == "logs":
    c = state.get(args[-1])
    if not c:
        sys.exit("Error: No such container: " + args[-1])
    print(c["log"])
elif args[0] in ("restart", "start", "stop"):
    c = state.get(args[1])
    if not c:
        sys.exit("Error response from daemon: No such container: " + args[1])
    if c.get("stuck"):
        sys.exit("Error response from daemon: cannot " + args[0] + " container: " + args[1])
    c["status"] = "exited" if args[0] == "stop" else "running"
    c["started"] = "2026-10-26T12:00:00.123456789Z" if args[0] != "stop" else c["started"]
    save()
    print(args[1])
else:
    sys.exit("fake docker can't " + args[0])
'''

# resident.sh start <id>, as the create command: makes the container
FAKE_CREATE = r'''
import json, pathlib, sys
here = pathlib.Path(__file__).parent
state = json.loads((here / "containers.json").read_text())
state["resident-" + sys.argv[1]] = {"status": "running", "started": "2026-10-26T12:00:00Z", "log": ""}
(here / "containers.json").write_text(json.dumps(state))
print(sys.argv[1] + ": started as someone (pool someone)")
print('watch for "Signed in as" with: resident.sh status ' + sys.argv[1])
'''

# two of these at once, each stopping and starting its own bot over and over, as two quick clicks on a dashboard
RACE = r'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("manage", sys.argv[1])
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)
config = manage.load_config(sys.argv[2])
for _ in range(int(sys.argv[4])):
    manage.act(config, "stop", sys.argv[3])
    manage.act(config, "start", sys.argv[3])
'''

# ssh: runs manage.py --ssh with the host's config, the remote command in SSH_ORIGINAL_COMMAND, as sshd would
FAKE_SSH = r'''
import os, subprocess, sys
os.environ["SSH_ORIGINAL_COMMAND"] = sys.argv[-1]
sys.exit(subprocess.run([sys.executable, sys.argv[1], "--config", sys.argv[2], "--ssh"]).returncode)
'''

ACCOUNTS = """# id  username
scout   Bot_Scout
bolt    Bot_Bolt off   # resting
juno    Bot_Juno
tank    Bot_Tank
"""

ROSTER = """{
  "about": "test",
  "bots": [
    {"id": "scout", "name": "Scout", "size": 1.0},
    {"id": "bolt", "off": true, "name": "Bolt", "size": 1.0},
    {"id": "juno", "name": "Juno", "size": 0.85},
    {"id": "tank", "name": "Tank", "size": 1.4}]
}
"""

CONTAINERS = {
    "resident-scout": {"status": "running", "started": "2026-10-26T11:15:02.5Z",
                       "log": 'Signing in to https://vr.darkride.ai as the AI resident "Bot_Scout"\n'
                              'Signed in as "Bot_Scout"'},
    "resident-bolt": {"status": "exited", "started": "2026-10-25T20:00:00Z", "log": ""},
    "resident-juno": {"status": "running", "started": "2026-10-26T11:16:00Z",
                      "log": "The connection key was refused (the reason is in the line above)"},
    "automata": {"status": "running", "started": "2026-10-20T00:00:00Z", "log": ""},
}


def load_manage():
    spec = importlib.util.spec_from_file_location("manage", MANAGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Host(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.dir = pathlib.Path(self.tmp.name)
        (d / "accounts").write_text(ACCOUNTS)
        (d / "bots.json").write_text(ROSTER)
        (d / "Bot_Scout.env").write_text(f"HIFI_AC_USERNAME=Bot_Scout\nHIFI_AC_KEY={KEY}\n")
        (d / "containers.json").write_text(json.dumps(CONTAINERS))
        (d / "docker.py").write_text(FAKE_DOCKER)
        (d / "create.py").write_text(FAKE_CREATE)
        (d / "ssh.py").write_text(FAKE_SSH)
        self.config = d / "manager.json"
        self.config.write_text(json.dumps({
            "accounts": "accounts", "roster": "bots.json", "container": "resident-{id}",
            "docker": [sys.executable, str(d / "docker.py")],
            "create": [sys.executable, str(d / "create.py"), "{id}"]}))

    def tearDown(self):
        self.tmp.cleanup()

    def manage(self, *args, config=None, stdin=None):
        run = subprocess.run([sys.executable, str(MANAGE), "--config", str(config or self.config)] + list(args),
                             capture_output=True, text=True, input=stdin or "")
        self.assertNotIn(KEY, run.stdout + run.stderr)
        self.assertNotIn("tvk_s3", run.stdout + run.stderr)
        return run

    def action(self, *args, ok=True, **kw):
        run = self.manage(*args, **kw)
        lines = run.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, run.stdout + run.stderr)
        result = json.loads(lines[0])
        self.assertEqual(set(result), {"ok", "message"})
        self.assertEqual(result["ok"], ok, result["message"])
        self.assertEqual(run.returncode, 0 if ok else 1)
        return result["message"]

    def bots(self, **kw):
        run = self.manage("status", "--json", **kw)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return {b["id"]: b for b in json.loads(run.stdout)["bots"]}

    def calls(self):
        return (self.dir / "calls.txt").read_text().splitlines()

    def containers(self, **changes):
        state = json.loads((self.dir / "containers.json").read_text())
        for name, change in changes.items():
            state.setdefault(name, {}).update(change)
        (self.dir / "containers.json").write_text(json.dumps(state))


class Manage(Host):
    def test_status(self):
        bots = self.bots()
        self.assertEqual(list(bots), ["scout", "bolt", "juno", "tank"])
        s = bots["scout"]
        self.assertEqual((s["name"], s["username"], s["enabled"], s["signed_in"]), ("Scout", "Bot_Scout", True, True))
        self.assertEqual(s["container"]["state"], "running")
        self.assertGreater(s["container"]["uptime_s"], 0)
        self.assertEqual(bots["bolt"]["enabled"], False)
        self.assertEqual(bots["bolt"]["container"], {"state": "exited", "uptime_s": None, "docker": "exited"})
        self.assertEqual(bots["bolt"]["signed_in"], False)
        self.assertEqual(bots["juno"]["signed_in"], False)            # its key was refused
        self.assertEqual(bots["tank"]["container"]["state"], "missing")
        for b in bots.values():
            self.assertEqual(set(b), {"id", "name", "username", "enabled", "container", "signed_in"})
        self.assertFalse([c for c in self.calls() if c.startswith("inspect") and "--format" not in c])
        self.assertIn("logs --since 2026-10-26T11:15:02.500000+00:00 --until 2026-10-26T11:25:02.500000+00:00 "
                      "resident-scout", self.calls())

    def test_parse_time(self):
        utc = datetime.timezone.utc
        parse = load_manage().parse_time
        self.assertEqual(parse("0001-01-01T00:00:00Z"), datetime.datetime(1, 1, 1, tzinfo=utc))   # never started
        self.assertEqual(parse("2026-10-26T11:15:02.5Z"), datetime.datetime(2026, 10, 26, 11, 15, 2, 500000, utc))
        self.assertEqual(parse("2026-10-26T11:15:02.123456789+01:00"),
                         datetime.datetime(2026, 10, 26, 10, 15, 2, 123456, utc))
        self.assertIsNone(parse("yesterday"))

    def test_status_of_a_container_never_started(self):
        self.containers(**{"resident-tank": {"status": "created", "started": "0001-01-01T00:00:00Z", "log": ""}})
        tank = self.bots()["tank"]
        self.assertEqual(tank["container"], {"state": "exited", "uptime_s": None, "docker": "created"})

    def test_signed_in_is_the_whole_username(self):
        self.containers(**{"resident-scout": {"log": 'Signed in as "Bot_Scout2"'}})
        self.assertIsNone(self.bots()["scout"]["signed_in"])

    def test_status_table(self):
        run = self.manage("status")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Bot_Scout", run.stdout)
        self.assertIn("missing", run.stdout)

    def test_restart(self):
        self.assertEqual(self.action("restart", "scout"), "scout: restarted")
        self.assertIn("restart resident-scout", self.calls())
        self.assertIn("is off", self.action("restart", "bolt", ok=False))
        self.assertIn("no container", self.action("restart", "tank", ok=False))

    def test_stop_marks_off_and_keeps_the_layout(self):
        self.assertEqual(self.action("stop", "scout"), "scout: stopped, marked off")
        self.assertIn("stop resident-scout", self.calls())
        self.assertIn("scout   Bot_Scout off\n", (self.dir / "accounts").read_text())
        roster = (self.dir / "bots.json").read_text()
        self.assertIn('{"id": "scout", "off": true, "name": "Scout", "size": 1.0},', roster)
        self.assertEqual(roster.replace('"off": true, "name": "Scout"', '"name": "Scout"'), ROSTER)
        self.assertEqual(self.bots()["scout"]["container"]["state"], "exited")
        self.assertEqual(self.action("stop", "scout"), "scout: wasn't running, already off")

    def test_stop_that_docker_refuses_changes_nothing(self):
        self.containers(**{"resident-scout": {"stuck": True}})
        self.assertIn("couldn't stop it, so it isn't marked off", self.action("stop", "scout", ok=False))
        self.assertEqual((self.dir / "accounts").read_text(), ACCOUNTS)
        self.assertEqual((self.dir / "bots.json").read_text(), ROSTER)

    def test_stop_a_bot_the_roster_hasnt(self):
        (self.dir / "accounts").write_text(ACCOUNTS + "kip     Bot_Kip\n")
        self.containers(**{"resident-kip": {"status": "running", "started": "2026-10-26T11:16:00Z", "log": ""}})
        self.assertEqual(self.action("stop", "kip"), "kip: stopped, marked off")
        self.assertIn("kip     Bot_Kip off\n", (self.dir / "accounts").read_text())
        self.assertEqual((self.dir / "bots.json").read_text(), ROSTER)

    def test_stop_says_when_the_roster_cant_be_changed(self):
        roster = ROSTER.replace('{"id": "juno", "name": "Juno"', '{"name": "Juno", "id": "juno"')
        (self.dir / "bots.json").write_text(roster)
        message = self.action("stop", "juno", ok=False)
        self.assertIn("juno: stopped and marked off in accounts, but not in the roster", message)
        self.assertIn("stop resident-juno", self.calls())
        self.assertIn("juno    Bot_Juno off\n", (self.dir / "accounts").read_text())

    def test_an_os_error_is_still_one_json_line(self):
        (self.dir / ".manager.lock").mkdir()                    # can't be opened as the lock
        self.assertTrue(self.action("start", "bolt", ok=False))
        self.assertEqual((self.dir / "accounts").read_text(), ACCOUNTS)

    def test_two_at_once_take_turns(self):
        race = self.dir / "race.py"
        race.write_text(RACE)
        runs = [subprocess.Popen([sys.executable, str(race), str(MANAGE), str(self.config), bot, "15"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for bot in ("scout", "juno")]
        for run in runs:
            out, err = run.communicate(timeout=300)
            self.assertEqual(run.returncode, 0, err)
        self.assertEqual((self.dir / "accounts").read_text(), ACCOUNTS)
        self.assertEqual((self.dir / "bots.json").read_text(), ROSTER)
        self.assertEqual(sorted(f.name for f in self.dir.glob(".*")), [".manager.lock"])     # no temp files left

    def test_start_takes_off_away(self):
        self.assertEqual(self.action("start", "bolt"), "bolt: started")
        self.assertIn("bolt    Bot_Bolt   # resting\n", (self.dir / "accounts").read_text())
        self.assertIn('{"id": "bolt", "name": "Bolt", "size": 1.0},', (self.dir / "bots.json").read_text())
        b = self.bots()["bolt"]
        self.assertEqual((b["enabled"], b["container"]["state"]), (True, "running"))
        self.assertEqual(self.action("start", "scout"), "scout: already running")

    def test_stop_then_start_round_trip(self):
        self.action("stop", "juno")
        self.action("start", "juno")
        self.assertEqual((self.dir / "accounts").read_text(), ACCOUNTS)
        self.assertEqual((self.dir / "bots.json").read_text(), ROSTER)

    def test_start_makes_a_missing_container(self):
        self.assertIn("container made and started (tank: started as", self.action("start", "tank"))
        self.assertEqual(self.bots()["tank"]["container"]["state"], "running")

    def test_bad_ids(self):
        self.assertIn("isn't in", self.action("stop", "woody", ok=False))
        for bad in ("../x", "Scout", "a b", "x;y", ""):
            self.assertIn("lower-case", self.action("stop", bad, ok=False))
        for bad in ("_bot", "scout\n"):                           # "-x" is refused over ssh, below
            self.assertIn("lower-case", self.action("stop", bad, ok=False))
        self.assertIn("usage: stop <id>", self.action("stop", ok=False))
        self.assertIn("isn't in", self.action("stop", "my_bot-2", ok=False))       # a good id, just not ours
        self.assertFalse((self.dir / "calls.txt").exists())
        self.assertEqual((self.dir / "accounts").read_text(), ACCOUNTS)

    def test_docker_down(self):
        config = json.loads(self.config.read_text())
        config["docker"] = [sys.executable, "-c", "import sys; sys.exit('Cannot connect to the Docker daemon')"]
        self.config.write_text(json.dumps(config))
        run = self.manage("status", "--json")
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout),
                         {"ok": False, "message": "docker ps: Cannot connect to the Docker daemon"})

    def test_badly_typed_configs_are_one_json_line(self):
        good = json.loads(self.config.read_text())
        bad = self.dir / "bad.json"
        cases = [
            ([], "isn't a JSON object"), (None, "isn't a JSON object"), ("x", "isn't a JSON object"),
            ({**good, "ssh": "x"}, "ssh isn't an object"),
            ({**good, "accounts": 5}, "accounts isn't a string"),
            ({**good, "roster": ["bots.json"]}, "roster isn't a string"),
            ({**good, "docker": "docker"}, "docker isn't a list of strings"),
            ({**good, "docker": []}, "docker isn't a list of strings"),
            ({**good, "container": "r-{bogus}"}, "container can fill in only"),
            ({**good, "container": "r-{0}"}, "container can fill in only"),
            ({**good, "container": "r-{id.x}"}, "container can fill in only"),
            ({**good, "container": "r-{id"}, "container can fill in only"),
            ({**good, "create": ["sh", "resident.sh", "start", "{nope}"]}, "create can fill in only"),
            ({**good, "create": "resident.sh start"}, "create isn't a list of strings"),
            ({**good, "backend": 1}, "backend isn't a string"),
            ({"backend": "ssh", "ssh": {"host": 1}}, "ssh host isn't a string"),
            ({"backend": "ssh", "ssh": {"host": "h", "port": [22]}}, "ssh port isn't a number"),
            ({"backend": "ssh", "ssh": {"host": "h", "options": "x"}}, "ssh options isn't a list of strings"),
            ({"backend": "ssh", "ssh": {"host": "h", "command": "ssh"}}, "ssh command isn't a list of strings"),
        ]
        for config, says in cases:
            bad.write_text(json.dumps(config))
            for args in (("status", "--json"), ("stop", "scout"), ("start", "tank")):
                self.assertIn(says, self.action(*args, config=bad, ok=False), (config, args))
            run = self.manage("status", config=bad)                   # the table: the error on stderr, no traceback
            self.assertEqual(run.returncode, 1)
            self.assertIn(says, run.stderr)
            self.assertNotIn("Traceback", run.stderr)
        self.assertFalse((self.dir / "calls.txt").exists())
        self.assertEqual((self.dir / "accounts").read_text(), ACCOUNTS)

    def test_errors_name_files_without_the_host_path(self):
        missing = self.dir / "nowhere" / "manager.json"
        message = self.action("status", "--json", config=missing, ok=False)
        self.assertIn("manager.json", message)
        self.assertNotIn(str(self.dir), message)
        config = json.loads(self.config.read_text())
        config["accounts"] = "no-such-accounts"
        self.config.write_text(json.dumps(config))
        message = self.action("stop", "scout", ok=False)
        self.assertIn("no-such-accounts", message)
        self.assertNotIn(str(self.dir), message)

    def test_a_start_that_fails_says_off_was_taken_away(self):
        self.containers(**{"resident-bolt": {"stuck": True}})
        self.assertIn("bolt: off taken away, but couldn't start it", self.action("start", "bolt", ok=False))
        self.assertIn("bolt    Bot_Bolt   # resting\n", (self.dir / "accounts").read_text())

    def test_bad_ids_never_reach_docker_or_ssh(self):
        client = self.dir / "client.json"
        client.write_text(json.dumps({"backend": "ssh", "ssh": {"host": "bots.lan", "command": ["no-such-ssh"]}}))
        for config in (self.config, client):
            self.assertIn("lower-case", self.action("restart", "x;reboot", config=config, ok=False))
        self.assertFalse((self.dir / "calls.txt").exists())

    def test_over_ssh(self):
        client = self.dir / "client.json"
        client.write_text(json.dumps({"backend": "ssh", "ssh": {
            "host": "bots.lan", "user": "dashboard",
            "command": [sys.executable, str(self.dir / "ssh.py"), str(MANAGE), str(self.config)]}}))
        self.assertEqual(self.bots(config=client)["scout"]["signed_in"], True)
        self.assertEqual(self.action("stop", "scout", config=client), "scout: stopped, marked off")
        self.assertIn("is off", self.action("restart", "scout", config=client, ok=False))

    def test_ssh_allows_only_the_four(self):
        for command in ("rm -rf /", "status", "stop scout; reboot", "logs resident-scout", "stop Scout",
                        "stop 'scout", "stop --json", "stop -x"):
            run = subprocess.run([sys.executable, str(MANAGE), "--config", str(self.config), "--ssh"],
                                 capture_output=True, text=True, env={**os.environ, "SSH_ORIGINAL_COMMAND": command})
            self.assertEqual(run.returncode, 1, command)
            self.assertFalse(json.loads(run.stdout)["ok"])
        self.assertFalse((self.dir / "calls.txt").exists())


# build_bots.py, as the build command: logs the call and writes the bot's loader with its roster entry in it, so the
# test can see what the build saw; "build-fails" (a count) beside it makes that many calls fail
FAKE_BUILD = r"""
import json, os, pathlib, subprocess, sys, time
here = pathlib.Path(__file__).parent
with open(here / "build.txt", "a") as f:
    f.write(sys.argv[1] + "\n")
hangs = here / "build-hangs"
if hangs.exists():                      # hangs once, and on POSIX starts a child that would outlive it (an rsync)
    hangs.unlink()
    if os.name == "posix":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        (here / "build-child").write_text(str(child.pid))
    time.sleep(60)
fails = here / "build-fails"
if fails.exists():
    n = int(fails.read_text())
    if n > 1:
        fails.write_text(str(n - 1))
    else:
        fails.unlink()
    sys.exit("Traceback ... MemoryError: building " + sys.argv[1])
bot = next(b for b in json.loads((here / "bots.json").read_text())["bots"] if b["id"] == sys.argv[1])
(here / "out").mkdir(exist_ok=True)
(here / "out" / ("bot-" + sys.argv[1] + ".js")).write_text(json.dumps(bot))
print("built " + sys.argv[1])
"""

# the push: logs the call and the loader it sends; "push-fails" as above
FAKE_PUSH = FAKE_BUILD.split("bot = next")[0].replace("build", "push") + r"""
with open(here / "pushed.txt", "a") as f:
    f.write((here / "out" / ("bot-" + sys.argv[1] + ".js")).read_text() + "\n")
"""

SETTINGS_ROSTER = """{
  "about": "test",
  "areas": {
    "office": {"x": [-24.5, -5.0], "y": [2.5, 6.5], "z": [-44.5, -31.5]},
    "ground": {"x": [-40, 40], "y": [-3.0, -1.5], "z": [-60, 20]}
  },
  "bots": [
    {"id": "scout", "name": "Scout", "role": "guide", "size": 1.0, "shell": [0.9, 0.9, 0.9], "accent": [0.1, 0.5, 1.0], "voice": {"s_hi": "Hello there, Martin"}, "style": {"head": "loaf", "eyes": "lens"}},
    {"id": "bolt", "off": true, "name": "Bolt", "size": 1.0, "shell": [0.86, 0.88, 0.92], "accent": [0.0, 0.85, 1.0]},
    {"id": "juno", "name": "Juno", "size": 0.85, "shell": [0.09, 0.09, 0.11], "accent": [1.0, 0.1, 0.75], "home": "office",
     "style": {"crest": true}},
    {"id": "tank", "name": "Tank", "size": 1.4, "shell": [0.14, 0.15, 0.13], "accent": [0.2, 1.0, 0.35]}]
}
"""


class Settings(Host):
    def setUp(self):
        super().setUp()
        d = self.dir
        (d / "bots.json").write_text(SETTINGS_ROSTER)
        (d / "build.py").write_text(FAKE_BUILD)
        (d / "push.py").write_text(FAKE_PUSH)
        config = json.loads(self.config.read_text())
        config["build"] = [sys.executable, "build.py", "{id}"]           # relative: run in the config's folder
        config["push"] = [sys.executable, "push.py", "{id}"]
        self.config.write_text(json.dumps(config))

    def answer(self, *args, stdin=None, ok=True, config=None):
        run = self.manage(*args, stdin=stdin, config=config)
        lines = run.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, run.stdout + run.stderr)
        result = json.loads(lines[0])
        self.assertEqual(result["ok"], ok, result["message"])
        self.assertEqual(run.returncode, 0 if ok else 1)
        return result

    def apply(self, bot_id, changes, *flags, base=None, ok=True, config=None):
        base = base or self.answer("settings", bot_id, config=config)["base"]
        return self.answer("apply", bot_id, *flags, stdin=json.dumps({"base": base, "set": changes}), ok=ok,
                           config=config)

    def log(self, name):
        path = self.dir / name
        return path.read_text().splitlines() if path.exists() else []

    def roster(self):
        return (self.dir / "bots.json").read_text()

    def test_settings(self):
        s = self.answer("settings", "scout")
        self.assertEqual(s["name"], "Scout")
        self.assertEqual(s["areas"], ["office", "ground"])
        self.assertEqual(s["settings"]["size"], 1.0)
        self.assertEqual(s["settings"]["style.head"], "loaf")
        self.assertIsNone(s["settings"]["home"])
        self.assertEqual(set(s["settings"]), set(load_manage().SETTINGS))
        self.assertNotIn("Hello there", json.dumps(s))                  # the rest of its entry isn't sent
        self.assertRegex(s["base"], "^[0-9a-f]{16}$")
        self.assertIn("isn't in bots.json", self.answer("settings", "woody", ok=False)["message"])
        self.assertIn("lower-case", self.answer("settings", "Scout", ok=False)["message"])

    def test_dry_run_changes_nothing(self):
        r = self.apply("scout", {"size": 1.2, "style.head": "dome"}, "--dry-run")
        self.assertEqual(r["diff"], [{"setting": "size", "from": 1.0, "to": 1.2},
                                     {"setting": "style.head", "from": "loaf", "to": "dome"}])
        self.assertEqual(r["message"], 'scout: would change size 1.0 -> 1.2, style.head "loaf" -> "dome"')
        self.assertEqual(self.roster(), SETTINGS_ROSTER)
        self.assertEqual(self.log("build.txt"), [])
        self.assertEqual(self.log("calls.txt"), [])

    def test_apply_changes_only_that_bot_builds_pushes_and_restarts(self):
        r = self.apply("scout", {"size": 1.2, "style.head": "dome", "style.fins": True, "home": "office",
                                 "rockets": True, "shell": [0.5, 0.25, 0.125]})
        self.assertEqual(r["message"], 'scout: size 1.0 -> 1.2, shell [0.9, 0.9, 0.9] -> [0.5, 0.25, 0.125], '
                                       'rockets (default) -> true, home (default) -> "office", style.head "loaf" -> '
                                       '"dome", style.fins (default) -> true; built, pushed, restarted')
        self.assertEqual(len(r["diff"]), 6)
        new = '    {"id": "scout", "name": "Scout", "role": "guide", "size": 1.2, "shell": [0.5, 0.25, 0.125], ' \
              '"accent": [0.1, 0.5, 1.0], "voice": {"s_hi": "Hello there, Martin"}, "style": {"head": "dome", ' \
              '"eyes": "lens", "fins": true}, "home": "office", "rockets": true},'
        old = SETTINGS_ROSTER.splitlines()
        self.assertEqual(self.roster().splitlines(),
                         [new if line.startswith('    {"id": "scout"') else line for line in old])
        self.assertEqual(self.log("build.txt"), ["scout"])
        self.assertEqual(self.log("push.txt"), ["scout"])
        self.assertEqual(json.loads(self.log("pushed.txt")[0])["size"], 1.2)      # pushed what was built from the new
        self.assertIn("restart resident-scout", self.log("calls.txt"))
        self.assertEqual(r["base"], self.answer("settings", "scout")["base"])       # the next apply can follow on
        self.assertEqual(sorted(f.name for f in self.dir.glob(".*")), [".manager.lock"])

    def test_a_multi_line_entry_is_changed_in_place(self):
        r = self.apply("juno", {"home": None, "style.crest": None, "obedience": 0.4})
        self.assertEqual(r["diff"], [{"setting": "obedience", "from": None, "to": 0.4},
                                     {"setting": "home", "from": "office", "to": None},
                                     {"setting": "style.crest", "from": True, "to": None}])
        juno = next(b for b in json.loads(self.roster())["bots"] if b["id"] == "juno")
        self.assertEqual(juno, {"id": "juno", "name": "Juno", "size": 0.85, "shell": [0.09, 0.09, 0.11],
                                "accent": [1.0, 0.1, 0.75], "style": {}, "obedience": 0.4})
        self.assertIn('    {"id": "tank"', self.roster())

    def test_a_stale_base_is_refused(self):
        base = self.answer("settings", "scout")["base"]
        self.apply("scout", {"size": 1.1})
        r = self.apply("scout", {"size": 0.9}, base=base, ok=False)
        self.assertIn("changed since its settings were read", r["message"])
        self.assertIn('"size": 1.1', self.roster())
        self.assertEqual(self.log("build.txt"), ["scout"])

    def test_bad_values_are_refused_and_change_nothing(self):
        cases = [
            ({"size": 9}, "size is a number from 0.25 to 4"), ({"size": "1"}, "size is a number"),
            ({"size": True}, "size is a number"), ({"wear": -0.1}, "wear is a number from 0 to 1"),
            ({"shell": [1, 2, 3]}, "shell is a colour"), ({"shell": [0.5, 0.5]}, "shell is a colour"),
            ({"shell": None}, "shell can be changed but not taken away"),
            ({"home": "attic"}, "home is one of the roster's areas (office, ground)"),
            ({"style.antennas": True}, "style.antennas is one of 0, 1, 2"),
            ({"style.antennas": 3}, "style.antennas is one of 0, 1, 2"),
            ({"style.head": "cube"}, "style.head is one of loaf, dome, wedge"),
            ({"rockets": "yes"}, "rockets is true or false"),
            ({"name": "Evil", "voice": {}}, "not a setting that can be changed here: name, voice"),
            ({"off": False}, "not a setting that can be changed here: off"),
        ]
        for changes, says in cases:
            self.assertIn(says, self.apply("scout", changes, ok=False)["message"], changes)
        for stdin in ("", "not json", "[]", '{"set": {}}', '{"base": "x"}', '{"base": "x", "set": []}'):
            self.assertIn("apply reads", self.answer("apply", "scout", stdin=stdin, ok=False)["message"], stdin)
        base = self.answer("settings", "scout")["base"]
        nan = '{"base": "%s", "set": {"size": NaN}}' % base
        self.assertIn("size is a number", self.answer("apply", "scout", stdin=nan, ok=False)["message"])
        self.assertIn("longer than", self.answer("apply", "scout", stdin="x" * 70000, ok=False)["message"])
        self.assertEqual(self.roster(), SETTINGS_ROSTER)
        self.assertEqual(self.log("build.txt"), [])

    def test_nothing_to_change(self):
        r = self.apply("scout", {"size": 1, "style.head": "loaf"})
        self.assertEqual((r["message"], r["diff"]), ("scout: nothing to change", []))
        self.assertEqual(self.roster(), SETTINGS_ROSTER)
        self.assertEqual(self.log("build.txt"), [])

    def test_a_failed_build_puts_the_roster_back_and_builds_again(self):
        (self.dir / "build-fails").write_text("1")
        r = self.apply("scout", {"size": 1.3}, ok=False)
        self.assertEqual(r["message"], "scout: the build failed: Traceback ... MemoryError: building scout; the roster "
                                       "is back as it was, built again as it was")
        self.assertEqual(self.roster(), SETTINGS_ROSTER)
        self.assertEqual(self.log("build.txt"), ["scout", "scout"])
        self.assertEqual(json.loads((self.dir / "out" / "bot-scout.js").read_text())["size"], 1.0)
        self.assertEqual(self.log("push.txt"), [])
        self.assertNotIn("restart resident-scout", self.log("calls.txt"))

    def test_a_failed_push_puts_everything_back(self):
        (self.dir / "push-fails").write_text("1")
        r = self.apply("scout", {"size": 1.3}, ok=False)
        self.assertEqual(r["message"], "scout: the push failed: Traceback ... MemoryError: pushing scout; the roster is "
                                       "back as it was, built again as it was, pushed again as it was")
        self.assertEqual(self.roster(), SETTINGS_ROSTER)
        self.assertEqual(json.loads(self.log("pushed.txt")[-1])["size"], 1.0)

    def test_when_putting_it_back_fails_too_it_says_so(self):
        (self.dir / "push-fails").write_text("2")
        r = self.apply("scout", {"size": 1.3}, ok=False)
        self.assertIn("built again as it was, the push failed: Traceback ... MemoryError: pushing scout too, so its "
                      "files may not match the roster: run the push by hand", r["message"])
        self.assertEqual(self.roster(), SETTINGS_ROSTER)

    def test_without_a_build_command_only_dry_runs(self):
        config = json.loads(self.config.read_text())
        del config["build"]
        self.config.write_text(json.dumps(config))
        self.assertEqual(len(self.apply("scout", {"size": 1.3}, "--dry-run")["diff"]), 1)
        self.assertIn("no build command", self.apply("scout", {"size": 1.3}, ok=False)["message"])
        self.assertEqual(self.roster(), SETTINGS_ROSTER)

    def test_without_a_push_command_builds_only(self):
        config = json.loads(self.config.read_text())
        del config["push"]
        self.config.write_text(json.dumps(config))
        self.assertEqual(self.apply("scout", {"size": 1.3})["message"], "scout: size 1.0 -> 1.3; built, restarted")
        self.assertEqual(self.log("push.txt"), [])

    def test_bots_that_arent_running_arent_restarted(self):
        self.assertIn("; built, pushed; it's off, so it shows when it's brought back",
                      self.apply("bolt", {"size": 1.1})["message"])
        self.assertIn("; built, pushed; it isn't running, so it shows when it next starts",
                      self.apply("tank", {"size": 1.1})["message"])
        self.assertFalse([c for c in self.log("calls.txt") if c.startswith("restart")])

    def test_a_failed_restart_says_the_change_is_in(self):
        self.containers(**{"resident-scout": {"stuck": True}})
        r = self.apply("scout", {"size": 1.3}, ok=False)
        self.assertIn("scout: size 1.0 -> 1.3; built, pushed, but couldn't restart it", r["message"])
        self.assertIn('"size": 1.3', self.roster())

    def test_a_build_that_hangs_is_killed_with_what_it_started_and_put_back(self):
        config = json.loads(self.config.read_text())
        config["build_timeout"] = 2
        self.config.write_text(json.dumps(config))
        (self.dir / "build-hangs").write_text("")
        began = time.monotonic()
        r = self.apply("scout", {"size": 1.3}, ok=False)
        self.assertLess(time.monotonic() - began, 30)
        self.assertEqual(r["message"], "scout: the build took longer than 2 s; the roster is back as it was, built "
                                       "again as it was")
        self.assertEqual(self.roster(), SETTINGS_ROSTER)
        self.assertEqual(json.loads((self.dir / "out" / "bot-scout.js").read_text())["size"], 1.0)
        if os.name == "posix":
            child = int((self.dir / "build-child").read_text())
            for _ in range(40):
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("the build's own child outlived it")

    def test_a_crlf_roster_and_accounts_keep_their_line_endings(self):
        crlf = SETTINGS_ROSTER.replace("\n", "\r\n")
        accounts = ACCOUNTS.replace("\n", "\r\n")
        (self.dir / "bots.json").write_bytes(crlf.encode())
        (self.dir / "accounts").write_bytes(accounts.encode())
        self.apply("scout", {"size": 1.3})
        after = (self.dir / "bots.json").read_bytes().decode()
        self.assertEqual(after.replace('"size": 1.3', '"size": 1.0'), crlf)
        self.action("stop", "scout")
        self.action("start", "scout")
        self.assertEqual((self.dir / "accounts").read_bytes().decode(), accounts)
        self.assertEqual((self.dir / "bots.json").read_bytes().decode(), after)

    def test_waiting_its_turn_gives_up_inside_a_dashboards_timeouts(self):
        manage = load_manage()
        self.assertLess(manage.LOCK_WAIT, 60)                                   # the console's action timeout
        self.assertLess(manage.apply_timeout(manage.BUILD_TIMEOUT) + manage.TIMEOUT, 900)   # its settings timeout
        manage.LOCK_WAIT = 0.3
        config = manage.load_config(self.config)
        request = json.dumps({"base": self.answer("settings", "scout")["base"], "set": {"size": 1.3}})
        with manage.taking_turns(config):
            began = time.monotonic()
            with self.assertRaisesRegex(manage.Failed, "another action is still running"):
                manage.act(config, "restart", "juno")
            self.assertLess(time.monotonic() - began, 5)
            with self.assertRaisesRegex(manage.Failed, "another action is still running"):
                manage.apply(config, "scout", request, False)
            self.assertEqual(len(manage.apply(config, "scout", request, True)["diff"]), 1)   # a dry run only reads
        self.assertEqual(self.roster(), SETTINGS_ROSTER)
        self.assertEqual(self.log("calls.txt"), [])

    def test_an_entry_without_its_id_first_is_refused(self):
        (self.dir / "bots.json").write_text(SETTINGS_ROSTER.replace('{"id": "tank", "name": "Tank"',
                                                                    '{"name": "Tank", "id": "tank"'))
        self.assertIn("isn't laid out as expected", self.answer("settings", "tank", ok=False)["message"])

    def test_badly_typed_build_configs(self):
        good = json.loads(self.config.read_text())
        bad = self.dir / "bad.json"
        for config, says in [({**good, "build": "python build.py"}, "build isn't a list of strings"),
                             ({**good, "push": [sys.executable, "push.py", "{username}"]}, "push can fill in only {id}"),
                             ({**good, "build_timeout": "60"}, "build_timeout isn't a whole number"),
                             ({**good, "build_timeout": 0}, "build_timeout isn't a whole number")]:
            bad.write_text(json.dumps(config))
            self.assertIn(says, self.answer("settings", "scout", config=bad, ok=False)["message"])
        self.assertEqual(self.log("build.txt"), [])

    def test_usage(self):
        self.assertIn("usage: settings <id>", self.answer("settings", ok=False)["message"])
        self.assertIn("usage: apply <id> [--dry-run]", self.answer("apply", "scout", "extra", ok=False)["message"])
        self.assertIn("usage: status", self.answer("status", "--json", "--dry-run", ok=False)["message"])

    def test_over_ssh(self):
        client = self.dir / "client.json"
        client.write_text(json.dumps({"backend": "ssh", "ssh": {
            "host": "bots.lan", "command": [sys.executable, str(self.dir / "ssh.py"), str(MANAGE), str(self.config)]}}))
        self.assertEqual(self.answer("settings", "scout", config=client)["settings"]["size"], 1.0)
        self.assertEqual(len(self.apply("scout", {"size": 1.3}, "--dry-run", config=client)["diff"]), 1)
        self.assertIn("built, pushed, restarted", self.apply("scout", {"size": 1.3}, config=client)["message"])
        self.assertIn('"size": 1.3', self.roster())

    def test_ssh_allows_settings_and_apply_only_as_given(self):
        allowed = {"settings scout": True, "apply scout --dry-run": True, "settings": False, "apply": False,
                   "apply scout --force": False, "apply scout --dry-run x": False, "settings scout juno": False,
                   "apply --dry-run scout": False}
        for command, ok in allowed.items():
            run = subprocess.run([sys.executable, str(MANAGE), "--config", str(self.config), "--ssh"],
                                 capture_output=True, text=True, input="{}",
                                 env={**os.environ, "SSH_ORIGINAL_COMMAND": command})
            message = json.loads(run.stdout)["message"]
            self.assertEqual("over ssh, only" not in message, ok, (command, message))
        self.assertEqual(self.roster(), SETTINGS_ROSTER)


if __name__ == "__main__":
    unittest.main()
