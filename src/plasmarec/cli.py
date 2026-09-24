"""Entry point. Bind a key to `plasmarec`: the first press opens the monitor
picker, the next one stops the recording (or cancels the picker).

A press while plasmarec is already running is forwarded over D-Bus before
GTK is imported, so stopping costs a few milliseconds instead of a GTK start.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from plasmarec import APP_ID, OBJECT_PATH, __version__


def activate_running() -> bool:
    """Activate a running instance; False when none owns the bus name."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            GLib.Variant("(s)", (APP_ID,)),
            None,
            Gio.DBusCallFlags.NONE,
            1000,
        )
        bus.call_sync(
            APP_ID,
            OBJECT_PATH,
            "org.freedesktop.Application",
            "Activate",
            GLib.Variant("(a{sv})", ({},)),
            None,
            Gio.DBusCallFlags.NONE,
            2000,
        )
    except GLib.Error:
        return False  # not running, or it exited between the two calls
    return True


def desktop_dir() -> Path:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    special = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
    return Path(special) if special else Path.home() / "Desktop"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plasmarec",
        description="Record one monitor to an MP4. Run once to pick a monitor and start, "
        "run again to stop. Settings: $XDG_CONFIG_HOME/plasmarec/config.toml.",
    )
    parser.add_argument("--version", action="version", version=f"plasmarec {__version__}")
    parser.parse_args(argv)

    if activate_running():
        return 0

    from plasmarec import config

    try:
        settings = config.load(desktop_dir())
    except config.ConfigError as exc:
        print(f"plasmarec: {exc}", file=sys.stderr)
        return 2

    from plasmarec import ui

    return ui.run(settings)
