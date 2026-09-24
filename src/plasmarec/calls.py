"""Whether a call app holds the mic, and for which stretches of a recording.

The mic is recorded to its own track the whole time and mixed in afterwards
only over the spans found here, so joining or leaving a call mid-recording is
handled and nothing from outside a call reaches the delivered file.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

POLL_S = 1.0
# The watch samples once a second, so a span edge is known to within a poll;
# widen each span by that much rather than clip the first or last word.
PAD_S = 0.3


def find_call(objects: list[dict[str, Any]], call_apps: Mapping[str, str]) -> str | None:
    """Label of the first call app with a running mic capture stream in a
    pw-dump object list, or None."""
    # Stream nodes often carry no application.* keys of their own; the owning
    # client object does, so merge the two before matching.
    clients = {
        obj.get("id"): (obj.get("info") or {}).get("props") or {}
        for obj in objects
        if obj.get("type") == "PipeWire:Interface:Client"
    }
    for obj in objects:
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if (
            obj.get("type") != "PipeWire:Interface:Node"
            or props.get("media.class") != "Stream/Input/Audio"
            or info.get("state") != "running"
        ):
            continue
        merged = {**clients.get(props.get("client.id"), {}), **props}
        ident = " ".join(
            str(merged.get(key, "")) for key in ("application.process.binary", "application.name")
        ).lower()
        for match, label in call_apps.items():
            if match in ident:
                return label
    return None


def probe(call_apps: Mapping[str, str]) -> str | None:
    """find_call against the live PipeWire graph. A failed pw-dump reads as
    no call: the mic then stays out, which is the safe side."""
    try:
        dump = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=3, check=True).stdout
        objects = json.loads(dump)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return find_call(objects, call_apps) if isinstance(objects, list) else None


class CallWatch(threading.Thread):
    """Samples `probe` every POLL_S and records, on `clock`, the spans during
    which it reported a call. `app` is the current label, for display."""

    def __init__(
        self,
        probe: Callable[[], str | None],
        clock: Callable[[], float] = time.monotonic,
        interval: float = POLL_S,
    ) -> None:
        super().__init__(daemon=True)
        self._probe = probe
        self._clock = clock
        self._interval = interval
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._spans: list[tuple[float, float]] = []
        self._since: float | None = None
        self.app: str | None = None

    def sample(self) -> None:
        app = self._probe()
        now = self._clock()
        with self._lock:
            if app and self._since is None:
                self._since = now
            elif not app and self._since is not None:
                self._spans.append((self._since, now))
                self._since = None
            self.app = app

    def run(self) -> None:
        while not self._done.is_set():
            self.sample()
            self._done.wait(self._interval)

    def finish(self) -> list[tuple[float, float]]:
        """Stop sampling, close any open span at now, and return the spans."""
        self._done.set()
        if self.is_alive():
            self.join()
        with self._lock:
            if self._since is not None:
                self._spans.append((self._since, self._clock()))
                self._since = None
            return list(self._spans)


def relative_spans(
    spans: list[tuple[float, float]], t0: float, pad: float = PAD_S
) -> list[tuple[float, float]]:
    """Clock spans -> sorted, padded, merged seconds from the first frame at t0.
    Merged spans never overlap, which finalize's gain expression relies on."""
    out: list[list[float]] = []
    for start, end in sorted(spans):
        rel_start, rel_end = max(0.0, start - t0 - pad), end - t0 + pad
        if rel_end <= 0:
            continue
        if out and rel_start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], rel_end)
        else:
            out.append([rel_start, rel_end])
    return [(a, b) for a, b in out]
