"""GTK4 layer-shell surfaces: the monitor picker and the status card.

Visual language: Basalt (dark floating panels, monospace, one blue accent).
The recording lifecycle lives in session.py; this module draws it and turns
input into calls on it. It needs a Wayland compositor with wlr-layer-shell,
so it is exempt from the coverage gate (see pyproject.toml).
"""

from __future__ import annotations

import signal
import subprocess
from ctypes import CDLL
from typing import Any

# gtk4-layer-shell has to be loaded before gi pulls in libwayland-client.
CDLL("libgtk4-layer-shell.so.0")

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from plasmarec import APP_ID, GHOST_INTERFACE, KWIN_SCRIPT  # noqa: E402
from plasmarec.config import Config  # noqa: E402
from plasmarec.session import Phase, Session  # noqa: E402

PICK_WIDTH = 400
CARD_WIDTH = 420
TOP_MARGIN = 28
# Basalt shadow-space: transparent surface kept around the card so its
# shadow-card is not clipped.
SHADOW_SIDE, SHADOW_TOP, SHADOW_BOTTOM = 30, 20, 40
POLL_MS = 100
# Card mapped -> KWin flags it -> first frame. The flag is set within a D-Bus
# round trip of the map; this keeps the first captured frame after it.
GHOST_SETTLE_MS = 150
FADE_IN_MS = 140

GHOST_XML = f"""<node><interface name="{GHOST_INTERFACE}"><method name="Confirm"/></interface></node>"""

CSS = """
window { background: transparent; }
.panel {
  background-color: #161618;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 17px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.55);
}
.panel.pick { transition: background-color 120ms; }
.panel.pick:hover { background-color: #1c1c1f; }
.title {
  color: #e6e6e6;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 18px;
  font-weight: bold;
}
.meta {
  color: #7f7f86;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 14px;
}
.chip {
  background-color: #202024;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 7px;
  color: #5c9ae6;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 14px;
  padding: 2px 11px;
}
.rule { background-color: rgba(255, 255, 255, 0.08); min-height: 1px; }
.body {
  color: #e6e6e6;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 16px;
}
.hint {
  color: #7f7f86;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 13px;
}
.hint.warn { color: #5c9ae6; }
"""


def clock(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    if hours:
        return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"
    return f"{rest // 60}:{rest % 60:02d}"


def notify(summary: str, body: str = "") -> None:
    subprocess.Popen(
        ["notify-send", "-a", "plasmarec", "-i", "media-record", summary, body],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip


def monitors() -> list[Any]:
    """GDK monitors left to right: the order the picker numbers them in."""
    items = Gdk.Display.get_default().get_monitors()
    found = [items.get_item(i) for i in range(items.get_n_items())]
    return sorted(found, key=lambda m: (m.get_geometry().x, m.get_geometry().y))


def pixel_size(monitor: Any) -> tuple[int, int]:
    geo, scale = monitor.get_geometry(), monitor.get_scale()
    return round(geo.width * scale), round(geo.height * scale)


def ensure_ghost_script() -> None:
    """Have KWin load the ghost script if it is installed but not running yet
    (just installed, not logged out since). Scripting.start loads every
    enabled script that is not loaded; it is a no-op for the rest."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        args = GLib.Variant("(s)", (KWIN_SCRIPT,))
        loaded = bus.call_sync(
            "org.kde.KWin",
            "/Scripting",
            "org.kde.kwin.Scripting",
            "isScriptLoaded",
            args,
            None,
            Gio.DBusCallFlags.NONE,
            1000,
        )
        if not loaded.unpack()[0]:
            bus.call_sync(
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting",
                "start",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                2000,
            )
    except GLib.Error:
        pass  # not KWin; the status card will say the video shows it


class Card:
    """Basalt anatomy: header (title, chip or meta), rule, body, hint."""

    def __init__(self, title: str, chip: str | None = None) -> None:
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.panel.add_css_class("panel")
        self.panel.set_margin_start(SHADOW_SIDE)
        self.panel.set_margin_end(SHADOW_SIDE)
        self.panel.set_margin_top(SHADOW_TOP)
        self.panel.set_margin_bottom(SHADOW_BOTTOM)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        header.set_margin_top(22)
        header.set_margin_start(29)
        header.set_margin_end(29)
        header.set_margin_bottom(14)
        self.title = Gtk.Label(label=title, xalign=0.0, hexpand=True)
        self.title.add_css_class("title")
        self.side = Gtk.Label(label=chip or "")
        self.side.add_css_class("chip" if chip else "meta")
        self.side.set_valign(Gtk.Align.CENTER if chip else Gtk.Align.BASELINE)
        header.append(self.title)
        header.append(self.side)
        rule = Gtk.Box()
        rule.add_css_class("rule")
        self.body = Gtk.Label(xalign=0.0)
        self.body.add_css_class("body")
        self.body.set_margin_top(14)
        self.hint = Gtk.Label(xalign=0.0)
        self.hint.add_css_class("hint")
        self.hint.set_margin_top(10)
        self.hint.set_margin_bottom(14)
        for label in (self.body, self.hint):
            label.set_margin_start(29)
            label.set_margin_end(29)
        for child in (header, rule, self.body, self.hint):
            self.panel.append(child)

    def set_hint(self, text: str, warn: bool = False) -> None:
        self.hint.set_label(text)
        if warn:
            self.hint.add_css_class("warn")
        else:
            self.hint.remove_css_class("warn")

    def fade_in(self) -> None:
        """Ease-out cubic. The frame clock only ticks once the surface is
        mapped, so the start of the fade is on screen."""
        self.panel.set_opacity(0.0)
        start: int | None = None

        def tick(widget: Any, frame_clock: Any) -> bool:
            nonlocal start
            now = frame_clock.get_frame_time()
            start = now if start is None else start
            progress = min(1.0, (now - start) / (FADE_IN_MS * 1000.0))
            widget.set_opacity(1.0 - (1.0 - progress) ** 3)
            return progress < 1.0

        self.panel.add_tick_callback(tick)


def overlay_window(app: Gtk.Application, monitor: Any, keyboard: Any, width: int) -> Any:
    win = Gtk.Window(application=app, decorated=False, resizable=False)
    win.set_default_size(width + 2 * SHADOW_SIDE, -1)
    LayerShell.init_for_window(win)
    LayerShell.set_namespace(win, "plasmarec")
    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
    LayerShell.set_monitor(win, monitor)
    LayerShell.set_keyboard_mode(win, keyboard)
    return win


class App(Gtk.Application):
    def __init__(self, config: Config) -> None:
        super().__init__(application_id=APP_ID)
        self.config = config
        self.started = False
        self.picking = False
        self.pickers: list[tuple[Any, Any]] = []
        self.monitor: Any = None
        self.session: Session | None = None
        self.card: Card | None = None
        self.ghosted = False
        self.ghost_registration = 0

    # -- D-Bus: the KWin script confirms each window it hid ------------------

    def do_dbus_register(self, connection: Any, object_path: str) -> bool:
        info = Gio.DBusNodeInfo.new_for_xml(GHOST_XML).interfaces[0]

        def on_call(
            _conn: Any, _sender: str, _path: str, _iface: str, method: str, _params: Any, invocation: Any
        ) -> None:
            if method == "Confirm":
                self.ghosted = True
                GLib.idle_add(self.render)
            invocation.return_value(None)

        # register_object_with_closures2 is GLib 2.84+; older GLib only has
        # the variant PyGObject now marks deprecated.
        register = getattr(connection, "register_object_with_closures2", None) or connection.register_object
        self.ghost_registration = register(object_path, info, on_call, None, None)
        return bool(Gtk.Application.do_dbus_register(self, connection, object_path))

    def do_dbus_unregister(self, connection: Any, object_path: str) -> None:
        if self.ghost_registration:
            connection.unregister_object(self.ghost_registration)
            self.ghost_registration = 0
        Gtk.Application.do_dbus_unregister(self, connection, object_path)

    # -- activation: the first opens the picker, later ones toggle ------------

    def do_activate(self) -> None:
        if self.started:
            self.toggle()
            return
        self.started = True
        self.hold()
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, self.on_signal)
        ensure_ghost_script()
        self.open_picker()

    def toggle(self) -> None:
        if self.picking:
            self.quit()
        elif self.session is not None:
            self.session.toggle()
            self.render()

    def on_signal(self) -> bool:
        self.toggle()
        return GLib.SOURCE_CONTINUE

    # -- picker ----------------------------------------------------------------

    def open_picker(self) -> None:
        self.picking = True
        for n, monitor in enumerate(monitors(), 1):
            # One surface takes the keyboard for the number keys and Esc; the
            # others only need the pointer.
            keyboard = LayerShell.KeyboardMode.EXCLUSIVE if n == 1 else LayerShell.KeyboardMode.NONE
            win = overlay_window(self, monitor, keyboard, PICK_WIDTH)
            card = Card("Record this screen", chip=str(n))
            card.panel.add_css_class("pick")
            width, height = pixel_size(monitor)
            card.body.set_label(f"{monitor.get_connector()}  {width}x{height}")
            card.set_hint(f"click or press {n}    esc cancels")
            click = Gtk.GestureClick()
            click.connect("released", lambda *_, m=monitor: self.choose(m))
            card.panel.add_controller(click)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self.on_pick_key)
            win.add_controller(keys)
            win.set_child(card.panel)
            win.present()
            card.fade_in()
            self.pickers.append((monitor, win))

    def on_pick_key(self, _controller: Any, keyval: int, _keycode: int, _state: Any) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.quit()
            return True
        char = chr(Gdk.keyval_to_unicode(keyval) or 0)
        if char.isdigit() and 1 <= int(char) <= len(self.pickers):
            self.choose(self.pickers[int(char) - 1][0])
        return True

    def choose(self, monitor: Any) -> None:
        if not self.picking:
            return
        self.picking = False
        for _, win in self.pickers:
            win.destroy()
        self.pickers = []
        self.monitor = monitor
        geometry = monitor.get_geometry()
        self.session = Session(monitor.get_connector(), (geometry.x, geometry.y), self.config)
        self.show_card()
        GLib.timeout_add(GHOST_SETTLE_MS, self.start)

    def start(self) -> bool:
        assert self.session is not None
        self.session.start()  # no-op if the hotkey already cancelled it
        GLib.timeout_add(POLL_MS, self.poll)
        return GLib.SOURCE_REMOVE

    # -- status card -----------------------------------------------------------

    def show_card(self) -> None:
        win = overlay_window(self, self.monitor, LayerShell.KeyboardMode.NONE, CARD_WIDTH)
        LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
        LayerShell.set_margin(win, LayerShell.Edge.TOP, TOP_MARGIN - SHADOW_TOP)
        # Click-through: an empty input region lets the pointer reach
        # whatever is under the card.
        win.connect("map", lambda w: w.get_surface().set_input_region(cairo.Region()))
        self.card = Card("Starting")
        win.set_child(self.card.panel)
        self.render()
        # Only a confirmation for this surface counts, not the pickers'.
        self.ghosted = False
        win.present()
        self.card.fade_in()

    def phrase(self, verb: str) -> str:
        key = self.config.shortcut.lower()
        return f"{key} {verb}s" if key else f"run plasmarec again to {verb}"

    def render(self) -> bool:
        session, card = self.session, self.card
        if session is None or card is None:
            return GLib.SOURCE_REMOVE
        connector = session.connector
        mic = f"mic on, {session.calls.app}" if session.calls.app else "mic off"
        card.body.set_label(f"{connector}  system audio  {mic}")
        if session.phase is Phase.STARTING:
            card.title.set_label("Starting")
            card.set_hint(
                f"choose the {connector} entry in KDE's dialog"
                if session.dialog_expected
                else self.phrase("cancel")
            )
        elif session.phase is Phase.RECORDING:
            card.title.set_label("Recording")
            card.side.set_label(clock(session.elapsed()))
            if self.ghosted:
                card.set_hint(f"{self.phrase('stop')}    not in the video")
            else:
                card.set_hint(f"{self.phrase('stop')}    visible in the video", warn=True)
        elif session.phase in (Phase.SAVING, Phase.CANCELLING):
            card.title.set_label("Saving" if session.phase is Phase.SAVING else "Cancelling")
            card.set_hint("")
        return GLib.SOURCE_REMOVE

    def poll(self) -> bool:
        session = self.session
        assert session is not None
        session.poll()
        self.render()
        if not session.phase.finished:
            return GLib.SOURCE_CONTINUE
        outcome = session.outcome
        if session.phase is Phase.SAVED and outcome.path is not None:
            notify(
                "Recording saved",
                f"{outcome.path.name}\n{clock(outcome.duration)}, {outcome.size / 1e6:.0f} MB",
            )
        elif session.phase is Phase.FAILED:
            kept = f"\nThe raw take is at {outcome.kept}" if outcome.kept else ""
            notify("Recording failed", outcome.error + kept)
        self.quit()
        return GLib.SOURCE_REMOVE


def run(config: Config) -> int:
    return int(App(config).run(None))
