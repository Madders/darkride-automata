//
//  duck-jaw.js: does a bot's jaw move as a viewer sees it while the bot makes a sound? (run-tour.ps1 -Script, with
//  duck-talker.js running so a bot answers). Samples each jaw bot's jaw rotation and audio loudness ten times a second
//  for 40 s and logs, per bot, the jaw's range while it was quiet and while it was making a sound.
//
(function () {
    function log(t) { print("TOUR: " + t); }
    function angle(q) { return 2 * Math.acos(Math.min(1, Math.abs(q.w))) * 180 / Math.PI; }
    var seen = {};
    Script.setTimeout(function () {
        var timer = Script.setInterval(function () {
            AvatarList.getAvatarIdentifiers().forEach(function (id) {
                var a = AvatarList.getAvatar(id);
                if (!a || !/\/duck-[a-z]+\.fst/.test(a.skeletonModelURL || "")) return;
                var names = a.getJointNames(), j = names.indexOf("jaw"), h = names.indexOf("head_roll");
                if (j < 0) return;
                // the jaw relative to the head: how far open it is, whatever the head is doing
                var rel = Quat.multiply(Quat.inverse(a.getAbsoluteJointRotationInObjectFrame(h)), a.getAbsoluteJointRotationInObjectFrame(j));
                var s = seen[id] = seen[id] || { name: (a.skeletonModelURL || "").split("/").pop(), quiet: [], loud: [] };
                (a.audioLoudness > 1 ? s.loud : s.quiet).push(angle(rel));
            });
        }, 100);
        Script.setTimeout(function () {
            Script.clearInterval(timer);
            Object.keys(seen).forEach(function (id) {
                var s = seen[id];
                function range(v) { return v.length ? Math.min.apply(null, v).toFixed(1) + ".." + Math.max.apply(null, v).toFixed(1) : "-"; }
                log(s.name + ": jaw quiet " + range(s.quiet) + " deg (" + s.quiet.length + "), making a sound " +
                    range(s.loud) + " deg (" + s.loud.length + ")");
            });
            log("DONE");
            Script.stop();
        }, 40000);
    }, 30000);
}());
