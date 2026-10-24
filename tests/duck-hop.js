//
//  duck-hop.js: makes a rocket bot hop (run-tour.ps1 -Script). Sends the first rocket bot about to the other level by
//  the gallery ring's hop, through the bots' command channel, and photographs it while it is in the air: rising or
//  falling fast, sampled ten times a second.
//
(function () {
    var UP = [11.1, 3.6, -44.6], DOWN = [11.6, -2.4, -45.1];    // either end of one of the rocket hops
    function log(t) { print("TOUR: " + t); }
    var shots = 0;
    Window.stillSnapshotTaken.connect(function (path) { log("saved " + path); });
    function rocketBot() {
        var found = null;
        AvatarList.getAvatarIdentifiers().forEach(function (id) {
            var a = AvatarList.getAvatar(id), m = (a && a.skeletonModelURL || "").match(/duck-(pip|kip)\.fst/);
            if (m && !found) { found = a; found.botId = m[1]; }
        });
        return found;
    }
    Script.setTimeout(function () {
        var a = rocketBot();
        if (!a) { log("no rocket bot"); Script.stop(); return; }
        var goal = a.position.y > 0 ? DOWN : UP;
        log("sending " + a.botId + " from " + JSON.stringify(a.position) + " to " + JSON.stringify(goal));
        Messages.sendMessage("darkride.bots.cmd", JSON.stringify({ bot: a.botId, goto: goal }));
        Camera.mode = "independent";
        var lastY = a.position.y, start = Date.now(), lastShot = 0;
        var timer = Script.setInterval(function () {
            var b = rocketBot(), p = b.position;
            var eye = Vec3.sum(p, { x: 3.0, y: 0.6, z: 3.0 });
            MyAvatar.position = Vec3.sum(eye, { x: 0, y: -40, z: 0 });
            Camera.position = eye;
            Camera.orientation = Quat.lookAtSimple(eye, p);
            var vy = (p.y - lastY) / 0.1;
            lastY = p.y;
            if (Math.abs(vy) > 1.5 && shots < 8 && Date.now() - lastShot > 250) {
                lastShot = Date.now();
                Window.takeSnapshot(false, false, 0, "hop-" + shots++);
                log("mid-air shot " + shots + " at y " + p.y.toFixed(2) + " (" + vy.toFixed(1) + " m/s)");
                var fx = Entities.findEntitiesByName("bot-rocket-" + b.botId, p, 30);
                fx.forEach(function (id) {
                    var e = Entities.getEntityProperties(id, ["position", "isEmitting", "emitRate", "textures", "visible"]);
                    log("  flame " + JSON.stringify(e.position) + " emitting " + e.isEmitting + " rate " + e.emitRate +
                        " visible " + e.visible + " " + e.textures);
                });
                if (!fx.length) log("  no flame entity in reach");
            }
            if (Date.now() - start > 150000 || shots >= 8) {
                Script.clearInterval(timer);
                log("ended at " + JSON.stringify(b.position));
                Script.setTimeout(function () { log("DONE"); Script.stop(); }, 1500);
            }
        }, 100);
    }, 40000);
}());
