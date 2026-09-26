from __future__ import annotations

import shutil
import signal
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from plasmarec import calls
from plasmarec.config import Config
from plasmarec.session import LOG_NAME, Phase, Session, spawn_recorder


class FakeRecorder:
    """Stands in for gpu-screen-recorder: SIGINT makes it exit, and the test
    decides when frames start (begin) or when it dies on its own (die)."""

    def __init__(self, argv: list[str], log: IO[Any]) -> None:
        self.argv = argv
        self.output = Path(argv[argv.index("-o") + 1])
        self.returncode: int | None = None
        self.signals: list[int] = []
        log.write("gsr info: waiting for the portal\n")

    def begin(self, t0_us: int = 5_000_000) -> None:
        self.output.write_bytes(b"video")
        Path(f"{self.output}.ts").write_text(f"monotonic_microsec\trealtime_microsec\n{t0_us}\t0\n")

    def die(self, code: int = 1) -> None:
        self.returncode = code

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        assert self.returncode is not None, "waited on a recorder that was never stopped"
        return self.returncode


class ManualWatch(calls.CallWatch):
    """A call watch the test samples by hand instead of on a thread."""

    def start(self) -> None:
        pass


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.cache, self.state, self.out = root / "cache", root / "state", root / "Desktop"
        self.now = 10.0
        self.recorders: list[FakeRecorder] = []
        self.finalized: list[tuple[Path, Path, list[tuple[float, float]], int]] = []
        self.answers: list[str | None] = []
        self.grants: list[tuple[Path, tuple[int, int]]] = []
        self.grant_ok = True
        self.watch = ManualWatch(lambda: self.answers.pop(0), clock=lambda: self.now)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def grant(self, token: Path, position: tuple[int, int]) -> bool:
        self.grants.append((token, position))
        return self.grant_ok

    def spawn(self, argv: list[str], log: IO[Any]) -> FakeRecorder:
        recorder = FakeRecorder(argv, log)
        self.recorders.append(recorder)
        return recorder

    def finalize(self, src: Path, dst: Path, spans: list[tuple[float, float]], kbps: int) -> None:
        self.finalized.append((src, dst, spans, kbps))
        shutil.copy(src, dst)

    def session(self, **overrides: Any) -> Session:
        options: dict[str, Any] = {
            "grant": self.grant,
            "spawn": self.spawn,
            "watch": self.watch,
            "finalize": self.finalize,
            "clock": lambda: self.now,
            "now": lambda: datetime(2026, 9, 23, 20, 56, 28),
            "cache": self.cache,
            "state": self.state,
        }
        options.update(overrides)
        return Session("DP-2", (960, 142), Config(output_dir=self.out, audio_kbps=128), **options)

    def recording(self) -> tuple[Session, FakeRecorder]:
        session = self.session()
        session.start()
        recorder = self.recorders[0]
        recorder.begin(t0_us=10_000_000)
        session.poll()
        self.assertIs(session.phase, Phase.RECORDING)
        return session, recorder


class Starting(Base):
    def test_start_grants_the_chosen_monitor_then_spawns_the_recorder(self) -> None:
        session = self.session()
        session.start()
        token = self.state / "DP-2.token"
        self.assertEqual(self.grants, [(token, (960, 142))])
        self.assertFalse(session.dialog_expected)
        argv = self.recorders[0].argv
        self.assertIn(str(token), argv)
        self.assertEqual(session.take, self.cache / "2026-09-23_20-56-28.mp4")
        self.assertIn("waiting for the portal", (self.cache / LOG_NAME).read_text())
        self.assertIs(session.phase, Phase.STARTING)

    def test_the_chosen_quality_reaches_the_recorder(self) -> None:
        config = Config(output_dir=self.out, quality="ultra")
        Session(
            "DP-2",
            (960, 142),
            config,
            grant=self.grant,
            spawn=self.spawn,
            watch=self.watch,
            cache=self.cache,
            state=self.state,
            now=lambda: datetime(2026, 9, 23, 20, 56, 28),
        ).start()
        argv = self.recorders[0].argv
        self.assertEqual(argv[argv.index("-q") + 1], "ultra")

    def test_without_a_grant_the_dialog_asks_unless_a_token_exists(self) -> None:
        self.grant_ok = False
        session = self.session()
        session.start()
        self.assertTrue(session.dialog_expected)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "DP-2.token").write_text("token from an earlier choice")
        session = self.session()
        session.start()
        self.assertFalse(session.dialog_expected)

    def test_stays_starting_until_the_first_frame(self) -> None:
        session = self.session()
        session.start()
        session.poll()
        self.assertIs(session.phase, Phase.STARTING)
        self.assertEqual(session.elapsed(), 0.0)
        self.recorders[0].begin(t0_us=10_000_000)
        session.poll()
        self.assertIs(session.phase, Phase.RECORDING)
        self.now = 12.5
        self.assertEqual(session.elapsed(), 2.5)

    def test_hotkey_before_the_first_frame_cancels_and_leaves_nothing(self) -> None:
        session = self.session()
        session.start()
        session.toggle()
        session.wait()
        self.assertIs(session.phase, Phase.CANCELLED)
        self.assertEqual(self.recorders[0].signals, [signal.SIGINT])
        self.assertEqual(list(self.cache.glob("*.mp4*")), [])

    def test_hotkey_before_spawning_cancels_without_a_recorder(self) -> None:
        session = self.session()
        session.toggle()  # pressed during the ghost settle delay
        session.wait()
        session.start()
        self.assertIs(session.phase, Phase.CANCELLED)
        self.assertEqual(self.recorders, [])

    def test_share_dialog_cancelled_is_a_failure_with_the_recorder_log(self) -> None:
        session = self.session()
        session.start()
        self.recorders[0].die(1)
        session.poll()
        self.assertIs(session.phase, Phase.FAILED)
        self.assertIn("cancelled or did not start", session.outcome.error)
        self.assertIn("waiting for the portal", session.outcome.error)

    def test_missing_recorder_binary_is_a_failure(self) -> None:
        def missing(argv: list[str], log: IO[Any]) -> FakeRecorder:
            raise FileNotFoundError("setpriv")

        session = self.session(spawn=missing)
        session.start()
        self.assertIs(session.phase, Phase.FAILED)
        self.assertIn("could not start gpu-screen-recorder", session.outcome.error)


class Saving(Base):
    def test_stop_delivers_the_file_and_cleans_the_cache(self) -> None:
        session, recorder = self.recording()
        self.now = 14.0
        session.toggle()
        self.assertIs(session.phase, Phase.SAVING)
        session.wait()
        self.assertIs(session.phase, Phase.SAVED)
        self.assertEqual(recorder.signals, [signal.SIGINT])
        delivered = self.out / "Recording_2026-09-23_20-56-28.mp4"
        self.assertEqual(session.outcome.path, delivered)
        self.assertEqual(delivered.read_bytes(), b"video")
        self.assertEqual((session.outcome.duration, session.outcome.size), (4.0, 5))
        self.assertEqual(list(self.cache.glob("*.mp4*")), [])
        self.assertEqual(self.finalized[0][3], 128)

    def test_call_spans_reach_finalize_relative_to_the_first_frame(self) -> None:
        session, _ = self.recording()
        for now, answer in ((11.0, None), (12.0, "Discord"), (15.0, None)):
            self.now = now
            self.answers.append(answer)
            session.calls.sample()
        self.now = 20.0
        session.stop()
        session.wait()
        pad = calls.PAD_S
        self.assertEqual(self.finalized[0][2], [(2.0 - pad, 5.0 + pad)])

    def test_never_overwrites_an_earlier_recording(self) -> None:
        self.out.mkdir(parents=True)
        (self.out / "Recording_2026-09-23_20-56-28.mp4").write_bytes(b"older")
        session, _ = self.recording()
        session.stop()
        session.wait()
        self.assertEqual(session.outcome.path, self.out / "Recording_2026-09-23_20-56-28-2.mp4")
        self.assertEqual((self.out / "Recording_2026-09-23_20-56-28.mp4").read_bytes(), b"older")

    def test_recorder_dying_mid_take_still_saves_what_it_wrote(self) -> None:
        session, recorder = self.recording()
        recorder.die(1)
        session.poll()
        session.wait()
        self.assertIs(session.phase, Phase.SAVED)
        self.assertEqual(recorder.signals, [])

    def test_failed_finalize_keeps_the_raw_take(self) -> None:
        def broken(src: Path, dst: Path, spans: list[tuple[float, float]], kbps: int) -> None:
            raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"moov atom not found\n")

        session, _ = self.recording()
        session._finalize = broken
        session.stop()
        session.wait()
        self.assertIs(session.phase, Phase.FAILED)
        self.assertIn("moov atom not found", session.outcome.error)
        self.assertEqual(session.outcome.kept, session.take)
        assert session.take is not None
        self.assertTrue(session.take.exists())

    def test_empty_take_is_a_failure(self) -> None:
        session, recorder = self.recording()
        recorder.output.write_bytes(b"")
        session.stop()
        session.wait()
        self.assertIs(session.phase, Phase.FAILED)
        self.assertIn("wrote no video", session.outcome.error)

    def test_toggle_while_saving_does_nothing(self) -> None:
        session, recorder = self.recording()
        session.stop()
        session.toggle()
        session.wait()
        self.assertEqual(recorder.signals, [signal.SIGINT])
        self.assertIs(session.phase, Phase.SAVED)
        self.assertEqual(session.elapsed(), session.outcome.duration)


class Edges(Base):
    def test_finished_phases(self) -> None:
        finished = {phase for phase in Phase if phase.finished}
        self.assertEqual(finished, {Phase.SAVED, Phase.CANCELLED, Phase.FAILED})

    def test_real_spawn_hands_the_log_to_the_child(self) -> None:
        self.cache.mkdir(parents=True)
        with open(self.cache / "log", "w") as log:
            child = spawn_recorder(["sh", "-c", "echo from-child; exit 3"], log)
        self.assertEqual(child.wait(timeout=10), 3)
        self.assertEqual((self.cache / "log").read_text(), "from-child\n")

    def test_stop_and_cancel_outside_their_phase_do_nothing(self) -> None:
        session = self.session()
        session.stop()  # not recording yet
        self.assertIs(session.phase, Phase.STARTING)
        session, recorder = self.recording()
        session.cancel()  # already recording
        self.assertIs(session.phase, Phase.RECORDING)
        self.assertEqual(recorder.signals, [])

    def test_poll_before_start_does_nothing(self) -> None:
        session = self.session()
        session.poll()
        self.assertIs(session.phase, Phase.STARTING)

    def test_missing_log_gives_an_empty_tail(self) -> None:
        self.assertEqual(self.session().log_tail(), "")


if __name__ == "__main__":
    unittest.main()
