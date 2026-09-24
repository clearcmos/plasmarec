from __future__ import annotations

import configparser
import io
import runpy
import sys
import tomllib
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

from plasmarec import APP_ID, OBJECT_PATH, cli, config

ROOT = Path(__file__).resolve().parents[1]


class Main(unittest.TestCase):
    def test_version(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            cli.main(["--version"])
        self.assertEqual(exit_.exception.code, 0)
        self.assertRegex(out.getvalue(), r"^plasmarec \d+\.\d+\.\d+")

    def test_python_dash_m_runs_main(self) -> None:
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["plasmarec", "--version"]),
            redirect_stdout(out),
            self.assertRaises(SystemExit),
        ):
            runpy.run_module("plasmarec", run_name="__main__")
        self.assertIn("plasmarec", out.getvalue())

    def test_a_running_instance_is_toggled_without_loading_gtk(self) -> None:
        sys.modules.pop("plasmarec.ui", None)
        with mock.patch.object(cli, "activate_running", return_value=True):
            self.assertEqual(cli.main([]), 0)
        self.assertNotIn("plasmarec.ui", sys.modules)

    def test_otherwise_starts_the_app_with_the_config(self) -> None:
        fake_ui = types.ModuleType("plasmarec.ui")
        fake_ui.run = mock.Mock(return_value=0)  # type: ignore[attr-defined]
        with (
            mock.patch.object(cli, "activate_running", return_value=False),
            mock.patch.object(cli, "desktop_dir", return_value=Path("/d")),
            mock.patch.object(config, "config_path", return_value=Path("/nonexistent.toml")),
            mock.patch.dict(sys.modules, {"plasmarec.ui": fake_ui}),
        ):
            self.assertEqual(cli.main([]), 0)
        (settings,), _ = fake_ui.run.call_args
        self.assertEqual(settings.output_dir, Path("/d"))

    def test_a_bad_config_exits_2_with_the_reason(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(cli, "activate_running", return_value=False),
            mock.patch.object(cli, "desktop_dir", return_value=Path("/d")),
            mock.patch.object(config, "load", side_effect=config.ConfigError("unknown key(s): fsp")),
            redirect_stderr(err),
        ):
            self.assertEqual(cli.main([]), 2)
        self.assertIn("unknown key(s): fsp", err.getvalue())


class ActivateRunning(unittest.TestCase):
    def test_no_session_bus_means_not_running(self) -> None:
        with mock.patch.dict("os.environ", {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}):
            self.assertFalse(cli.activate_running())

    def test_activates_the_owner_of_the_app_id(self) -> None:
        from gi.repository import Gio

        bus = mock.Mock()
        with mock.patch.object(Gio, "bus_get_sync", return_value=bus):
            self.assertTrue(cli.activate_running())
        owner, activate = (c.args for c in bus.call_sync.call_args_list)
        self.assertEqual(owner[3], "GetNameOwner")
        self.assertEqual(owner[4].unpack(), (APP_ID,))
        self.assertEqual(activate[:4], (APP_ID, OBJECT_PATH, "org.freedesktop.Application", "Activate"))

    def test_owner_gone_between_calls_means_not_running(self) -> None:
        from gi.repository import Gio, GLib

        bus = mock.Mock()
        bus.call_sync.side_effect = [None, GLib.Error("name has no owner")]
        with mock.patch.object(Gio, "bus_get_sync", return_value=bus):
            self.assertFalse(cli.activate_running())

    def test_desktop_dir_is_the_xdg_desktop(self) -> None:
        self.assertTrue(cli.desktop_dir().is_absolute())


class DesktopEntry(unittest.TestCase):
    def test_launches_the_installed_command(self) -> None:
        entry = configparser.ConfigParser(interpolation=None)
        entry.read(ROOT / "data" / f"{APP_ID}.desktop")
        scripts: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        self.assertEqual(entry["Desktop Entry"]["Exec"], "plasmarec")
        self.assertIn("plasmarec", scripts)


if __name__ == "__main__":
    unittest.main()
