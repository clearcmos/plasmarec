// plasmarec ghost: hide plasmarec's own windows from every screen capture.
//
// KWin 6.6+ leaves a window whose excludeFromCapture is true out of its
// screencasts. Layer-shell surfaces carry no app id, and KWin names them after
// the executable (python3.x), so a window is matched by the pid that owns
// plasmarec's D-Bus name. After setting the flag, the script tells plasmarec,
// which otherwise warns on its status card that the card will be in the video.

var SERVICE = "io.github.clearcmos.plasmarec";
var PATH = "/io/github/clearcmos/plasmarec";
var INTERFACE = "io.github.clearcmos.plasmarec.Ghost";

function ghost(window) {
    if (!window.resourceClass.startsWith("python")) {
        return;
    }
    // A KWin without the property would take the assignment as a plain JS
    // field and hide nothing; staying silent keeps plasmarec's warning up.
    if (typeof window.excludeFromCapture !== "boolean") {
        return;
    }
    callDBus("org.freedesktop.DBus", "/org/freedesktop/DBus",
             "org.freedesktop.DBus", "GetConnectionUnixProcessID", SERVICE,
             function (pid) {
                 if (pid !== window.pid) {
                     return;
                 }
                 window.excludeFromCapture = true;
                 callDBus(SERVICE, PATH, INTERFACE, "Confirm");
             });
}

workspace.windowAdded.connect(ghost);
workspace.windowList().forEach(ghost);
