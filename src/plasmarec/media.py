"""Turn gpu-screen-recorder's take into the delivered file."""

from __future__ import annotations

import subprocess
from pathlib import Path


def audio_tracks(path: Path) -> int:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return 0
    return len(res.stdout.split())


def finalize_command(
    src: Path, dst: Path, spans: list[tuple[float, float]], tracks: int, audio_kbps: int
) -> list[str]:
    """ffmpeg argv: video plus system audio (track 0), with the mic (track 1)
    mixed in over `spans`, or dropped by a plain stream copy when there are no
    spans or no mic track."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
    if tracks >= 2 and spans:
        # The spans are merged, so this sum is 0 or 1: the mic's gain at t.
        gate = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)
        cmd += [
            "-filter_complex",
            f"[0:a:1]volume='{gate}':eval=frame[mic];"
            "[0:a:0][mic]amix=inputs=2:duration=first:normalize=0[aout]",
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        ]  # fmt: skip
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy"]
    return [*cmd, "-movflags", "+faststart", str(dst)]


def finalize(src: Path, dst: Path, spans: list[tuple[float, float]], audio_kbps: int) -> None:
    cmd = finalize_command(src, dst, spans, audio_tracks(src), audio_kbps)
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL, capture_output=True)


def unique(path: Path) -> Path:
    """`path`, or the first free `<stem>-N<suffix>` beside it."""
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate
