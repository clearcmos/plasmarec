from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plasmarec import config

DESKTOP = Path("/home/u/Desktop")


class Parse(unittest.TestCase):
    def test_empty_file_gives_defaults(self) -> None:
        cfg = config.parse({}, DESKTOP)
        self.assertEqual(cfg.output_dir, DESKTOP)
        self.assertEqual((cfg.fps, cfg.audio_kbps, cfg.shortcut), (60, 160, ""))
        self.assertEqual(cfg.call_apps, config.DEFAULT_CALL_APPS)

    def test_values_override_defaults(self) -> None:
        cfg = config.parse(
            {"output_dir": "~/Videos", "fps": 30, "audio_kbps": 128, "shortcut": "Meta+F2"}, DESKTOP
        )
        self.assertEqual(cfg.output_dir, Path.home() / "Videos")
        self.assertEqual((cfg.fps, cfg.audio_kbps, cfg.shortcut), (30, 128, "Meta+F2"))

    def test_call_apps_replace_the_defaults_and_match_lowercase(self) -> None:
        cfg = config.parse({"call_apps": {"WebCord": "Discord"}}, DESKTOP)
        self.assertEqual(cfg.call_apps, {"webcord": "Discord"})

    def test_unknown_key_is_an_error_not_ignored(self) -> None:
        # A misspelt key would otherwise leave the setting at its default unnoticed.
        with self.assertRaisesRegex(config.ConfigError, "unknown key.*fsp"):
            config.parse({"fsp": 30}, DESKTOP)

    def test_bad_values_are_rejected(self) -> None:
        for data in (
            {"fps": True},
            {"fps": 0},
            {"fps": "60"},
            {"audio_kbps": 9000},
            {"output_dir": ""},
            {"output_dir": 3},
            {"shortcut": 2},
            {"call_apps": ["discord"]},
            {"call_apps": {"discord": ""}},
        ):
            with self.subTest(data=data), self.assertRaises(config.ConfigError):
                config.parse(data, DESKTOP)


class Load(unittest.TestCase):
    def test_missing_file_gives_defaults(self) -> None:
        cfg = config.load(DESKTOP, Path("/nonexistent/plasmarec/config.toml"))
        self.assertEqual(cfg.output_dir, DESKTOP)

    def test_reads_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('fps = 30\n[call_apps]\nzoom = "Zoom"\n')
            cfg = config.load(DESKTOP, path)
        self.assertEqual(cfg.fps, 30)
        self.assertEqual(cfg.call_apps, {"zoom": "Zoom"})

    def test_errors_name_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            for text in ("fps = [", "fps = 0"):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaisesRegex(config.ConfigError, str(path)):
                    config.load(DESKTOP, path)

    def test_default_path_follows_xdg_config_home(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": "/xdg"}):
            self.assertEqual(config.config_path(), Path("/xdg/plasmarec/config.toml"))


if __name__ == "__main__":
    unittest.main()
