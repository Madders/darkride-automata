//
//  bouncer.js: arrive at the bouncer's post as a visitor would, and photograph it (run-tour.ps1 -Script). The test
//  avatar walks in towards the post from 20 m out (so the bouncer should turn, watch and challenge it), with the
//  camera off to one side; then close views from the front, side and back.
//
(function () {
    var POST = { x: -3.4, y: -2.42, z: -12.5 }, FROM = { x: -0.2, y: -1.8, z: 12 };   // Madders Place: by the zen garden
    var taken = 0;
    function log(t) { print("TOUR: " + t); }
    Window.stillSnapshotTaken.connect(function (path) { log("saved " + path); });
    function bouncer() {
        var found = null;
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            var a = AvatarList.getAvatar(id);
            if (a && /bot-?warden|duck-warden\.fst/.test(a.skeletonModelURL || "")) found = a;
        });
        return found;
    }
    function shoot(name) { Window.takeSnapshot(false, false, 0, name); taken++; }
    Script.setTimeout(function () {
        var b = bouncer();
        log(b ? "bouncer at " + JSON.stringify(b.position) : "no bouncer");
        if (!b) { Script.stop(); return; }
        Camera.mode = "independent";
        // walk in: the avatar moves towards the gate a metre a second
        var t = 0, walkIn = Script.setInterval(function () {
            t += 0.5;
            var k = Math.min(1, t / 18);
            MyAvatar.position = Vec3.mix(FROM, { x: -0.6, y: -2.2, z: -9 }, k);
            MyAvatar.orientation = Quat.lookAtSimple(MyAvatar.position, POST);
            var eye = Vec3.sum(b.position, { x: 6, y: 1.5, z: 6 });
            Camera.position = eye;
            Camera.orientation = Quat.lookAtSimple(eye, Vec3.mix(b.position, MyAvatar.position, 0.35));
            if (t === 8 || t === 13 || t === 18 || t === 24) shoot("arrive-" + t);
            if (t >= 26) {
                Script.clearInterval(walkIn);
                var p = bouncer().position, views = [[0, 1.5, 7], [7, 1.0, 0], [-4, 2.5, -6]], v = 0;
                var close = Script.setInterval(function () {
                    var eye2 = Vec3.sum(p, Vec3.multiplyQbyV(bouncer().orientation, { x: views[v][0], y: views[v][1], z: -views[v][2] }));
                    Camera.position = eye2;
                    Camera.orientation = Quat.lookAtSimple(eye2, Vec3.sum(p, { x: 0, y: 0.6, z: 0 }));
                    Script.setTimeout(function () { shoot("close-" + v); v++; }, 700);
                    if (v >= views.length - 1) {
                        Script.clearInterval(close);
                        Script.setTimeout(function () { log("DONE"); Script.stop(); }, 3000);
                    }
                }, 2500);
            }
        }, 500);
    }, 45000);
}());
