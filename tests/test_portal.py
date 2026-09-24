from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from plasmarec import APP_ID, portal  # noqa: E402

# The restore data xdg-desktop-portal-kde 6.7.5 stored for a user's choice of
# the output at logical position 960,142, read back from the permission store.
KDE_ROW_960X142 = bytes.fromhex(
    "000000030000000e006f0075007400700075007400730000000900000000010000000a000000"
    "000e00390036003000780031003400320000000c0072006500670069006f006e0000001300"
    "0000000000000000ffffffffffffffff0000000e00770069006e0064006f00770073000100"
    "000000000019514c6973743c57696e646f77526573746f7265496e666f3e0000000000"
)

# Method signatures of org.freedesktop.impl.portal.PermissionStore.
SIGNATURES = {"Lookup": "(ss)", "Delete": "(ss)", "Set": "(sbsa{sas}v)"}


class FakeStore:
    """The permission store over a fake bus: rows keyed by token id, and
    every call checked against the real method signatures."""

    def __init__(self, rows: dict[str, dict[str, list[str]]] | None = None) -> None:
        self.rows: dict[str, tuple[dict[str, list[str]], Any]] = {
            token: (perms, None) for token, perms in (rows or {}).items()
        }
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def call_sync(self, name: str, path: str, iface: str, method: str, params: Any,
                  reply_type: Any, flags: int, timeout: int, cancellable: Any) -> Any:  # fmt: skip
        assert (name, path, iface) == portal.STORE
        assert params.get_type_string() == SIGNATURES[method], (method, params.get_type_string())
        self.calls.append(method)
        if method in self.fail:
            raise GLib.Error("org.freedesktop.portal.Error.NotFound")
        args = params.unpack()
        if method == "Lookup":
            if args[1] not in self.rows:
                raise GLib.Error("org.freedesktop.portal.Error.NotFound")
            perms, _ = self.rows[args[1]]
            return GLib.Variant("(a{sas}v)", (perms, GLib.Variant("s", "")))
        if method == "Delete":
            del self.rows[args[1]]
        if method == "Set":
            table, create, token, perms, data = args
            assert table == portal.TABLE and create
            self.rows[token] = (perms, data)
        return None


class RestoreData(unittest.TestCase):
    def test_matches_the_row_kde_wrote(self) -> None:
        self.assertEqual(portal.restore_data(960, 142), KDE_ROW_960X142)

    def test_other_positions_differ_only_in_the_output_name(self) -> None:
        data = portal.restore_data(2667, 0)
        name = "2667x0".encode("utf-16-be")
        self.assertIn(len(name).to_bytes(4, "big") + name, data)
        self.assertEqual(len(data), len(KDE_ROW_960X142) - len("960x142".encode("utf-16-be")) + len(name))

    def test_tokens_look_like_the_portals_and_never_repeat(self) -> None:
        token = portal.new_token()
        self.assertRegex(token, r"^[A-Za-z0-9_-]{22}$")
        self.assertNotEqual(token, portal.new_token())


class Grant(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.token_file = Path(self.dir.name) / "state" / "DP-2.token"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_writes_a_row_for_plasmarec_and_the_token_to_the_file(self) -> None:
        store = FakeStore()
        self.assertTrue(portal.grant(self.token_file, (960, 142), bus=store))
        token = self.token_file.read_text()
        perms, data = store.rows[token]
        self.assertEqual(perms, {APP_ID: ["yes"]})
        backend, version, raw = data
        self.assertEqual((backend, version, bytes(raw)), ("KDE", 1, KDE_ROW_960X142))
        self.assertEqual(store.calls, ["Set"])

    def test_replaces_plasmarecs_previous_row(self) -> None:
        store = FakeStore({"old": {APP_ID: ["yes"]}})
        self.token_file.parent.mkdir(parents=True)
        self.token_file.write_text("old\n")
        portal.grant(self.token_file, (0, 0), bus=store)
        self.assertNotIn("old", store.rows)
        self.assertEqual(list(store.rows), [self.token_file.read_text()])

    def test_never_deletes_a_row_another_app_shares(self) -> None:
        store = FakeStore({"old": {"": ["yes"], APP_ID: ["yes"]}})
        self.token_file.parent.mkdir(parents=True)
        self.token_file.write_text("old")
        portal.grant(self.token_file, (0, 0), bus=store)
        self.assertIn("old", store.rows)

    def test_previous_row_already_gone_is_fine(self) -> None:
        # The portal replaces a token once it restores it, so the file often
        # names a row that no longer exists.
        store = FakeStore()
        store.fail.add("Lookup")
        self.token_file.parent.mkdir(parents=True)
        self.token_file.write_text("consumed")
        self.assertTrue(portal.grant(self.token_file, (0, 0), bus=store))

    def test_unreachable_store_leaves_the_file_alone(self) -> None:
        store = FakeStore()
        store.fail.add("Set")
        self.token_file.parent.mkdir(parents=True)
        self.token_file.write_text("earlier choice")
        self.assertFalse(portal.grant(self.token_file, (0, 0), bus=store))
        self.assertEqual(self.token_file.read_text(), "earlier choice")

    def test_no_session_bus_is_not_an_error(self) -> None:
        from unittest import mock

        with mock.patch.dict("os.environ", {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}):
            self.assertFalse(portal.grant(self.token_file, (0, 0)))

    def test_token_file_holds_only_the_token(self) -> None:
        # gpu-screen-recorder reads the file verbatim as the restore token.
        portal.grant(self.token_file, (0, 0), bus=FakeStore())
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{22}", self.token_file.read_text()))


if __name__ == "__main__":
    unittest.main()
