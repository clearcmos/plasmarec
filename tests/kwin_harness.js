// Runs the KWin ghost script against a fake KWin and prints what it did.
//
// stdin: {"script": path, "ownerPid": n, "windows": [...]} where a window with
// "late": true arrives through workspace.windowAdded after the script loads
// and one with "noProperty": true models a KWin without excludeFromCapture.
// stdout: {"windows": [...], "calls": [[service, path, interface, method, ...args]]}
"use strict";

const fs = require("fs");
const vm = require("vm");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const calls = [];
const windows = input.windows.map((w) => {
    const win = { resourceClass: w.resourceClass, pid: w.pid };
    if (!w.noProperty) {
        win.excludeFromCapture = false;
    }
    return { win, late: Boolean(w.late) };
});
let added = null;

const sandbox = {
    console,
    workspace: {
        windowAdded: { connect: (fn) => { added = fn; } },
        windowList: () => windows.filter((w) => !w.late).map((w) => w.win),
    },
    callDBus: (service, path, iface, method, ...rest) => {
        calls.push([service, path, iface, method, ...rest.filter((a) => typeof a !== "function")]);
        const callback = rest.find((a) => typeof a === "function");
        if (method === "GetConnectionUnixProcessID" && callback) {
            callback(input.ownerPid);
        }
    },
};

vm.runInNewContext(fs.readFileSync(input.script, "utf8"), sandbox);
windows.filter((w) => w.late).forEach((w) => added(w.win));
process.stdout.write(JSON.stringify({ windows: windows.map((w) => w.win), calls }));
