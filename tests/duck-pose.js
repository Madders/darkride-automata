//
//  duck-pose.js: close side and front views of each bot, to check its pose and which way it faces
//  (run-tour.ps1 -Script). The front view is taken from the way the avatar faces: the duck's beak should point at it.
//
(function () {
    var taken = 0, list = [], shots = [];
    function log(t) { print("TOUR: " + t); }
    Window.stillSnapshotTaken.connect(function (path) { log("saved " + path); });
    function bots() {
        return AvatarList.getAvatarIdentifiers().filter(function (id) { return id; }).map(function (id) {
            return AvatarList.getAvatar(id);
        }).filter(function (a) { return /\/duck(-[a-z0-9]+)?\.fst/.test(a.skeletonModelURL || ""); });
    }
    function shoot() {
        if (taken >= shots.length) { log("DONE"); Script.stop(); return; }
        var s = shots[taken], a = s.bot, p = a.position;
        var fwd = Quat.getForward(a.orientation), right = Quat.getRight(a.orientation);
        var dir = s.kind === "front" ? fwd : right;
        var eye = Vec3.sum(p, Vec3.sum(Vec3.multiply(1.8, dir), { x: 0, y: 0.15, z: 0 }));
        MyAvatar.position = Vec3.sum(eye, { x: 0, y: -40, z: 0 });
        Camera.position = eye;
        Camera.orientation = Quat.lookAtSimple(eye, p);
        Script.setTimeout(function () {
            log(s.kind + " of bot " + (taken >> 1) + " (" + (a.skeletonModelURL || "").split("/").pop() + ")");
            Window.takeSnapshot(false, false, 0, "pose-" + taken);
            taken++;
            Script.setTimeout(shoot, 1200);
        }, 600);
    }
    Script.setTimeout(function () {
        Camera.mode = "independent";
        list = bots();
        list.forEach(function (a) { shots.push({ bot: a, kind: "side" }, { bot: a, kind: "front" }); });
        log(list.length + " bots");
        shoot();
    }, 45000);
}());
