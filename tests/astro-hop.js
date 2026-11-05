//
//  astro-hop.js (made from duck-hop.js and bot-kinds-shoot.js): photographs the astromech (arty) side-on through rocket
//  hops (run-tour.ps1 -Script) and checks they're flown, not jumped. Sends it to the lower end of the gallery ring's
//  hop, then up it and back down, through the bots' command channel. Standing at the foot of a hop, its route starts
//  with the hop: that first step was once walked, so viewers saw it vanish and reappear a floor up.
//  The camera keeps square to the hop and follows the bot up and down, a picture every 150 ms; every change of height
//  the viewer gets is logged with its Hips and neck_pitch rotations. A hop counts when the bot reaches the other floor
//  and was seen at the top of a flight's arc, over a metre above where it stands on the upper floor (a flight peaks
//  about 0.9 m above it; a walked hop climbs straight up to it, a floor in 0.1 s, so it's never seen above it). A jump
//  of more than JUMP m between two frames is reported as a TELEPORT. The last line says PASS or FAIL.
//  Each hop is only sent once the bot stands still within NEAR of that end of the hop (so its route starts with the
//  hop, not a walk to it), and the line "route starts at" says where it was. It's sent to the foot up to three times
//  (and back to the top twice if it has moved off after the hop up); if it never stands there, or leaves the domain,
//  that's a FAIL.
//  The bot is found by the session it announces, as the bots find each other, so it's found even when the viewer
//  shows it as the default avatar.
//
(function () {
    var UP = [11.1, 3.6, -44.6], DOWN = [11.6, -2.4, -45.1];    // either end of one of the rocket hops (its nav cells)
    var ID = "arty", SHOTS = 40, EVERY = 150, OUT = 3.4;
    // m across from the hop's end: a walk stops about a cell (25 cm) short of its last cell and the astromech's
    // hinge moves it a little more; anything further off isn't that cell (the next hop's foot is 2.7 m away)
    var NEAR = 0.6;
    var LAND = 20000;       // ms after the pictures for it to reach the other floor (a turn first, a slow viewer)
    // m between two frames: a walked hop can be a floor (6 m) in one; a flight's frames can be 3 m apart, as the viewer
    // is slow while it saves pictures and now and then gets an old height late (seen: -0.14, -1.96, 1.09 in 50 ms)
    var JUMP = 4.5;
    // a flight's arc (duck-brain.js: 0.5 + 0.45 m per metre of rise over the straight line) peaks ~4.95 here, a metre
    // and a half over the upper floor; standing there the bot's origin is ~4.03 (and jumping in place, ~4.5)
    var FLOWN = UP[1] + 1.0;
    function log(t) { print("TOUR: " + t); }
    Window.stillSnapshotTaken.connect(function (path) { log("saved " + path); });
    // the bot by the session it announces on the bots' channel every few seconds, or else by the model it wears (the
    // avatar mixer doesn't always pass a model on, and a viewer then shows that bot as the default avatar)
    var session = null;
    function bare(id) { return String(id).replace(/[{}]/g, "").toLowerCase(); }
    Messages.subscribe("darkride.bots");
    Messages.messageReceived.connect(function (channel, message) {
        if (channel !== "darkride.bots") return;
        try { var m = JSON.parse(message); if (m.bot === ID && m.session) session = bare(m.session); } catch (e) { /* not a bot's */ }
    });
    function find() {
        var found = null;
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            var a = AvatarList.getAvatar(id);
            if (a && session && bare(id) === session) found = a;
            else if (!found && !session && a && /duck-arty\.fst/.test(a.skeletonModelURL || "")) found = a;
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
        if (!names || names.indexOf("neck_pitch") < 0) names = a.getJointNames();     // (not yet its own skeleton)
        return ["Hips", "neck_pitch"].map(function (n) {
            var i = names.indexOf(n);
            return n + " " + (i < 0 ? "?" : angle(a.getJointRotation(i)));
        }).join("  ");
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
    var lastY = null, start = 0, teleports = 0, hops = 0, peak = null, following = false;
    Script.update.connect(function () {
        var b = following && find();
        if (!b) return;
        aim(b);
        var y = b.position.y;
        if (lastY !== null && Math.abs(y - lastY) > JUMP) { teleports++; log("TELEPORT " + lastY.toFixed(2) + " -> " + y.toFixed(2)); }
        if (peak !== null && y > peak) peak = y;
        if (start && (lastY === null || Math.abs(y - lastY) > 0.005))
            log((Date.now() - start) + " ms  y " + y.toFixed(2) + "  " + joints(b));
        lastY = y;
    });
    function fail(why) { log(why); log("FAIL"); following = false; Script.stop(); }
    function flat(a, b) { return Math.sqrt((a.x - b.x) * (a.x - b.x) + (a.z - b.z) * (a.z - b.z)); }
    // then(bot) once test(bot) holds, or then(null) after ms
    function waitFor(test, ms, then) {
        var t0 = Date.now(), timer = Script.setInterval(function () {
            var b = find();
            if (b && test(b)) { Script.clearInterval(timer); then(b); }
            else if (Date.now() - t0 > ms) { Script.clearInterval(timer); then(null); }
        }, 200);
    }
    // standing still (1.5 s) at one end of the hop, where its last walk or hop ended, so a route from there starts with
    // the hop (one that only passed close by, or has wandered off while idling, would start with a walk)
    function settled(end, ms, then) {
        var last = null, since = 0;
        waitFor(function (b) {
            var p = b.position, now = Date.now();
            if (!last || flat(p, last) > 0.02) { last = p; since = now; }
            return flat(p, v(end)) < NEAR && Math.abs(p.y - end[1]) < 1.5 && now - since > 1500;
        }, ms, then);
    }
    function reach(name, end, tries, then) {
        Messages.sendMessage("darkride.bots.cmd", JSON.stringify({ bot: ID, goto: end }));
        settled(end, 120000, function (b) {
            if (b) { log("at " + name + ": " + JSON.stringify(b.position)); then(); }
            else if (tries > 1) { log("not standing at " + name + " yet; sending it again"); reach(name, end, tries - 1, then); }
            else fail("arty never stood at " + name);
        });
    }
    // a burst of pictures from the moment it's told to go; a hop counts if it gets to the other floor, flown
    function hop(name, from, goal, then) {
        var b = find();
        if (!b) { fail(name + ": arty has gone"); return; }
        var y0 = b.position.y;
        log(name + ": route starts at " + JSON.stringify(b.position) + ", " + flat(b.position, v(from)).toFixed(2) +
            " m from the hop's end");
        Messages.sendMessage("darkride.bots.cmd", JSON.stringify({ bot: ID, goto: goal }));
        log(name + ": sent to " + JSON.stringify(goal));
        start = Date.now(); peak = y0;
        var n = 0, timer = Script.setInterval(function () {
            Window.takeSnapshot(false, false, 0, name + "-" + (n < 10 ? "0" : "") + n);
            if (++n < SHOTS) return;
            Script.clearInterval(timer);
            waitFor(function (a) { return Math.abs(a.position.y - y0) > 4; }, LAND, function (a) {
                if (!a) { log(name + ": didn't change floor"); peak = null; Script.setTimeout(then, 1500); return; }
                log(name + ": on the other floor " + (Date.now() - start) + " ms after it was sent");
                Script.setTimeout(function () {         // (it may still be on its way up when seen a floor away)
                    log(name + ": highest " + peak.toFixed(2) + (peak > FLOWN ? ", flown" : ", not flown"));
                    if (peak > FLOWN) hops++;
                    peak = null;
                    then();
                }, 1500);
            });
        }, EVERY);
    }
    Script.setTimeout(function () {
        Menu.setIsOptionChecked("Names Above Heads", false);
        Menu.setIsOptionChecked("Show Overlays", false);
        Camera.mode = "independent";
        // (a new client takes a minute or so to hear of every bot)
        waitFor(function () { return true; }, 150000, function (b) {
            if (!b) { fail("no arty"); return; }
            log("arty at " + JSON.stringify(b.position) + (session ? " (by its announcements)" : " (by its model)") +
                ", wearing " + (b.skeletonModelURL || "").replace(/.*\//, ""));
            Script.setTimeout(go, 10000);                       // (its model loading)
        });
    }, 30000);              // (time for the domain to load; start the domain a few minutes before, so the bots have hatched)
    function go() {
        following = true;
        reach("the foot of the hop", DOWN, 3, function () {
            teleports = 0;                  // (getting there may have used other hops)
            hop("up", DOWN, UP, function () {
                function down() {
                    hop("down", UP, DOWN, function () {
                        log(hops + " of 2 hops, " + teleports + " teleports");
                        log(hops === 2 && !teleports ? "PASS" : "FAIL");
                        Script.stop();
                    });
                }
                settled(UP, 10000, function (a) {
                    if (a) { down(); return; }
                    var b = find();
                    log("not standing at the top of the hop (at " + JSON.stringify(b && b.position) + "); sending it back");
                    reach("the top of the hop", UP, 2, down);
                });
            });
        });
    }
}());
