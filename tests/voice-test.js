//
//  voice-test.js: a test visitor that gives the bots spoken commands (run as a persistent script beside the bots, with
//  the hearing service running). It walks up to a bot (Kip if about) and says, in turn: "Kip, sit down", "Jump!",
//  "Come here" and "Dance" (test/cmd-*.wav), a command every 14 s, logging each; the bots' logs say what they heard
//  and whether they did it.
//
(function () {
    var BASE = Script.resolvePath(".").replace(/[^\/]*$/, "");
    var COMMANDS = ["cmd-sit", "cmd-jump", "cmd-come", "cmd-dance"], sounds = {}, k = 0;
    function log(t) { print("VOICE: " + t); }
    function target() {
        var best = null;
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            var a = AvatarList.getAvatar(id), m = a && (a.skeletonModelURL || "").match(/duck-([a-z0-9]+)\.fst/);
            if (!m || m[1] === "warden") return;
            if (!best || m[1] === "kip") { best = a; best.botId = m[1]; }
        });
        return best;
    }
    Agent.isAvatar = true;
    Agent.avatarViewRadius = 100;
    Script.setTimeout(function () {
        Avatar.displayName = "Voice tester";
        COMMANDS.forEach(function (c) { sounds[c] = SoundCache.getSound(BASE + c + ".wav"); });
        Script.setInterval(function () {
            var b = target();
            if (!b) { log("no bot"); return; }
            // stand two metres in front of it, facing it
            var fwd = Quat.getForward(b.orientation);
            Avatar.position = Vec3.sum(b.position, Vec3.sum(Vec3.multiply(2, fwd), { x: 0, y: 0.5, z: 0 }));
            Avatar.orientation = Quat.lookAtSimple(Avatar.position, b.position);
            var c = COMMANDS[k++ % COMMANDS.length];
            if (sounds[c].downloaded) { Agent.playAvatarSound(sounds[c]); log("said " + c + " to " + b.botId); }
        }, 14000);
    }, 30000);
}());
