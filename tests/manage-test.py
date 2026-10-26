"""tools/manage.py against a stand-in docker (and ssh): no containers, no keys, no host needed.

Runs status, restart, stop and start on a copy of an accounts file and roster in a temporary folder, with a fake
docker that keeps its containers in a JSON file and logs every call, then checks the JSON that comes out, what the
files say afterwards, what docker was asked, and that a key beside the accounts never shows up anywhere.

Usage: python tests/manage-test.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
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


class Manage(unittest.TestCase):
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

    def manage(self, *args, config=None):
        run = subprocess.run([sys.executable, str(MANAGE), "--config", str(config or self.config)] + list(args),
                             capture_output=True, text=True)
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
        for command in ("rm -rf /", "status", "stop scout; reboot", "logs resident-scout", "stop Scout"):
            run = subprocess.run([sys.executable, str(MANAGE), "--config", str(self.config), "--ssh"],
                                 capture_output=True, text=True, env={**os.environ, "SSH_ORIGINAL_COMMAND": command})
            self.assertEqual(run.returncode, 1, command)
            self.assertFalse(json.loads(run.stdout)["ok"])
        self.assertFalse((self.dir / "calls.txt").exists())


if __name__ == "__main__":
    unittest.main()
