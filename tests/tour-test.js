//
//  tour-test.js: a test visitor for a guide bot (run as a persistent script beside the bots). It arrives at the
//  domain's landing spot (ARRIVE), waits there for the guide to come and offer a tour, then follows the guide round,
//  keeping a couple of metres behind it, and logs where the guide goes; the guide's own log says what it's doing
//  ("offering a tour", "at stop", "tour over"). With LAG_AFTER set, it stops following after that many seconds, to
//  see the guide wait and then give up.
//
(function () {
    var ARRIVE = { x: 0, y: -0.9, z: 0 }, GUIDE = "scout", LAG_AFTER = 0, SPEED = 2.2;
    var started = 0, lastLog = 0, guideSeen = false;
    function log(t) { print("TOURTEST: " + t); }
    function guide() {
        var found = null;
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            var a = AvatarList.getAvatar(id);
            if (a && new RegExp("duck-" + GUIDE + "\\.fst").test(a.skeletonModelURL || "")) found = a;
        });
        return found;
    }
    Agent.isAvatar = true;
    Agent.avatarViewRadius = 100;
    Script.setTimeout(function () {
        Avatar.displayName = "Tour tester";
        Avatar.position = ARRIVE;
        started = Date.now();
        log("arrived");
        Script.setInterval(function () {
            var g = guide(), now = Date.now();
            if (!g) { if (now - lastLog > 10000) { lastLog = now; log("no guide yet"); } return; }
            if (!guideSeen) { guideSeen = true; log("guide is about at " + JSON.stringify(g.position)); }
            if (LAG_AFTER && now - started > LAG_AFTER * 1000) return;            // stops following: a straggler
            var d = Vec3.distance(g.position, Avatar.position);
            if (d > 3) {                                                           // keep a couple of metres behind
                var to = Vec3.normalize(Vec3.subtract(g.position, Avatar.position));
                var step = Math.min(d - 2.5, SPEED * 0.2);
                var p = Vec3.sum(Avatar.position, Vec3.multiply(step, to));
                p.y = g.position.y + 0.4;
                Avatar.position = p;
                Avatar.orientation = Quat.lookAtSimple(Avatar.position, g.position);
            }
            if (now - lastLog > 10000) {
                lastLog = now;
                var q = g.position;
                log("guide at " + q.x.toFixed(1) + " " + q.y.toFixed(1) + " " + q.z.toFixed(1) + ", me " + d.toFixed(1) + " m behind");
            }
        }, 200);
    }, 45000);
}());
