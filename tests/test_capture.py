from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plasmarec import APP_ID, capture


def flag(argv: list[str], name: str) -> list[str]:
    """Every value given to `name`, in order."""
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == name]


class Command(unittest.TestCase):
    def setUp(self) -> None:
        self.argv = capture.command(
            Path("/c/take.mp4"), Path("/s/DP-2.token"), fps=30, audio_kbps=128, unit="app-x-1.scope"
        )

    def test_records_through_the_portal_with_the_monitors_token(self) -> None:
        # KMS capture (-w DP-2) would record the status card; only the portal
        # path goes through KWin's screencast, which leaves it out.
        self.assertEqual(flag(self.argv, "-w"), ["portal"])
        self.assertEqual(flag(self.argv, "-restore-portal-session"), ["yes"])
        self.assertEqual(flag(self.argv, "-portal-session-token-filepath"), ["/s/DP-2.token"])

    def test_system_audio_is_track_zero_and_mic_track_one(self) -> None:
        self.assertEqual(flag(self.argv, "-a"), ["default_output", "default_input"])

    def test_output_codecs_and_settings(self) -> None:
        self.assertEqual(flag(self.argv, "-o"), ["/c/take.mp4"])
        self.assertEqual(flag(self.argv, "-c"), ["mp4"])
        self.assertEqual(flag(self.argv, "-k"), ["h264"])
        self.assertEqual(flag(self.argv, "-ac"), ["aac"])
        self.assertEqual(flag(self.argv, "-f"), ["30"])
        self.assertEqual(flag(self.argv, "-ab"), ["128"])
        self.assertEqual(flag(self.argv, "-write-first-frame-ts"), ["yes"])
        self.assertNotIn("-fm", self.argv)

    def test_recorder_runs_in_plasmarecs_own_scope_and_dies_with_it(self) -> None:
        # Regression: launched from a terminal instead of xremap, the portal saw
        # a different app, ignored the saved token and showed its dialog again.
        split = self.argv.index("--")
        self.assertEqual(self.argv[:3], ["systemd-run", "--user", "--scope"])
        self.assertIn("--unit=app-x-1.scope", self.argv[:split])
        self.assertIn("--expand-environment=no", self.argv[:split])
        rest = self.argv[split + 1 :]
        self.assertEqual(rest[:5], ["setpriv", "--pdeathsig", "INT", "--", "gpu-screen-recorder"])


class ScopeUnit(unittest.TestCase):
    def test_names_parse_back_to_the_app_id(self) -> None:
        # The portal reads app-<id>-<random>.scope: everything after the last
        # hyphen is the random part, so neither it nor the id may contain one.
        unit = capture.scope_unit()
        match = re.fullmatch(r"app-(.+)-([^-]+)\.scope", unit)
        assert match is not None
        self.assertEqual(match.group(1), APP_ID)
        self.assertNotIn("-", APP_ID)
        self.assertNotEqual(unit, capture.scope_unit())


class FirstFrame(unittest.TestCase):
    def test_parses_monotonic_microseconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "take.mp4.ts"
            path.write_text("monotonic_microsec\trealtime_microsec\n12345678\t1790000000000000\n")
            self.assertEqual(capture.read_first_frame(path), 12.345678)

    def test_not_yet_written_or_partial_reads_as_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "take.mp4.ts"
            self.assertIsNone(capture.read_first_frame(path))
            for text in ("", "monotonic_microsec\trealtime_microsec\n", "header\nnot-a-number\t1\n"):
                path.write_text(text)
                with self.subTest(text=text):
                    self.assertIsNone(capture.read_first_frame(path))

    def test_lives_next_to_the_output(self) -> None:
        self.assertEqual(capture.first_frame_path(Path("/c/a.mp4")), Path("/c/a.mp4.ts"))


class Paths(unittest.TestCase):
    def test_follow_xdg_directories(self) -> None:
        env = {"XDG_STATE_HOME": "/state", "XDG_CACHE_HOME": "/cache"}
        with mock.patch.dict("os.environ", env):
            self.assertEqual(capture.state_dir(), Path("/state/plasmarec"))
            self.assertEqual(capture.cache_dir(), Path("/cache/plasmarec"))
            self.assertEqual(capture.token_path("HDMI-A-1"), Path("/state/plasmarec/HDMI-A-1.token"))

    def test_fall_back_to_home(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_STATE_HOME": "", "XDG_CACHE_HOME": ""}):
            self.assertEqual(capture.state_dir(), Path.home() / ".local/state/plasmarec")
            self.assertEqual(capture.cache_dir(), Path.home() / ".cache/plasmarec")


if __name__ == "__main__":
    unittest.main()
