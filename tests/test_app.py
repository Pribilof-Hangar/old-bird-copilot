import time
import unittest
from pathlib import Path

from landing_callouts.app import NO_SIM, Controller
from landing_callouts.config import Config


def drain(ctl):
    out = []
    while not ctl.events.empty():
        out.append(ctl.events.get_nowait())
    return out


class TestController(unittest.TestCase):
    def make(self, **cfg):
        ctl = Controller(Config(**cfg), Path("unused.toml"), use_sim=False, print_only=True)
        ctl.start()
        self.addCleanup(ctl.shutdown)
        return ctl

    def run_demo(self, ctl):
        ctl.start_demo(speed=200)
        deadline = time.monotonic() + 10
        while ctl.demo_running and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(ctl.demo_running)
        return drain(ctl)

    def test_demo_through_controller(self):
        ctl = self.make()
        self.assertEqual(ctl.status, NO_SIM)
        events = self.run_demo(ctl)
        callouts = [p for k, p in events if k == "callout"]
        self.assertEqual(callouts[0], "one thousand, speed one zero eight")
        self.assertEqual(callouts[-1], "ten")   # Boeing default: no "retard"
        self.assertEqual(len([k for k, _ in events if k == "touchdown"]), 1)
        self.assertIn(("demo_done", None), events)

    def test_flight_settings_apply(self):
        ctl = self.make()
        ctl.set_flight(vref_kt=0, minimums_ft=200)
        callouts = [p for k, p in self.run_demo(ctl) if k == "callout"]
        self.assertIn("minimums", callouts)
        self.assertIn("approaching minimums", callouts)
        self.assertNotIn("two hundred", callouts)

    def test_suggested_vref_from_live_weight(self):
        from landing_callouts.engine import CalloutEngine, Sample
        ctl = self.make()
        engine = CalloutEngine(ctl.flight_config())
        sample = Sample(0.0, 900, 115, -600, False, True, weight_lb=84_000, fuel_lb=6_000)
        ctl.aircraft_title = "PMDG DC-6A"
        ctl._process(engine, sample, record=False)
        self.assertEqual((ctl.suggestion.full, ctl.suggestion.flaps40), (103, 109))
        ctl.aircraft_title = "Beechcraft King Air 350i"
        ctl._process(engine, Sample(0.1, 899, 115, -600, False, True, weight_lb=14_000), record=False)
        self.assertIsNone(ctl.suggestion)

    def test_takeoff_speeds_only_on_the_ground(self):
        from landing_callouts.engine import CalloutEngine, Sample
        ctl = self.make()
        ctl.aircraft_title = "PMDG DC-6A"
        engine = CalloutEngine(ctl.flight_config())
        ctl._process(engine, Sample(0.0, 0, 0, 0, True, True, weight_lb=90_000), record=False)
        self.assertIsNotNone(ctl.takeoff_suggestion)                     # parked: shown
        ctl._process(engine, Sample(0.1, 3000, 160, 800, False, False, weight_lb=89_500), record=False)
        self.assertIsNone(ctl.takeoff_suggestion)                        # climbing: hidden
        self.assertIsNotNone(ctl.suggestion)                             # landing Vref still shown
        ctl._process(engine, Sample(0.2, 0, 20, 0, True, True, weight_lb=85_000), record=False)
        self.assertIsNotNone(ctl.takeoff_suggestion)                     # landed: back for the next leg

    def test_missed_weight_reading_keeps_the_suggestion(self):
        from landing_callouts.engine import CalloutEngine, Sample
        ctl = self.make()
        ctl.aircraft_title = "PMDG DC-6A"
        engine = CalloutEngine(ctl.flight_config())
        ctl._process(engine, Sample(0.0, 800, 114, -650, False, True, weight_lb=77_700), record=False)
        before = ctl.suggestion
        ctl._process(engine, Sample(0.1, 790, 114, -650, False, True, weight_lb=None), record=False)
        self.assertEqual(ctl.suggestion, before)

    def test_destination(self):
        ctl = self.make()
        self.assertEqual(ctl.set_destination("pasn").name, "St Paul Island Airport")
        self.assertEqual(ctl.descent.destination.code, "PASN")
        self.assertIsNone(ctl.set_destination("NOPE1"))
        self.assertIsNone(ctl.descent.destination)
        self.assertIsNone(ctl.set_destination(""))

    def test_descent_rate_dc6_recommends_1000(self):
        ctl = self.make(descent_rate_fpm=800)
        self.assertEqual(ctl.descent_rate, 800)          # Universal: the config's rate
        ctl.aircraft_title = "PMDG DC-6A"
        self.assertEqual(ctl.descent_rate, 1000)         # DC-6 profile
        ctl.set_descent_rate(500)                        # slider
        self.assertEqual(ctl.descent_rate, 500)
        ctl.set_descent_rate(1000)                       # back on the recommendation
        self.assertIsNone(ctl.descent_rate_override)

    def test_stop_demo(self):
        ctl = self.make()
        ctl.start_demo(speed=1)
        time.sleep(0.2)
        ctl.stop_demo()
        deadline = time.monotonic() + 2
        while ctl.demo_running and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(ctl.demo_running)


if __name__ == "__main__":
    unittest.main()
