//
//  crowd-watch.js: watches the wandering bots from overhead (run as a persistent script beside the bots, from the bots'
//  folder so it finds nav.json and bots.json) and says how much they bunch up and where they spend their time. Every 5 s it notes,
//  for each wanderer (not the guide or the bouncer), how many other bots are within CROWD m, whether it's under cover
//  (nav.json's covered cells: indoors) and whether it's near the landing spot (ARRIVE). Every minute and at the end
//  (after MINUTES) it prints CROWDWATCH lines: the share of samples a wanderer was in a crowd (2+ other bots close),
//  the longest any one stayed in a crowd, the share of samples outdoors and near the landing spot, and the guide's
//  furthest distance from its post (a guide milling about moves; one standing at its post doesn't).
//
(function () {
    var MINUTES = 10, CROWD = 3, ARRIVE = { x: 0, y: -1.8, z: -2 }, NEAR_ARRIVE = 8, POSTED = [];   // (the roster's guides and bouncers)
    var nav = null, columns = {}, start = 0, samples = 0, crowded = 0, outdoors = 0, landing = 0, streak = {}, longest = { s: 0 };
    var guide = null, guideFurthest = 0;
    function log(t) { print("CROWDWATCH: " + t); }
    function pct(n) { return samples ? Math.round(100 * n / samples) + "%" : "-"; }
    function covered(p) {
        var list = columns[Math.floor((p.x - nav.origin[0]) / nav.cell) * 100000 + Math.floor((p.z - nav.origin[1]) / nav.cell)];
        var best = null;
        (list || []).forEach(function (c) { if (!best || Math.abs(c[2] - p.y) < Math.abs(best[2] - p.y)) best = c; });
        return best ? (best[3] & 4) !== 0 : false;
    }
    function bots() {
        var out = [];
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            var a = AvatarList.getAvatar(id), m = a && (a.skeletonModelURL || "").match(/duck-([a-z0-9]+)\.fst/);
            if (m && a.position) out.push({ id: m[1], pos: a.position });
        });
        return out;
    }
    function sample() {
        var all = bots(), now = Date.now();
        all.forEach(function (b) {
            if (guide && b.id === guide.id) {                      // how far it strays from its post, along the ground
                var dx = b.pos.x - guide.post[0], dz = b.pos.z - guide.post[2];
                guideFurthest = Math.max(guideFurthest, Math.sqrt(dx * dx + dz * dz));
            }
            if (POSTED.indexOf(b.id) >= 0) return;
            var company = all.filter(function (o) { return o !== b && Vec3.distance(o.pos, b.pos) < CROWD; }).length;
            samples++;
            if (company >= 2) {
                crowded++;
                streak[b.id] = streak[b.id] || now;
                if (now - streak[b.id] > longest.s * 1000) longest = { s: Math.round((now - streak[b.id]) / 1000), id: b.id };
            } else delete streak[b.id];
            if (!covered(b.pos)) outdoors++;
            if (Vec3.distance(b.pos, ARRIVE) < NEAR_ARRIVE) landing++;
        });
    }
    function report(last) {
        log((last ? "RESULT " : "") + Math.round((Date.now() - start) / 60000) + " min, " + samples + " samples: in a crowd " +
            pct(crowded) + ", longest crowd " + longest.s + " s" + (longest.id ? " (" + longest.id + ")" : "") + ", outdoors " +
            pct(outdoors) + ", near the landing spot " + pct(landing) + ", guide strayed up to " + guideFurthest.toFixed(1) + " m");
    }
    Agent.isAvatar = true;
    Agent.avatarViewRadius = 100;
    Script.setTimeout(function () {
        Avatar.displayName = "Crowd watcher";
        Avatar.position = { x: ARRIVE.x, y: 40, z: -20 };          // well out of the bots' sight
        nav = Script.require(Script.resolvePath("nav.json"));
        Script.require(Script.resolvePath("bots.json")).bots.forEach(function (b) {
            if (b.role) POSTED.push(b.id);
            if (b.role === "guide" && b.post) guide = b;
        });
        nav.cells.forEach(function (c) { var k = c[0] * 100000 + c[1]; (columns[k] = columns[k] || []).push(c); });
        log("watching for " + MINUTES + " min");
        Script.setTimeout(function () {                            // the bots hatch over the first half minute or so
            start = Date.now();
            var tick = Script.setInterval(function () {
                sample();
                if (samples && Date.now() - start > MINUTES * 60000) { report(true); Script.clearInterval(tick); }
                else if (Math.floor((Date.now() - start) / 60000) !== Math.floor((Date.now() - start - 5000) / 60000)) report(false);
            }, 5000);
        }, 60000);
    }, 5000);
}());
