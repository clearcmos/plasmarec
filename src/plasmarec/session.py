"""One recording, from spawning the recorder to the delivered file.

The GTK layer drives this: it calls poll() on a short timer from its main
loop and renders `phase`, elapsed() and the call watch's `app`. Nothing here
touches GTK, so the whole lifecycle is testable without a display. Saving and
cancelling run on a worker thread; the UI only reads `phase` and `outcome`.

    starting --first frame--> recording --stop--> saving --> saved | failed
       |                          |
       +--cancel / recorder died--+--recorder died--> saving (keeps the take)
       v
    cancelling --> cancelled          (failed, when the recorder never started)
"""

from __future__ import annotations

import enum
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Protocol

from plasmarec import calls, capture, media, portal
from plasmarec.config import Config

LOG_NAME = "gpu-screen-recorder.log"


class Phase(enum.Enum):
    STARTING = "starting"
    RECORDING = "recording"
    SAVING = "saving"
    CANCELLING = "cancelling"
    SAVED = "saved"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def finished(self) -> bool:
        return self in (Phase.SAVED, Phase.CANCELLED, Phase.FAILED)


class Process(Protocol):
    def poll(self) -> int | None: ...
    def send_signal(self, sig: int) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def spawn_recorder(argv: list[str], log: IO[Any]) -> Process:
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


@dataclass
class Outcome:
    path: Path | None = None  # the delivered file
    duration: float = 0.0  # seconds, first frame to stop
    size: int = 0  # bytes
    error: str = ""
    kept: Path | None = None  # raw take left in the cache after a failed save


class Session:
    def __init__(
        self,
        connector: str,
        position: tuple[int, int],
        config: Config,
        *,
        grant: Callable[[Path, tuple[int, int]], bool] = portal.grant,
        spawn: Callable[[list[str], IO[Any]], Process] = spawn_recorder,
        watch: calls.CallWatch | None = None,
        finalize: Callable[[Path, Path, list[tuple[float, float]], int], None] = media.finalize,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
        cache: Path | None = None,
        state: Path | None = None,
    ) -> None:
        self.connector = connector
        self.position = position  # logical, as KDE names the output in its grant
        self.config = config
        self._grant = grant
        self.calls = watch or calls.CallWatch(lambda: calls.probe(config.call_apps), clock)
        self._spawn = spawn
        self._finalize = finalize
        self._clock = clock
        self._now = now
        self._cache = cache or capture.cache_dir()
        self._token = capture.token_path(connector, state)
        self._process: Process | None = None
        self._worker: threading.Thread | None = None
        self.phase = Phase.STARTING
        self.t0: float | None = None
        self.name = ""
        self.take: Path | None = None
        self.outcome = Outcome()
        # KDE's share dialog asks only if plasmarec could not write its own
        # grant and there is no restore token from an earlier choice either.
        self.dialog_expected = False

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        if self.phase is not Phase.STARTING:
            return  # cancelled before the recorder was spawned
        self._cache.mkdir(parents=True, exist_ok=True)
        self._token.parent.mkdir(parents=True, exist_ok=True)
        stamp = self._now().strftime("%Y-%m-%d_%H-%M-%S")
        self.name = f"Recording_{stamp}.mp4"
        self.take = self._cache / f"{stamp}.mp4"
        had_token = self._token.exists()
        self.dialog_expected = not self._grant(self._token, self.position) and not had_token
        argv = capture.command(
            self.take,
            self._token,
            self.config.fps,
            self.config.audio_kbps,
            capture.scope_unit(),
            quality=self.config.quality,
        )
        try:
            with open(self._cache / LOG_NAME, "w") as log:
                self._process = self._spawn(argv, log)
        except OSError as exc:
            self._fail(f"could not start gpu-screen-recorder: {exc}")
            return
        self.calls.start()

    def poll(self) -> None:
        """Advance on what the recorder has done. Call from the UI loop."""
        if self.phase not in (Phase.STARTING, Phase.RECORDING) or self._process is None:
            return
        if self._process.poll() is not None:
            if self.t0 is None:
                self._fail("the screen share was cancelled or did not start")
                self._discard()
            else:
                self.stop()  # the recorder died mid-take: keep what it wrote
            return
        if self.phase is Phase.STARTING and self.take is not None:
            self.t0 = capture.read_first_frame(capture.first_frame_path(self.take))
            if self.t0 is not None:
                self.phase = Phase.RECORDING

    def elapsed(self) -> float:
        if self.t0 is None:
            return 0.0
        if self.phase is Phase.RECORDING:
            return max(0.0, self._clock() - self.t0)
        return self.outcome.duration

    def toggle(self) -> None:
        """The hotkey pressed again: cancel before the first frame, else stop."""
        if self.phase is Phase.STARTING:
            self.cancel()
        elif self.phase is Phase.RECORDING:
            self.stop()

    def stop(self) -> None:
        if self.phase is not Phase.RECORDING or self.t0 is None:
            return
        self.phase = Phase.SAVING
        self.outcome.duration = self._clock() - self.t0
        self._signal()
        self._run(self._save)

    def cancel(self) -> None:
        if self.phase is not Phase.STARTING:
            return
        self.phase = Phase.CANCELLING
        self._signal()

        def reap() -> None:
            self.calls.finish()
            if self._process is not None:
                self._process.wait()
            self._discard()
            self.phase = Phase.CANCELLED

        self._run(reap)

    def wait(self, timeout: float | None = None) -> None:
        """Block until a save or cancel in progress is done."""
        if self._worker is not None:
            self._worker.join(timeout)

    # -- internals -------------------------------------------------------------

    def _signal(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.send_signal(signal.SIGINT)

    def _run(self, target: Callable[[], None]) -> None:
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()

    def _save(self) -> None:
        assert self.t0 is not None and self._process is not None and self.take is not None
        spans = calls.relative_spans(self.calls.finish(), self.t0)
        self._process.wait()
        try:
            if not self.take.exists() or self.take.stat().st_size == 0:
                raise RuntimeError("the recorder wrote no video")
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            dst = media.unique(self.config.output_dir / self.name)
            self._finalize(self.take, dst, spans, self.config.audio_kbps)
            size = dst.stat().st_size
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            reason = str(exc)
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                reason = exc.stderr.decode(errors="replace").strip().splitlines()[-1]
            self.outcome.kept = self.take if self.take.exists() else None
            self._fail(f"saving failed: {reason}")
            return
        capture.first_frame_path(self.take).unlink(missing_ok=True)
        self.take.unlink(missing_ok=True)
        self.outcome.path, self.outcome.size = dst, size
        self.phase = Phase.SAVED

    def _discard(self) -> None:
        if self.take is None:
            return
        self.take.unlink(missing_ok=True)
        capture.first_frame_path(self.take).unlink(missing_ok=True)

    def _fail(self, reason: str) -> None:
        tail = self.log_tail()
        self.outcome.error = f"{reason} ({tail})" if tail else reason
        self.calls.finish()
        self.phase = Phase.FAILED

    def log_tail(self) -> str:
        try:
            lines = (self._cache / LOG_NAME).read_text().strip().splitlines()
        except OSError:
            return ""
        return lines[-1] if lines else ""
