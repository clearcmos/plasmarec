"""Pre-authorize the screencast of one monitor, so KDE's share dialog never asks.

The screencast portal keeps each restore token as a row in its permission
store (table "screencast"): the token id, `{app_id: ["yes"]}`, and data from
the backend. xdg-desktop-portal-kde's data is `('KDE', 1, <bytes>)`, where
the bytes are a Qt QDataStream of a map naming the shared output by its
logical position, `"<x>x<y>"`. Rows written by the portal itself look exactly
like the one `restore_data` builds; tests/test_portal.py pins that against a
row KDE wrote.

Before each recording plasmarec writes that row for the monitor picked in its
own picker, so the pick is the consent. If a KDE release changes the format,
the portal cannot use the row and shows its dialog instead; it cannot share a
different monitor than the position names.
"""

from __future__ import annotations

import base64
import os
import struct
from pathlib import Path
from typing import Any

from plasmarec import APP_ID

STORE = (
    "org.freedesktop.impl.portal.PermissionStore",
    "/org/freedesktop/impl/portal/PermissionStore",
    "org.freedesktop.impl.portal.PermissionStore",
)
TABLE = "screencast"

# QMetaType ids and the user type xdg-desktop-portal-kde writes.
_QVARIANTLIST, _QSTRING, _QRECT, _USERTYPE = 9, 10, 19, 65536
_WINDOW_LIST = b"QList<WindowRestoreInfo>\x00"


def _qstring(text: str) -> bytes:
    data = text.encode("utf-16-be")
    return struct.pack(">I", len(data)) + data


def _qvariant(type_id: int) -> bytes:
    return struct.pack(">IB", type_id, 0)  # type, then the is-null flag


def restore_data(x: int, y: int) -> bytes:
    """KDE's restore data for "the output at logical position (x, y)"."""
    return b"".join([
        struct.pack(">I", 3),  # map entries, in key order
        _qstring("outputs"), _qvariant(_QVARIANTLIST), struct.pack(">I", 1),
        _qvariant(_QSTRING), _qstring(f"{x}x{y}"),
        _qstring("region"), _qvariant(_QRECT), struct.pack(">iiii", 0, 0, -1, -1),
        _qstring("windows"), _qvariant(_USERTYPE),
        struct.pack(">I", len(_WINDOW_LIST)), _WINDOW_LIST, struct.pack(">I", 0),
    ])  # fmt: skip


def new_token() -> str:
    """Same shape as the portal's own tokens: 16 random bytes, base64url."""
    return base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()


def _session_bus() -> Any:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    return Gio.bus_get_sync(Gio.BusType.SESSION)


def grant(token_file: Path, position: tuple[int, int], bus: Any = None) -> bool:
    """Write a restore row for the monitor at `position` and put its token in
    `token_file`, which gpu-screen-recorder then presents to the portal.

    The row named by the file's previous token is deleted first, but only if
    it belongs to plasmarec alone, so the store keeps one row per monitor.
    False when the store is unreachable; the dialog then asks as before.
    """
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    try:
        bus = bus or _session_bus()
        previous = token_file.read_text().strip() if token_file.exists() else ""
        if previous:
            try:
                perms, _ = bus.call_sync(*STORE, "Lookup", GLib.Variant("(ss)", (TABLE, previous)),
                                         None, 0, 2000, None).unpack()  # fmt: skip
                if set(perms) == {APP_ID}:
                    bus.call_sync(*STORE, "Delete", GLib.Variant("(ss)", (TABLE, previous)),
                                  None, 0, 2000, None)  # fmt: skip
            except GLib.Error:
                pass  # already gone: the portal replaces a token once it is used
        token = new_token()
        data = GLib.Variant("(suv)", ("KDE", 1, GLib.Variant("ay", restore_data(*position))))
        args = GLib.Variant("(sbsa{sas}v)", (TABLE, True, token, {APP_ID: ["yes"]}, data))
        bus.call_sync(*STORE, "Set", args, None, 0, 2000, None)
    except GLib.Error:
        return False
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token)
    return True
