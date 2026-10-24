//
//  photo-shoot.js: pictures of the bots to share (run-tour.ps1 -Script). Hides name plates (stops any name tag
//  script) and the on-screen toolbar, keeps the photographer's avatar out of the way, then: the bouncer from a low
//  hero angle, three-quarters and close on its guns; each wandering bot close up, twice (they move, so two chances at
//  a good stride); and the bouncer with a bot near it, if one wanders close. Each shot tries several angles and takes
//  the first with nothing in the way between the camera and the bot.
//
(function () {
    var shots = [], n = 0, roster = null;
    function log(t) { print("TOUR: " + t); }
    Window.stillSnapshotTaken.connect(function (path) { log("saved " + path); });
    function bots() {
        var out = [];
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            var a = AvatarList.getAvatar(id), m = a && (a.skeletonModelURL || "").match(/duck-([a-z0-9]+)\.fst/);
            if (m) { a.botId = m[1]; out.push(a); }
        });
        return out;
    }
    function find(id) { return bots().filter(function (b) { return b.botId === id; })[0]; }
    function size(id) {
        var b = roster && roster.bots.filter(function (x) { return x.id === id; })[0];
        return b && b.size || 1;
    }
    function clear(from, to) {
        var d = Vec3.subtract(to, from), len = Vec3.length(d);
        var hit = Entities.findRayIntersection({ origin: from, direction: Vec3.normalize(d) }, true);
        return !hit.intersects || hit.distance > len - 0.2;
    }
    // a camera `around` degrees round from the bot's front, `back` metres out, `up` metres above its feet, aimed
    // `aimUp` above its feet; the first of the angles with a clear view wins
    function frame(b, s, angles, back, up, aimUp) {
        var feet = Vec3.sum(b.position, { x: 0, y: -0.48 * s, z: 0 }), aim = Vec3.sum(feet, { x: 0, y: aimUp, z: 0 });
        for (var k = 0; k < angles.length; k++) {
            var yaw = Quat.multiply(b.orientation, Quat.fromPitchYawRollDegrees(0, angles[k], 0));
            var eye = Vec3.sum(feet, Vec3.sum(Vec3.multiply(back, Quat.getForward(yaw)), { x: 0, y: up, z: 0 }));
            if (clear(aim, eye)) {
                Camera.position = eye;
                Camera.orientation = Quat.lookAtSimple(eye, aim);
                return true;
            }
        }
        return false;
    }
    function shoot(name, place) {
        shots.push(function (next) {
            if (!place()) { log("no clear view for " + name); next(); return; }
            Script.setTimeout(function () { Window.takeSnapshot(false, false, 0, name); n++; Script.setTimeout(next, 900); }, 700);
        });
    }
    function run() {
        if (!shots.length) { log("DONE " + n + " shots"); Script.stop(); return; }
        shots.shift()(run);
    }
    Script.setTimeout(function () {
        // no name plates, no toolbar
        Menu.setIsOptionChecked("Names Above Heads", false);
        Menu.setIsOptionChecked("Show Overlays", false);
        ScriptDiscoveryService.getRunning().forEach(function (s) {
            if (/name.?tag|plate/i.test(s.url || s.name || "")) { log("stopping " + (s.url || s.name)); ScriptDiscoveryService.stopScript(s.url); }
        });
        Camera.mode = "independent";
        MyAvatar.position = Vec3.sum(MyAvatar.position, { x: 0, y: -60, z: 0 });
        var list = bots();
        if (list.length) {
            try { roster = Script.require(list[0].skeletonModelURL.replace(/[^\/]*$/, "") + "bots.json"); } catch (e) { log("no roster"); }
        }
        log(list.length + " bots: " + list.map(function (b) { return b.botId; }).join(", "));
        if (find("warden")) {
            shoot("warden-hero", function () { var b = find("warden"); return frame(b, 3, [25, 45, -20, 65, 0], 6.0, 0.6, 2.1); });
            shoot("warden-three-quarter", function () { var b = find("warden"); return frame(b, 3, [40, 60, -40, 20], 5.0, 2.3, 1.8); });
            shoot("warden-guns", function () { var b = find("warden"); return frame(b, 3, [-55, 55, -35, 35], 3.4, 2.5, 2.1); });
            shoot("warden-wide", function () { var b = find("warden"); return frame(b, 3, [10, 30, -10], 10, 1.7, 1.6); });
        }
        [1, 2].forEach(function (round) {
            list.forEach(function (b0) {
                if (b0.botId === "warden") return;
                shoot(b0.botId + "-" + round, function () {
                    var b = find(b0.botId), s = size(b0.botId);
                    if (!b) return false;
                    var angles = round === 1 ? [32, -32, 60, -60, 0, 100, -100] : [-40, 40, 75, -75, 15, 120, -120];
                    return frame(b, s, angles, 1.2 + 0.9 * s, 0.6 * s, 0.45 * s);
                });
            });
        });
        shoot("warden-and-friend", function () {
            var g = find("warden"), near = null, best = 12;
            if (!g) return false;
            bots().forEach(function (b) {
                var d = Vec3.distance(b.position, g.position);
                if (b.botId !== "warden" && d < best) { best = d; near = b; }
            });
            if (!near) return false;
            var mid = Vec3.mix(g.position, near.position, 0.4);
            var eye = Vec3.sum(mid, { x: 5, y: 1.2, z: 5 });
            Camera.position = eye;
            Camera.orientation = Quat.lookAtSimple(eye, Vec3.sum(mid, { x: 0, y: 0.3, z: 0 }));
            return true;
        });
        run();
    }, 50000);
}());
