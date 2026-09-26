from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from plasmarec import APP_ID, GHOST_INTERFACE, OBJECT_PATH, view
from plasmarec.session import Outcome, Phase


def card(phase: Phase, **kw: object) -> view.CardText:
    args: dict[str, Any] = {
        "connector": "DP-1",
        "call_app": None,
        "dialog_expected": False,
        "ghosted": True,
        "shortcut": "Meta+F2",
        "elapsed": 0.0,
    }
    args.update(kw)
    return view.card_text(phase, **args)


class Clock(unittest.TestCase):
    def test_minutes_and_seconds(self) -> None:
        self.assertEqual(view.clock(0), "0:00")
        self.assertEqual(view.clock(61.9), "1:01")

    def test_hours_appear_only_past_an_hour(self) -> None:
        self.assertEqual(view.clock(3599), "59:59")
        self.assertEqual(view.clock(3600 + 62), "1:01:02")


class StatusCard(unittest.TestCase):
    def test_body_names_the_call_app_while_the_mic_is_mixed_in(self) -> None:
        self.assertEqual(
            card(Phase.RECORDING, call_app="Discord").body, "DP-1  system audio  mic on, Discord"
        )
        self.assertEqual(card(Phase.RECORDING).body, "DP-1  system audio  mic off")

    def test_starting_says_how_to_cancel(self) -> None:
        text = card(Phase.STARTING)
        self.assertEqual((text.title, text.hint), ("Starting", "meta+f2 cancels"))

    def test_starting_points_at_kde_dialog_when_one_is_expected(self) -> None:
        self.assertEqual(
            card(Phase.STARTING, dialog_expected=True).hint, "choose the DP-1 entry in KDE's dialog"
        )

    def test_recording_without_confirmation_warns_the_card_is_in_the_video(self) -> None:
        text = card(Phase.RECORDING, ghosted=False, elapsed=75)
        self.assertEqual(text.hint, "meta+f2 stops    visible in the video")
        self.assertTrue(text.warn)
        self.assertEqual(text.side, "1:15")

    def test_recording_once_confirmed_says_it_is_not_in_the_video(self) -> None:
        text = card(Phase.RECORDING)
        self.assertEqual(text.hint, "meta+f2 stops    not in the video")
        self.assertFalse(text.warn)

    def test_no_shortcut_means_running_plasmarec_again(self) -> None:
        self.assertEqual(
            card(Phase.RECORDING, shortcut="").hint, "run plasmarec again to stop    not in the video"
        )

    def test_saving_and_cancelling_clear_the_hint(self) -> None:
        self.assertEqual((card(Phase.SAVING).title, card(Phase.SAVING).hint), ("Saving", ""))
        self.assertEqual(card(Phase.CANCELLING).title, "Cancelling")

    def test_a_finished_phase_leaves_title_and_hint_alone(self) -> None:
        text = card(Phase.SAVED)
        self.assertIsNone(text.title)
        self.assertIsNone(text.hint)


class Notice(unittest.TestCase):
    def test_saved_names_the_file_length_and_size(self) -> None:
        outcome = Outcome(path=Path("/d/Recording_x.mp4"), duration=65, size=12_400_000)
        self.assertEqual(
            view.outcome_notice(Phase.SAVED, outcome), ("Recording saved", "Recording_x.mp4\n1:05, 12 MB")
        )

    def test_failed_points_at_the_kept_take(self) -> None:
        outcome = Outcome(error="encoder died", kept=Path("/c/raw.mp4"))
        self.assertEqual(
            view.outcome_notice(Phase.FAILED, outcome),
            ("Recording failed", "encoder died\nThe raw take is at /c/raw.mp4"),
        )

    def test_cancelled_sends_nothing(self) -> None:
        self.assertIsNone(view.outcome_notice(Phase.CANCELLED, Outcome()))


class PickerKeys(unittest.TestCase):
    def test_escape_cancels_and_e_opens_the_quality_panel(self) -> None:
        self.assertEqual(view.picker_key("Escape", "\x1b", 3), view.CANCEL)
        self.assertEqual(view.picker_key("e", "e", 3), view.SETTINGS)
        self.assertEqual(view.picker_key("E", "E", 3), view.SETTINGS)

    def test_a_number_picks_that_monitor_only_if_it_exists(self) -> None:
        self.assertEqual(view.picker_key("2", "2", 3), 1)
        self.assertIsNone(view.picker_key("4", "4", 3))
        self.assertIsNone(view.picker_key("0", "0", 3))

    def test_other_keys_do_nothing(self) -> None:
        self.assertIsNone(view.picker_key("x", "x", 3))
        self.assertIsNone(view.picker_key("Shift_L", "\x00", 3))


class SettingsKeys(unittest.TestCase):
    def test_arrows_wrap_around(self) -> None:
        self.assertEqual(view.settings_key("Down", "", 3, 4), 0)
        self.assertEqual(view.settings_key("Up", "", 0, 4), 3)
        self.assertEqual(view.settings_key("Tab", "\t", 1, 4), 2)
        self.assertEqual(view.settings_key("k", "k", 1, 4), 0)

    def test_numbers_mark_a_preset_and_e_saves(self) -> None:
        self.assertEqual(view.settings_key("3", "3", 0, 4), 2)
        self.assertEqual(view.settings_key("e", "e", 2, 4), view.SAVE)
        self.assertEqual(view.settings_key("Escape", "\x1b", 2, 4), view.CANCEL)

    def test_a_meaningless_key_keeps_the_mark(self) -> None:
        self.assertEqual(view.settings_key("9", "9", 1, 4), 1)
        self.assertEqual(view.settings_key("x", "x", 1, 4), 1)


class PointerScript(unittest.TestCase):
    def test_calls_plasmarec_back_with_the_cursor_output(self) -> None:
        path, name, text = view.pointer_script("/run/user/1000", "42-7")
        self.assertEqual(path, Path("/run/user/1000/plasmarec-pointer-42-7.js"))
        self.assertEqual(name, "plasmarec-pointer-42-7")
        self.assertIn("workspace.screenAt(workspace.cursorPos)", text)
        self.assertIn(f'callDBus("{APP_ID}", "{OBJECT_PATH}", "{GHOST_INTERFACE}", "Pointer"', text)

    def test_each_press_gets_its_own_file(self) -> None:
        # KWin's loadScript returns -1 for a path it has already loaded, so a
        # reused path would send every press after the first to the fallback.
        self.assertNotEqual(view.pointer_script("/r", "1-1")[0], view.pointer_script("/r", "1-2")[0])


class Dismiss(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_register_writes_this_pid_under_the_name_basalt_dismiss_matches(self) -> None:
        view.register_dismiss(self.tmp.name)
        path = Path(self.tmp.name) / "basalt" / "plasmarec.pid"
        self.assertEqual(path.read_text(), f"{os.getpid()}\n")

    def test_unregister_removes_it_and_tolerates_a_missing_file(self) -> None:
        view.register_dismiss(self.tmp.name)
        view.unregister_dismiss(self.tmp.name)
        self.assertFalse(view.dismiss_pidfile(self.tmp.name).exists())
        view.unregister_dismiss(self.tmp.name)


class PickerBody(unittest.TestCase):
    def test_names_the_output_its_pixels_and_the_quality(self) -> None:
        self.assertEqual(view.picker_body("DP-2", (2560, 1440), "Very high"), "DP-2  2560x1440  very high")


if __name__ == "__main__":
    unittest.main()
