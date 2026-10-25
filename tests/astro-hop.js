//
//  astro-hop.js (made from duck-hop.js and bot-kinds-shoot.js): photographs the astromech (arty) side-on through rocket
//  hops (run-tour.ps1 -Script) and checks they're flown, not jumped. Sends it to the lower end of the gallery ring's
//  hop, then up it and back down, through the bots' command channel. Standing at the foot of a hop, its route starts
//  with the hop: that first step was once walked, so viewers saw it vanish and reappear a floor up.
//  The camera keeps square to the hop and follows the bot up and down, a picture every 150 ms; every change of height
//  the viewer gets is logged with its Hips and neck_pitch rotations, and a jump of more than 3 m between two frames
//  is reported as a TELEPORT. The last line says PASS or FAIL.
//
(function () {
    var UP = [11.1, 3.6, -44.6], DOWN = [11.6, -2.4, -45.1];    // either end of one of the rocket hops
    var ID = "arty", SHOTS = 40, EVERY = 150, OUT = 3.4;
    function log(t) { print("TOUR: " + t); }
    Window.stillSnapshotTaken.connect(function (path) { log("saved " + path); });
    function find() {
        var found = null;
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            var a = AvatarList.getAvatar(id);
            if (!found && a && /duck-arty\.fst/.test(a.skeletonModelURL || "")) found = a;
        });
        return found;
    }
    function v(p) { return { x: p[0], y: p[1], z: p[2] }; }
    function clear(from, to) {
        var d = Vec3.subtract(to, from), len = Vec3.length(d);
        var hit = Entities.findRayIntersection({ origin: from, direction: Vec3.normalize(d) }, true);
        return !hit.intersects || hit.distance > len - 0.2;
    }
    function angle(q) { var e = Quat.safeEulerAngles(q); return e.x.toFixed(0) + "," + e.y.toFixed(0) + "," + e.z.toFixed(0); }
    var names = null;
    function joints(a) {
        names = names || a.getJointNames();
        return ["Hips", "neck_pitch"].map(function (n) { return n + " " + angle(a.getJointRotation(names.indexOf(n))); }).join("  ");
    }
    // square to the hop, on the side with a clear view of its foot (from the foot: the bot may start anywhere)
    var across = Vec3.normalize(Vec3.cross({ x: UP[0] - DOWN[0], y: 0, z: UP[2] - DOWN[2] }, Vec3.UNIT_Y));
    var side = null;
    function aim(b) {
        var p = b.position;
        if (!side) {
            var foot = Vec3.sum(v(DOWN), { x: 0, y: 0.8, z: 0 });
            side = clear(foot, Vec3.sum(foot, Vec3.multiply(OUT, across))) ? across : Vec3.multiply(-1, across);
        }
        var eye = Vec3.sum(p, Vec3.sum(Vec3.multiply(OUT, side), { x: 0, y: 0.3, z: 0 }));
        Camera.position = eye;
        Camera.orientation = Quat.lookAtSimple(eye, p);
        MyAvatar.position = Vec3.sum(eye, Vec3.multiply(1.5, side));     // close by (the mixer favours what's near)
    }
    var lastY = null, start = 0, teleports = 0, hops = 0, following = false;
    Script.update.connect(function () {
        var b = following && find();
        if (!b) return;
        aim(b);
        var y = b.position.y;
        // (a flight is quick, and the viewer slow while it saves pictures, so frames can be a metre or two apart; a
        // walked hop is a floor's height in one)
        if (lastY !== null && Math.abs(y - lastY) > 3) { teleports++; log("TELEPORT " + lastY.toFixed(2) + " -> " + y.toFixed(2)); }
        if (start && (lastY === null || Math.abs(y - lastY) > 0.005))
            log((Date.now() - start) + " ms  y " + y.toFixed(2) + "  " + joints(b));
        lastY = y;
    });
    function waitFor(test, ms, then) {
        var t0 = Date.now(), timer = Script.setInterval(function () {
            var b = find();
            if (b && test(b) || Date.now() - t0 > ms) { Script.clearInterval(timer); then(b); }
        }, 200);
    }
    // a burst of pictures from the moment it's told to go; a hop counts if it got to the other floor
    function hop(name, goal, then) {
        var from = find().position.y;
        Messages.sendMessage("darkride.bots.cmd", JSON.stringify({ bot: ID, goto: goal }));
        log(name + ": sent to " + JSON.stringify(goal));
        start = Date.now();
        var n = 0, timer = Script.setInterval(function () {
            Window.takeSnapshot(false, false, 0, name + "-" + (n < 10 ? "0" : "") + n);
            if (++n >= SHOTS) {
                Script.clearInterval(timer);
                if (Math.abs(find().position.y - from) > 4) hops++; else log(name + ": didn't change floor");
                Script.setTimeout(then, 1500);
            }
        }, EVERY);
    }
    Script.setTimeout(function () {
        Menu.setIsOptionChecked("Names Above Heads", false);
        Menu.setIsOptionChecked("Show Overlays", false);
        Camera.mode = "independent";
        var b = find();
        if (!b) { log("no arty"); log("FAIL"); Script.stop(); return; }
        log("arty at " + JSON.stringify(b.position));
        following = true;
        Messages.sendMessage("darkride.bots.cmd", JSON.stringify({ bot: ID, goto: DOWN }));
        waitFor(function (a) { return Vec3.distance(a.position, v(DOWN)) < 1.2; }, 120000, function (a) {
            log("at the foot of the hop: " + JSON.stringify(a && a.position));
            teleports = 0;                  // (getting there may have used other hops)
            Script.setTimeout(function () {
                hop("up", UP, function () {
                    hop("down", DOWN, function () {
                        log(hops + " of 2 hops, " + teleports + " teleports");
                        log(hops === 2 && !teleports ? "PASS" : "FAIL");
                        Script.stop();
                    });
                });
            }, 4000);
        });
    }, 30000);              // (time for the domain and the bot's model to load; start the domain a couple of minutes before)
}());
