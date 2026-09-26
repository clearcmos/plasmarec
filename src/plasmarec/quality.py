"""Recording quality presets and the remembered choice.

The presets are gpu-screen-recorder's own `-q` levels. In its default QP
bitrate mode each one holds a constant quality, so a higher level costs file
size rather than motion detail. The picker's settings panel chooses one, and
the choice is kept in $XDG_STATE_HOME/plasmarec/quality for every later
recording.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from plasmarec import capture


@dataclass(frozen=True)
class Preset:
    key: str  # the gpu-screen-recorder -q value
    label: str
    note: str


PRESETS: tuple[Preset, ...] = (
    Preset("medium", "Medium", "smallest files"),
    Preset("high", "High", "smaller files"),
    Preset("very_high", "Very high", "the default"),
    Preset("ultra", "Ultra", "largest files, finest detail"),
)
DEFAULT = "very_high"
KEYS = frozenset(p.key for p in PRESETS)


def label(key: str) -> str:
    return next(p.label for p in PRESETS if p.key == key)


def path(directory: Path | None = None) -> Path:
    return (directory or capture.state_dir()) / "quality"


def load(directory: Path | None = None) -> str:
    """The saved preset, or the default when none is saved or it is unreadable.

    A bad file falls back rather than failing: it only ever holds what save()
    wrote, and a recording should not refuse to start over it."""
    try:
        key = path(directory).read_text().strip()
    except OSError:
        return DEFAULT
    return key if key in KEYS else DEFAULT


def save(key: str, directory: Path | None = None) -> None:
    if key not in KEYS:
        raise ValueError(f"unknown quality preset {key!r}")
    target = path(directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed over it, so a crash mid-write
    # leaves the old choice rather than a truncated file.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".quality.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{key}\n")
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
