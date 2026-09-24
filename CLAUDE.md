# plasmarec

Records one monitor on KDE Plasma 6.6+ (Wayland) to an MP4 through
gpu-screen-recorder, with a Basalt status card that KWin leaves out of the
video, and the mic mixed in only while a call app holds it. See README.md for
what users see.

## Structure

| Path | Role |
|---|---|
| `src/plasmarec/cli.py` | Entry point. Forwards to a running instance over D-Bus before importing GTK (the second key press stops quickly). |
| `src/plasmarec/config.py` | `config.toml` parsing; unknown keys and bad types are errors. |
| `src/plasmarec/calls.py` | PipeWire call detection from `pw-dump`, the sampling thread, span math. |
| `src/plasmarec/capture.py` | gpu-screen-recorder argv, per-monitor restore tokens, the first-frame file. |
| `src/plasmarec/media.py` | ffmpeg finalize: mix the mic over call spans or drop its track by stream copy. |
| `src/plasmarec/session.py` | The recording lifecycle as a GTK-free state machine (phase diagram in its docstring). |
| `src/plasmarec/ui.py` | GTK4 layer-shell picker and status card, the `Ghost.Confirm` D-Bus method. Exempt from coverage: needs a live layer-shell compositor. |
| `data/kwin/plasmarecghost/` | KWin script: sets `excludeFromCapture` on plasmarec's windows, then calls `Ghost.Confirm`. |
| `data/io.github.clearcmos.plasmarec.desktop` | Launcher users bind a shortcut to. |
| `packaging/arch/PKGBUILD` | Arch package; CI builds it from the tested commit. |
| `tests/` | One `test_<module>.py` per module; `kwin_harness.js` runs the KWin script under node against a fake KWin. |

The app id, object path, ghost interface and KWin script id are defined once in
`src/plasmarec/__init__.py`; `tests/test_kwin_script.py` fails if `main.js` or
`metadata.json` disagree with them.

## Commands

Set up once: `uv venv --system-site-packages --python /usr/bin/python3 && uv sync`

- Everything CI runs on Arch: `make check`
- Tests: `make test` (or `uv run pytest -q`)
- Lint and format check: `make lint`; apply formatting: `make format`
- Types: `make typecheck` (mypy strict)
- Coverage gate (85): `make coverage`
- Package: `cd packaging/arch && makepkg -si` (builds from GitHub main)
- Workflows: `actionlint`

## Conventions

- Basalt design tokens are copied into `ui.py`'s CSS; the spec lives in the
  author's system repo, and each consumer carries its own copy.
- No PyPI runtime dependencies. PyGObject and pycairo come from the
  distribution, which is why the venv uses the system site packages.
- Only `cli.py` (Gio, for the fast path) and `ui.py` import gi. Keep the other
  modules GTK-free so they run on the Python 3.12 floor without a display.
- Changelog: none kept. Git history and tagged GitHub releases carry the notes.
- Updates: dependabot, monthly, for GitHub Actions and the uv dev group. The
  Arch CI image floats on purpose.
- House style: no emojis, no em dashes, factual README.

## Decision log

### 2026-09-23: extracted from a private system-config script

- **Portal capture, never KMS.** KWin honors `excludeFromCapture` in its own
  screencast, which the portal uses. `gpu-screen-recorder -w <connector>` is
  KMS capture of the scanout buffer and records the card.
- **Per-monitor restore tokens.** The portal cannot be told which monitor to
  share. One token file per connector makes KDE's dialog a one-time step per
  monitor. Verified on three monitors: after the first dialog each, later
  recordings started without it.
- **Ghost matched by pid.** Layer-shell surfaces have no app id; KWin sets
  their `resourceClass` to the executable name (`python3.14`). The script asks
  D-Bus for the pid owning the app id and compares it with `window.pid`.
  Matching the class alone would hide every Python overlay on the desktop.
- **Confirmation instead of assumption.** On KWin older than 6.6,
  `excludeFromCapture` is not a property; assigning it only creates a JS field
  and hides nothing. The script checks `typeof ... === "boolean"` first, and
  the card says "visible in the video" until `Ghost.Confirm` arrives.
- **EnabledByDefault plus Scripting.start.** A package cannot edit each user's
  kwinrc. `EnabledByDefault: true` makes KWin load the installed script, and
  `org.kde.kwin.Scripting.start` over D-Bus loads it immediately (verified on
  KWin 6.7.5: a script with no kwinrc entry went from not loaded to loaded).
- **Spectacle screenshots omit the card too** (observed on KWin 6.7.5). To look
  at the design, run a copy with a different `APP_ID`.
- **No `-fm cfr`.** gpu-screen-recorder documents that CFR can overload the
  encoder and fall to 1-2 fps. VFR MP4 plays everywhere.
- **Mic on its own track, gated at the end.** It is sampled once a second from
  PipeWire and mixed with `volume=...:eval=frame`, timed against
  `-write-first-frame-ts`. Without a call the track is dropped by stream copy,
  so the delivered file never carries mic audio from outside a call.
- **`setpriv --pdeathsig INT`** so the recorder saves instead of recording on
  unattended if the Python process dies.
- **uv must be pointed at /usr/bin/python3.** Left alone, `uv venv` picked a
  uv-managed CPython 3.13 that cannot import the system's PyGObject.
- **PKGBUILD lives in `packaging/arch/`.** At the repo root, makepkg checks
  its git source out into `src/plasmarec`, which is the Python package, and
  ruff and mypy would then scan the duplicate tree.
- **`register_object_with_closures2` with a fallback.** PyGObject marks
  `DBusConnection.register_object` deprecated; the replacement needs GLib 2.84,
  and KDE neon's Ubuntu 24.04 base has 2.80.
