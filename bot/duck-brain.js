//
//  duck-brain.js: a wandering bot, a scripted agent avatar on the Microduck's skeleton (darkride-bots). Loaded by
//  duck-bot.js, the domain's persistent script, run with as many instances as bots: each wears its bot's model.
//  See docs/how-it-works.md.
//
//  It lives on nav.json (build_nav.py: every walkable 25 cm cell of the world on every level, linked where the step is
//  small and nothing blocks, with edges, walls, rocket hops and points of interest) and moves with duck-cycles.json
//  (walk, turn and idle loops and one-shot moves recorded from Pollen Robotics' Microduck policies). It picks something
//  to do, plans a route with A* (keeping away from drops, a little off walls), walks it (turning on the spot for sharp
//  corners, its height following the floor, up stairs too), and then:
//    - at an edge: faces the drop, leans its neck out to peer over ("oooh"), looks left and right, and backs away;
//    - at a wall: faces it and looks it up and down;
//    - at a point of interest: looks at it;
//    - near a visitor: watches them, and sometimes walks up to say hello with a chirp and a nod;
//    - near another bot: they face each other, trill and nod;
//    - now and then: sits down for a while, pecks at the ground, does a kick, or rolls (only with room ahead).
//  It listens: when a visitor nearby talks (their audio loudness), it stops, turns to face them and tilts its head
//  this way and that, and once they pause it answers with a chirp (or a questioning "hm?") and a nod. It never speaks,
//  only beeps, played as its own avatar audio so they come from the bot, and its jaw (if it has one) moves with them.
//  Its head is its own (look-at), blended over the walk cycle. ES5 only (the engine's script engine).
//
//  Bots (bots.json, build_bots.py): each copy of this script takes the first bot on the roster that isn't already
//  about (they announce themselves on a message channel) and becomes it: its model, its size (the gait slows or
//  quickens with size, as a real one's would: speed with the square root of size), whether it climbs stairs, a home
//  area it keeps to, and rockets in its feet for some (hopping up to and down from upper floors at the rocket hops).
//  A scripted avatar without an account has no display name: its looks tell it apart.
//
//  Voice commands (the roster's "hearing": a URL of tools/hearing.py): the bot nearest a talking visitor keeps what it
//  hears and, when they pause, has it recognised; what was said goes out on the command channel, and the bot named
//  (or, with no name, any bot close to the speaker) does it: sit, jump, roll, come here, follow me, stay, go away,
//  dance, hello. Not always: each bot has an obedience (bots.json "obedience", 0-1, default 0.7), and a refusal is a
//  sad note, a questioning "hm?" or a kick instead.
//
//  Personality (bots.json "personality", each 0-1; any left out come from the bot's id, so no two are alike):
//    energy       how long it idles, how brisk its walk, how often it sits, pecks, kicks and rolls;
//    curiosity    how much it goes to look at things (edges, walls, points of interest) and walks up to visitors;
//    shyness      how much it keeps its distance from visitors it doesn't know (it backs off if they come close);
//    chattiness   how often it chirps on its own and answers;
//    sociability  how often it seeks out other bots, and goes for a walk with a friend.
//  Memory: it remembers the visitors it has met (by name, if they have one) and how they treated it ("good" and "bad"),
//  and the bots it has met. Old friends get a warmer welcome (a trill, a hop); a shy bot isn't shy with them; it
//  avoids anyone who has scolded it more than praised it; and bots that have met often become friends and sometimes
//  walk about together. With the roster's "hearing" service running, memories are kept there (POST/GET /memory/<id>)
//  and last across restarts.
//
//  A mouse droid ("style": {"build": "mouse"}): a little box on hidden legs that scurries about at speed, turns on a
//  sixpence, pauses to chatter at walls and corners, never sits or rolls, and bolts with a squeal from any stranger who
//  comes near (it's shy whatever its personality says, unless they're an old friend).
//
//  An astromech ("build": "astromech", an R-unit sort): it glides along on its feet's wheels (no walk cycle), leaning
//  back on its legs as it drives (body and legs pivot at the ankles, its feet staying flat on the floor), keeps its neck still and turns its whole dome to look at things, whistles and warbles, screams when
//  startled, and instead of sitting or rolling spins its dome or rocks on the spot.
//  A power droid ("build": "power", a gonk): it waddles slowly on its stubby legs, honks, and never rolls.
//
//  A bot with "role": "guide" (its own loader too) waits at its post by where visitors arrive. When someone new turns
//  up and stays a moment it trundles over and offers a tour ("lines": "offer"); unless they say no, it leads them round
//  the roster's "tour" (stops, each a spot to stand, a thing to look at and a line to say), saying "this way" now and
//  then, stopping to call anyone who falls behind ("wait"), and giving up if they wander off ("lost"); at the end it
//  says goodbye ("end") and goes back to its post. "Tour" (spoken) asks it for one at any time.
//
//  A bot with "role": "bouncer" (bots.json) runs from its own loader (bot-<id>.js, which sets BOT_ID) and guards a
//  post instead of wandering: it watches everyone within GUARD_RANGE, swivels to follow them and turns to face anyone
//  well off to the side, challenges arrivals with its canned lines ("lines" in the roster: a welcome for names it
//  recognises, a challenge for the rest), answers anyone who talks to it, shifts its feet now and then and mutters.
//
(function () {
    var BASE = Script.resolvePath(".").replace(/[^\/]*$/, "");
    var SPEEDUP = 1.5;              // the policy's walk is a slow amble at this size
    var MOVE_SPEEDUP = 1.3;         // and its one-shot moves a touch slow
    var TURN_RATE = 0.6;            // rad/s when turning on the spot
    var TURN_FIRST = 0.9;           // turn on the spot when the way on is more than this far round (rad)
    var STEER = 1.2;                // rad/s of steering while walking
    var LOOK_RANGE = 4.0;           // visitors closer than this get watched
    var LISTEN_RANGE = 5.0;         // and those talking closer than this get listened to
    var LOUD = 40;                  // audio loudness (mean |sample| of 16-bit audio) that counts as talking
    var VIEW_RADIUS = 100;          // m all round in which it gets other avatars (bots must see each other to share out names)
    var FLAT_STEP = 0.12;           // m: a non-climber won't take a step bigger than this (stairs are about 0.2)
    var NECK_SIGN = 1, HEAD_PITCH_SIGN = 1, HEAD_YAW_SIGN = 1, HEAD_ROLL_SIGN = 1;   // flip if the duck looks the wrong way
    var SOUNDS = ["chirp_1", "chirp_2", "chirp_3", "chirp_4", "chirp_5", "chirp_6", "chirp_7", "chirp_8",
                  "question", "trill", "ooh", "sad", "alert", "rocket"];

    var nav = null, cyc = null, columns = {}, sounds = {};
    var me = { pos: null, yaw: 0, node: -1 };
    var state = { name: "start", t: 0 }, path = [], pathAt = 0, plannedAt = 0, progressAt = 0, lastPos = null;
    var cycleName = "idle", cycleTime = 0;
    var order = null, rotations = [], extraJoints = [], jawIndex = -1, jaw = 0;
    var look = { target: null, weight: 0, neck: 0, headPitch: 0, headYaw: 0, headRoll: 0 };
    var ear = { who: null, pos: null, heardAt: 0, talk: 0, replied: true, replyAt: 0, tilt: 0, tiltAt: 0, nodUntil: 0,
                boredUntil: 0, loudest: 0 };
    var visitorSince = {}, greetedAt = {}, lastTick = 0, lastSaid = 0, hopsOf = {}, flame = null;
    var GUARD_RANGE = 15, ARRIVE_RANGE = 12, guard = null, speech = [];
    var heardChunks = [], HEARD_KEEP = 8000;          // what it heard in the last 8 s, as {t, audio}
    var following = null, stayUntil = 0, tour = null, guide = null;
    var mind = { energy: 0.5, curiosity: 0.5, shyness: 0.5, chattiness: 0.5, sociability: 0.5 };
    var memory = { visitors: {}, bots: {} }, memoryDirty = false, memorySavedAt = 0, PACE = 1, MOUSE = false, ASTRO = false, POWER = false;
    var lean = 0, LEAN_DRIVE = 0.24, ANKLE_Y = -0.38;    // an astromech's lean back as it drives (rad); its ankles' height
    var roster = null, bot = null, SIZE = 1, GAIT = 1, area = null;

    function log(t) { print("duck " + (Agent.sessionUUID || "").substr(1, 4) + ": " + t); }
    function rnd(a, b) { return a + Math.random() * (b - a); }
    function pick(list) { return list[Math.floor(Math.random() * list.length)]; }
    function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
    function wrap(a) { while (a > Math.PI) a -= 2 * Math.PI; while (a < -Math.PI) a += 2 * Math.PI; return a; }
    function yawQuat(yaw) { return Quat.angleAxis(yaw * 180 / Math.PI, Vec3.UNIT_Y); }
    function yawTowards(p) { return Math.atan2(-(p.x - me.pos.x), -(p.z - me.pos.z)); }

    // ── its voice ───────────────────────────────────────────────────────────
    // "chirp" is any of the eight short ones; one sound at a time, and not too chatty
    function say(name, chance) {
        if (chance !== undefined && Math.random() > chance) return false;
        if (MOUSE) name = { alert: "mouse_squeal", question: "mouse_squeal", sad: "mouse_squeal", ooh: "mouse_squeal" }[name] ||
                          (/^(rocket|servo|clank|whirr)$/.test(name) ? name : "mouse_" + (1 + Math.floor(Math.random() * 3)));
        else if (ASTRO) name = { alert: "astro_scream", sad: "sad", ooh: "astro_scream" }[name] ||
                          (/^(rocket|servo|clank|whirr|sad)$/.test(name) ? name : "astro_" + (1 + Math.floor(Math.random() * 5)));
        else if (POWER) name = { sad: "sad" }[name] ||
                          (/^(rocket|servo|clank|whirr)$/.test(name) ? name : "gonk_" + (1 + Math.floor(Math.random() * 2)));
        else if (name === "chirp") name = "chirp_" + (1 + Math.floor(Math.random() * 8));
        if (!sounds[name]) sounds[name] = SoundCache.getSound(BASE + "sounds/" + name + ".wav");   // a bot's own lines
        var s = sounds[name];
        if (!s || !s.downloaded || Agent.isPlayingAvatarSound || Date.now() - lastSaid < 600) return false;
        Agent.playAvatarSound(s);
        lastSaid = Date.now();
        return true;
    }

    // a sequence of lines, said one after another with a pause between (the bouncer's challenges)
    function sayInTurn(names) { speech = speech.concat(names); }
    function speak(now) {
        if (speech.length && !Agent.isPlayingAvatarSound && now - lastSaid > 900 && say(speech[0])) speech.shift();
    }
    function line(kind) {                          // one of the bot's lines of a kind, or a sequence of them
        var l = bot.lines && bot.lines[kind];
        return l && l.length ? pick(l) : null;
    }

    // ── the map ─────────────────────────────────────────────────────────────
    function cellPos(i) {
        var c = nav.cells[i];
        return { x: nav.origin[0] + (c[0] + 0.5) * nav.cell, y: c[2], z: nav.origin[1] + (c[1] + 0.5) * nav.cell };
    }
    // a cell this bot may be on (inside its home area, if it has one), and a step it may take (non-climbers: flat only)
    function inHatch(i) {                        // the roster's hatching area for bots without a home (bots.json "hatch")
        var h = roster.hatch && roster.areas[roster.hatch];
        if (!h) return true;
        var p = cellPos(i);
        return p.x >= h.x[0] && p.x <= h.x[1] && p.y >= h.y[0] && p.y <= h.y[1] && p.z >= h.z[0] && p.z <= h.z[1];
    }
    function inArea(i) {
        if (i < 0) return false;
        if (!area) return true;
        var p = cellPos(i);
        return p.x >= area.x[0] && p.x <= area.x[1] && p.y >= area.y[0] && p.y <= area.y[1] &&
               p.z >= area.z[0] && p.z <= area.z[1];
    }
    // only cells it can get to from where it hatched (so a bot without stairs never plans a route it can't take)
    var reach = null;
    function allowed(i) { return inArea(i) && (!reach || reach[i] === 1); }
    function stepOk(u, v) {
        return bot.climbs !== false || Math.abs(nav.cells[v][2] - nav.cells[u][2]) <= FLAT_STEP;
    }
    function canStep(u, v) { return allowed(v) && stepOk(u, v); }
    function findReach(start) {
        var seen = new Array(nav.cells.length), stack = [start];
        seen[start] = 1;
        while (stack.length) {
            var u = stack.pop(), next = nav.links[u];
            if (bot.rockets && hopsOf[u]) next = next.concat(hopsOf[u]);
            for (var k = 0; k < next.length; k++) {
                var v = next[k];
                if (seen[v] || !inArea(v) || (!isHop(u, v) && !stepOk(u, v))) continue;
                seen[v] = 1; stack.push(v);
            }
        }
        return seen;
    }
    function isHop(u, v) { return !!(hopsOf[u] && hopsOf[u].indexOf(v) >= 0); }
    function isEdge(i) { return (nav.cells[i][3] & 1) !== 0; }
    function isWall(i) { return (nav.cells[i][3] & 2) !== 0; }
    function nearestCell(p, maxRise) {
        var cx = Math.floor((p.x - nav.origin[0]) / nav.cell), cz = Math.floor((p.z - nav.origin[1]) / nav.cell);
        var best = -1, bestD = 1e9;
        for (var r = 0; r <= 6 && best < 0; r++) {
            for (var dx = -r; dx <= r; dx++) {
                for (var dz = -r; dz <= r; dz++) {
                    var list = columns[(cx + dx) * 100000 + (cz + dz)];
                    if (!list) continue;
                    for (var k = 0; k < list.length; k++) {
                        var q = cellPos(list[k]), dy = Math.abs(q.y - p.y);
                        if (dy > (maxRise || 1.5)) continue;
                        var d = (q.x - p.x) * (q.x - p.x) + (q.z - p.z) * (q.z - p.z) + dy * dy * 4;
                        if (d < bestD) { bestD = d; best = list[k]; }
                    }
                }
            }
        }
        return best;
    }
    // the 8 directions with no linked neighbour: where the drop or the wall is
    function openSides(i) {
        var c = nav.cells[i], have = {};
        nav.links[i].forEach(function (j) { have[(nav.cells[j][0] - c[0]) + "," + (nav.cells[j][1] - c[1])] = true; });
        var out = [];
        for (var dx = -1; dx <= 1; dx++) for (var dz = -1; dz <= 1; dz++) {
            if ((dx || dz) && !have[dx + "," + dz]) out.push({ x: dx, z: dz });
        }
        return out;
    }
    function facingOf(sides) {
        var x = 0, z = 0;
        sides.forEach(function (s) { x += s.x; z += s.z; });
        return Math.atan2(-x, -z);     // Darkride yaw for facing that way (forward is -z)
    }
    // open floor straight ahead for this far, on the same level, with no drop or wall: room for a roll
    function clearAhead(metres) {
        var fwd = { x: -Math.sin(me.yaw), z: -Math.cos(me.yaw) };
        for (var s = 0.25; s <= metres; s += 0.25) {
            var p = { x: me.pos.x + fwd.x * s, y: me.pos.y, z: me.pos.z + fwd.z * s };
            var c = nearestCell(p, 0.15);
            if (c < 0 || !allowed(c) || isEdge(c) || isWall(c)) return false;
            var q = cellPos(c);
            if (Math.abs(q.x - p.x) > nav.cell || Math.abs(q.z - p.z) > nav.cell) return false;
        }
        return true;
    }

    // A* over the cells; drops cost a lot unless they're where we're going, walls a little
    function route(from, to) {
        if (from < 0 || to < 0) return [];
        var g = {}, came = {}, closed = {};
        var goal = cellPos(to);
        var heap = [[0, from]];
        g[from] = 0;
        var steps = 0;
        while (heap.length && steps++ < 60000) {
            var top = heap[0], last = heap.pop();
            if (heap.length) { heap[0] = last; siftDown(heap, 0); }
            var u = top[1];
            if (u === to) break;
            if (closed[u]) continue;
            closed[u] = true;
            var pu = cellPos(u), links = nav.links[u];
            if (bot.rockets && hopsOf[u]) links = links.concat(hopsOf[u]);
            for (var k = 0; k < links.length; k++) {
                var v = links[k], hop = isHop(u, v);
                if (closed[v] || (hop ? !allowed(v) : !canStep(u, v))) continue;
                var pv = cellPos(v);
                var cost = Math.sqrt((pv.x - pu.x) * (pv.x - pu.x) + (pv.z - pu.z) * (pv.z - pu.z)) + Math.abs(pv.y - pu.y);
                if (hop) cost += 6;                                     // a rocket hop is a treat, not the usual way
                if (isEdge(v) && v !== to) cost += 3;
                if (isWall(v)) cost += 0.4;
                var ng = g[u] + cost;
                if (g[v] === undefined || ng < g[v]) {
                    g[v] = ng; came[v] = u;
                    var h = Math.sqrt((goal.x - pv.x) * (goal.x - pv.x) + (goal.z - pv.z) * (goal.z - pv.z)) + Math.abs(goal.y - pv.y);
                    heap.push([ng + h, v]); siftUp(heap, heap.length - 1);
                }
            }
        }
        if (came[to] === undefined && from !== to) return [];
        var out = [to];
        while (out[0] !== from) out.unshift(came[out[0]]);
        return out;
    }
    function siftUp(h, i) {
        while (i > 0) { var p = (i - 1) >> 1; if (h[p][0] <= h[i][0]) break; var t = h[p]; h[p] = h[i]; h[i] = t; i = p; }
    }
    function siftDown(h, i) {
        for (;;) {
            var l = 2 * i + 1, r = l + 1, m = i;
            if (l < h.length && h[l][0] < h[m][0]) m = l;
            if (r < h.length && h[r][0] < h[m][0]) m = r;
            if (m === i) break;
            var t = h[m]; h[m] = h[i]; h[i] = t; i = m;
        }
    }

    // ── others ──────────────────────────────────────────────────────────────
    function others() {
        var out = [];
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            if (!id || id === Agent.sessionUUID) return;
            var a = AvatarList.getAvatar(id);
            if (!a || !a.position) return;
            out.push({ id: id, pos: a.position, loud: a.audioLoudness || 0,
                       duck: /\/duck(-[a-z0-9]+)?\.fst/.test(a.skeletonModelURL || "") });
        });
        return out;
    }
    function dist(a, b) { return Vec3.distance(a, b); }

    // ── personality and memory ──────────────────────────────────────────────
    // a trait from the roster, or one of its own from its id (0.15-0.85), so bots without one still differ
    function trait(name) {
        var given = bot.personality && bot.personality[name];
        if (given !== undefined) return clamp(given, 0, 1);
        var h = 2166136261, key = bot.id + ":" + name;
        for (var k = 0; k < key.length; k++) { h ^= key.charCodeAt(k); h = (h * 16777619) >>> 0; }
        return 0.15 + 0.7 * (h % 1000) / 1000;
    }
    function setMind() {
        Object.keys(mind).forEach(function (k) { mind[k] = trait(k); });
        MOUSE = !!(bot.style && bot.style.build === "mouse");
        ASTRO = !!(bot.style && bot.style.build === "astromech");
        POWER = !!(bot.style && bot.style.build === "power");
        if (MOUSE) { mind.shyness = Math.max(mind.shyness, 0.9); mind.energy = Math.max(mind.energy, 0.7); }
        // a lively bot walks a little quicker; a mouse droid scurries, an astromech rolls along, a power droid plods
        PACE = (0.85 + 0.3 * mind.energy) * (MOUSE ? 2.6 : ASTRO ? 1.15 : POWER ? 0.55 : 1);
        log("personality " + JSON.stringify(mind).replace(/(\d\.\d\d)\d+/g, "$1"));
    }
    // who a visitor is to it: their name if they have one (remembered across visits), else this visit's session
    function visitorKey(id) {
        var a = AvatarList.getAvatar(id), n = a && (a.sessionDisplayName || a.displayName) || "";
        return n && !/^anonymous/i.test(n) ? "name:" + n.toLowerCase() : "session:" + id;
    }
    function knownVisitor(id) { return memory.visitors[visitorKey(id)]; }
    function remember(id, change) {
        var k = visitorKey(id), v = memory.visitors[k] = memory.visitors[k] || { met: 0, likes: 0, first: Date.now() };
        if (change.met) v.met++;
        if (change.likes) v.likes = clamp(v.likes + change.likes, -5, 5);
        v.last = Date.now();
        if (k.indexOf("name:") === 0) memoryDirty = true;  // only named visitors are worth keeping
        return v;
    }
    function friendly(id) { var v = knownVisitor(id); return v && v.met >= 2 && v.likes >= 0; }
    function wary(id) { var v = knownVisitor(id); return v && v.likes < 0; }
    function botId(session) { var h = heard[String(session)]; return h && h.bot; }
    function botFriend(session) { var b = botId(session); return b && (memory.bots[b] || 0) >= 3; }
    function memoryUrl() {
        var u = roster.memory || (roster.hearing && roster.hearing.replace(/\/hear$/, "/memory"));
        return u ? u + "/" + bot.id : null;
    }
    function loadMemory() {
        var u = memoryUrl();
        if (!u) return;
        var xhr = new XMLHttpRequest();
        xhr.open("GET", u, true);
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== 4 || xhr.status !== 200) return;
            try {
                var m = JSON.parse(xhr.responseText);
                if (m && m.visitors) {
                    memory = { visitors: m.visitors, bots: m.bots || {} };
                    log("remembers " + Object.keys(memory.visitors).length + " visitors and " + Object.keys(memory.bots).length + " bots");
                }
            } catch (e) { /* nothing kept */ }
        };
        xhr.send();
    }
    function saveMemory(now) {
        var u = memoryUrl();
        if (!u || !memoryDirty || now - memorySavedAt < 60000) return;
        memoryDirty = false; memorySavedAt = now;
        var keep = { visitors: {}, bots: memory.bots };
        Object.keys(memory.visitors).forEach(function (k) { if (k.indexOf("name:") === 0) keep.visitors[k] = memory.visitors[k]; });
        var xhr = new XMLHttpRequest();
        xhr.open("POST", u, true);
        xhr.send(JSON.stringify(keep));
    }
    // a shy bot backs off from a stranger who comes too close (not from friends, and not while it's busy)
    function shyOf(near, now) {
        if (mind.shyness < 0.45 || following || now < stayUntil) return false;
        if (!(state.name === "idle" || state.name === "walk" || state.name === "act" && state.kind !== "listen" && state.kind !== "greet")) return false;
        var close = 0.9 + 1.6 * mind.shyness;
        for (var k = 0; k < near.length; k++) {
            var o = near[k];
            if (o.duck || friendly(o.id) || dist(o.pos, me.pos) > close * SIZE + 0.5) continue;
            if (now - (ear.shyAt || 0) < 6000) return false;
            ear.shyAt = now;
            var away = Vec3.normalize(Vec3.subtract(me.pos, o.pos));
            var c = nearestCell(Vec3.sum(me.pos, Vec3.multiply((MOUSE ? 5 : 2.5) + 2 * mind.shyness, away)), 0.6);
            if (allowed(c) && !isEdge(c)) {
                say(MOUSE || Math.random() < 0.5 ? "alert" : "question");
                goTo(c, { name: "turn-to", at: o.pos }, "shy of a visitor");
                return true;
            }
        }
        return false;
    }

    // ── deciding what to do ─────────────────────────────────────────────────
    function goTo(cell, then, why) {
        var t0 = Date.now();
        var p = route(me.node, cell);
        if (Date.now() - t0 > 100) log("route of " + p.length + " cells took " + (Date.now() - t0) + " ms");
        if (p.length < 2) { idle(rnd(1, 3)); return; }
        path = p; pathAt = 1; plannedAt = Date.now(); progressAt = Date.now(); lastPos = me.pos;
        state = { name: "walk", then: then, why: why };
    }
    // (a lively bot idles less, a lazy one more)
    function idle(seconds) { state = { name: "idle", until: Date.now() + seconds * 1000 * (1.5 - mind.energy) }; }

    function decide() {
        var near = others(), now = Date.now();
        if (now < stayUntil) { idle(4); return; }                    // told to stay
        if (following) {                                             // told to follow: keep close to them
            var f = following.who && AvatarList.getAvatar(following.who);
            if (!f || !f.position || now > following.until) { following = null; say(f && f.position ? "trill" : "chirp", 0.5); }
            else {
                if (dist(f.position, me.pos) > 2.2 + SIZE) comeTo(f.position, { name: "idle" });
                else idle(1.5);
                return;
            }
        }
        // a visitor who's been close a while: go and say hello now and then (sooner for a curious bot, and for old
        // friends; a shy one mostly leaves strangers be, and anyone who scolded it is left alone)
        var noticeAfter = 6000 - 4500 * mind.curiosity;
        for (var k = 0; k < near.length; k++) {
            var o = near[k], d = dist(o.pos, me.pos);
            if (!o.duck && d < LOOK_RANGE * (0.8 + 0.5 * mind.curiosity) && now - (visitorSince[o.id] || now) > noticeAfter &&
                now - (greetedAt[o.id] || 0) > (friendly(o.id) ? 45000 : 90000)) {
                greetedAt[o.id] = now;
                if (wary(o.id)) { say("sad", 0.5); continue; }
                if (!friendly(o.id) && (MOUSE || Math.random() < mind.shyness * 0.8)) continue;
                var spot = nearestCell(Vec3.sum(o.pos, Vec3.multiply(0.8 + 0.5 * SIZE, Vec3.normalize(Vec3.subtract(me.pos, o.pos)))), 1.2);
                if (allowed(spot)) { goTo(spot, { name: "greet", who: o.id }, friendly(o.id) ? "an old friend" : "a visitor"); return; }
            }
            var seekAfter = (botFriend(o.id) ? 60000 : 200000) * (1.3 - mind.sociability);
            if (o.duck && d < 6 + 12 * mind.sociability && now - (greetedAt[o.id] || 0) > seekAfter) {
                greetedAt[o.id] = now;
                var meet = nearestCell(Vec3.mix(me.pos, o.pos, 0.4), 1.2);
                if (allowed(meet)) { goTo(meet, { name: "greet", who: o.id, duck: true }, botFriend(o.id) ? "a friend" : "another bot"); return; }
            }
        }
        // something on the spot now and then: a sit, a peck at the ground, a kick, or a roll if there's room
        if (MOUSE && Math.random() < 0.3) { say("chirp", 0.5); idle(rnd(0.5, 2)); return; }    // a pause, a chatter
        if (ASTRO && Math.random() < 0.1 + 0.25 * mind.energy) {                             // a dome spin and a whistle
            say("chirp");
            state = { name: "act", kind: "spin", start: Date.now(), until: Date.now() + rnd(2500, 4500), headOverride: true,
                      dir: Math.random() < 0.5 ? -1 : 1 };
            return;
        }
        if (!MOUSE && !ASTRO && me.node >= 0 && !isEdge(me.node) && Math.random() < 0.1 + 0.25 * mind.energy) {
            var m = Math.random();
            if (m < 0.3) startMove("peck");
            else if (m < 0.55) startMove("sit", null, rnd(6, 20));
            else if (m < 0.75 && !POWER && clearAhead(2.5 * SIZE)) startMove("roll");
            else startMove(Math.random() < 0.5 ? "kick_left" : "kick_right");
            return;
        }
        say("chirp", 0.03 + 0.25 * mind.chattiness);
        // a curious bot goes to look at things more than it just wanders
        var target = -1, then = null, why = "";
        var roll = Math.random() < 0.4 + 0.5 * mind.curiosity ? Math.random() * 0.72 : 0.72 + Math.random() * 0.28;
        if (MOUSE && roll < 0.42) roll = 0.3;                         // a mouse droid hugs walls, never peers over
        if (roll < 0.25) {                                            // peer over an edge
            target = pickCell(function (i) { return isEdge(i); });
            then = { name: "peer" }; why = "an edge";
        } else if (roll < 0.42) {                                     // study a wall
            target = pickCell(function (i) { return isWall(i) && !isEdge(i); });
            then = { name: "wall" }; why = "a wall";
        } else if (roll < 0.72 && nav.pois.length) {                  // look at something
            var poi = pick(nav.pois), p = { x: poi.p[0], y: poi.p[1], z: poi.p[2] };
            var c = nearestCell(Vec3.sum(p, { x: rnd(-1.5, 1.5), y: -1, z: rnd(-1.5, 1.5) }), 3);
            target = allowed(c) ? c : -1; then = { name: "look", at: p }; why = poi.kind;
        } else {                                                      // wander
            target = pickCell(function (i) { return !isEdge(i) && !isWall(i); });
            then = { name: "idle" }; why = "somewhere";
        }
        if (target >= 0) goTo(target, then, why); else idle(2);
    }
    // a random cell of a kind, not too near and not too far (so walks stay a few metres to a couple of dozen)
    function pickCell(ok) {
        for (var tries = 0; tries < 400; tries++) {
            var i = Math.floor(Math.random() * nav.cells.length);
            if (!ok(i) || !allowed(i)) continue;
            var d = dist(cellPos(i), me.pos);
            if (d > 3 && d < 25) return i;
        }
        return -1;
    }

    // ── listening ───────────────────────────────────────────────────────────
    // the nearest visitor making noise is the one we listen to; when they pause, we answer
    function hear(near, dt, now) {
        var speaker = null, best = LISTEN_RANGE;
        near.forEach(function (o) {
            var d = dist(o.pos, me.pos);
            if (!o.duck && o.loud > LOUD && d < best) { best = d; speaker = o; }
            if (!o.duck && d < LISTEN_RANGE) ear.loudest = Math.max(ear.loudest, o.loud);
        });
        keepHearing();
        if (speaker) {
            if (ear.who !== speaker.id) { ear.who = speaker.id; ear.talk = 0; log("listening to a visitor"); }
            if (!ear.talkStart || now - ear.heardAt > 1500) ear.talkStart = now;
            ear.heardAt = now; ear.talk += dt; ear.replied = false;
            ear.replyAt = now + rnd(650, 1100);        // a beat after they stop (gaps between words are shorter)
        }
        if (ear.who) {
            near.forEach(function (o) { if (o.id === ear.who) ear.pos = Vec3.sum(o.pos, { x: 0, y: 0.6, z: 0 }); });
            if (!ear.replied && now > ear.replyAt && ear.talk > 0.35) {
                ear.replied = true;
                if (bot.role === "bouncer") { var r = line("reply"); if (r) sayInTurn([r]); }
                else if (roster.hearing && nearestBotTo(ear.pos, near)) recognise(now, ear.who, ear.pos);
                else say(ear.talk > 2.5 && Math.random() < 0.5 ? "question" : "chirp");
                ear.nodUntil = now + 900;
                ear.talk = 0;
            }
            // tilt the head one way and the other while they talk, as if trying to follow
            if (now - ear.heardAt < 3000) {
                if (now > ear.tiltAt) { ear.tilt = pick([-0.35, -0.2, 0, 0.25, 0.4]); ear.tiltAt = now + rnd(1200, 3200); }
            } else ear.tilt = 0;
            if (now - ear.heardAt > 8000) { ear.who = null; ear.tilt = 0; }
        }
    }
    function listening(now) { return ear.who && now - ear.heardAt < 4000; }

    // ── voice commands ──────────────────────────────────────────────────────
    function keepHearing() {
        if (!roster.hearing || bot.role === "bouncer") return;
        var chunk = Agent.takeHeardAudio();
        if (!chunk || !chunk.byteLength) return;
        var now = Date.now();
        heardChunks.push({ t: now, audio: chunk });
        while (heardChunks.length && heardChunks[0].t < now - HEARD_KEEP) heardChunks.shift();
    }
    // am I the bot nearest this speaker? (only one has each thing said recognised)
    function nearestBotTo(p, near) {
        var mine = dist(p, me.pos);
        return !near.some(function (o) { return o.duck && dist(o.pos, p) < mine - 0.05; });
    }
    function recognise(now, who, where) {
        // what it heard from just before they started talking (cut by when it arrived: the mixer's pace varies)
        var from = ear.talkStart - 700, keep = heardChunks.filter(function (c) { return c.t >= from; }), size = 0, at = 0;
        keep.forEach(function (c) { size += c.audio.byteLength; });
        var all = new Uint8Array(size);
        keep.forEach(function (c) { all.set(new Uint8Array(c.audio), at); at += c.audio.byteLength; });
        heardChunks = [];
        var xhr = new XMLHttpRequest();
        xhr.open("POST", roster.hearing, true);
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== 4) return;
            var text = "";
            try { text = JSON.parse(xhr.responseText).text || ""; } catch (e) { /* no answer */ }
            var act = commandIn(text);
            log("heard \"" + text + "\"" + (act ? ": " + act.act + (act.bot ? " for " + act.bot : "") : ""));
            if (!act) { say(Math.random() < 0.4 ? "question" : "chirp"); return; }
            Messages.sendMessage(CHANNEL + ".cmd", JSON.stringify({ bot: act.bot || null, act: act.act, near: [where.x, where.y, where.z],
                                                                     speaker: who }));
        };
        xhr.send(all.buffer);
    }
    // what was said, as {act, bot}: the first command word, and a bot's name if one was said
    var WORDS = { tour: "tour", yes: "yes", okay: "yes", sure: "yes",
                  sit: "sit", jump: "jump", roll: "roll", come: "come", here: "come", follow: "follow", stay: "stay",
                  stop: "stay", wait: "stay", away: "away", dance: "dance", spin: "dance", hello: "hello", hi: "hello",
                  good: "good", bad: "bad", no: "bad" };
    function commandIn(text) {
        var words = text.toLowerCase().split(/\s+/), act = null, named = null;
        words.forEach(function (w) {
            if (!act && WORDS[w]) act = WORDS[w];
            roster.bots.forEach(function (b) { if (b.name.toLowerCase() === w) named = b.id; });
        });
        return act ? { act: act, bot: named } : null;
    }
    // a command for this bot (or for any bot close to the speaker): do it, mostly
    function obey(m) {
        var now = Date.now(), at = m.near ? { x: m.near[0], y: m.near[1], z: m.near[2] } : me.pos;
        if (bot.role === "guide") { guideObey(m); return; }
        if (bot.role || state.name === "hop" || state.name === "move" || m.act === "tour" || m.act === "yes") return;
        if (!m.bot && dist(at, me.pos) > LISTEN_RANGE + 1) return;
        var face = Math.atan2(-(at.x - me.pos.x), -(at.z - me.pos.z));
        if (m.act === "good") { if (m.speaker) remember(m.speaker, { likes: 1 }); say("trill"); look.nod = 0.3; return; }
        if (m.act === "bad") { if (m.speaker) remember(m.speaker, { likes: -1 }); say("sad"); return; }
        var willing = (bot.obedience !== undefined ? bot.obedience : 0.7) + (m.speaker && friendly(m.speaker) ? 0.15 : 0) -
                      (m.speaker && wary(m.speaker) ? 0.3 : 0);
        if (Math.random() > willing) {
            log("won't " + m.act);
            var refuse = Math.random();
            if (refuse < 0.4) say("sad");
            else if (refuse < 0.75) say("question");
            else { say("chirp"); startMove(Math.random() < 0.5 ? "kick_left" : "kick_right"); }
            return;
        }
        if ((MOUSE || ASTRO) && (m.act === "sit" || m.act === "roll") || POWER && m.act === "roll") { say("question"); return; }
        log("doing " + m.act);
        following = null;
        if (m.act === "sit") { say("chirp_5"); startMove("sit", null, rnd(8, 15)); }
        else if (m.act === "jump") jumpInPlace();
        else if (m.act === "roll") { if (clearAhead(2.5 * SIZE)) startMove("roll"); else { say("sad"); jumpInPlace(); } }
        else if (m.act === "come") comeTo(at, null);
        else if (m.act === "follow") { following = { who: m.speaker, until: now + 90000 }; say("trill"); comeTo(at, null); }
        else if (m.act === "stay") { stayUntil = now + 45000; say("chirp"); idle(3); }
        else if (m.act === "away") {
            say("sad");
            var far = pickCell(function (i) { return dist(cellPos(i), at) > 10 && !isEdge(i); });
            if (far >= 0) goTo(far, { name: "idle" }, "sent away");
        } else if (m.act === "dance") {
            say("trill");
            state = { name: "turn", yaw: wrap(me.yaw + 3.0), after: { name: "turn", yaw: wrap(me.yaw + 6.0),
                      after: { name: "turn", yaw: me.yaw, after: { name: "idle", until: now + 2000 } } } };
        } else if (m.act === "hello") {
            state = { name: "turn", yaw: face, after: { name: "act", kind: "greet", start: now, until: now + 3500,
                                                        lookAt: Vec3.sum(at, { x: 0, y: 0.6, z: 0 }) } };
        }
    }
    function comeTo(at, then) {
        var away = Vec3.normalize(Vec3.subtract(me.pos, at));
        var c = nearestCell(Vec3.sum(at, Vec3.multiply(0.8 + 0.5 * SIZE, away)), 1.5);
        if (allowed(c)) goTo(c, then || { name: "greet", who: null }, "called");
        else say("question");
    }
    // a little jump where it stands: a crouch and a spring (with a puff of flame for a bot with rockets)
    function jumpInPlace() {
        say(bot.rockets ? "rocket" : "trill");
        state = { name: "hop", from: me.pos, to: me.pos, next: me.node, t: 0, flight: 0.7 * GAIT, arc: 0.45 * SIZE,
                  inPlace: true, lit: !bot.rockets, after: { name: "idle", until: Date.now() + 1500 } };
    }

    // ── each tick ───────────────────────────────────────────────────────────
    function tick(dt) {
        var now = Date.now(), near = others();
        near.forEach(function (o) {
            if (dist(o.pos, me.pos) < LOOK_RANGE) {
                if (!visitorSince[o.id]) { visitorSince[o.id] = now; if (!o.duck && !bot.role) say("alert", 0.4); }
            } else delete visitorSince[o.id];
        });
        // whoever's closest within range gets watched, unless we're busy peering
        var watch = null, best = LOOK_RANGE;
        near.forEach(function (o) {
            var d = dist(o.pos, me.pos);
            if (d < best) { best = d; watch = Vec3.sum(o.pos, { x: 0, y: o.duck ? 0 : 0.6, z: 0 }); }
        });
        hear(near, dt, now);
        speak(now);
        if (bot.role === "bouncer") { guardTick(dt, now, near); return; }

        // someone talking to us: stop what we're doing (unless mid-move or peering over a drop) and face them
        if (bot.role === "guide" && tour && state.name === "walk") keepTogether(now);
        if (listening(now) && now - ear.heardAt < 500 && now > ear.boredUntil && !tour &&
            (state.name === "walk" || state.name === "idle" ||
             state.name === "act" && (state.kind === "look" || state.kind === "wall" || state.kind === "greet"))) {
            state = { name: "turn", yaw: yawTowards(ear.pos),
                      after: { name: "act", kind: "listen", start: now, until: now + 6000 } };
        }

        if (bot.role !== "guide" && shyOf(near, now)) { /* backing off */ }
        else if (state.name === "start" || state.name === "idle" && now > state.until) {
            if (bot.role === "guide") guideDecide(near, now); else decide();
        }
        if (state.name === "walk" && Math.random() < dt * 0.02 * mind.chattiness) say("chirp", 0.6);
        saveMemory(now);

        if (state.name === "walk") walk(dt, now);
        else if (state.name === "turn") turnTo(dt, state.yaw, state.after);
        else if (state.name === "act") act(dt, now);
        else if (state.name === "move") moving(dt, now);
        else if (state.name === "hop") hopping(dt, now);
        else setCycle("idle");

        var lookAt = listening(now) ? ear.pos : state.name === "act" && state.lookAt ? state.lookAt : watch;
        if (state.name === "move" && !state.sat) lookAt = null;
        steerHead(lookAt, dt, now, state.name === "act" ? state.headOverride : null);
    }

    // ── a guide ─────────────────────────────────────────────────────────────
    function guideDecide(near, now) {
        var g = guide = guide || { seen: {}, offered: {}, nextMutter: now + 90000 };
        if (tour) {
            var v = AvatarList.getAvatar(tour.who);
            if (!v || !v.position) { endTour("lost"); return; }
            if (now < tour.start) { idle(0.8); return; }                  // waiting to see if they'll say no
            var d = dist(v.position, me.pos);
            if (d > 9) {                                                  // they've fallen behind: wait, and call
                tour.lostSince = tour.lostSince || now;
                if (now - tour.lostSince > 30000) { endTour("lost"); return; }
                waitFor(v.position, now);
                return;
            }
            tour.lostSince = 0;
            if (tour.stop >= bot.tour.length) { endTour("end"); return; }
            var s = bot.tour[tour.stop], c = nearestCell({ x: s.at[0], y: s.at[1], z: s.at[2] }, 1.0);
            if (!allowed(c)) { log("can't reach tour stop " + (tour.stop + 1)); tour.stop++; idle(0.2); return; }
            if (tour.stop > 0 && Math.random() < 0.6) { var f = line("follow"); if (f) sayInTurn([].concat(f)); }
            goTo(c, { name: "stop", i: tour.stop }, "tour stop " + (tour.stop + 1));
            return;
        }
        // someone new who's stayed a moment, not offered one lately: go and offer them a tour
        var post = bot.post ? { x: bot.post[0], y: bot.post[1], z: bot.post[2] } : me.pos;
        for (var k = 0; k < near.length; k++) {
            var o = near[k];
            if (o.duck || heard[String(o.id)] || dist(o.pos, post) > ARRIVE_RANGE) { delete g.seen[o.id]; continue; }
            g.seen[o.id] = g.seen[o.id] || now;
            var key = visitorKey(o.id);
            if (now - g.seen[o.id] > 4000 && now - (g.offered[key] || 0) > 600000) {
                g.offered[key] = now;
                var spot = nearestCell(Vec3.sum(o.pos, Vec3.multiply(1.6, Vec3.normalize(Vec3.subtract(me.pos, o.pos)))), 1.2);
                if (allowed(spot)) { say("chirp"); goTo(spot, { name: "offer", who: o.id }, "a newcomer"); return; }
            }
        }
        // otherwise back to its post, and wait there
        if (bot.post && dist(me.pos, post) > 1.2) {
            var home = nearestCell(post, 1.0);
            if (allowed(home)) { goTo(home, { name: "post" }, "back to its post"); return; }
        }
        if (now > g.nextMutter) {
            g.nextMutter = now + rnd(120000, 300000);
            var m = line("idle");
            if (m) sayInTurn([].concat(m));
        }
        idle(rnd(2, 4));
    }
    // on the way to a stop: if they're lagging, stop and wait for them
    function keepTogether(now) {
        var v = AvatarList.getAvatar(tour.who);
        if (v && v.position && dist(v.position, me.pos) > 7) waitFor(v.position, now);
    }
    function waitFor(at, now) {
        state = { name: "turn", yaw: yawTowards(at),
                  after: { name: "act", kind: "wait", start: now, until: now + 3000, lookAt: Vec3.sum(at, { x: 0, y: 0.6, z: 0 }) } };
        if (now - (tour.calledAt || 0) > 9000) { tour.calledAt = now; var w = line("wait"); if (w) sayInTurn([].concat(w)); else say("question"); }
    }
    function endTour(why) {
        var l = line(why);
        if (l) sayInTurn([].concat(l));
        log("tour over: " + why);
        tour = null;
        idle(3);
    }
    function guideObey(m) {
        var now = Date.now(), at = m.near ? { x: m.near[0], y: m.near[1], z: m.near[2] } : me.pos;
        if (!m.bot && dist(at, me.pos) > LISTEN_RANGE + 2) return;
        if (m.act === "good") { if (m.speaker) remember(m.speaker, { likes: 1 }); say("trill"); return; }
        if (tour && (m.act === "bad" || m.act === "away" || m.act === "stay")) {
            if (now < tour.start) { var no = line("decline"); if (no) sayInTurn([].concat(no)); tour = null; idle(2); }
            else endTour("end");
            return;
        }
        if (tour && m.act === "yes") { tour.start = now; return; }
        if (!tour && (m.act === "tour" || m.act === "yes" || m.act === "follow")) {
            log("asked for a tour");
            tour = { who: m.speaker, stop: 0, start: now };
            var f = line("follow");
            if (f) sayInTurn([].concat(f));
            idle(1);
        }
    }

    // ── a bouncer at its post ───────────────────────────────────────────────
    function recognised(o) {
        var a = AvatarList.getAvatar(o.id), name = ((a && (a.displayName || a.sessionDisplayName)) || "").toLowerCase();
        return name && (bot.recognise || []).some(function (r) { return name.indexOf(r.toLowerCase()) >= 0; });
    }
    function guardTick(dt, now, near) {
        var g = guard = guard || { post: me.yaw, lastSeen: 0, nextShift: now + rnd(8000, 15000), nextMutter: now + 60000,
                                   challenged: {}, shifting: 0, nextTurn: 0 };
        // who's about: the nearest visitor is watched; anyone arriving is challenged (or welcomed)
        var watched = null, best = GUARD_RANGE;
        near.forEach(function (o) {
            if (o.duck || heard[String(o.id)]) return;           // other bots (by their model, or their announcing)
            var d = dist(o.pos, me.pos);
            if (d < best) { best = d; watched = o; }
            if (d < ARRIVE_RANGE && now - (g.challenged[o.id] || 0) > 600000) {
                g.challenged[o.id] = now;
                var lines = recognised(o) ? line("welcome") : line("challenge");
                if (lines) sayInTurn([].concat(lines));
                log((recognised(o) ? "welcoming " : "challenging ") + o.id.substr(1, 4));
            }
        });
        if (watched) g.lastSeen = now;
        // the body: turn to face whoever it's watching if they're well off to the side (never far from its post), back
        // to its post a while after they've gone; otherwise stand, shifting its weight now and then
        if (state.name === "turn") {
            turnTo(dt, state.yaw, state.after);
        } else if (g.shifting > now) {
            setCycle("walk", 0.5);
        } else {
            setCycle("idle");
            var want = g.post;
            if (watched) want = g.post + clamp(wrap(yawTowards(watched.pos) - g.post), -1.3, 1.3);
            if (now > g.nextTurn &&
                (watched ? Math.abs(wrap(want - me.yaw)) > 0.9 : now - g.lastSeen > 12000 && Math.abs(wrap(g.post - me.yaw)) > 0.1)) {
                g.nextTurn = now + 5000;                 // a big machine doesn't fidget: one turn, then a pause
                say("servo");
                state = { name: "turn", yaw: watched ? want : g.post, after: { name: "guard" } };
            } else if (now > g.nextShift) {
                g.nextShift = now + rnd(10000, 25000);
                g.shifting = now + 1600;
                say("servo");
                Script.setTimeout(function () { say("clank"); }, 900);
            } else if (!watched && now > g.nextMutter) {
                g.nextMutter = now + rnd(90000, 240000);
                var m = line("idle");
                if (m) sayInTurn([m]);
            }
        }
        steerHead(watched ? Vec3.sum(watched.pos, { x: 0, y: 0.6, z: 0 }) : null, dt, now, null);
    }

    function walk(dt, now) {
        var target = cellPos(path[Math.min(pathAt + 1, path.length - 1)]);
        var want = yawTowards(target);
        var err = wrap(want - me.yaw);
        if (pathAt + 1 < path.length && isHop(path[pathAt], path[pathAt + 1])) {
            // the next step is a rocket hop: face it, then go
            if (Math.abs(err) > 0.15) { state = { name: "turn", yaw: want, after: state }; return; }
            var rise = target.y - me.pos.y;
            state = { name: "hop", from: me.pos, to: target, next: path[pathAt + 1], t: 0,
                      flight: (0.9 + 0.12 * Math.abs(rise)) * GAIT, arc: 0.5 + 0.45 * Math.abs(rise), after: state };
            log("rocket hop " + (rise > 0 ? "up " : "down ") + Math.abs(rise).toFixed(1) + " m");
            return;
        }
        if (Math.abs(err) > TURN_FIRST) { state = { name: "turn", yaw: want, after: state }; return; }
        me.yaw = wrap(me.yaw + clamp(err, -STEER * (MOUSE ? 3 : 1) * dt, STEER * (MOUSE ? 3 : 1) * dt));
        if (MOUSE && Math.random() < dt * 0.25) say("whirr", 0.5);
        var speed = cyc.cycles.walk.speed * SPEEDUP * SIZE / GAIT * PACE;
        var step = speed * dt;
        var here = cellPos(path[pathAt]);
        var fwd = { x: -Math.sin(me.yaw), z: -Math.cos(me.yaw) };
        me.pos = { x: me.pos.x + fwd.x * step, y: me.pos.y, z: me.pos.z + fwd.z * step };
        // the floor height: between this cell and the next by distance
        var span = Math.max(0.01, Math.sqrt((target.x - here.x) * (target.x - here.x) + (target.z - here.z) * (target.z - here.z)));
        var done = clamp(1 - Math.sqrt((target.x - me.pos.x) * (target.x - me.pos.x) + (target.z - me.pos.z) * (target.z - me.pos.z)) / span, 0, 1);
        me.pos.y = here.y + (target.y - here.y) * done;
        if (Math.sqrt((target.x - me.pos.x) * (target.x - me.pos.x) + (target.z - me.pos.z) * (target.z - me.pos.z)) < nav.cell * 0.9) {
            pathAt++;
            me.node = path[pathAt];
        }
        if (ASTRO) setCycle("idle"); else setCycle("walk", PACE);
        if (dist(me.pos, lastPos) > 0.5) { lastPos = me.pos; progressAt = now; }
        if (pathAt >= path.length - 1) arrive(state.then);
        else if (now - progressAt > 8000) { log("stuck, replanning"); idle(1); }
    }

    // a rocket hop: a crouch with the rockets lighting, the flight (a lob, high enough to clear the edge), a landing
    var CROUCH = 0.45, SETTLE = 0.35;
    function hopping(dt, now) {
        var h = state;
        h.t += dt;
        setCycle("idle");
        var s = clamp((h.t - CROUCH) / h.flight, 0, 1);
        if (h.t >= CROUCH && !h.lit) { h.lit = true; if (!h.inPlace) say("rocket"); fire(h.from, true); }
        me.pos = { x: h.from.x + (h.to.x - h.from.x) * s, y: h.from.y + (h.to.y - h.from.y) * s + h.arc * 4 * s * (1 - s),
                   z: h.from.z + (h.to.z - h.from.z) * s };
        if (h.lit && !h.landed) moveFire(me.pos);
        if (s >= 1 && !h.landed) { h.landed = true; fire(h.to, false); }
        if (h.t >= CROUCH + h.flight + SETTLE) {
            me.pos = h.to; me.node = h.next;
            state = h.after;
            if (h.inPlace) return;
            pathAt++;
            progressAt = now; lastPos = me.pos;
            if (pathAt >= path.length - 1) arrive(state.then);
        }
    }
    // the rockets' flame: a particle jet under the bot while it flies, then a puff of smoke where it lands
    function fire(at, lift) {
        var c = bot.accent, size = SIZE;
        if (flame) { Entities.editEntity(flame, { isEmitting: false, lifetime: 2 }); flame = null; }
        var id = Entities.addEntity({
            type: "ParticleEffect", name: "bot-rocket-" + bot.id, position: at, lifetime: lift ? 6 : 3,
            collisionless: true, ignorePickIntersection: true, textures: BASE + "fx-spark.png",
            emitOrientation: Quat.fromPitchYawRollDegrees(90, 0, 0), polarFinish: lift ? 0.3 : 1.5,
            emitRate: lift ? 220 : 120, maxParticles: 600, lifespan: lift ? 0.6 : 1.4,
            emitSpeed: (lift ? 2.6 : 0.7) * size, speedSpread: 0.8 * size,
            emitAcceleration: { x: 0, y: lift ? -0.5 : 0.5, z: 0 }, accelerationSpread: { x: 0.3, y: 0.2, z: 0.3 },
            particleRadius: 0.16 * size, radiusStart: 0.1 * size, radiusFinish: (lift ? 0.4 : 0.6) * size,
            // white-hot at the nozzles, through orange with a touch of the bot's own colour, to smoke
            colorStart: lift ? { red: 255, green: 250, blue: 230 } : { red: 190, green: 185, blue: 180 },
            color: lift ? { red: 255 * (0.7 + 0.3 * c[0]), green: 140 + 60 * c[1], blue: 40 + 120 * c[2] }
                        : { red: 120, green: 118, blue: 120 },
            colorFinish: { red: 70, green: 68, blue: 72 }, alphaStart: 1, alpha: lift ? 0.9 : 0.6, alphaFinish: 0,
            emitterShouldTrail: true, isEmitting: true
        }, "domain");
        if (lift) flame = id;
        else Script.setTimeout(function () { Entities.editEntity(id, { isEmitting: false }); }, 300);
    }
    function moveFire(at) {
        if (flame) Entities.editEntity(flame, { position: Vec3.sum(at, { x: 0, y: 0.04 * SIZE, z: 0 }) });
    }

    function turnTo(dt, yaw, after) {
        var err = wrap(yaw - me.yaw);
        if (Math.abs(err) < 0.12) { state = after; return; }
        var rate = TURN_RATE / GAIT * (MOUSE ? 4 : ASTRO ? 2 : 1);
        me.yaw = wrap(me.yaw + clamp(err, -rate * dt, rate * dt));
        setCycle(ASTRO ? "idle" : err > 0 ? "turn_left" : "turn_right", 2);
        if (bot.role === "bouncer") {                // a heavy foot coming down every step or so
            state.stepAt = (state.stepAt || 0) + dt;
            if (state.stepAt > 1.3 * GAIT / 1.7) { state.stepAt = 0; say("clank"); }
        }
    }

    function arrive(then) {
        var now = Date.now();
        me.node = path[path.length - 1];
        if (!then || then.name === "idle") { idle(rnd(2, 6)); return; }
        if (then.name === "peer") {
            state = { name: "turn", yaw: facingOf(openSides(me.node)),
                      after: { name: "act", kind: "peer", start: now, until: now + 6000, headOverride: true } };
        } else if (then.name === "wall") {
            state = { name: "turn", yaw: facingOf(openSides(me.node)),
                      after: { name: "act", kind: "wall", start: now, until: now + 5000, headOverride: true } };
        } else if (then.name === "look") {
            state = { name: "turn", yaw: yawTowards(then.at),
                      after: { name: "act", kind: "look", start: now, until: now + rnd(3, 6), lookAt: then.at } };
        } else if (then.name === "greet") {
            var who = then.who && AvatarList.getAvatar(then.who), at = who && who.position ? who.position : (ear.pos || me.pos);
            var old = false;
            if (then.who && then.duck) { var b = botId(then.who); if (b) { memory.bots[b] = (memory.bots[b] || 0) + 1; memoryDirty = true; } }
            else if (then.who) old = remember(then.who, { met: true }).met > 2;
            state = { name: "turn", yaw: yawTowards(at),
                      after: { name: "act", kind: "greet", duck: then.duck, who: then.who, old: old, start: now, until: now + 3500,
                               lookAt: Vec3.sum(at, { x: 0, y: then.duck ? 0 : 0.6, z: 0 }) } };
        } else if (then.name === "stop") {
            var stop = bot.tour[then.i], sight = { x: stop.look[0], y: stop.look[1], z: stop.look[2] };
            state = { name: "turn", yaw: yawTowards(sight),
                      after: { name: "act", kind: "show", start: now, until: now + 4000, lookAt: sight } };
            if (stop.say) sayInTurn([].concat(stop.say));
        } else if (then.name === "post") {
            state = { name: "turn", yaw: bot.facing !== undefined ? bot.facing : me.yaw, after: { name: "idle", until: now + 3000 } };
        } else if (then.name === "offer") {
            var v = AvatarList.getAvatar(then.who), there = v && v.position ? v.position : me.pos;
            var back = remember(then.who, { met: true }).met > 1;
            state = { name: "turn", yaw: yawTowards(there),
                      after: { name: "act", kind: "greet", start: now, until: now + 3500, lookAt: Vec3.sum(there, { x: 0, y: 0.6, z: 0 }) } };
            var offer = (back && line("again")) || line("offer");
            if (offer) sayInTurn([].concat(offer));
            tour = { who: then.who, stop: 0, start: now + 7000 };       // a moment to say no
            log("offering a tour" + (back ? " (again)" : ""));
        } else if (then.name === "turn-to") {
            state = { name: "turn", yaw: yawTowards(then.at), after: { name: "idle", until: now + 2500 } };
        }
        log("at " + (then.name) + " (" + nav.cells[me.node][2].toFixed(1) + " m up)");
    }

    function act(dt, now) {
        setCycle("idle");
        var t = (now - state.start) / 1000;
        if (state.kind === "peer") {
            // lean out and look down over the drop, glance left and right, then back off
            state.pose = { neck: 0.55 * Math.min(1, t), headPitch: 0.5 * Math.min(1, t), headYaw: 0.5 * Math.sin(t * 1.3), headRoll: 0 };
            if (t > 1.2 && !state.said) state.said = say("ooh", 0.6) || true;
        } else if (state.kind === "wall") {
            state.pose = { neck: -0.15, headPitch: -0.45 * Math.sin(t * 0.9), headYaw: 0.3 * Math.sin(t * 0.6), headRoll: 0 };
            if (t > 2 && !state.said) state.said = say("chirp", 0.2) || true;
        } else if (state.kind === "look") {
            if (t > 1 && !state.said) state.said = say("chirp", 0.3) || true;
        } else if (state.kind === "greet") {
            state.pose = null;
            look.nod = 0.35 * Math.max(0, Math.sin(t * 6)) * (t < 2.4 ? 1 : 0);
            if (!state.said) state.said = say(state.duck || state.old ? "trill" : "chirp") || t > 1;
        } else if (state.kind === "spin") {
            state.pose = { neck: 0, headPitch: 0, headYaw: state.dir * 1.25 * Math.sin(t * 2.2), headRoll: 0 };
        } else if (state.kind === "show") {
            if (speech.length || Agent.isPlayingAvatarSound) state.until = Math.max(state.until, now + 1800);
        } else if (state.kind === "listen") {
            // keep facing them, and stay while they keep talking (but not for ever)
            state.until = Math.max(state.until, ear.heardAt + 6000);
            if (ear.pos && Math.abs(wrap(yawTowards(ear.pos) - me.yaw)) > 1.0) {
                state = { name: "turn", yaw: yawTowards(ear.pos), after: state };
                return;
            }
            if (t > 45) { ear.boredUntil = now + 30000; state.until = now; say("sad", 0.5); }
        }
        if (now > state.until) {
            look.nod = 0;
            if (state.kind === "show") { if (tour) tour.stop++; idle(0.3); return; }
            if (bot.role === "guide") { idle(0.5); return; }
            if (state.kind === "peer") {
                // back away from the edge: a cell a few metres the other way
                var away = Vec3.sum(me.pos, Vec3.multiply(3, { x: Math.sin(me.yaw), y: 0, z: Math.cos(me.yaw) }));
                var c = nearestCell(away, 0.6);
                if (allowed(c)) { goTo(c, { name: "idle" }, "away from the edge"); return; }
            }
            if (state.kind === "greet" && state.old && Math.random() < 0.5) { jumpInPlace(); return; }   // pleased to see you
            if (state.kind === "greet" && state.duck && state.who && botFriend(state.who) && Math.random() < mind.sociability) {
                following = { who: state.who, until: now + rnd(30000, 70000), buddy: true };              // walk together a while
                log("going for a walk with " + botId(state.who));
                idle(0.5);
                return;
            }
            if (state.kind === "greet" && !MOUSE && !ASTRO && Math.random() < 0.35) {
                // show off a little (no roll: someone's right in front)
                if (Math.random() < 0.5) startMove("sit", null, rnd(3, 6));
                else startMove(Math.random() < 0.5 ? "kick_left" : "kick_right");
                return;
            }
            idle(rnd(1, 4));
        }
    }

    // ── one-shot moves (record_moves.py) ────────────────────────────────────
    // played from where the duck stands and the way it faces; a sit holds at its sitFrame for `hold` seconds
    function startMove(kind, after, hold) {
        var m = cyc.moves && cyc.moves[kind];
        if (!m) { idle(1); return; }
        if (kind !== "sit") look.weight = 0;
        state = { name: "move", kind: kind, move: m, t: 0, frame: 0, origin: me.pos, yaw: me.yaw, hold: hold || 0, after: after };
        log(kind);
    }
    function moving(dt, now) {
        var m = state.move, i = Math.floor(state.t * cyc.fps);
        if (m.sitFrame && i >= m.sitFrame && state.hold > 0) {
            if (!state.sat) say("chirp_5", 0.4);
            state.sat = true; state.hold -= dt; state.frame = m.sitFrame;
            return;
        }
        state.sat = false;
        state.t += dt * MOVE_SPEEDUP / GAIT;
        state.frame = Math.min(i, m.frames.length - 1);
        if (i < m.frames.length) return;
        // done: take the move's final place and heading, back on the map
        var last = m.frames[m.frames.length - 1];
        var off = Vec3.multiplyQbyV(yawQuat(state.yaw), { x: last[0] * SIZE, y: 0, z: last[2] * SIZE });
        var p = { x: state.origin.x + off.x, y: state.origin.y, z: state.origin.z + off.z };
        var c = nearestCell(p, 0.5);
        me.yaw = wrap(state.yaw + m.turn);
        if (allowed(c)) { me.node = c; me.pos = { x: p.x, y: cellPos(c).y, z: p.z }; }
        if (state.kind === "roll") say("trill", 0.7);
        if (state.after) state = state.after; else idle(rnd(1, 3));
    }

    // ── the body ────────────────────────────────────────────────────────────
    function setCycle(name, rate) {
        if (cycleName !== name) { cycleName = name; cycleTime = 0; }
        state.rate = rate || 1;
    }
    function steerHead(target, dt, now, override) {
        var want = { neck: 0, headPitch: 0, headYaw: 0, headRoll: 0 }, w = 0;
        if (override && state.pose) {
            want = state.pose; w = 1;
        } else if (target) {
            var d = Vec3.subtract(target, Vec3.sum(me.pos, { x: 0, y: 0.9 * SIZE, z: 0 }));
            var yawTo = wrap(Math.atan2(-d.x, -d.z) - me.yaw);
            var flat = Math.sqrt(d.x * d.x + d.z * d.z);
            want = { neck: 0, headPitch: clamp(-Math.atan2(d.y, flat), -0.6, 0.6), headYaw: clamp(yawTo, -1.3, 1.3),
                     headRoll: listening(now) ? ear.tilt : 0 };
            w = Math.abs(yawTo) < 2.2 ? 1 : 0;
        }
        var k = Math.min(1, dt * 3);
        look.weight += (w - look.weight) * k;
        look.neck += (want.neck - look.neck) * k;
        look.headPitch += (want.headPitch - look.headPitch) * k;
        look.headYaw += (want.headYaw - look.headYaw) * k;
        look.headRoll += (want.headRoll - look.headRoll) * Math.min(1, dt * 4);
        // the jaw: open with the loudness of its own sound while it makes one, quick to follow like a mouth
        var talking = Agent.isPlayingAvatarSound ? clamp((Avatar.audioLoudness || 0) / 2500, 0, 1) * 0.5 : 0;
        jaw += (talking - jaw) * Math.min(1, dt * 20);
        // the answering nod
        look.earNod = now < ear.nodUntil ? 0.3 * Math.max(0, Math.sin((ear.nodUntil - now) / 1000 * 10)) : 0;
    }
    function jointQuat(name, angle) {
        var b = cyc.base[name];
        return Quat.multiply({ x: b[0], y: b[1], z: b[2], w: b[3] }, Quat.angleAxis(angle * 180 / Math.PI, Vec3.UNIT_Y));
    }
    function headAngle(name) {
        if (name === "neck_pitch") return NECK_SIGN * look.neck;
        if (name === "head_pitch") return HEAD_PITCH_SIGN * (look.headPitch + (look.nod || 0) + (look.earNod || 0));
        if (name === "head_yaw") return HEAD_YAW_SIGN * look.headYaw;
        if (name === "head_roll") return HEAD_ROLL_SIGN * look.headRoll;
        return null;
    }
    // a frame's Hips rotation then its joints, from index k0 of the row, with the head blended in
    // A viewer takes each joint's rotation in the model's frame (Rig::copyJointsFromJointData), not relative to its
    // parent, and turns every avatar's model half round (avatar models face +z; the duck faces -z, as it walks). So:
    // ROOT gets that half turn back, and every joint the product of its parents' rotations and its own.
    var HALF_TURN = { x: 0, y: 1, z: 0, w: 0 };
    function applyRow(row, k0) {
        var abs = { Hips: Quat.multiply(HALF_TURN, { x: row[k0], y: row[k0 + 1], z: row[k0 + 2], w: row[k0 + 3] }) };
        var level = ASTRO ? { Hips: HALF_TURN } : null;            // (the same chain without the lean)
        if (ASTRO) abs.Hips = Quat.multiply(HALF_TURN, Quat.angleAxis(lean * 180 / Math.PI, Vec3.UNIT_X));
        rotations[order[0]] = abs.Hips;
        for (var j = 0; j < cyc.joints.length; j++) {
            var name = cyc.joints[j], k = k0 + 4 + j * 4;
            var q = { x: row[k], y: row[k + 1], z: row[k + 2], w: row[k + 3] };
            var extra = look.weight > 0.01 || ASTRO ? headAngle(name) : null;
            if (ASTRO && extra !== null) q = jointQuat(name, cyc.rest[name] + (name === "head_yaw" ? extra * look.weight : 0));
            else if (ASTRO) q = jointQuat(name, cyc.rest[name]);       // legs at rest: they're rigid struts
            else if (extra !== null) q = Quat.slerp(q, jointQuat(name, cyc.rest[name] + extra), look.weight);
            abs[name] = Quat.multiply(abs[cyc.parents[name]], q);
            if (level) {
                level[name] = Quat.multiply(level[cyc.parents[name]], q);
                if (/_ankle$/.test(name)) abs[name] = level[name];       // the feet stay flat on the floor
            }
            rotations[order[j + 1]] = abs[name];
        }
        for (var e = 0; e < extraJoints.length; e++) rotations[extraJoints[e]] = HALF_TURN;
        if (jawIndex >= 0 && cyc.jaw) {                              // the jaw: opens with its own sounds
            var b = cyc.jaw.base;
            var local = Quat.multiply({ x: b[0], y: b[1], z: b[2], w: b[3] },
                                      Quat.angleAxis(cyc.jaw.open * jaw * 180 / Math.PI, Vec3.UNIT_Y));
            rotations[jawIndex] = Quat.multiply(abs[cyc.jaw.parent], local);
        }
        Avatar.setJointRotations(rotations);
    }
    function pose(dt) {
        if (state.name === "move") {
            var row = state.move.frames[state.frame];
            var off = Vec3.multiplyQbyV(yawQuat(state.yaw), Vec3.multiply(SIZE, { x: row[0], y: row[1], z: row[2] }));
            Avatar.position = Vec3.sum(state.origin, Vec3.sum(off, { x: 0, y: cyc.trunkHeight * SIZE, z: 0 }));
            Avatar.orientation = yawQuat(state.yaw);
            if (order) applyRow(row, 3);
            return;
        }
        var c = cyc.cycles[cycleName];
        cycleTime += dt * (state.rate || 1) * SPEEDUP / GAIT;
        var frame = c.frames[Math.floor(cycleTime * cyc.fps) % c.frames.length];
        Avatar.position = { x: me.pos.x, y: me.pos.y + (cyc.trunkHeight + frame[0]) * SIZE, z: me.pos.z };
        Avatar.orientation = yawQuat(me.yaw);
        if (ASTRO) {
            // lean back while driving, easing in and out; the body turns about its hips, so shift it to keep the line
            // through its ankles still (the pivot), and the feet (on the ankles) are held level in applyRow
            lean += ((state.name === "walk" ? LEAN_DRIVE : 0) - lean) * Math.min(1, dt * 2.5);
            var keep = { x: 0, y: ANKLE_Y * (1 - Math.cos(lean)) * SIZE, z: -ANKLE_Y * Math.sin(lean) * SIZE };
            Avatar.position = Vec3.sum(Avatar.position, Vec3.multiplyQbyV(Avatar.orientation, keep));
        }
        if (order) applyRow(frame, 1);
    }
    // the avatar's joint order: the .fst's jointIndex list, which matches the skeleton a viewer builds (ROOT, the duck's
    // joints, then the mesh node; those two stay at rest)
    function resolveJoints() {
        var names = Avatar.getJointNames();
        if (!names || !names.length) return;
        var found = ["Hips"].concat(cyc.joints).map(function (n) { return names.indexOf(n); });
        if (found.indexOf(-1) >= 0) return;
        order = found;
        rotations = [];
        extraJoints = [];                         // ROOT and the mesh node: the viewer's, not the duck's
        jawIndex = names.indexOf("jaw");          // the bots' own models have a jaw; duck.fst doesn't
        for (var i = 0; i < names.length; i++) {
            rotations.push(HALF_TURN);
            if (found.indexOf(i) < 0 && i !== jawIndex) extraJoints.push(i);
        }
        log("joints ready (" + names.length + ")");
    }

    var lastReport = 0, lastClaimCheck = 0;

    // ── which bot it is ─────────────────────────────────────────────────────
    // the bots others already are, with the session of each: from what they announce on the bots' channel every few
    // seconds (reliable), and from the models they wear (which the avatar mixer doesn't always pass on)
    var CHANNEL = "darkride.bots", heard = {};     // or the roster's own "channel", so two kits in one domain don't mix
    function announce() {
        if (bot) Messages.sendMessage(CHANNEL, JSON.stringify({ bot: bot.id, session: String(Agent.sessionUUID) }));
    }
    function listenToBots() {                     // once the agent has its mixers (subscribing earlier lost agents)
        var r = Script.require(BASE + "bots.json");
        if (r.channel) CHANNEL = r.channel;
        Messages.subscribe(CHANNEL);
        Messages.subscribe(CHANNEL + ".cmd");
        Messages.messageReceived.connect(function (channel, message) {
            if (channel === CHANNEL + ".cmd") { command(message); return; }
            if (channel !== CHANNEL) return;
            try {
                var m = JSON.parse(message);
                if (m.session !== String(Agent.sessionUUID)) heard[m.session] = { bot: m.bot, at: Date.now() };
            } catch (e) { /* not ours */ }
        });
    }
    // commands for one bot (tests, and later voice commands): {"bot": "pip", "goto": [x, y, z]}
    function command(message) {
        try {
            var m = JSON.parse(message);
            if (!bot) return;
            if (m.act) { if (m.bot === bot.id || (!m.bot && m.near)) obey(m); return; }
            if (m.bot !== bot.id) return;
            if (m.goto) {
                var c = nearestCell({ x: m.goto[0], y: m.goto[1], z: m.goto[2] }, 2);
                if (allowed(c)) { log("told to go to " + JSON.stringify(m.goto)); goTo(c, { name: "idle" }, "told to"); }
            }
        } catch (e) { log("bad command: " + e); }
    }
    function botsAbout() {
        var taken = {}, now = Date.now(), present = {};
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            if (!id || id === Agent.sessionUUID) return;
            present[String(id)] = true;
            var a = AvatarList.getAvatar(id), m = ((a && a.skeletonModelURL) || "").match(/duck-([a-z0-9]+)\.fst/);
            if (m) taken[m[1]] = String(id);
        });
        Object.keys(heard).forEach(function (session) {
            if (now - heard[session].at < 25000) taken[heard[session].bot] = session;
            else delete heard[session];
        });
        return taken;
    }
    function claim() {
        if (typeof BOT_ID !== "undefined") {      // run from a bot's own loader (bot-<id>.js): that bot
            for (var b = 0; b < roster.bots.length; b++) if (roster.bots[b].id === BOT_ID) return roster.bots[b];
        }
        var taken = botsAbout(), free = roster.bots.filter(function (x) { return !x.role; });
        for (var i = 0; i < free.length; i++) if (!taken[free[i].id]) return free[i];
        return pick(free);                        // more copies than bots: double up
    }
    // two copies that picked the same bot at once: the one with the higher session id picks again
    function checkClaim() {
        if (typeof BOT_ID !== "undefined") return;
        var other = botsAbout()[bot.id];
        if (other && String(other) < String(Agent.sessionUUID)) {
            log("someone else is " + bot.name + "; picking again");
            become(claim());
        }
    }
    function become(b) {
        bot = b; SIZE = b.size || 1; GAIT = Math.sqrt(SIZE);
        area = b.home ? roster.areas[b.home] : null;
        Avatar.displayName = b.name;
        Avatar.skeletonModelURL = BASE + "duck-" + b.id + ".fst";
        order = null; rotations = [];
        // hatch: its home area's spot, or somewhere open in its home area, or in the roster's hatching area, or anywhere
        reach = null;                             // a new identity: its reach is worked out again from its hatching
        var start = -1;
        if (b.post) start = nearestCell({ x: b.post[0], y: b.post[1], z: b.post[2] }, 1.0);
        else if (area && area.hatch) start = nearestCell({ x: area.hatch[0], y: area.hatch[1], z: area.hatch[2] }, 1.0);
        for (var tries = 0; tries < 4000 && !allowed(start); tries++) {
            var c = Math.floor(Math.random() * nav.cells.length);
            if (allowed(c) && (area || inHatch(c)) && !isEdge(c) && !isWall(c) && nav.links[c].length === 8) start = c;
        }
        var t0 = Date.now();
        reach = findReach(start);
        log("can reach " + reach.filter(function (x) { return x; }).length + " cells (" + (Date.now() - t0) + " ms)");
        me.node = start;
        me.pos = cellPos(start);
        me.yaw = b.facing !== undefined ? b.facing : rnd(-Math.PI, Math.PI);
        if (b.post) me.pos = { x: b.post[0], y: cellPos(start).y, z: b.post[2] };
        state = { name: b.role === "bouncer" ? "guard" : "start" };
        guard = null; tour = null; guide = null;
        setMind();
        memory = { visitors: {}, bots: {} };
        loadMemory();
        announce();
        log("I'm " + b.name + " (size " + SIZE + (b.climbs === false ? ", no stairs" : "") + (b.home ? ", home " + b.home : "") +
            "), hatched at " + JSON.stringify(me.pos));
    }

    function update(dt) {
        if (!nav) return;
        if (!order) resolveJoints();
        var now = Date.now();
        if (now - lastTick < 33) return;          // 30 updates a second is plenty
        var step = lastTick ? Math.min(0.1, (now - lastTick) / 1000) : 0.033;
        lastTick = now;
        try {
            tick(step);
            pose(step);
        } catch (e) {
            log("error: " + e);
            idle(2);
        }
        if (now - lastClaimCheck > 8000) {
            lastClaimCheck = now; announce(); checkClaim();
            var names = Avatar.getJointNames();
            if (order && (!names || names.length !== rotations.length)) { order = null; log("model changed; finding joints again"); }
        }
        // how loud nearby visitors have been, to tune LOUD
        if (now - lastReport > 30000) {
            if (ear.loudest > 0) log("loudest nearby visitor in the last while: " + ear.loudest.toFixed(0));
            ear.loudest = 0; lastReport = now;
        }
    }

    Agent.isAvatar = true;
    // the avatar mixer only sends the avatars ahead of an agent, plus those within this radius all round (needs a
    // server with Agent.avatarViewRadius, 2026-10-13; older ones see only ahead)
    Agent.avatarViewRadius = VIEW_RADIUS;
    Agent.isListeningToAudioStream = true;          // what it hears, for voice commands (Agent.takeHeardAudio)
    log("avatar view radius " + Agent.avatarViewRadius);
    Script.setTimeout(listenToBots, 4000);
    Script.setTimeout(function () {
        var loadStart = Date.now();
        roster = Script.require(BASE + "bots.json");
        nav = Script.require(BASE + "nav.json");
        log("map loaded in " + (Date.now() - loadStart) + " ms");
        cyc = Script.require(BASE + "duck-cycles.json");
        (nav.hops || []).forEach(function (h) {
            (hopsOf[h[0]] = hopsOf[h[0]] || []).push(h[1]);
            (hopsOf[h[1]] = hopsOf[h[1]] || []).push(h[0]);
        });
        SOUNDS.forEach(function (n) { sounds[n] = SoundCache.getSound(BASE + "sounds/" + n + ".wav"); });
        for (var i = 0; i < nav.cells.length; i++) {
            var key = nav.cells[i][0] * 100000 + nav.cells[i][1];
            (columns[key] = columns[key] || []).push(i);
        }
        // copies start a few seconds apart, so each sees which bots the earlier ones became
        become(claim());
        Script.update.connect(update);
    }, 6000 + Math.random() * 9000);
    Script.scriptEnding.connect(function () {
        if (bot) { memorySavedAt = 0; saveMemory(Date.now()); }
        Messages.unsubscribe(CHANNEL);
        Messages.unsubscribe(CHANNEL + ".cmd");
    });
}());
