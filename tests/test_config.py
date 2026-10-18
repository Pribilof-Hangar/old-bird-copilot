import contextlib
import dataclasses
import io
import unittest
from pathlib import Path

from landing_callouts import config

ROOT = Path(__file__).resolve().parent.parent


class TestConfig(unittest.TestCase):
    def test_style_of(self):
        self.assertEqual(config.style_of(config.Config().heights, 0), "boeing")
        self.assertEqual(config.style_of(config.STYLE_PRESETS["airbus"]["heights"], 10), "airbus")
        self.assertEqual(config.style_of([1000, 500, 100], 0), "custom")
        with self.assertRaisesRegex(config.ConfigError, "callout_style"):
            config.from_dict({"callout_style": "embraer"})

    def test_unknown_key_warns(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = config.from_dict({"heigths": [1000], "vref_kt": 110})
        self.assertIn("heigths", err.getvalue())
        self.assertEqual(cfg.vref_kt, 110)

    def test_retired_setting_is_ignored_quietly(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            config.from_dict({"auto_flaps": True, "aircraft": "dc6"})
        self.assertEqual(err.getvalue(), "")

    def test_bad_values_give_clear_errors(self):
        with self.assertRaisesRegex(config.ConfigError, "rate"):
            config.from_dict({"rate": 20})
        with self.assertRaisesRegex(config.ConfigError, "true or false"):
            config.from_dict({"require_gear_down": "yes"})
        with self.assertRaisesRegex(config.ConfigError, "whole numbers"):
            config.from_dict({"heights": [1000, "five"]})

    def test_texts_override(self):
        cfg = config.from_dict({"texts": {"retard": "idle", "50": "fifty feet"}})
        self.assertEqual(cfg.text("retard"), "idle")
        self.assertEqual(cfg.text("50", "fifty"), "fifty feet")
        self.assertEqual(cfg.text("sink_rate"), "sink rate")

    def test_save_round_trip(self):
        import tempfile
        import tomllib
        cfg = config.from_dict({"vref_kt": 112, "voice": 'Zira "quoted"', "agl_offset_ft": -2.5,
                                "rollout_callouts_kt": [80, 60], "texts": {"50": "fifty feet", "retard": "idle"}})
        again = config.from_dict(tomllib.loads(config.dump(cfg)))
        for f in dataclasses.fields(config.Config):
            if f.name != "base_dir":
                self.assertEqual(getattr(again, f.name), getattr(cfg, f.name), f.name)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            config.save(cfg, path)
            self.assertEqual(config.load(path).vref_kt, 112)

    def test_overrides(self):
        cfg = config.with_overrides(config.Config(), vref_kt=112, minimums_ft=None)
        self.assertEqual(cfg.vref_kt, 112)
        self.assertEqual(cfg.minimums_ft, 0)


if __name__ == "__main__":
    unittest.main()
