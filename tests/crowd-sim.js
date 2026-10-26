//
//  crowd-sim.js: runs the brain for several bots at once in Node, with no domain: each copy in its own sandbox, against
//  a built roster folder (bots.json, nav.json, duck-cycles.json: what package.py writes), sharing a pretend avatar list
//  and message bus, on a pretend clock that runs as fast as it can. Then it says what crowd-watch.js says from a real
//  domain: how much the wanderers bunch up, how long the longest crowd lasted, how much of their time they spend
//  outdoors (nav.json's cells with nothing over them) and by the landing spot, how far the guide strays from its post,
//  and how often they chatter. No visitors, no voices, no viewer: a quick check of the brain's choices, not a test of
//  how the bots look or sound (crowd-watch.js on a local domain is that). --memory gives them the memories a bots'
//  service keeps (bots-memory.json: which bots are already friends).
//
//  Usage: node tests/crowd-sim.js <roster folder> [--brain bot/duck-brain.js] [--copies 6] [--loaders scout,warden]
//                                  [--minutes 30] [--seed 1] [--landing 0,-1.8,-2] [--memory bots-memory.json] [--verbose 1]
//
"use strict";
var fs = require("fs"), path = require("path"), vm = require("vm");

var args = process.argv.slice(2), opt = { brain: path.join(__dirname, "..", "bot", "duck-brain.js"), copies: 6,
    loaders: "scout,warden", minutes: 30, seed: 1, landing: "0,-1.8,-2" };
var dir = null;
for (var a = 0; a < args.length; a++) {
    if (args[a].indexOf("--") === 0) opt[args[a].slice(2)] = args[++a]; else dir = args[a];
}
if (!dir) { console.error("usage: node tests/crowd-sim.js <roster folder> [--brain ...] [--copies N] [--minutes M]"); process.exit(2); }
var memories = opt.memory ? JSON.parse(fs.readFileSync(opt.memory, "utf8")) : null;
var landing = opt.landing.split(",").map(Number), LANDING = { x: landing[0], y: landing[1], z: landing[2] };

// ── the pretend world ───────────────────────────────────────────────────────
var now = 1e12, seed = Number(opt.seed) >>> 0 || 1;
function random() {                                    // mulberry32: the same run every time for a seed
    seed = (seed + 0x6D2B79F5) >>> 0;
    var t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
var files = {};
function load(name) {
    if (!files[name]) files[name] = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
    return files[name];
}
var timers = [], agents = [], bus = [], plays = {};

var Vec3 = {
    UNIT_X: { x: 1, y: 0, z: 0 }, UNIT_Y: { x: 0, y: 1, z: 0 }, UNIT_Z: { x: 0, y: 0, z: 1 },
    sum: function (a, b) { return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z }; },
    subtract: function (a, b) { return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }; },
    multiply: function (a, b) {
        if (typeof a === "number") { var t = a; a = b; b = t; }
        return { x: a.x * b, y: a.y * b, z: a.z * b };
    },
    length: function (a) { return Math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z); },
    normalize: function (a) { var l = Vec3.length(a) || 1; return { x: a.x / l, y: a.y / l, z: a.z / l }; },
    distance: function (a, b) { return Vec3.length(Vec3.subtract(a, b)); },
    mix: function (a, b, t) { return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t }; },
    multiplyQbyV: function (q, v) {
        var ix = q.w * v.x + q.y * v.z - q.z * v.y, iy = q.w * v.y + q.z * v.x - q.x * v.z,
            iz = q.w * v.z + q.x * v.y - q.y * v.x, iw = -q.x * v.x - q.y * v.y - q.z * v.z;
        return { x: ix * q.w + iw * -q.x + iy * -q.z - iz * -q.y, y: iy * q.w + iw * -q.y + iz * -q.x - ix * -q.z,
                 z: iz * q.w + iw * -q.z + ix * -q.y - iy * -q.x };
    }
};
var Quat = {
    angleAxis: function (deg, ax) {
        var h = deg * Math.PI / 360, s = Math.sin(h), n = Vec3.normalize(ax);
        return { x: n.x * s, y: n.y * s, z: n.z * s, w: Math.cos(h) };
    },
    multiply: function (a, b) {
        return { x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y, y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
                 z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w, w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z };
    },
    fromPitchYawRollDegrees: function (p, y, r) {
        return Quat.multiply(Quat.angleAxis(y, Vec3.UNIT_Y), Quat.multiply(Quat.angleAxis(p, Vec3.UNIT_X), Quat.angleAxis(r, Vec3.UNIT_Z)));
    },
    slerp: function (a, b, t) {                         // (a plain blend is plenty for poses nobody sees)
        var q = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t, w: a.w + (b.w - a.w) * t };
        var l = Math.sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w) || 1;
        return { x: q.x / l, y: q.y / l, z: q.z / l, w: q.w / l };
    }
};

function makeAgent(n, botId) {
    var me = { session: "{" + ("000" + n).slice(-4) + "0000-0000-4000-8000-000000000000}", update: null, handlers: [],
               subs: {}, avatar: null, playingUntil: 0 };
    var avatar = me.avatar = { position: { x: 0, y: -100, z: 0 }, orientation: { x: 0, y: 0, z: 0, w: 1 }, displayName: "",
                               skeletonModelURL: "", audioLoudness: 0,
                               getJointNames: function () { return []; }, setJointRotations: function () {} };
    var sandbox = {
        print: function (t) { if (opt.verbose) console.log(t); },
        Vec3: Vec3, Quat: Quat, Uint8Array: Uint8Array, JSON: JSON,
        Script: {
            resolvePath: function () { return "file:///" + dir.replace(/\\/g, "/") + "/duck-brain.js"; },
            require: function (url) { return load(url.replace(/^.*\//, "")); },
            include: function () {},
            setTimeout: function (fn, ms) { timers.push({ at: now + ms, fn: fn }); },
            update: { connect: function (fn) { me.update = fn; } },
            scriptEnding: { connect: function () {} },
            stop: function () { me.update = null; }
        },
        Agent: { sessionUUID: me.session, isAvatar: false, avatarViewRadius: 0, isListeningToAudioStream: false,
                 get isPlayingAvatarSound() { return now < me.playingUntil; },
                 playAvatarSound: function (s) { plays[s.name] = (plays[s.name] || 0) + 1; me.playingUntil = now + 400; },
                 takeHeardAudio: function () { return null; } },
        Avatar: avatar,
        AvatarList: {
            getAvatarIdentifiers: function () { return agents.map(function (g) { return g.session; }); },
            getAvatar: function (id) {
                for (var k = 0; k < agents.length; k++) if (agents[k].session === id) return agents[k].avatar;
                return null;
            }
        },
        Messages: {
            subscribe: function (c) { me.subs[c] = true; },
            unsubscribe: function (c) { delete me.subs[c]; },
            sendMessage: function (c, m) { bus.push({ channel: c, message: m }); },
            messageReceived: { connect: function (fn) { me.handlers.push(fn); } }
        },
        SoundCache: { getSound: function (url) { return { name: url.replace(/^.*\//, "").replace(/\.wav$/, ""), downloaded: true }; } },
        Entities: { addEntity: function () { return "{fx}"; }, editEntity: function () {} },
        XMLHttpRequest: function () {                  // the bots' service: only their memories, from --memory
            var x = this;
            x.open = function (method, url) { x.method = method; x.url = url; };
            x.send = function () {
                var id = (x.url || "").match(/\/memory\/([a-z0-9]+)$/);
                if (x.method !== "GET" || !id || !memories || !memories[id[1]]) return;
                timers.push({ at: now + 200, fn: function () {
                    x.readyState = 4; x.status = 200; x.responseText = JSON.stringify(memories[id[1]]); x.onreadystatechange();
                } });
            };
        }
    };
    if (botId) sandbox.BOT_ID = botId;
    vm.createContext(sandbox);
    vm.runInContext("Math.random = __random; Date.now = __now;", Object.assign(sandbox, {
        __random: random, __now: function () { return now; } }));
    vm.runInContext(fs.readFileSync(opt.brain, "utf8"), sandbox, { filename: path.basename(opt.brain) });
    agents.push(me);
}

// ── run ─────────────────────────────────────────────────────────────────────
var loaders = opt.loaders ? opt.loaders.split(",") : [];
loaders.forEach(function (id, k) { makeAgent(k + 1, id); });
for (var c = 0; c < Number(opt.copies); c++) makeAgent(loaders.length + c + 1, null);

var nav = load("nav.json"), columns = {};
nav.cells.forEach(function (cell) { var key = cell[0] * 100000 + cell[1]; (columns[key] = columns[key] || []).push(cell); });
function covered(p) {
    var list = columns[Math.floor((p.x - nav.origin[0]) / nav.cell) * 100000 + Math.floor((p.z - nav.origin[1]) / nav.cell)] || [];
    var best = null;
    list.forEach(function (cell) { if (!best || Math.abs(cell[2] - p.y) < Math.abs(best[2] - p.y)) best = cell; });
    return best ? (best[3] & 4) !== 0 : false;
}
function botOf(g) { var m = (g.avatar.skeletonModelURL || "").match(/duck-([a-z0-9]+)\.fst/); return m && m[1]; }

var guide = load("bots.json").bots.filter(function (b) { return b.role === "guide" && b.post; })[0];
var STEP = 33, end = now + Number(opt.minutes) * 60000, watchFrom = now + 60000, nextSample = watchFrom;
var s = { samples: 0, crowded: 0, outdoors: 0, landing: 0, longest: 0, longestBot: "", streak: {},
          guidePost: guide ? { x: guide.post[0], y: guide.post[1], z: guide.post[2] } : null, guideFar: 0 };
var POSTED = {};                                     // the bots at posts (guides, bouncers): not wanderers
load("bots.json").bots.forEach(function (b) { if (b.role) POSTED[b.id] = true; });
while (now < end) {
    now += STEP;
    timers.sort(function (x, y) { return x.at - y.at; });
    while (timers.length && timers[0].at <= now) timers.shift().fn();
    var mail = bus; bus = [];
    mail.forEach(function (m) {
        agents.forEach(function (g) { if (g.subs[m.channel]) g.handlers.forEach(function (h) { h(m.channel, m.message); }); });
    });
    agents.forEach(function (g) { if (g.update) g.update(STEP / 1000); });
    if (now >= nextSample) {
        nextSample += 5000;
        var all = agents.filter(function (g) { return botOf(g); });
        all.forEach(function (g) {
            var id = botOf(g), p = g.avatar.position;
            if (guide && id === guide.id) s.guideFar = Math.max(s.guideFar, Math.sqrt((p.x - s.guidePost.x) * (p.x - s.guidePost.x) +
                                                                                (p.z - s.guidePost.z) * (p.z - s.guidePost.z)));
            if (POSTED[id]) return;
            var company = all.filter(function (o) { return o !== g && Vec3.distance(o.avatar.position, p) < 3; }).length;
            s.samples++;
            if (company >= 2) {
                s.crowded++;
                s.streak[id] = s.streak[id] || now;
                if (now - s.streak[id] > s.longest) { s.longest = now - s.streak[id]; s.longestBot = id; }
            } else delete s.streak[id];
            if (!covered({ x: p.x, y: p.y - 0.5, z: p.z })) s.outdoors++;
            if (Vec3.distance(p, LANDING) < 8) s.landing++;
        });
    }
}
var wanderers = agents.filter(function (g) { var id = botOf(g); return id && !POSTED[id]; }).map(botOf);
var chatter = Object.keys(plays).filter(function (k) { return /^(chirp_\d|trill|question|ooh|sad|alert|whirr|(astro|mouse|gonk)_\w+)$/.test(k); })
    .reduce(function (t, k) { return t + plays[k]; }, 0);
function pct(n) { return s.samples ? Math.round(100 * n / s.samples) + "%" : "-"; }
var minutes = (Number(opt.minutes) - 1);
console.log("CROWDSIM: " + path.basename(opt.brain) + ", " + wanderers.length + " wanderers (" + wanderers.join(", ") + "), " +
    minutes + " min watched, seed " + opt.seed + ": in a crowd " + pct(s.crowded) + ", longest crowd " +
    Math.round(s.longest / 1000) + " s" + (s.longestBot ? " (" + s.longestBot + ")" : "") + ", outdoors " + pct(s.outdoors) +
    ", near the landing spot " + pct(s.landing) + ", guide strayed up to " + s.guideFar.toFixed(1) + " m, sounds " +
    (Object.keys(plays).reduce(function (t, k) { return t + plays[k]; }, 0) / Math.max(1, agents.length) / Number(opt.minutes)).toFixed(1) +
    " a bot a minute (chirps etc. " + (chatter / Math.max(1, agents.length) / Number(opt.minutes)).toFixed(1) + ")");
if (opt.verbose) console.log("sounds: " + JSON.stringify(plays));
