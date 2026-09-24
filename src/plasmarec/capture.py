"""The gpu-screen-recorder invocation and the files it leaves behind.

Capture goes through the screencast portal. KWin leaves windows flagged
excludeFromCapture out of its own screencasts, which is what keeps the status
card out of the video; gpu-screen-recorder's KMS capture (-w <connector>)
reads the scanout buffer instead and would record the card.

The portal cannot be told which monitor to share, so each monitor keeps its
own restore token: the first recording of a monitor shows KDE's share dialog
once, and later ones restore that choice without asking.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "plasmarec"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "plasmarec"


def token_path(connector: str, directory: Path | None = None) -> Path:
    return (directory or state_dir()) / f"{connector}.token"


def first_frame_path(output: Path) -> Path:
    """-write-first-frame-ts puts this next to the output once frames flow."""
    return Path(f"{output}.ts")


def command(output: Path, token: Path, fps: int, audio_kbps: int) -> list[str]:
    return [
        # If plasmarec dies mid-take, the recorder gets SIGINT and saves
        # rather than recording on unattended.
        "setpriv", "--pdeathsig", "INT", "--",
        "gpu-screen-recorder",
        "-w", "portal",
        "-restore-portal-session", "yes",
        "-portal-session-token-filepath", str(token),
        "-c", "mp4",
        "-k", "h264",
        "-ac", "aac",
        "-ab", str(audio_kbps),
        "-f", str(fps),
        # No -fm cfr: gpu-screen-recorder warns that constant frame rate can
        # overload the encoder and collapse to 1-2 fps. VFR MP4 plays anywhere.
        "-q", "very_high",
        "-cursor", "yes",
        # Track 0 is system audio, track 1 the mic; media.finalize depends
        # on that order.
        "-a", "default_output",
        "-a", "default_input",
        "-write-first-frame-ts", "yes",
        "-v", "no",
        "-o", str(output),
    ]  # fmt: skip


def read_first_frame(path: Path) -> float | None:
    """Monotonic seconds of the first frame, or None until the file exists.

    The file is a header line and then `<monotonic_us>\t<realtime_us>`.
    """
    try:
        lines = path.read_text().splitlines()
        return int(lines[1].split()[0]) / 1e6
    except (OSError, IndexError, ValueError):
        return None
