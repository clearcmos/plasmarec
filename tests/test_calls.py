from __future__ import annotations

import json
import subprocess
import unittest
from typing import Any
from unittest import mock

from plasmarec import calls
from plasmarec.config import DEFAULT_CALL_APPS


def node(id_: int, media_class: str, state: str, client: int, **props: str) -> dict[str, Any]:
    return {
        "id": id_,
        "type": "PipeWire:Interface:Node",
        "info": {"state": state, "props": {"media.class": media_class, "client.id": client, **props}},
    }


def client(id_: int, binary: str) -> dict[str, Any]:
    return {
        "id": id_,
        "type": "PipeWire:Interface:Client",
        "info": {"props": {"application.process.binary": binary}},
    }


class FindCall(unittest.TestCase):
    def test_running_capture_from_a_call_app_counts(self) -> None:
        objects = [client(1, "Discord"), node(10, "Stream/Input/Audio", "running", 1)]
        self.assertEqual(calls.find_call(objects, DEFAULT_CALL_APPS), "Discord")

    def test_identity_can_come_from_the_node_itself(self) -> None:
        objects = [node(10, "Stream/Input/Audio", "running", 99, **{"application.name": "Brave"})]
        self.assertEqual(calls.find_call(objects, DEFAULT_CALL_APPS), "Brave")

    def test_other_captures_idle_streams_and_playback_do_not(self) -> None:
        objects = [
            client(1, "gpu-screen-recorder"),
            client(2, "brave"),
            client(3, "discord"),
            node(10, "Stream/Input/Audio", "running", 1),  # the recorder's own mic stream
            node(11, "Stream/Input/Audio", "idle", 2),  # opened, not capturing
            node(12, "Stream/Output/Audio", "running", 3),  # playback, not the mic
        ]
        self.assertIsNone(calls.find_call(objects, DEFAULT_CALL_APPS))

    def test_matching_uses_the_configured_apps(self) -> None:
        objects = [client(1, "webcord"), node(10, "Stream/Input/Audio", "running", 1)]
        self.assertIsNone(calls.find_call(objects, DEFAULT_CALL_APPS))
        self.assertEqual(calls.find_call(objects, {"webcord": "WebCord"}), "WebCord")


class Probe(unittest.TestCase):
    def run_with(self, result: Any) -> str | None:
        with mock.patch("plasmarec.calls.subprocess.run", **result):
            return calls.probe(DEFAULT_CALL_APPS)

    def test_reads_the_live_graph(self) -> None:
        dump = json.dumps([client(1, "zoom"), node(2, "Stream/Input/Audio", "running", 1)])
        done = subprocess.CompletedProcess([], 0, stdout=dump)
        self.assertEqual(self.run_with({"return_value": done}), "Zoom")

    def test_a_failed_or_garbled_dump_reads_as_no_call(self) -> None:
        # No call keeps the mic out of the file, the safe side of the error.
        for result in (
            {"side_effect": FileNotFoundError("pw-dump")},
            {"side_effect": subprocess.TimeoutExpired("pw-dump", 3)},
            {"return_value": subprocess.CompletedProcess([], 0, stdout="not json")},
            {"return_value": subprocess.CompletedProcess([], 0, stdout='{"a": 1}')},
        ):
            with self.subTest(result=result):
                self.assertIsNone(self.run_with(result))


class Watch(unittest.TestCase):
    def test_spans_follow_the_probe(self) -> None:
        answers = iter([None, "Discord", "Discord", None, "Brave"])
        now = iter([1.0, 2.0, 3.0, 4.0, 5.0, 9.0])
        watch = calls.CallWatch(lambda: next(answers), clock=lambda: next(now))
        for _ in range(5):
            watch.sample()
        self.assertEqual(watch.app, "Brave")
        # The Brave call is still open at finish, so it closes at the finish time.
        self.assertEqual(watch.finish(), [(2.0, 4.0), (5.0, 9.0)])

    def test_thread_samples_until_finished(self) -> None:
        watch = calls.CallWatch(lambda: "Zoom", interval=0.001)
        watch.start()
        spans = watch.finish()
        self.assertFalse(watch.is_alive())
        self.assertEqual(len(spans), 1)
        self.assertEqual(watch.app, "Zoom")


class RelativeSpans(unittest.TestCase):
    def test_relative_padded_and_merged(self) -> None:
        spans = calls.relative_spans([(105.0, 110.0), (110.2, 112.0), (90.0, 95.0)], 100.0, pad=0.3)
        # (90, 95) ended before the first frame; the other two touch once padded.
        self.assertEqual(spans, [(4.7, 12.3)])

    def test_span_open_at_the_first_frame_starts_at_zero(self) -> None:
        self.assertEqual(calls.relative_spans([(98.0, 101.0)], 100.0)[0][0], 0.0)

    def test_separate_spans_stay_separate_and_sorted(self) -> None:
        spans = calls.relative_spans([(120.0, 121.0), (101.0, 102.0)], 100.0, pad=0.0)
        self.assertEqual(spans, [(1.0, 2.0), (20.0, 21.0)])


if __name__ == "__main__":
    unittest.main()
