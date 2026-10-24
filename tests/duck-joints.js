//
//  duck-joints.js: do the bots' joints move as a viewer gets them? (run-tour.ps1 -Script). Samples each bot's left knee
//  and head joints a few times half a second apart and logs whether they change, with how many joints the viewer has.
//
(function () {
    function log(t) { print("TOUR: " + t); }
    function bots() {
        return AvatarList.getAvatarIdentifiers().filter(function (id) { return id; }).map(function (id) {
            var a = AvatarList.getAvatar(id); a.sid = id; return a;
        }).filter(function (a) { return /\/duck(-[a-z0-9]+)?\.fst/.test(a.skeletonModelURL || ""); });
    }
    function angle(q) { return Math.round(2 * Math.acos(Math.min(1, Math.abs(q.w))) * 1800 / Math.PI) / 10; }
    Script.setTimeout(function () {
        var list = bots(), samples = {}, n = 0;
        log(list.length + " bots");
        list.forEach(function (a) {
            // stand near it, so the avatar mixer sends it in full
            if (!n++) {
                MyAvatar.position = Vec3.sum(a.position, { x: 2, y: 0.5, z: 2 });
                MyAvatar.orientation = Quat.cancelOutRollAndPitch(Quat.lookAtSimple(MyAvatar.position, a.position));
                Camera.mode = "first person";
            }
        });
        var timer = Script.setInterval(function () {
            bots().forEach(function (a) {
                var names = a.getJointNames ? a.getJointNames() : [];
                var k = names.indexOf("left_knee"), h = names.indexOf("head_pitch");
                var s = samples[a.sid] = samples[a.sid] || [];
                s.push((k >= 0 ? angle(a.getJointRotation(k)) : "?") + "/" + (h >= 0 ? angle(a.getJointRotation(h)) : "?"));
                s.joints = names.length;
                s.where = Vec3.distance(a.position, MyAvatar.position).toFixed(1);
                if (s.length === 1) log(a.sid.substr(1, 4) + " joint names: " + names.join(","));
            });
        }, 500);
        Script.setTimeout(function () {
            Script.clearInterval(timer);
            Object.keys(samples).forEach(function (id) {
                var s = samples[id];
                log(id.substr(1, 4) + " (" + s.joints + " joints, " + s.where + " m): knee/head " + s.join(" "));
            });
            log("DONE");
            Script.stop();
        }, 6000);
    }, 45000);
}());
