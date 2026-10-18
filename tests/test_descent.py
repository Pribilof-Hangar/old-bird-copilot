import unittest

from landing_callouts import airports
from landing_callouts.airports import Airport
from landing_callouts.config import Config
from landing_callouts.descent import DescentPlanner, descent_distance, readouts
from landing_callouts.engine import Sample

FIELD = Airport("TEST", "Test Field", 57.0, -170.0, 63)   # arrival altitude 63 + 1500 = 1563 ft


class Leg:
    """Fly straight south toward FIELD, one sample a second."""

    def __init__(self, cfg: Config | None = None, dist_nm=90.0, alt=10_000.0, gs=240.0):
        self.planner = DescentPlanner(cfg or Config(descent_rate_fpm=700))
        self.planner.set_destination(FIELD)
        self.dist, self.alt, self.gs, self.t = dist_nm, alt, gs, 0.0
        self.said: list[tuple[float, str]] = []   # (distance, text)

    def fly(self, seconds: float, vs: float = 0.0, until_nm: float | None = None):
        for _ in range(int(seconds)):
            if until_nm is not None and self.dist <= until_nm:
                break
            self.t += 1
            self.dist -= self.gs / 3600
            self.alt += vs / 60
            s = Sample(self.t, self.alt - 63, 200, vs, False, False, lat=FIELD.lat + self.dist / 60,
                       lon=FIELD.lon, gs_kt=self.gs, alt_msl_ft=self.alt)
            self.said += [(self.dist, c.text) for c in self.planner.update(s)]
        return self

    def texts(self):
        return [t for _, t in self.said]


class TestTopOfDescent(unittest.TestCase):
    def test_called_once_at_the_right_distance(self):
        # 8,437 ft to lose at 700 fpm and 240 kt = 48.2 nm, plus 5 nm margin: start down 53 nm out
        leg = Leg().fly(3600, until_nm=40)
        self.assertEqual(leg.said[1][1], "top of descent")
        self.assertAlmostEqual(leg.said[1][0], 53.2, delta=0.2)
        self.assertEqual(leg.texts().count("top of descent"), 1)

    def test_slower_rate_starts_further_out(self):
        leg = Leg(Config(descent_rate_fpm=500)).fly(3600, until_nm=60)
        self.assertAlmostEqual(leg.said[1][0], 72.5, delta=0.2)   # 8,437 / 500 * 4 + 5

    def test_following_the_path_is_quiet_after_the_call(self):
        leg = Leg().fly(3600, until_nm=53.2)
        leg.fly(3600, vs=-700, until_nm=6)
        self.assertEqual(leg.texts(), ["request descent", "top of descent"])

    def test_late_descent_gets_high_on_profile(self):
        leg = Leg().fly(3600, until_nm=40)          # ignored the top of descent
        self.assertEqual(leg.texts(), ["request descent", "top of descent", "high on profile"])
        self.assertAlmostEqual(leg.said[2][0], 47.5, delta=0.3)   # ~1000 ft high: 5.7 nm late
        leg.fly(3600, vs=-700, until_nm=20)         # still high while descending: not repeated
        self.assertEqual(leg.texts().count("high on profile"), 1)

    def test_no_calls_climbing_out_on_a_short_hop(self):
        leg = Leg(dist_nm=30, alt=600).fly(300, vs=1000)
        self.assertEqual(leg.texts(), [])

    def test_already_past_the_top_of_descent_when_set(self):
        leg = Leg(dist_nm=30, alt=9000).fly(60)    # level, high, never cruised before it: no false "top of descent"
        self.assertNotIn("top of descent", leg.texts())

    def test_calls_can_be_off_but_info_stays(self):
        leg = Leg(Config(descent_calls=False, descent_rate_fpm=700)).fly(3600, until_nm=40)
        self.assertEqual(leg.texts(), [])
        self.assertIsNotNone(leg.planner.info.tod_nm)

    def test_plans_with_the_altimeter_reading(self):
        # altimeter reads 9,000 (pressure set) while the true altitude is 10,000: the plan follows the gauge
        p = DescentPlanner(Config(descent_rate_fpm=700))
        p.set_destination(FIELD)
        s = Sample(0, 9000, 200, 0, False, False, lat=FIELD.lat + 40 / 60, lon=FIELD.lon, gs_kt=240,
                   alt_msl_ft=10_000, alt_ind_ft=9_000)
        p.update(s)
        self.assertAlmostEqual(p.info.dev_ft, 9_000 - p.info.path_ft)
        self.assertEqual(readouts(p.info, s.altitude_ft, 240, 5).path_sub, "you 9,000 ft")

    def test_no_destination(self):
        p = DescentPlanner(Config())
        s = Sample(0, 9000, 200, 0, False, False, lat=58.0, lon=-170.0, gs_kt=240, alt_msl_ft=9000)
        self.assertEqual(p.update(s), [])
        self.assertIsNone(p.info)


class TestEarlyStart(unittest.TestCase):
    """2026-10-10: started down ~1 nm before the top of descent and flew the path; on a path the top of
    descent stays just ahead, so "top of descent" never came and the panel kept saying "in 1 nm"."""

    def leg(self):
        leg = Leg(Config(descent_rate_fpm=1000), dist_nm=90)
        leg.planner.dc6 = True
        return leg

    def test_starting_a_little_early_still_gets_the_call(self):
        leg = self.leg().fly(3600, until_nm=58.7)          # top of descent at 57.7: start 1 nm early
        leg.fly(3600, vs=-1000, until_nm=40)
        self.assertIn("top of descent", leg.texts())
        r = readouts(leg.planner.info, leg.alt, leg.gs, leg.planner.margin_nm)
        self.assertEqual((r.start_down, r.diff), ("started", "on path"))
        self.assertNotEqual(r.path, "cruise")

    def test_starting_much_earlier_is_quiet_but_shows_the_path(self):
        leg = self.leg().fly(3600, until_nm=70)            # an early clearance: 12 nm before
        leg.fly(3600, vs=-1000, until_nm=50)
        self.assertNotIn("top of descent", leg.texts())
        r = readouts(leg.planner.info, leg.alt, leg.gs, leg.planner.margin_nm)
        self.assertEqual(r.start_down, "started")
        self.assertTrue(r.diff.endswith("low"))

    def test_a_turbulence_dip_in_cruise_is_not_a_descent(self):
        leg = self.leg().fly(3600, until_nm=70)
        leg.fly(5, vs=-600)                                # a few seconds of sink
        leg.fly(3600, until_nm=50)                         # level again, through the top of descent
        self.assertIn("top of descent", leg.texts())


class TestOnPathDisplay(unittest.TestCase):
    def test_descending_exactly_on_the_path_says_started_not_now(self):
        # 2026-10-17 screenshot: on the path the top of descent sits right here, which showed an amber "NOW"
        leg = Leg(Config(descent_rate_fpm=1000), dist_nm=90)
        leg.planner.dc6 = True
        leg.fly(3600, until_nm=57.7)                      # exactly at the top of descent
        leg.fly(300, vs=-1000)                            # five minutes down the path
        r = readouts(leg.planner.info, leg.alt, leg.gs, leg.planner.margin_nm)
        self.assertEqual((r.start_down, r.diff), ("started", "on path"))
        self.assertFalse(r.alert)


class TestRequestDescent(unittest.TestCase):
    def test_two_minutes_before_the_top_of_descent(self):
        leg = Leg().fly(3600, until_nm=40)
        self.assertEqual(leg.said[0][1], "request descent")
        self.assertAlmostEqual(leg.said[0][0], 53.2 + 8, delta=0.2)   # 2 min at 240 kt = 8 nm earlier
        self.assertEqual(leg.texts().count("request descent"), 1)

    def test_can_be_off(self):
        leg = Leg(Config(descent_rate_fpm=700, request_descent_call=False)).fly(3600, until_nm=40)
        self.assertNotIn("request descent", leg.texts())
        self.assertIn("top of descent", leg.texts())

    def test_not_when_already_past_it(self):
        leg = Leg(dist_nm=30, alt=9000).fly(60)
        self.assertNotIn("request descent", leg.texts())


class TestDc6Profile(unittest.TestCase):
    def leg(self, **kw):
        leg = Leg(**kw)
        leg.planner.dc6 = True
        leg.planner.rate_fpm = 1000
        return leg

    def test_top_of_descent_for_a_level_off_30_nm_out(self):
        # 10,000 to 3,063 ft (field 63 + 3,000) at 1000 fpm and 240 kt = 27.7 nm, plus 30 nm level segment
        leg = self.leg().fly(3600, until_nm=50)
        self.assertEqual(leg.said[1][1], "top of descent")
        self.assertAlmostEqual(leg.said[1][0], 57.7, delta=0.2)

    def test_level_off_reminder_on_the_way_down(self):
        leg = self.leg().fly(3600, until_nm=57.7)
        leg.fly(3600, vs=-1000, until_nm=31)             # down the path to 3,063 ft
        texts = leg.texts()
        self.assertEqual(texts, ["request descent", "top of descent", "five hundred to level off"])
        dist_at_call = leg.said[2][0]
        self.assertAlmostEqual(dist_at_call, 32, delta=0.3)   # 500 ft above the level-off, 2 nm before it

    def test_no_high_call_on_the_level_segment(self):
        leg = self.leg(dist_nm=29, alt=4_500).fly(120)   # inside 30 nm, still above the level-off
        self.assertNotIn("high on profile", leg.texts())

    def test_universal_has_no_level_off_call(self):
        leg = Leg().fly(3600, until_nm=53.2)
        leg.fly(3600, vs=-700, until_nm=6)
        self.assertNotIn("five hundred to level off", leg.texts())

    def test_readouts_show_the_level_off_plan(self):
        leg = self.leg(dist_nm=90).fly(1)
        self.assertEqual(leg.planner.info.arrive_ft, 3_063)
        r = readouts(leg.planner.info, leg.alt, leg.gs, leg.planner.margin_nm)
        self.assertEqual(r.start_down, "in 32 nm")


class TestCalculator(unittest.TestCase):
    def test_fifteen_thousand_at_500_fpm(self):
        nm, minutes = descent_distance(15_000, 1_500, 240, 500)
        self.assertAlmostEqual(nm, 108.0)        # 27 min at 4 nm a minute
        self.assertAlmostEqual(minutes, 27.0)

    def test_margin_and_nothing_to_lose(self):
        self.assertEqual(descent_distance(1_000, 1_500, 240, 500, margin_nm=5), (5.0, 0.0))

    def test_planner_uses_the_app_rate(self):
        leg = Leg(Config(descent_rate_fpm=1000))
        leg.planner.rate_fpm = 500                     # DC-6 mode / calculator slider
        leg.fly(3600, until_nm=60)
        self.assertAlmostEqual(leg.said[1][0], 72.5, delta=0.2)


class TestReadouts(unittest.TestCase):
    def panel(self, **kw):
        leg = Leg(**kw)
        leg.fly(1)
        return readouts(leg.planner.info, leg.alt, leg.gs, 5)

    def test_cruise_before_top_of_descent(self):
        r = self.panel(dist_nm=90)
        self.assertEqual((r.distance, r.start_down, r.start_sub, r.path, r.diff),
                         ("90 nm", "in 37 nm", "about 9 min", "cruise", "—"))
        self.assertEqual(r.path_sub, "you 10,000 ft")
        self.assertFalse(r.alert)

    def test_high_after_top_of_descent(self):
        r = self.panel(dist_nm=40)                          # 10,000 ft, path wants 7,700
        self.assertEqual((r.start_down, r.start_sub, r.path, r.diff), ("passed", "13 nm ago", "7,700 ft", "2,300 high"))
        self.assertEqual(r.diff_sub, "needs 970 fpm")       # 8,437 ft over 35 nm at 240 kt (8.75 min)
        self.assertTrue(r.alert)

    def test_on_path(self):
        r = self.panel(dist_nm=40, alt=7_800)
        self.assertEqual(r.diff, "on path")
        self.assertFalse(r.alert)

    def test_time_to_start_down(self):
        r = self.panel(dist_nm=53.3)
        self.assertEqual(r.start_down, "NOW")
        self.assertTrue(r.alert)

    def test_below_the_path_waits(self):
        r = self.panel(dist_nm=40, alt=3_000)
        self.assertEqual((r.start_down, r.path), ("in 27 nm", "cruise"))

    def test_at_pattern_altitude(self):
        r = self.panel(dist_nm=8, alt=1600)
        self.assertEqual((r.start_down, r.path, r.path_sub), ("done", "1,563 ft", "arrival altitude"))

    def test_no_position_yet(self):
        self.assertEqual(readouts(None, None, None, 5).distance, "—")


class TestAirports(unittest.TestCase):
    def test_lookup(self):
        ap = airports.find(" pasn ")
        self.assertEqual((ap.code, ap.elev_ft), ("PASN", 63))
        self.assertIsNone(airports.find("XXXXX"))
        self.assertIsNone(airports.find(""))

    def test_distance(self):
        self.assertAlmostEqual(airports.distance_nm(57, -170, 58, -170), 60.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
