//
//  duck-talker.js: a test visitor for the ducks' listening (run as a persistent script beside duck-brain.js). An agent
//  avatar walks up to the nearest duck and "talks" (Duck/test/talk.wav, voiced syllables), pauses, and talks again,
//  logging the duck's audio loudness so the log shows whether and when it answers, and how its head turns.
//
(function () {
    var BASE = Script.resolvePath(".").replace(/[^\/]*$/, "");
    var talk = null, target = null, round = 0, lastLoud = {};
    function log(t) { print("TALKER: " + t); }
    function ducks() {
        return AvatarList.getAvatarIdentifiers().filter(function (id) { return id && id !== Agent.sessionUUID; })
            .map(function (id) { var a = AvatarList.getAvatar(id); a.id = id; return a; })
            .filter(function (a) { return /\/duck(-[a-z0-9]+)?\.fst/.test(a.skeletonModelURL || ""); });
    }
    Agent.isAvatar = true;
    // the avatar mixer only sends the avatars ahead of an agent, plus those within this radius all round (needs a
    // server with Agent.avatarViewRadius, 2026-10-13; older ones see only ahead)
    Agent.avatarViewRadius = 25;
    log("avatar view radius " + Agent.avatarViewRadius);
    Script.setTimeout(function () {
        Avatar.displayName = "Talker";
        talk = SoundCache.getSound(BASE + "talk.wav");
        Script.setInterval(function () {
            var list = ducks();
            if (!list.length) {
                log("no ducks among " + AvatarList.getAvatarIdentifiers().map(function (id) {
                    var a = AvatarList.getAvatar(id);
                    return (a.displayName || "?") + "/" + (a.skeletonModelURL || "-").split("/").pop() + "@" + (a.position ? a.position.y.toFixed(1) : "?");
                }).join(", ") + " (" + Date.now() % 100000 + ")");
                return;
            }
            // a bot with a jaw if there is one, to watch it talk back
            target = list.filter(function (a) { return /duck-(bolt|pip|kip|moss|echo)\.fst/.test(a.skeletonModelURL || ""); })[0] || list[0];
            // stand 2 m from the duck, facing it
            var p = target.position, away = Vec3.normalize({ x: 1, y: 0, z: 0.5 });
            Avatar.position = Vec3.sum(p, Vec3.sum(Vec3.multiply(2, away), { x: 0, y: 0.6, z: 0 }));
            Avatar.orientation = Quat.lookAtSimple(Avatar.position, p);
            if (talk.downloaded) { Agent.playAvatarSound(talk); log("round " + (++round) + ": talking to duck " + target.id.substr(1, 4)); }
        }, 9000);
        // the ducks' loudness: non-zero when one is chirping
        Script.setInterval(function () {
            ducks().forEach(function (d) {
                var loud = d.audioLoudness || 0;
                if ((loud > 0) !== (lastLoud[d.id] > 0)) log("duck " + d.id.substr(1, 4) + (loud > 0 ? " makes a sound (" + loud.toFixed(0) + ")" : " quiet"));
                lastLoud[d.id] = loud;
            });
        }, 50);
    }, 6000);
}());
