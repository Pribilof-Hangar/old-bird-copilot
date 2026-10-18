import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path

from landing_callouts.__main__ import replay
from landing_callouts.config import Config
from landing_callouts.demo import approach_samples
from landing_callouts.engine import CalloutEngine, Sample
from landing_callouts.recorder import FlightRecorder, read_recording


class TestRecorder(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.logs = []
        self.rec = FlightRecorder(self.dir, log=self.logs.append)

    def tearDown(self):
        self.rec.stop()

    def record_demo(self):
        self.rec.start()
        self.rec.event("settings: style=boeing vref=100 minimums=200 retard=0 heights=[...]")
        engine = CalloutEngine(Config(vref_kt=100, minimums_ft=200))
        for s in approach_samples():
            self.rec.sample(s, [c.text for c in engine.update(s)])
        self.rec.event("FLAG")
        self.rec.stop()
        return self.rec.path

    def test_round_trip_and_replay_matches(self):
        path = self.record_demo()
        rows = read_recording(path)
        said = [c for r in rows for c in r["callouts"]]
        self.assertIn("minimums", said)
        self.assertEqual(said[-1], "ten")
        self.assertIn("FLAG", [r["event"] for r in rows])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            replay(str(path), Config(), vref_set=False, mins_set=False)
        self.assertIn("0 rows differ", out.getvalue())

    def test_overspeed_flags_round_trip(self):
        self.rec.start()
        self.rec.sample(Sample(1.0, 2000, 180, -800, False, True, 40, 20, True, False), ["flap speed"])
        self.rec.sample(Sample(1.1, 1999, 180, -800, False, True, 40, 20, None, None), [])
        self.rec.stop()
        a, b = [r["sample"] for r in read_recording(self.rec.path) if "sample" in r]
        self.assertEqual((a.flap_overspeed, a.gear_overspeed, a.flaps_deg), (True, False, 20.0))
        self.assertEqual((b.flap_overspeed, b.gear_overspeed), (None, None))

    def test_weight_and_fuel_round_trip(self):
        self.rec.start()
        self.rec.sample(Sample(1.0, 900, 120, -600, False, True, weight_lb=92450.4, fuel_lb=8210.0), [])
        self.rec.sample(Sample(1.1, 899, 120, -600, False, True), [])
        self.rec.stop()
        a, b = [r["sample"] for r in read_recording(self.rec.path) if "sample" in r]
        self.assertEqual((a.weight_lb, a.fuel_lb), (92450.0, 8210.0))
        self.assertEqual((b.weight_lb, b.fuel_lb), (None, None))

    def test_slow_rate_in_cruise(self):
        self.rec.start()
        for i in range(100):  # 10 s of cruise at 10 Hz
            self.rec.sample(Sample(i * 0.1, 9000, 200, 0, False, False, 40), [])
        self.rec.stop()
        self.assertEqual(len([r for r in read_recording(self.rec.path) if "sample" in r]), 10)

    def test_keeps_newest_only(self):
        for i in range(5):
            (self.dir / f"flight-2000010{i}-000000.csv").write_text("x")
        rec = FlightRecorder(self.dir, keep=3, log=self.logs.append)
        rec.start()
        rec.stop()
        self.assertEqual(len(list(self.dir.glob("flight-*.csv"))), 3)
        self.assertTrue(rec.path.exists())

    def test_inactive_recorder_ignores_writes(self):
        self.rec.sample(Sample(time.monotonic(), 100, 100, -500, False, True), ["one hundred"])
        self.rec.event("FLAG")
        self.assertFalse(self.rec.active)
        self.assertEqual(list(self.dir.glob("*.csv")), [])


if __name__ == "__main__":
    unittest.main()
