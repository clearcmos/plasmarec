from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plasmarec import capture, quality


class Quality(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "state"

    def test_nothing_saved_gives_the_default(self) -> None:
        self.assertEqual(quality.load(self.dir), quality.DEFAULT)

    def test_a_saved_choice_is_remembered(self) -> None:
        quality.save("ultra", self.dir)
        self.assertEqual(quality.load(self.dir), "ultra")
        quality.save("medium", self.dir)
        self.assertEqual(quality.load(self.dir), "medium")

    def test_save_leaves_no_temporary_files(self) -> None:
        quality.save("high", self.dir)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["quality"])

    def test_an_unknown_saved_value_falls_back_to_the_default(self) -> None:
        self.dir.mkdir()
        quality.path(self.dir).write_text("insane\n")
        self.assertEqual(quality.load(self.dir), quality.DEFAULT)

    def test_saving_an_unknown_preset_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            quality.save("insane", self.dir)
        self.assertFalse(quality.path(self.dir).exists())

    def test_a_failed_write_keeps_the_old_choice(self) -> None:
        quality.save("high", self.dir)
        with mock.patch("os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            quality.save("ultra", self.dir)
        self.assertEqual(quality.load(self.dir), "high")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["quality"])

    def test_default_location_is_the_state_dir(self) -> None:
        self.assertEqual(quality.path(), capture.state_dir() / "quality")

    def test_presets_are_the_recorder_levels_in_order(self) -> None:
        self.assertEqual([p.key for p in quality.PRESETS], ["medium", "high", "very_high", "ultra"])
        self.assertIn(quality.DEFAULT, quality.KEYS)
        self.assertEqual(quality.label("very_high"), "Very high")

    def test_the_command_carries_the_preset(self) -> None:
        argv = capture.command(Path("/o.mp4"), Path("/t"), 60, 160, "u", quality="ultra")
        self.assertEqual(argv[argv.index("-q") + 1], "ultra")


if __name__ == "__main__":
    unittest.main()
