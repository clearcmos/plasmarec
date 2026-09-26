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
| `src/plasmarec/quality.py` | The quality presets and the remembered choice in the state dir (not a `config.toml` key, so the picker can write it). |
| `src/plasmarec/calls.py` | PipeWire call detection from `pw-dump`, the sampling thread, span math. |
| `src/plasmarec/capture.py` | gpu-screen-recorder argv, per-monitor restore tokens, the first-frame file. |
| `src/plasmarec/portal.py` | Writes the portal permission-store row that pre-authorizes the picked monitor (KDE's restore-data format). |
| `src/plasmarec/media.py` | ffmpeg finalize: mix the mic over call spans or drop its track by stream copy. |
| `src/plasmarec/session.py` | The recording lifecycle as a GTK-free state machine (phase diagram in its docstring). |
| `src/plasmarec/view.py` | Everything the surfaces decide without GTK: status-card and notification text, picker and quality-panel keys, the cursor-query KWin script, the Esc-from-anywhere pidfile. |
| `src/plasmarec/ui.py` | GTK4 layer-shell picker, quality panel and status card, the `Ghost` D-Bus methods (`Confirm`, `Pointer`). Draws what `view.py` decides. Exempt from coverage: needs a live layer-shell compositor. |
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
- Only `cli.py` and `portal.py` (Gio, for D-Bus) and `ui.py` (GTK) import gi.
  Keep the other modules gi-free, and nothing outside `ui.py` may need a
  display, so the suite runs on the Python 3.12 floor.
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
- **plasmarec writes its own restore row (no dialog at all).** A restore
  token is only a permission-store row: `{app_id: ["yes"]}` plus
  `('KDE', 1, <QDataStream map>)` naming the output by logical position
  `"<x>x<y>"`. Rows KDE wrote for three monitors read `960x142`, `0x0` and
  `2667x0`, their kscreen positions. `portal.restore_data` reproduces KDE's
  bytes exactly (golden test in `tests/test_portal.py`). A format change makes
  the portal fall back to its dialog; it cannot share a different output than
  the position names. Chosen over patching xdg-desktop-portal-kde, which
  would mean maintaining a fork of a system package that users do not have.
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
- **The recorder runs in `app-<app id>-<random>.scope`.** The portal files
  restore tokens under the caller's app id, parsed from its systemd unit. A
  token saved when xremap launched the recorder (`xremap.service`) was ignored
  when a terminal launched it (`app-foot@....service`), and KDE's dialog came
  back. `systemd-run --scope` execs in place, so the pid, the SIGINT stop and
  the pdeathsig are unchanged; `--expand-environment=no` because it otherwise
  rewrites `$` in arguments.
- **uv must be pointed at /usr/bin/python3.** Left alone, `uv venv` picked a
  uv-managed CPython 3.13 that cannot import the system's PyGObject.
- **PKGBUILD lives in `packaging/arch/`.** At the repo root, makepkg checks
  its git source out into `src/plasmarec`, which is the Python package, and
  ruff and mypy would then scan the duplicate tree.
- **`register_object_with_closures2` with a fallback.** PyGObject marks
  `DBusConnection.register_object` deprecated; the replacement needs GLib 2.84,
  and KDE neon's Ubuntu 24.04 base has 2.80.
- **The keyboard picker takes the keyboard back when it loses it.** KWin
  activates an exclusive layer surface only when its keyboard mode changes
  (`LayerShellV1Window::handleAcceptsFocusChanged`), so any window that maps or
  is clicked kept the focus and Esc stopped cancelling. `on_picker_focus` turns
  the mode off and on again, `REGRAB_MS` apart so KWin sees two commits. No unit
  test covers it because `ui.py` needs a live compositor; it was verified
  against KWin by activating another window and reading
  `workspace.activeWindow` from a KWin script, then sending Esc.

### 2026-09-26: Basalt dismissal, window type, and the quality panel

- **Esc pressed anywhere cancels the picker.** The author's desktop binds Esc
  to `basalt-dismiss`, which sends SIGUSR1 to every pid registered in
  `$XDG_RUNTIME_DIR/basalt/` whose command line contains the file's name. The
  picker registers `plasmarec.pid` while it is up and removes it when a monitor
  is chosen or on shutdown, so a running recording never hears the signal.
  Without the hook the file is inert; the regrab above covers Esc on its own.
- **Layer-shell namespace `utility`.** KWin types a layer surface by its
  namespace, and any name outside its list (the old `plasmarec`) makes it a
  Normal window, which runs KWin's Scale zoom on top of the cards' own fade.
  `utility` gets no KWin animation. The ghost script matches by pid, not by
  namespace, so capture exclusion is unaffected.
- **E asks KWin where the cursor is.** The quality panel opens on the output
  under the cursor, so a one-shot KWin script reports
  `workspace.screenAt(workspace.cursorPos)` through the `Pointer` D-Bus
  method. Tracking hover on the picker cards missed a cursor anywhere else on
  a screen (a layer surface sees the pointer only over itself), and
  `activeOutputName` follows the keyboard, which one picker holds. Each press
  loads a fresh file and plugin name because `loadScript` refuses a path it
  has loaded before; 300 ms without an answer falls back to the start output.
- **`view.py` split out of `ui.py`.** `ui.py` is exempt from coverage, and it
  had grown to hold card text, both key maps, the cursor-query script and the
  pidfile, none of which needs GTK. They moved to `view.py`, which is covered;
  `ui.py` keeps only drawing and wiring.
