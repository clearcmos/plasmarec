"""What the surfaces show and what keys do, as plain data.

ui.py draws layer-shell surfaces and needs a live compositor, so it is exempt
from the coverage gate. Every decision it makes that does not need GTK lives
here instead, where it is tested: the status card's text, the notification
after a take, the picker and quality-panel keys, the one-shot KWin script
that finds the cursor's output, and the pidfile that lets Esc pressed
anywhere cancel the picker.

Keys arrive as their GDK name (`Gdk.keyval_name`) and the character they
type, so nothing here imports gi.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path

from plasmarec import APP_ID, GHOST_INTERFACE, OBJECT_PATH
from plasmarec.session import Outcome, Phase

# -- the status card -----------------------------------------------------------


def clock(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    if hours:
        return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"
    return f"{rest // 60}:{rest % 60:02d}"


def phrase(shortcut: str, verb: str) -> str:
    """How to stop or cancel: the configured key, or running plasmarec again."""
    key = shortcut.lower()
    return f"{key} {verb}s" if key else f"run plasmarec again to {verb}"


@dataclass(frozen=True)
class CardText:
    title: str | None  # None leaves the title as it is
    body: str
    hint: str | None = None  # None leaves the hint as it is
    warn: bool = False
    side: str | None = None  # the elapsed time while recording


def card_text(
    phase: Phase,
    connector: str,
    call_app: str | None,
    dialog_expected: bool,
    ghosted: bool,
    shortcut: str,
    elapsed: float,
) -> CardText:
    """The status card for one moment of a session.

    `ghosted` is whether the KWin script confirmed the card is left out of
    the capture; until it does, the card says it is in the video.
    """
    mic = f"mic on, {call_app}" if call_app else "mic off"
    body = f"{connector}  system audio  {mic}"
    if phase is Phase.STARTING:
        if dialog_expected:
            return CardText("Starting", body, f"choose the {connector} entry in KDE's dialog")
        return CardText("Starting", body, phrase(shortcut, "cancel"))
    if phase is Phase.RECORDING:
        stop = phrase(shortcut, "stop")
        if ghosted:
            return CardText("Recording", body, f"{stop}    not in the video", side=clock(elapsed))
        return CardText("Recording", body, f"{stop}    visible in the video", warn=True, side=clock(elapsed))
    if phase in (Phase.SAVING, Phase.CANCELLING):
        return CardText("Saving" if phase is Phase.SAVING else "Cancelling", body, "")
    return CardText(None, body)


def outcome_notice(phase: Phase, outcome: Outcome) -> tuple[str, str] | None:
    """The desktop notification after a finished session, or None for none."""
    if phase is Phase.SAVED and outcome.path is not None:
        size = f"{outcome.size / 1e6:.0f} MB"
        return "Recording saved", f"{outcome.path.name}\n{clock(outcome.duration)}, {size}"
    if phase is Phase.FAILED:
        kept = f"\nThe raw take is at {outcome.kept}" if outcome.kept else ""
        return "Recording failed", outcome.error + kept
    return None


def picker_body(connector: str, size: tuple[int, int], quality_label: str) -> str:
    return f"{connector}  {size[0]}x{size[1]}  {quality_label.lower()}"


# -- keys ----------------------------------------------------------------------

CANCEL = "cancel"
SETTINGS = "settings"
SAVE = "save"

_DOWN = ("Down", "Tab", "j")
_UP = ("Up", "ISO_Left_Tab", "k")


def _number(char: str, count: int) -> int | None:
    """The 0-based row a digit key names, or None when it names none."""
    if len(char) == 1 and char.isdigit() and 1 <= int(char) <= count:
        return int(char) - 1
    return None


def picker_key(name: str, char: str, count: int) -> str | int | None:
    """A key on the monitor picker: CANCEL, SETTINGS, a monitor index, or None."""
    if name == "Escape":
        return CANCEL
    if name in ("e", "E"):
        return SETTINGS
    return _number(char, count)


def settings_key(name: str, char: str, current: int, count: int) -> str | int:
    """A key on the quality panel: CANCEL, SAVE, or the preset to mark next.

    Arrows and Tab wrap around; a key that means nothing keeps `current`.
    """
    if name == "Escape":
        return CANCEL
    if name in ("e", "E"):
        return SAVE
    if name in _DOWN:
        return (current + 1) % count
    if name in _UP:
        return (current - 1) % count
    number = _number(char, count)
    return current if number is None else number


# -- the cursor's output -------------------------------------------------------

# Reports the output under the cursor through the Pointer D-Bus method. A
# layer surface cannot see the pointer outside itself, and KWin's
# activeOutputName follows the keyboard, which one picker holds, so only
# KWin's own cursorPos says which screen the user is on.
POINTER_JS = """var o = workspace.screenAt(workspace.cursorPos);
callDBus("%s", "%s", "%s", "Pointer", o ? o.name : "");
"""


def pointer_script(runtime_dir: str, stamp: str) -> tuple[Path, str, str]:
    """Where to write the one-shot script, its KWin plugin name, and its text.

    `stamp` must differ on every press: KWin's loadScript refuses a path it
    has already loaded, and reuses script ids after unloadScript.
    """
    name = f"plasmarec-pointer-{stamp}"
    return Path(runtime_dir) / f"{name}.js", name, POINTER_JS % (APP_ID, OBJECT_PATH, GHOST_INTERFACE)


# -- Esc from anywhere ---------------------------------------------------------


def dismiss_pidfile(runtime_dir: str) -> Path:
    """Where the picker registers for Basalt's Esc-from-anywhere hook.

    The hook (the author's `basalt-dismiss`, bound to Esc) sends SIGUSR1 to
    each pid registered in this directory whose command line contains the
    file's name, so the name must stay `plasmarec`.
    """
    return Path(runtime_dir) / "basalt" / "plasmarec.pid"


def register_dismiss(runtime_dir: str) -> None:
    path = dismiss_pidfile(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n")


def unregister_dismiss(runtime_dir: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        dismiss_pidfile(runtime_dir).unlink()
