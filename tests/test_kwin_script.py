"""The KWin ghost script, run under node against a fake KWin.

CI sets PLASMAREC_REQUIRE_NODE=1 so a runner without node fails instead of
skipping these silently.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any

from plasmarec import APP_ID, GHOST_INTERFACE, KWIN_SCRIPT, OBJECT_PATH

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "data" / "kwin" / KWIN_SCRIPT
SCRIPT = PACKAGE / "contents" / "code" / "main.js"
NODE = shutil.which("node")
REQUIRED = os.environ.get("PLASMAREC_REQUIRE_NODE") == "1"
OWNER = 4242
CONFIRM = [APP_ID, OBJECT_PATH, GHOST_INTERFACE, "Confirm"]
LOOKUP = ["org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
          "GetConnectionUnixProcessID", APP_ID]  # fmt: skip


def run(windows: list[dict[str, Any]]) -> dict[str, Any]:
    assert NODE is not None
    payload = json.dumps({"script": str(SCRIPT), "ownerPid": OWNER, "windows": windows})
    res = subprocess.run([NODE, str(ROOT / "tests" / "kwin_harness.js")], input=payload,
                         capture_output=True, text=True, check=True)  # fmt: skip
    result: dict[str, Any] = json.loads(res.stdout)
    return result


@unittest.skipUnless(NODE or REQUIRED, "node is not installed")
class GhostScript(unittest.TestCase):
    def test_hides_plasmarecs_window_and_confirms(self) -> None:
        result = run([{"resourceClass": "python3.14", "pid": OWNER}])
        self.assertIs(result["windows"][0]["excludeFromCapture"], True)
        self.assertEqual(result["calls"], [LOOKUP, CONFIRM])

    def test_windows_that_arrive_later_are_hidden_too(self) -> None:
        result = run([{"resourceClass": "python3.12", "pid": OWNER, "late": True}])
        self.assertIs(result["windows"][0]["excludeFromCapture"], True)

    def test_other_python_windows_are_left_alone(self) -> None:
        # Other Python overlays share the resource class; only the pid tells.
        result = run([{"resourceClass": "python3.14", "pid": 7}])
        self.assertIs(result["windows"][0]["excludeFromCapture"], False)
        self.assertEqual(result["calls"], [LOOKUP])

    def test_non_python_windows_cost_no_dbus_call(self) -> None:
        result = run([{"resourceClass": "brave-browser", "pid": OWNER}])
        self.assertIs(result["windows"][0]["excludeFromCapture"], False)
        self.assertEqual(result["calls"], [])

    def test_kwin_without_the_property_gets_no_false_confirmation(self) -> None:
        # Pre-6.6 KWin: assigning would only add a JS field and hide nothing,
        # so plasmarec must keep warning that the card is in the video.
        result = run([{"resourceClass": "python3.14", "pid": OWNER, "noProperty": True}])
        self.assertNotIn("excludeFromCapture", result["windows"][0])
        self.assertEqual(result["calls"], [])


class Package(unittest.TestCase):
    def test_metadata_matches_the_script_id_and_loads_by_default(self) -> None:
        meta = json.loads((PACKAGE / "metadata.json").read_text())
        self.assertEqual(meta["KPackageStructure"], "KWin/Script")
        self.assertEqual(meta["KPlugin"]["Id"], KWIN_SCRIPT)
        # A package cannot edit each user's kwinrc; EnabledByDefault is what
        # makes KWin load the installed script.
        self.assertIs(meta["KPlugin"]["EnabledByDefault"], True)

    def test_script_names_the_same_bus_endpoints_as_plasmarec(self) -> None:
        text = SCRIPT.read_text()
        for value in (APP_ID, OBJECT_PATH, GHOST_INTERFACE):
            self.assertIn(f'"{value}"', text)


if __name__ == "__main__":
    unittest.main()
