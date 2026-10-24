//
//  duck-follow.js: follows the wandering ducks with the camera and photographs them in turn (run-tour.ps1 -Script).
//
(function () {
    var shots = 12, taken = 0;
    function log(t) { print("TOUR: " + t); }
    Window.stillSnapshotTaken.connect(function (path) { log("saved " + path); });
    function ducks() {
        return AvatarList.getAvatarIdentifiers().filter(function (id) { return id; }).map(function (id) {
            return AvatarList.getAvatar(id);
        }).filter(function (a) { return /\/duck(-[a-z0-9]+)?\.fst/.test(a.skeletonModelURL || ""); });
    }
    Script.setTimeout(function () {
        Camera.mode = "independent";
        var timer = Script.setInterval(function () {
            var list = ducks();
            if (!list.length) { log("no ducks"); return; }
            var d = list[taken % list.length], p = d.position;
            var fwd = Quat.getForward(d.orientation);
            // in front of the duck and to one side, looking back at it
            var eye = Vec3.sum(p, Vec3.sum(Vec3.multiply(2.6, fwd), { x: 1.2, y: 0.9, z: 0.6 }));
            MyAvatar.position = Vec3.sum(eye, { x: 0, y: -40, z: 0 });
            Camera.position = eye;
            Camera.orientation = Quat.lookAtSimple(eye, Vec3.sum(p, { x: 0, y: 0.2, z: 0 }));
            Script.setTimeout(function () {
                log("duck " + (taken % list.length) + " at " + p.x.toFixed(1) + "," + p.y.toFixed(1) + "," + p.z.toFixed(1));
                Window.takeSnapshot(false, false, 0, "follow-" + taken);
                if (++taken >= shots) { Script.clearInterval(timer); Script.setTimeout(function () { log("DONE"); Script.stop(); }, 2000); }
            }, 1500);
        }, 7000);
    }, 30000);
}());
