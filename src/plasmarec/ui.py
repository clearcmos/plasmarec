"""GTK4 layer-shell surfaces: the monitor picker and the status card.

Visual language: Basalt (dark floating panels, monospace, one blue accent).
The recording lifecycle lives in session.py; this module draws it and turns
input into calls on it. It needs a Wayland compositor with wlr-layer-shell,
so it is exempt from the coverage gate (see pyproject.toml).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from ctypes import CDLL
from dataclasses import replace
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

from plasmarec import APP_ID, GHOST_INTERFACE, KWIN_SCRIPT, quality  # noqa: E402
from plasmarec.config import Config  # noqa: E402
from plasmarec.session import Phase, Session  # noqa: E402

PICK_WIDTH = 400
SETTINGS_WIDTH = 440
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
# Keyboard mode off, then back on this long later: long enough for the off
# state to reach KWin in its own commit, so it sees the change.
REGRAB_MS = 50

GHOST_XML = f"""<node><interface name="{GHOST_INTERFACE}">
<method name="Confirm"/>
<method name="Pointer"><arg name="output" type="s" direction="in"/></method>
</interface></node>"""
# Longest wait for KWin to say where the cursor is before E opens the quality
# panel on the start output instead.
POINTER_WAIT_MS = 300
# Loaded once per E press: reports the output under the cursor. A layer
# surface cannot see the pointer outside itself, and KWin's activeOutputName
# follows the keyboard, which one picker holds, so only KWin's own
# cursorPos says which screen the user is on.
POINTER_JS = """var o = workspace.screenAt(workspace.cursorPos);
callDBus("%s", "%s", "%s", "Pointer", o ? o.name : "");
"""

CSS = """
window { background: transparent; }
.panel {
  background-color: #1f1f23;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 17px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.55);
}
.panel.pick { transition: background-color 120ms; }
.panel.pick:hover { background-color: #25252a; }
.title {
  color: #e6e6e6;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 18px;
  font-weight: bold;
}
.meta {
  color: #86868d;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 14px;
}
.chip {
  background-color: #29292f;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 7px;
  color: #5c9ae6;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 14px;
  padding: 2px 11px;
}
.row {
  border-radius: 7px;
  padding: 10px 11px;
  transition: background-color 120ms;
}
.row:hover { background-color: #25252a; }
.secondary {
  color: #a0a0a0;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 16px;
}
.rule { background-color: rgba(255, 255, 255, 0.08); min-height: 1px; }
.body {
  color: #e6e6e6;
  font-family: "Hack", "JetBrains Mono", monospace;
  font-size: 16px;
}
.hint {
  color: #86868d;
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


def active_output() -> str | None:
    """The connector KWin treats as the active output, or None off KWin.

    Asked before plasmarec maps anything: once the first picker holds the
    keyboard, KWin's active output is that picker's monitor, not the one the
    user was working on."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        reply = bus.call_sync(
            "org.kde.KWin", "/KWin", "org.kde.KWin", "activeOutputName",
            None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 1000,
        )  # fmt: skip
    except GLib.Error:
        return None
    name = reply.unpack()[0]
    return str(name) if name else None


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


def overlay_window(app: Gtk.Application, monitor: Any | None, keyboard: Any, width: int) -> Any:
    """A layer surface on `monitor`, or with None on the active output."""
    win = Gtk.Window(application=app, decorated=False, resizable=False)
    win.set_default_size(width + 2 * SHADOW_SIDE, -1)
    LayerShell.init_for_window(win)
    # KWin types a layer surface by its namespace; "utility" is one it leaves
    # unanimated, so the cards' own Basalt motion is all that runs. Any other
    # name makes the surface a Normal window and adds KWin's Scale zoom.
    LayerShell.set_namespace(win, "utility")
    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
    if monitor is not None:
        LayerShell.set_monitor(win, monitor)
    LayerShell.set_keyboard_mode(win, keyboard)
    return win


DISMISS_PIDFILE = os.path.join(GLib.get_user_runtime_dir(), "basalt", "plasmarec.pid")


def register_dismiss() -> None:
    """Let Esc pressed anywhere reach the picker, via SIGUSR1 (on_dismiss)."""
    os.makedirs(os.path.dirname(DISMISS_PIDFILE), exist_ok=True)
    with open(DISMISS_PIDFILE, "w") as f:
        f.write(f"{os.getpid()}\n")


def unregister_dismiss() -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(DISMISS_PIDFILE)


class App(Gtk.Application):
    def __init__(self, config: Config) -> None:
        super().__init__(application_id=APP_ID)
        self.config = replace(config, quality=quality.load())
        self.started = False
        self.picking = False
        self.pickers: list[tuple[Any, Any]] = []
        # The quality panel: its window, one (row, chip) per preset, and the
        # preset it currently marks. Open only while picking.
        self.settings: Any = None
        self.setting_rows: list[tuple[Any, Any]] = []
        self.setting = 0
        self.monitor: Any = None
        # Connector of the output the user was on when plasmarec started; the
        # quality panel opens there.
        self.home: str | None = None
        # E asks KWin which output the cursor is on (see ask_pointer) and
        # waits for the answer: the pending script and the fallback timer.
        self.pointer_script: tuple[str, str] | None = None
        self.pointer_timer = 0
        self.session: Session | None = None
        self.card: Card | None = None
        self.ghosted = False
        self.ghost_registration = 0

    # -- D-Bus: the KWin script confirms each window it hid ------------------

    def do_dbus_register(self, connection: Any, object_path: str) -> bool:
        info = Gio.DBusNodeInfo.new_for_xml(GHOST_XML).interfaces[0]

        def on_call(
            _conn: Any, _sender: str, _path: str, _iface: str, method: str, params: Any, invocation: Any
        ) -> None:
            if method == "Confirm":
                self.ghosted = True
                GLib.idle_add(self.render)
            elif method == "Pointer":
                GLib.idle_add(self.pointer_answered, params.unpack()[0])
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
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.on_dismiss)
        ensure_ghost_script()
        self.home = active_output()
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

    def on_dismiss(self) -> bool:
        """SIGUSR1 from Basalt's Esc-from-anywhere hook cancels the picker.

        The hook is the author's `basalt-dismiss`, bound to Esc below the
        compositor. It signals every pid registered in the Basalt runtime
        directory, so this is registered only while the picker is up, and a
        running recording never hears it."""
        if self.picking:
            self.quit()
        return GLib.SOURCE_CONTINUE

    def do_shutdown(self) -> None:
        unregister_dismiss()
        self.drop_pointer_script()
        Gtk.Application.do_shutdown(self)

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
            chosen = quality.label(self.config.quality).lower()
            card.body.set_label(f"{monitor.get_connector()}  {width}x{height}  {chosen}")
            card.set_hint(f"click or press {n}    e quality    esc cancels")
            click = Gtk.GestureClick()
            click.connect("released", lambda *_, m=monitor: self.choose(m))
            card.panel.add_controller(click)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self.on_pick_key)
            win.add_controller(keys)
            if keyboard == LayerShell.KeyboardMode.EXCLUSIVE:
                win.connect("notify::is-active", self.on_picker_focus)
            win.set_child(card.panel)
            win.present()
            card.fade_in()
            self.pickers.append((monitor, win))
        register_dismiss()

    def on_picker_focus(self, win: Any, _pspec: Any) -> None:
        """Take the keyboard back whenever another window gets it.

        KWin activates an exclusive layer surface only when its keyboard mode
        changes, not for as long as it is mapped, so a window that opens or is
        clicked keeps the focus and Esc stops reaching the picker. Turning the
        mode off and on again makes KWin activate the picker once more."""
        if not self.picking or win.is_active():
            return
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)

        def regrab() -> bool:
            if self.picking:
                LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.EXCLUSIVE)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(REGRAB_MS, regrab)

    def on_pick_key(self, _controller: Any, keyval: int, _keycode: int, _state: Any) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.quit()
            return True
        if keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self.open_settings()
            return True
        char = chr(Gdk.keyval_to_unicode(keyval) or 0)
        if char.isdigit() and 1 <= int(char) <= len(self.pickers):
            self.choose(self.pickers[int(char) - 1][0])
        return True

    # -- quality settings --------------------------------------------------------

    def open_settings(self) -> None:
        """E on a picker: open the quality panel on the output under the cursor.

        KWin answers through the Pointer D-Bus method; without an answer in
        POINTER_WAIT_MS (not KWin, or scripting failed) it opens on the
        output the user was on at start."""
        if not self.picking or self.settings is not None or self.pointer_timer:
            return
        self.pointer_timer = GLib.timeout_add(POINTER_WAIT_MS, self.pointer_answered, "")
        if not self.ask_pointer():
            self.pointer_answered("")

    def ask_pointer(self) -> bool:
        """Load and run a one-shot KWin script that calls Pointer back.

        Each press gets a fresh file and plugin name: loadScript refuses a
        path it has already loaded, and ids are reused after unloadScript."""
        stamp = f"{os.getpid()}-{GLib.get_monotonic_time()}"
        path = os.path.join(GLib.get_user_runtime_dir(), f"plasmarec-pointer-{stamp}.js")
        name = os.path.basename(path)[:-3]
        object_path = self.get_dbus_object_path() or ""
        with open(path, "w") as f:
            f.write(POINTER_JS % (APP_ID, object_path, GHOST_INTERFACE))
        self.pointer_script = (path, name)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            reply = bus.call_sync(
                "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript",
                GLib.Variant("(ss)", (path, name)), GLib.VariantType("(i)"), Gio.DBusCallFlags.NONE, 1000,
            )  # fmt: skip
            script_id = reply.unpack()[0]
            if script_id < 0:
                return False
            bus.call_sync(
                "org.kde.KWin", f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run",
                None, None, Gio.DBusCallFlags.NONE, 1000,
            )  # fmt: skip
        except GLib.Error:
            return False
        return True

    def pointer_answered(self, connector: str) -> bool:
        """The cursor's output from KWin, or "" when it did not answer in time."""
        if not self.pointer_timer:
            return GLib.SOURCE_REMOVE  # already answered, or the timer fired first
        GLib.source_remove(self.pointer_timer)
        self.pointer_timer = 0
        self.drop_pointer_script()
        self.show_settings(connector or self.home)
        return GLib.SOURCE_REMOVE

    def drop_pointer_script(self) -> None:
        if self.pointer_script is None:
            return
        path, name = self.pointer_script
        self.pointer_script = None
        with contextlib.suppress(OSError):
            os.unlink(path)
        with contextlib.suppress(GLib.Error):
            Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
                "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript",
                GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE, 1000,
            )  # fmt: skip

    def show_settings(self, target: str | None) -> None:
        """Swap the pickers for one quality panel on the `target` output.

        Still picking as far as the hotkey and Esc are concerned: either one
        cancels the whole flow, as it does on the pickers."""
        if not self.picking or self.settings is not None:
            return
        for _, win in self.pickers:
            win.destroy()
        self.pickers = []
        home = next((m for m in monitors() if m.get_connector() == target), None)
        win = overlay_window(self, home, LayerShell.KeyboardMode.EXCLUSIVE, SETTINGS_WIDTH)
        win.connect("notify::is-active", self.on_picker_focus)
        card = Card("Recording quality")
        card.body.set_visible(False)
        card.set_hint("click saves    arrows or 1-4, then e    esc cancels")
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rows.set_margin_top(10)
        rows.set_margin_start(18)  # rows reach past the text column by their padding
        rows.set_margin_end(18)
        self.setting_rows = []
        for index, preset in enumerate(quality.PRESETS):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            row.add_css_class("row")
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            name = Gtk.Label(label=f"{index + 1}  {preset.label}", xalign=0.0)
            name.add_css_class("body")
            note = Gtk.Label(label=preset.note, xalign=0.0)
            note.add_css_class("secondary")
            text.append(name)
            text.append(note)
            chip = Gtk.Label(label="selected", valign=Gtk.Align.CENTER)
            chip.add_css_class("chip")
            row.append(text)
            row.append(chip)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_, i=index: self.pick_setting(i))
            row.add_controller(click)
            rows.append(row)
            self.setting_rows.append((row, chip))
        card.panel.insert_child_after(rows, card.body)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_settings_key)
        win.add_controller(keys)
        win.set_child(card.panel)
        self.settings = win
        self.mark(next(i for i, p in enumerate(quality.PRESETS) if p.key == self.config.quality))
        win.present()
        card.fade_in()

    def mark(self, index: int) -> None:
        self.setting = index % len(self.setting_rows)
        for i, (_, chip) in enumerate(self.setting_rows):
            chip.set_visible(i == self.setting)

    def pick_setting(self, index: int) -> None:
        """A click chooses and saves in one step, like the monitor cards."""
        self.mark(index)
        self.save_settings()

    def on_settings_key(self, _controller: Any, keyval: int, _keycode: int, _state: Any) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.quit()
        elif keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self.save_settings()
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_Tab, Gdk.KEY_j):
            self.mark(self.setting + 1)
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_k):
            self.mark(self.setting - 1)
        else:
            char = chr(Gdk.keyval_to_unicode(keyval) or 0)
            if char.isdigit() and 1 <= int(char) <= len(self.setting_rows):
                self.mark(int(char) - 1)
        return True

    def save_settings(self) -> None:
        key = quality.PRESETS[self.setting].key
        try:
            quality.save(key)
        except OSError as exc:
            notify("Quality not saved", str(exc))
        else:
            self.config = replace(self.config, quality=key)
        self.settings.destroy()
        self.settings = None
        self.setting_rows = []
        self.open_picker()

    def choose(self, monitor: Any) -> None:
        if not self.picking:
            return
        self.picking = False
        unregister_dismiss()
        if self.settings is not None:
            self.settings.destroy()
            self.settings = None
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
