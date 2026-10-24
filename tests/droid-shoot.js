//
//  droid-shoot.js (made from photo-shoot.js): pictures of the newer droids (the astromech, the power droid, the mouse
//  droid and the guide), to check their builds in a viewer (run-tour.ps1 -Script). Hides name plates and the toolbar,
//  keeps the photographer's avatar out of the way, then takes several shots of each, from angles with a clear view;
//  side-on ones of the astromech catch it leaning as it drives.
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
        // the new droids: several of each, side-on ones of the astromech to catch it leaning as it drives
        [["arty", [90, -90, 60, -60, 30]], ["arty", [-90, 90, -60, 60]], ["arty", [90, -90, 120, -120]],
         ["arty", [35, -35, 70]], ["gonk", [35, -35, 70, -70]], ["gonk", [90, -90, 20]],
         ["skitter", [35, -35, 70, -70]], ["skitter", [90, -90, 10]], ["scout", [35, -35, 70]], ["scout", [-40, 40, 100]]
        ].forEach(function (shot, k) {
            shoot(shot[0] + "-" + k, function () {
                var b = find(shot[0]), s = size(shot[0]);
                return b ? frame(b, s, shot[1], 1.5 + 0.9 * s, 0.55 * s, 0.4 * s) : false;
            });
        });
        run();
    }, 120000);             // (the bots take a couple of minutes to hatch after a restart)
}());
