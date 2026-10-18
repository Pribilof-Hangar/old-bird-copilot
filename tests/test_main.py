import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from landing_callouts import __main__ as main
from landing_callouts import config


def args(**kw):
    return SimpleNamespace(**{"config": None, "vref": None, "minimums": None, "landing": None, **kw})


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.toml"

    def load(self):
        warnings = []
        with mock.patch.object(config, "find_config", lambda: self.path if self.path.exists() else None), \
                mock.patch.object(config, "default_config_path", lambda: self.path):
            cfg, path = main.load_config(args(), warnings)
        return cfg, path, warnings

    def test_first_start_creates_settings_quietly(self):
        cfg, path, warnings = self.load()
        self.assertEqual(warnings, [])
        self.assertTrue(self.path.is_file())
        self.assertEqual(cfg.base_dir, self.dir)

    def test_damaged_settings_are_kept_and_defaults_used(self):
        self.path.write_text("this is [not toml", encoding="utf-8")
        cfg, path, warnings = self.load()
        self.assertEqual(len(warnings), 1)
        self.assertIn("couldn't be read", warnings[0])
        backups = list(self.dir.glob("config.toml.damaged-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "this is [not toml")
        self.assertEqual(cfg.vref_kt, config.Config().vref_kt)
        self.assertTrue(config.load(self.path))      # a fresh, valid file is back

    def test_binary_garbage_is_handled_too(self):
        self.path.write_bytes(b"\xff\xfe\x00garbage\x9d")
        cfg, path, warnings = self.load()
        self.assertIn("couldn't be read", warnings[0])

    def test_normal_settings_load(self):
        config.save(config.Config(vref_kt=99), self.path)
        cfg, path, warnings = self.load()
        self.assertEqual((cfg.vref_kt, warnings), (99, []))


class TestDataFolder(unittest.TestCase):
    def test_per_user_folder(self):
        with mock.patch.dict("os.environ", {"APPDATA": r"C:\Users\Someone\AppData\Roaming"}):
            self.assertEqual(config.user_data_dir(), Path(r"C:\Users\Someone\AppData\Roaming\Old Bird Copilot"))

    def test_installed_copy_uses_the_per_user_folder(self):
        exe_dir = Path(tempfile.mkdtemp())
        with mock.patch.object(config.sys, "frozen", True, create=True), \
                mock.patch.object(config.sys, "executable", str(exe_dir / "OldBirdCopilot.exe")):
            self.assertEqual(config.default_config_path(), config.user_data_dir() / "config.toml")
            (exe_dir / "config.toml").write_text("", encoding="utf-8")       # a portable (zip) copy
            self.assertEqual(config.find_config(), exe_dir / "config.toml")


if __name__ == "__main__":
    unittest.main()
