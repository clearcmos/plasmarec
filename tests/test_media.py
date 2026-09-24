from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from plasmarec import media


def make_take(path: Path) -> None:
    """3 s of video, a silent system track and a loud constant mic track:
    the shape gpu-screen-recorder writes with -a default_output -a default_input."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=3",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip


def mean_volume(path: Path, start: float, length: float) -> float:
    res = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", str(start), "-t", str(length), "-i", str(path),
         "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    for line in res.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split()[0])
    raise AssertionError("volumedetect printed nothing")


class Finalize(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.take = self.root / "take.mp4"
        make_take(self.take)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_without_a_call_the_mic_track_is_dropped(self) -> None:
        out = self.root / "out.mp4"
        media.finalize(self.take, out, [], audio_kbps=128)
        self.assertEqual(media.audio_tracks(out), 1)
        self.assertLess(mean_volume(out, 0.5, 2.0), -80)

    def test_mic_is_mixed_in_only_during_the_call(self) -> None:
        out = self.root / "out.mp4"
        media.finalize(self.take, out, [(1.0, 2.0)], audio_kbps=128)
        self.assertEqual(media.audio_tracks(out), 1)
        self.assertLess(mean_volume(out, 0.2, 0.6), -80)
        self.assertGreater(mean_volume(out, 1.2, 0.6), -30)
        self.assertLess(mean_volume(out, 2.3, 0.5), -80)

    def test_video_is_copied_not_reencoded(self) -> None:
        out = self.root / "out.mp4"
        media.finalize(self.take, out, [(1.0, 2.0)], audio_kbps=128)
        probe = ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                 "packet=size", "-of", "csv=p=0"]  # fmt: skip
        before = subprocess.run([*probe, str(self.take)], capture_output=True, text=True, check=True)
        after = subprocess.run([*probe, str(out)], capture_output=True, text=True, check=True)
        self.assertEqual(before.stdout, after.stdout)

    def test_a_broken_take_raises(self) -> None:
        broken = self.root / "broken.mp4"
        broken.write_bytes(b"not a video")
        with self.assertRaises(subprocess.CalledProcessError):
            media.finalize(broken, self.root / "out.mp4", [], audio_kbps=128)


class FinalizeCommand(unittest.TestCase):
    def test_spans_but_no_mic_track_falls_back_to_a_copy(self) -> None:
        cmd = media.finalize_command(Path("a"), Path("b"), [(1.0, 2.0)], tracks=1, audio_kbps=128)
        self.assertNotIn("-filter_complex", cmd)
        self.assertIn("0:a:0?", cmd)

    def test_gain_expression_covers_every_span(self) -> None:
        cmd = media.finalize_command(Path("a"), Path("b"), [(1.0, 2.5), (4.0, 5.0)], 2, 128)
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("volume='between(t,1.000,2.500)+between(t,4.000,5.000)':eval=frame", graph)
        self.assertEqual(cmd[-1], "b")


class Misc(unittest.TestCase):
    def test_audio_tracks_of_a_missing_file_is_zero(self) -> None:
        self.assertEqual(media.audio_tracks(Path("/nonexistent.mp4")), 0)

    def test_unique_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "Recording.mp4"
            self.assertEqual(media.unique(first), first)
            first.touch()
            (Path(tmp) / "Recording-2.mp4").touch()
            self.assertEqual(media.unique(first), Path(tmp) / "Recording-3.mp4")


if __name__ == "__main__":
    unittest.main()
