# plasmarec

Records one monitor on KDE Plasma (Wayland) to an MP4, with one key to start
and stop. While it records, a status card at the top of that monitor shows the
elapsed time and whether your microphone is in the mix. The card is left out of
the video.

- Press the key: a card appears on every monitor. Click one, or press its
  number (monitors count left to right). Esc cancels.
- Press the key again: the recording stops and lands on your Desktop as
  `Recording_<date>_<time>.mp4` (H.264 video, AAC audio).
- System audio is always recorded. Your microphone is mixed in only for the
  parts of the recording where a call app (Discord, a browser in Google Meet,
  Zoom, and so on) was using it.

## Requirements

- KDE Plasma 6.6 or later on Wayland. KWin 6.6 is where windows can be
  excluded from screen capture; on older versions the card shows up in the
  video, and plasmarec says so on the card.
- [gpu-screen-recorder](https://git.dec05eba.com/gpu-screen-recorder/about/),
  ffmpeg, PipeWire (`pw-dump`), xdg-desktop-portal-kde.
- Python 3.12+ with PyGObject and pycairo, GTK 4, gtk4-layer-shell,
  libnotify (`notify-send`), util-linux (`setpriv`), and a systemd user
  session (systemd 254 or later).

## Install

Arch Linux, from this repository:

```sh
git clone https://github.com/clearcmos/plasmarec.git && cd plasmarec/packaging/arch && makepkg -si
```

The package installs the `plasmarec` command, a launcher, and a KWin script
(`plasmarecghost`) that is enabled by default. plasmarec asks KWin to load the
script the first time it runs, so there is no need to log out.

Then bind a key: System Settings > Keyboard > Shortcuts > Add New >
Application > plasmarec.

KDE's "Share screen with" dialog does not appear: the monitor you click in
plasmarec's picker is the consent (see How it works). If a KDE update changes
the format plasmarec writes, the dialog comes back; choose the same monitor.

## Configuration

Optional, in `~/.config/plasmarec/config.toml`. Every key has a default, and an
unknown key or a wrong type is an error rather than being ignored.

```toml
output_dir = "~/Videos"   # default: your XDG Desktop folder
fps = 60
audio_kbps = 160
shortcut = "Meta+F2"      # shown on the card as the key that stops

# Replaces the built-in list. A running mic stream whose process binary or
# application name contains a key counts as a call; the value is its label.
[call_apps]
discord = "Discord"
firefox = "Firefox"
```

## How it works

- Capture is gpu-screen-recorder through the screencast portal, which uses
  KWin's own screencast. The portal has no way to be told which monitor to
  share; it only restores an earlier choice from a token. Before each
  recording plasmarec writes that choice itself: a row in the portal's
  permission store (the store `flatpak permission-set` edits) allowing
  plasmarec to share the output at the clicked monitor's position, in the
  format xdg-desktop-portal-kde uses for its own rows. This skips KDE's
  consent dialog, which is the point; clicking the monitor is the consent.
- The KWin script sets KWin's `excludeFromCapture` on plasmarec's windows and
  reports back over D-Bus. Only after that report does the card say "not in the
  video".
- The microphone is recorded to a second track. When you stop, the mic is
  mixed in over the call spans (polled from PipeWire once a second) or the
  track is dropped with a stream copy, so the delivered file has one audio
  track either way. Nothing from the mic outside a call reaches it.
- The raw take is written to `~/.cache/plasmarec` first, together with
  gpu-screen-recorder's log. If saving fails, the take is left there.

## Limitations

- Whole monitors only; no region or window recording, no pause.
- A call counts from the moment the app opens the mic, including while you are
  muted in it, and any browser tab using the mic counts as a call.
- Recordings use a variable frame rate. Players and browsers handle it; some
  video editors prefer constant frame rate.
- Other desktops are not supported. niri and Hyprland can hide surfaces from
  capture only as black boxes, and GNOME has no layer-shell.

## Development

```sh
uv venv --system-site-packages --python /usr/bin/python3 && uv sync
make check        # lint, format check, type check, tests with the coverage gate
make test         # tests only
make format       # apply ruff format
```

PyGObject is not installable from PyPI without building it against the system
GTK, so the virtual environment uses the system site packages. The KWin script
tests need `node`.

## License

MIT
