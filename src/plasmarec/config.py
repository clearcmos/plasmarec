"""User configuration: $XDG_CONFIG_HOME/plasmarec/config.toml.

Every key is optional. An unknown key or a value of the wrong type is an
error rather than a silent default, so a typo cannot quietly turn a setting
off.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# A running mic capture stream whose process binary or application name
# contains one of these keys counts as being in a call; the value is how the
# status card names it. Browsers are included because a web call (Meet,
# Discord in a tab) captures through the browser process.
DEFAULT_CALL_APPS: dict[str, str] = {
    "discord": "Discord",
    "vesktop": "Discord",
    "brave": "Brave",
    "chrome": "Chrome",
    "chromium": "Chromium",
    "firefox": "Firefox",
    "zoom": "Zoom",
    "slack": "Slack",
    "teams": "Teams",
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    output_dir: Path
    fps: int = 60
    audio_kbps: int = 160
    # How the status card names the key that stops a recording, for example
    # "Meta+F2". plasmarec does not own the binding, so it cannot read it.
    shortcut: str = ""
    call_apps: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CALL_APPS))


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "plasmarec" / "config.toml"


def _int(data: Mapping[str, object], key: str, default: int, low: int, high: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{key} must be an integer from {low} to {high}, got {value!r}")
    return value


def parse(data: Mapping[str, object], default_output_dir: Path) -> Config:
    known = {"output_dir", "fps", "audio_kbps", "shortcut", "call_apps"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown key(s): {', '.join(unknown)}")

    output_dir = data.get("output_dir", str(default_output_dir))
    if not isinstance(output_dir, str) or not output_dir:
        raise ConfigError(f"output_dir must be a path, got {output_dir!r}")

    shortcut = data.get("shortcut", "")
    if not isinstance(shortcut, str):
        raise ConfigError(f"shortcut must be a string, got {shortcut!r}")

    call_apps = data.get("call_apps", DEFAULT_CALL_APPS)
    if not isinstance(call_apps, dict) or not all(
        isinstance(k, str) and k and isinstance(v, str) and v for k, v in call_apps.items()
    ):
        raise ConfigError('call_apps must be a table of match = "Label" strings')

    return Config(
        output_dir=Path(output_dir).expanduser(),
        fps=_int(data, "fps", 60, 1, 240),
        audio_kbps=_int(data, "audio_kbps", 160, 32, 512),
        shortcut=shortcut,
        call_apps={k.lower(): v for k, v in call_apps.items()},
    )


def load(default_output_dir: Path, path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    try:
        return parse(data, default_output_dir)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
