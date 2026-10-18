import unittest

from landing_callouts.config import STYLE_PRESETS, Config
from landing_callouts.demo import approach_samples
from landing_callouts.engine import CalloutEngine, Sample, build_thresholds

DT = 0.1


def airbus(**kw) -> Config:
    """Airbus preset (has "retard"). Most mechanics tests use it."""
    return Config(**{"callout_style": "airbus", **STYLE_PRESETS["airbus"], **kw})


class Flight:
    """Drives an engine with samples and records every callout key."""

    def __init__(self, cfg: Config | None = None, agl=1500.0, ias=130.0, gear_down=True, throttle=None,
                 flaps=None):
        self.engine = CalloutEngine(cfg or airbus())
        self.t = 0.0
        self.agl = agl
        self.ias = ias
        self.vs = 0.0
        self.gear_down = gear_down
        self.throttle = throttle
        self.flaps = flaps
        self.flap_over = self.gear_over = None
        self.wind_dir = self.wind_kt = self.hdg = None
        self.on_ground = agl <= 0
        self.said: list[str] = []
        self.texts: list[str] = []
        self.events: list[str] = []
        self.feed()

    def feed(self, **changes):
        for k, v in changes.items():
            setattr(self, k, v)
        s = Sample(self.t, self.agl, self.ias, self.vs, self.on_ground, self.gear_down, self.throttle,
                   self.flaps, self.flap_over, self.gear_over,
                   wind_dir_deg=self.wind_dir, wind_kt=self.wind_kt, heading_deg=self.hdg)
        out = self.engine.update(s)
        self.said += [c.key for c in out]
        self.texts += [c.text for c in out]
        self.events += self.engine.events
        self.t += DT
        return out

    def fly_to(self, target: float, fpm: float = 700, vs: float | None = None):
        """Climb or descend to `target` at `fpm`, one sample per 0.1 s. `vs` overrides the reported
        vertical speed (e.g. 0 for level flight over rising terrain)."""
        step = fpm / 60 * DT
        self.vs = vs if vs is not None else (fpm if target > self.agl else -fpm)
        while abs(self.agl - target) > 1e-9:
            if target > self.agl:
                self.agl = min(target, self.agl + step)
            else:
                self.agl = max(target, self.agl - step)
            self.on_ground = self.agl <= 0 and self.vs <= 0
            if self.on_ground:
                self.agl = 0.0
            self.feed()
        return self

    def hold(self, seconds: float, **changes):
        for _ in range(round(seconds / DT)):
            self.feed(**changes)
        return self


ALL_HEIGHTS = ["1000", "500", "400", "300", "200", "100", "50", "40", "30", "20", "retard"]


class TestApproach(unittest.TestCase):
    def test_full_approach_calls_every_height_once_then_touchdown(self):
        f = Flight(airbus(announce_touchdown=True), throttle=0)
        f.fly_to(0, fpm=700)
        self.assertEqual(f.said, ALL_HEIGHTS + ["touchdown"])
        self.assertEqual(len([e for e in f.events if e.startswith("Touchdown")]), 1)

    def test_demo_approach(self):
        engine = CalloutEngine(airbus(rollout_callouts_kt=[80, 60]))
        said = []
        for s in approach_samples():
            said += [c.key for c in engine.update(s)]
        self.assertEqual([k for k in said if not k.startswith("flaps_")], ALL_HEIGHTS + ["rollout_80", "rollout_60"])
        self.assertEqual([k for k in said if k.startswith("flaps_")], ["flaps_40", "flaps_50"])
        self.assertIsNotNone(engine.last_touchdown)
        self.assertTrue(-200 < engine.last_touchdown.vs_fpm < -100)

    def test_speed_readout(self):
        f = Flight(airbus(stable_check=False), ias=112, throttle=0)
        f.fly_to(0)
        self.assertIn("one thousand, speed one one two", f.texts)
        self.assertIn("five hundred, speed one one two", f.texts)
        self.assertIn("four hundred", f.texts)

    def test_stable_check_replaces_the_500_speed_readout(self):
        f = Flight(ias=112, throttle=0)
        f.fly_to(0)
        self.assertIn("one thousand, speed one one two", f.texts)
        self.assertIn("five hundred, stable", f.texts)

    def test_climb_with_gear_up_says_nothing(self):
        f = Flight(agl=0, gear_down=False)
        f.fly_to(3000, fpm=1500)
        self.assertEqual(f.said, [])

    def test_cruise_over_hills_gear_up_says_nothing(self):
        # Level flight (VS 0) while the terrain rises and falls underneath.
        f = Flight(agl=3000, gear_down=False)
        f.fly_to(800, fpm=900, vs=0).fly_to(3000, fpm=900, vs=0).fly_to(400, fpm=900, vs=0)
        self.assertEqual(f.said, [])

    def test_gear_up_descent_only_calls_above_1000(self):
        f = Flight(agl=3000, gear_down=False)
        f.fly_to(1500, fpm=700)
        self.assertEqual(f.said, ["2500"])
        f.fly_to(600, fpm=700)
        self.assertNotIn("1000", f.said)
        self.assertNotIn("500", f.said)

    def test_countdown_does_not_repeat_with_jitter(self):
        f = Flight(agl=70, throttle=0)
        f.fly_to(52, fpm=300)
        for base in (50, 40, 30, 20):
            f.fly_to(base - 1, fpm=300)
            for _ in range(3):
                f.feed(agl=base + 2)
                f.feed(agl=base - 3)
        self.assertEqual(f.said, ["50", "40", "30", "20"])

    def test_go_around_rearms(self):
        f = Flight(throttle=0)
        f.fly_to(150).fly_to(1500, fpm=1000).fly_to(0)
        first = ["1000", "500", "400", "300", "200"]
        self.assertEqual(f.said, first + ALL_HEIGHTS)

    def test_jump_speaks_only_lowest(self):
        f = Flight(agl=70, throttle=0)
        f.feed(agl=60)
        f.feed(agl=25)
        self.assertEqual(f.said, ["30"])
        f.fly_to(0, fpm=300)
        self.assertEqual(f.said, ["30", "20", "retard"])

    def test_teleport_speaks_nothing(self):
        f = Flight(agl=1000.5)
        f.feed(agl=0, on_ground=True)
        f.hold(2)
        self.assertEqual(f.said, [])
        self.assertEqual(f.events, [])


class TestStyles(unittest.TestCase):
    def test_boeing_is_default(self):
        f = Flight(Config(), agl=3000, throttle=0)
        f.fly_to(0)
        self.assertEqual(f.said, ["2500", "1000", "500", "100", "50", "40", "30", "20", "10"])
        self.assertEqual(f.texts[0], "twenty five hundred")
        self.assertEqual(f.texts[-1], "ten")

    def test_boeing_minimums_wording(self):
        f = Flight(Config(minimums_ft=200), throttle=0)
        f.fly_to(0)
        self.assertIn("approaching minimums", f.texts)
        self.assertIn("minimums", f.texts)

    def test_airbus_wording(self):
        f = Flight(airbus(minimums_ft=200), agl=3000, throttle=0)
        f.fly_to(0)
        self.assertEqual(f.texts[0], "two thousand five hundred")
        self.assertIn("hundred above", f.texts)
        self.assertIn("minimum", f.texts)
        self.assertEqual(f.said[-1], "retard")

    def test_text_override_beats_style(self):
        cfg = airbus(texts={"minimums": "decide"})
        self.assertEqual(cfg.text("minimums"), "decide")
        self.assertEqual(cfg.text("approaching_minimums"), "hundred above")


class TestNoSpeedCallsAfterTakeoff(unittest.TestCase):
    """Bug report 2026-10-06: King Air climbing out at 150 kt with Vref 100 heard "plus twenty"."""

    def takeoff(self, **cfg):
        f = Flight(Config(vref_kt=100, **cfg), agl=0, ias=0, throttle=90)
        for ias in range(0, 151, 5):                 # takeoff roll
            f.feed(ias=ias)
        f.fly_to(800, fpm=1500)                      # climb out, gear still down, 150 kt
        return f

    def test_climb_out_is_silent(self):
        f = self.takeoff()
        self.assertFalse([k for k in f.said if k.startswith("speed_")])
        self.assertFalse(f.engine.speed_watch)

    def test_level_off_low_after_takeoff_is_silent(self):
        f = self.takeoff()
        f.hold(10, vs=0)                             # pattern work at 800 ft, gear still down
        self.assertFalse([k for k in f.said if k.startswith("speed_")])

    def test_calls_return_once_descending_for_the_approach(self):
        f = self.takeoff()
        f.hold(5, vs=0)
        f.fly_to(400, fpm=600)                       # turned final and started down, still at 150 kt
        self.assertIn("speed_plus_50", f.said)

    def test_warnings_mode_too(self):
        f = self.takeoff(speed_callouts="warnings")
        self.assertNotIn("speed_high", f.said)

    def test_level_downwind_is_silent(self):
        f = Flight(Config(vref_kt=100), agl=940, ias=175)
        f.hold(30, vs=-60)                           # level pattern leg, gear down, fast
        self.assertFalse([k for k in f.said if k.startswith("speed_")])

    def test_wobble_on_the_plus_twenty_line_calls_once(self):
        # From the 2026-10-06 recording: 117-120 kt with Vref 100 used to flip +15/+20 every few seconds.
        f = Flight(Config(vref_kt=100), agl=900, ias=113)
        f.hold(4, vs=-700)                           # established on final
        for ias in [115, 120, 118, 120, 117, 120, 118, 117, 120, 118, 120, 117, 120] * 2:
            f.hold(2, ias=ias, vs=-700)
        speed = [t for t in f.texts if t.startswith("plus")]
        self.assertEqual(speed, ["plus ten", "plus fifteen", "plus twenty"])

    def test_improvement_is_called_once_it_holds(self):
        f = Flight(Config(vref_kt=100), agl=900, ias=121)
        f.hold(4, vs=-700)                           # established on final
        f.hold(3, ias=113, vs=-700)                  # better, but only 3 s: nothing yet
        self.assertEqual([t for t in f.texts if t.startswith("plus")], ["plus twenty"])
        f.hold(3, ias=113, vs=-700)                  # held 6 s now
        self.assertEqual([t for t in f.texts if t.startswith("plus")], ["plus twenty", "plus ten"])

    def test_go_around_climb_is_silent(self):
        f = Flight(Config(vref_kt=100), agl=600, ias=100)
        f.fly_to(300, fpm=600)                       # on speed: nothing to say
        f.fly_to(900, fpm=1200, vs=None)             # go-around: climbing and accelerating
        f.feed(ias=135)
        f.fly_to(950, fpm=1200)
        self.assertFalse([k for k in f.said if k.startswith("speed_")])


class TestSpeedDeviation(unittest.TestCase):
    def fly(self, speeds, agl=900, gear_down=True, vref=105, secs_each=3):
        f = Flight(Config(vref_kt=vref), agl=agl, ias=vref, gear_down=gear_down)
        f.hold(3.5, vs=-600)                           # established on final (on speed)
        for ias in speeds:
            f.hold(secs_each, ias=ias, vs=-600)
        return f

    def test_calls_band_changes_only(self):
        f = self.fly([117, 117, 121, 116, 110, 99, 105], secs_each=6)
        self.assertEqual(f.texts, ["plus ten", "plus fifteen", "plus ten", "ref", "minus five", "ref"])

    def test_says_actual_deviation_past_plus_twenty(self):
        # 2026-10-06 DC-6 recording: 151 kt with Vref 100 was called "plus twenty".
        self.assertEqual(self.fly([151], vref=100).texts, ["plus fifty"])

    def test_slowing_from_way_off(self):
        f = self.fly([151, 140, 130, 121, 116, 111, 104], vref=100, secs_each=6)
        self.assertEqual(f.texts, ["plus fifty", "plus twenty", "plus fifteen", "plus ten", "ref"])

    def test_hovering_at_plus_fifty_calls_once(self):
        f = self.fly([150, 149, 151, 148, 150, 149, 151, 150], vref=100, secs_each=2)
        self.assertEqual(f.texts, ["plus fifty"])

    def test_ref_only_after_a_deviation(self):
        self.assertEqual(self.fly([105, 107, 103, 106], secs_each=6).texts, [])   # on speed all the way

    def test_ref_needs_to_hold_5_seconds(self):
        f = self.fly([118])                            # "plus ten"
        f.hold(3, ias=106, vs=-600)
        self.assertEqual(f.texts, ["plus ten"])        # back on speed only 3 s: not yet
        f.hold(3, ias=106, vs=-600)
        self.assertEqual(f.texts, ["plus ten", "ref"])

    def test_ref_wording_can_be_changed(self):
        f = Flight(Config(vref_kt=105, texts={"speed_ref": "speed"}), agl=900, ias=105)
        f.hold(3.5, vs=-600)
        f.hold(3, ias=117, vs=-600)
        f.hold(6, ias=105, vs=-600)
        self.assertEqual(f.texts[-1], "speed")

    def test_default_mode_has_no_speed_high_low(self):
        f = self.fly([140, 90])
        self.assertNotIn("speed_high", f.said)
        self.assertNotIn("speed_low", f.said)
        self.assertEqual(f.texts, ["plus thirty", "minus ten"])   # 140 kt is Vref +35

    def test_gusts_around_a_threshold_call_once(self):
        f = Flight(Config(vref_kt=105), agl=900, ias=105)
        for i in range(60):                            # 6 s bouncing between +9 and +12
            f.feed(ias=114 if i % 2 else 117, vs=-600)
        self.assertEqual(f.texts, ["plus ten"])

    def test_quick_changes_are_spaced_out(self):
        f = Flight(Config(vref_kt=105), agl=900, ias=105)
        f.hold(3.5, vs=-600)                           # established on final
        f.feed(ias=116, vs=-600)
        f.feed(ias=121, vs=-600)                        # 0.1 s later: too soon for another call
        self.assertEqual(f.texts, ["plus ten"])
        f.hold(2.5, ias=121)
        self.assertEqual(f.texts, ["plus ten", "plus fifteen"])

    def test_only_on_final(self):
        self.assertEqual(self.fly([120], agl=1500).texts, [])      # above 1000 ft
        self.assertEqual(self.fly([120], gear_down=False).texts, [])
        self.assertEqual(self.fly([120], vref=0).texts, [])       # no Vref set

    def test_reentering_final_announces_again(self):
        f = Flight(Config(vref_kt=105), agl=1100, ias=117)
        f.fly_to(900, fpm=600)
        self.assertEqual(f.texts[-1], "plus ten")
        f.fly_to(1100, fpm=600).hold(35, vs=0)      # out of the window for a while (go-around, new circuit)
        f.fly_to(900, fpm=600)                         # back on final
        self.assertEqual([t for t in f.texts if t.startswith("plus")], ["plus ten", "plus ten"])


class TestHeightCalloutsSwitch(unittest.TestCase):
    def test_off_silences_countdown_and_retard_only(self):
        cfg = airbus(height_callouts=False, minimums_ft=200, landing_target_fpm=200, vref_kt=105)
        f = Flight(cfg, agl=3000, ias=118, throttle=40, flaps=40)
        f.fly_to(150, fpm=700)
        f.fly_to(0, fpm=150)
        self.assertFalse([k for k in f.said if k.isdigit()])          # no 2500 ... 10
        self.assertNotIn("retard", f.said)
        self.assertFalse(any("speed one" in t for t in f.texts))      # 1000/500 speed readouts ride along
        self.assertIn("approaching_minimums", f.said)                 # minimums still called
        self.assertIn("minimums", f.said)
        self.assertIn("speed_plus_10", f.said)                        # speed deviation still works
        self.assertIn("landing_result", f.said)                       # landing target still works

    def test_on_by_default(self):
        self.assertTrue(Config().height_callouts)
        self.assertEqual(build_thresholds(Config())[1000], "1000")


class TestLandingTarget(unittest.TestCase):
    def land(self, fpm, **cfg):
        f = Flight(Config(**cfg), agl=60, throttle=0)
        f.fly_to(0, fpm=fpm)
        return f

    def test_soft_landing_within_target(self):
        f = self.land(150, landing_target_fpm=200)
        self.assertEqual(f.texts[-1], "one five zero, within target")
        self.assertTrue(f.engine.last_touchdown.within_target)
        self.assertIn("within 200 fpm target", f.events[-1])

    def test_hard_landing_over_target(self):
        f = self.land(320, landing_target_fpm=200)
        self.assertEqual(f.texts[-1], "three two zero, over target")
        self.assertFalse(f.engine.last_touchdown.within_target)
        self.assertIn("OVER 200 fpm target", f.events[-1])

    def test_exactly_on_target_counts_as_within(self):
        self.assertTrue(self.land(200, landing_target_fpm=200).engine.last_touchdown.within_target)

    def test_bounce_is_not_a_second_landing(self):
        # 2026-10-07 recording: a skip 5 s after touchdown gave a second "one nine, within target"
        f = self.land(170, landing_target_fpm=200)
        td = f.engine.last_touchdown
        f.hold(4, ias=85)
        f.feed(agl=1.5, on_ground=False, vs=12)          # skips back into the air...
        f.hold(1, agl=1.5, on_ground=False)
        f.feed(agl=0, on_ground=True, vs=19)              # ...and settles again
        f.hold(3)
        self.assertEqual(f.said.count("landing_result"), 1)
        self.assertIs(f.engine.last_touchdown, td)
        self.assertTrue(any(e.startswith("Bounce") for e in f.events))

    def test_a_later_landing_still_counts(self):
        f = self.land(170, landing_target_fpm=200)
        f.hold(15, ias=60)
        f.feed(agl=20, on_ground=False, vs=500)           # touch-and-go, then land again later
        f.fly_to(0, fpm=150)
        self.assertEqual(f.said.count("landing_result"), 2)

    def test_no_target_no_call(self):
        f = self.land(150)
        self.assertNotIn("landing_result", f.said)
        self.assertIsNone(f.engine.last_touchdown.within_target)

    def test_result_can_be_silent(self):
        f = self.land(150, landing_target_fpm=200, speak_landing_result=False)
        self.assertNotIn("landing_result", f.said)
        self.assertTrue(f.engine.last_touchdown.within_target)   # still shown in Landings


class TestTakeoff(unittest.TestCase):
    def roll(self, cfg=None, flaps=20, dc6=False, rotate_at=None, top=125):
        f = Flight(cfg or Config(v1_kt=93, vr_kt=101, v2_kt=106), agl=0, ias=0, flaps=flaps)
        f.engine.dc6 = dc6
        f.hold(2, ias=5)                                  # lined up
        for ias in range(5, top, 2):                      # take-off roll
            f.feed(ias=ias)
        f.fly_to(600, fpm=900, vs=900)                    # lift off and climb
        return f

    def test_v1_rotate_positive_rate(self):
        f = self.roll()
        self.assertEqual(f.texts, ["vee one", "rotate", "positive rate"])

    def test_optional_eighty_knots_and_v2(self):
        f = self.roll(Config(v1_kt=93, vr_kt=101, v2_kt=106, call_80kt=True, call_v2=True))
        self.assertEqual(f.texts, ["eighty knots", "vee one", "rotate", "vee two", "positive rate"])

    def test_v1_equal_vr_said_together(self):
        f = self.roll(Config(v1_kt=101, vr_kt=101))
        self.assertEqual(f.texts[0], "vee one, rotate")

    def test_no_speeds_set_only_positive_rate(self):
        self.assertEqual(self.roll(Config()).texts, ["positive rate"])

    def test_rejected_takeoff_then_go_again(self):
        f = Flight(Config(v1_kt=93, vr_kt=101), agl=0, ias=0)
        for ias in range(0, 70, 2):
            f.feed(ias=ias)
        for ias in range(70, 0, -3):                     # abort before V1, slow down, try again
            f.feed(ias=ias)
        for ias in range(0, 110, 2):
            f.feed(ias=ias)
        self.assertEqual(f.texts, ["vee one", "rotate"])

    def test_nothing_on_the_landing_rollout(self):
        f = Flight(Config(v1_kt=93, vr_kt=101), agl=60, ias=100)
        f.fly_to(0, fpm=300)
        for ias in range(100, 20, -3):
            f.feed(ias=ias)
        self.assertFalse(any(t in ("vee one", "rotate", "positive rate") for t in f.texts))

    def test_dc6_flaps_check(self):
        self.assertEqual(self.roll(flaps=0, dc6=True).texts[0], "check flaps")
        self.assertNotIn("check flaps", self.roll(flaps=20, dc6=True).texts)
        self.assertNotIn("check flaps", self.roll(flaps=0, dc6=False).texts)   # only in DC-6 mode

    def test_no_flaps_check_while_taxiing_after_landing(self):
        # 2026-10-07 recording: taxi speed nudged past 40 kt with the flaps coming up -> "check flaps"
        f = Flight(Config(), agl=60, ias=100, flaps=50)
        f.engine.dc6 = True
        f.fly_to(0, fpm=300)
        for ias in range(100, 38, -3):
            f.feed(ias=ias)
        for ias in (39, 41, 43, 45, 42, 40, 44):         # fast taxi, flaps retracting
            f.feed(ias=ias, flaps=f.flaps - 5 if f.flaps > 0 else 0)
        self.assertNotIn("check flaps", f.texts)

    def test_flaps_check_happens_at_60_on_a_real_roll(self):
        f = self.roll(flaps=0, dc6=True)
        self.assertEqual(f.texts[0], "check flaps")

    def test_dc6_reverse_cue(self):
        f = Flight(Config(), agl=60, ias=100)
        f.engine.dc6 = True
        f.fly_to(0, fpm=300)
        for ias in range(100, 20, -3):
            f.feed(ias=ias)
        self.assertIn("forty knots", f.texts)


class TestCrosswindRemarks(unittest.TestCase):
    def setUp(self):
        import landing_callouts.engine as eng
        eng._remark_count = 0

    def straight_in(self, wind_dir=200, wind_kt=35, heading=110, **cfg):
        f = Flight(Config(**cfg), agl=1400, ias=110)
        f.wind_dir, f.wind_kt, f.hdg = wind_dir, wind_kt, heading
        f.fly_to(700, fpm=700)
        return f

    def test_strong_crosswind_gets_a_remark(self):
        f = self.straight_in()
        wind = [t for t in f.texts if t.startswith("wind")]
        self.assertEqual(wind, ["wind two zero zero at thirty five, crosswind thirty five from the right. "
                                "these are some crosswinds!"])

    def test_lines_rotate_across_approaches(self):
        lines = []
        for _ in range(4):
            f = self.straight_in()
            lines.append([t for t in f.texts if t.startswith("wind")][0].split(". ", 1)[1])
        self.assertEqual(lines, ["these are some crosswinds!", "putting her to the test today, captain",
                                 "gonna have to crab this one in", "these are some crosswinds!"])

    def test_no_remark_for_a_normal_crosswind_or_when_off(self):
        self.assertFalse(any("!" in t for t in self.straight_in(wind_kt=15).texts))
        self.assertFalse(any("!" in t for t in self.straight_in(crosswind_remarks=False).texts))

    def test_said_later_once_lined_up_if_still_turning_at_1000(self):
        f = Flight(Config(), agl=1300, ias=115)
        f.wind_dir, f.wind_kt, f.hdg = 170, 34, 160
        while f.agl > 900:                                    # turning through 1000 ft
            f.feed(agl=f.agl - 1.2, vs=-700, hdg=(f.hdg - 1) % 360)
        f.hdg = 90
        while f.agl > 300:                                    # lined up on runway 09, descending
            f.feed(agl=f.agl - 1.2, vs=-700)
        texts = [t for t in f.texts if "crosswind" in t or t.startswith("wind")]
        self.assertEqual(texts[0], "wind one seven zero at thirty four")         # plain while turning
        self.assertEqual(texts[1], "crosswind thirty three from the right. these are some crosswinds!")

    def test_never_in_the_flare(self):
        f = Flight(Config(), agl=180, ias=105)
        f.wind_dir, f.wind_kt, f.hdg = 170, 34, 90
        f.hold(12, vs=-500)
        f.fly_to(0, fpm=500)
        self.assertFalse(any("crosswinds" in t for t in f.texts))


class TestSpeedReference(unittest.TestCase):
    def test_dc6_speed_calls_wait_for_landing_flaps(self):
        # 2026-10-08: "plus sixty" at 1000 ft, flaps 20, still slowing and turning onto final
        f = Flight(Config(vref_kt=99), agl=1050, ias=158, flaps=20)
        f.engine.dc6 = True
        f.fly_to(900, fpm=700)
        self.assertFalse([t for t in f.texts if t.startswith("plus")])
        f.hold(5, vs=-700, flaps=40, ias=120)                # judged against the flaps-full Vref
        self.assertEqual([t for t in f.texts if t.startswith("plus")], ["plus twenty"])

    def test_dc6_missing_flaps_reading_does_not_block(self):
        f = Flight(Config(vref_kt=99), agl=900, ias=114, flaps=None)
        f.engine.dc6 = True
        f.hold(4, vs=-650)
        self.assertEqual([t for t in f.texts if t.startswith("plus")], ["plus fifteen"])

    def test_flaps_20_still_watched_outside_dc6_mode(self):
        f = Flight(Config(vref_kt=99), agl=900, ias=114, flaps=20)
        f.hold(4, vs=-650)
        self.assertEqual([t for t in f.texts if t.startswith("plus")], ["plus fifteen"])


class TestWindCheck(unittest.TestCase):
    def approach(self, wind_dir, wind_kt, heading=110, **cfg):
        f = Flight(Config(**cfg), agl=1300, ias=110)
        f.wind_dir, f.wind_kt, f.hdg = wind_dir, wind_kt, heading
        f.fly_to(800, fpm=700)
        return [t for t in f.texts if t.startswith("wind")]

    def test_crosswind_from_the_left(self):
        # heading 110, wind from 070 at 15: 40 deg off the left, ~10 kt crosswind
        self.assertEqual(self.approach(70, 15), ["wind zero seven zero at fifteen, crosswind ten from the left"])

    def test_crosswind_from_the_right(self):
        self.assertEqual(self.approach(150, 20), ["wind one five zero at twenty, crosswind thirteen from the right"])

    def test_straight_down_the_runway_and_calm(self):
        self.assertEqual(self.approach(110, 12), ["wind one one zero at twelve"])
        self.assertEqual(self.approach(200, 2), ["wind calm"])

    def test_tailwind(self):
        self.assertEqual(self.approach(290, 8), ["wind two nine zero at eight, tailwind eight"])

    def test_north_is_three_six_zero_and_can_be_off(self):
        self.assertEqual(self.approach(2, 10, heading=0), ["wind three six zero at ten"])
        self.assertEqual(self.approach(70, 15, wind_check=False), [])

    def test_halves_round_up(self):
        self.assertEqual(self.approach(165, 10, heading=165), ["wind one seven zero at ten"])

    def test_low_circuit_gets_it_when_final_starts(self):
        # 2026-10-07 recording: circuit flown at ~480 ft never passed 1000 ft
        f = Flight(Config(), agl=480, ias=120)
        f.wind_dir, f.wind_kt, f.hdg = 170, 12, 91
        f.hold(10, vs=0)                                  # low final leg, gear down, lined up
        self.assertFalse([t for t in f.texts if t.startswith("wind")])
        f.fly_to(150, fpm=500)                            # start down on final
        winds = [t for t in f.texts if t.startswith("wind")]
        self.assertEqual(winds, ["wind one seven zero at twelve, crosswind twelve from the right"])

    def test_turning_onto_final_says_only_the_wind(self):
        # same low circuit, but still turning from base onto final when the descent starts
        f = Flight(Config(), agl=480, ias=120)
        f.wind_dir, f.wind_kt, f.hdg = 170, 12, 190
        f.hold(2, vs=0)
        while f.agl > 300:                                # descending while turning 2 deg per reading
            f.feed(agl=f.agl - 1, vs=-600, hdg=(f.hdg - 2) % 360)
        winds = [t for t in f.texts if t.startswith("wind")]
        self.assertEqual(winds, ["wind one seven zero at twelve"])

    def test_slow_curving_approach_says_only_the_wind(self):
        # 2026-10-07 recording: turning 039 -> 126 at ~0.7 deg/s, the 1000 ft call said "tailwind eight"
        f = Flight(Config(), agl=1300, ias=117)
        f.wind_dir, f.wind_kt, f.hdg = 165, 23, 30
        while f.agl > 800:
            f.feed(agl=f.agl - 1.15, vs=-690, hdg=f.hdg + 0.07)   # 0.7 deg per second
        winds = [t for t in f.texts if t.startswith("wind")]
        self.assertEqual(winds, ["wind one seven zero at twenty three"])

    def test_once_per_approach(self):
        f = Flight(Config(), agl=1300, ias=110)
        f.wind_dir, f.wind_kt, f.hdg = 70, 15, 110
        f.fly_to(800, fpm=700)                            # called at 1000 ft
        f.hold(20, vs=0)                                  # level off (final ends) ...
        f.fly_to(400, fpm=700)                            # ... and start down again below 1000
        self.assertEqual(len([t for t in f.texts if t.startswith("wind")]), 1)

    def test_no_wind_call_on_fast_low_level_flying(self):
        f = Flight(Config(), agl=480, ias=220, gear_down=False)
        f.wind_dir, f.wind_kt, f.hdg = 170, 12, 91
        f.fly_to(150, fpm=900)
        self.assertFalse([t for t in f.texts if t.startswith("wind")])

    def test_not_on_climb_out(self):
        f = Flight(Config(), agl=0, ias=0)
        f.wind_dir, f.wind_kt, f.hdg = 70, 15, 110
        f.fly_to(1500, fpm=1000)
        self.assertFalse([t for t in f.texts if t.startswith("wind")])


class TestStableCheck(unittest.TestCase):
    def through_500(self, ias=105, fpm=700, gear_down=True, flaps=50, **cfg):
        f = Flight(Config(**{"vref_kt": 105, **cfg}), agl=700, ias=ias, gear_down=gear_down, flaps=flaps)
        f.fly_to(400, fpm=fpm)
        return [t for t in f.texts if "stable" in t]

    def test_stable(self):
        self.assertEqual(self.through_500(), ["five hundred, stable"])

    def test_not_when_rising_ground_crosses_500(self):
        # 2026-10-09: descending onto a low circuit, then level/climbing while the ground rises under you
        f = Flight(Config(vref_kt=99), agl=900, ias=130, flaps=30)
        f.fly_to(850, fpm=450)                        # on final for a moment
        for agl in range(800, 450, -25):              # +170 fpm, but the ground comes up
            f.hold(0.5, vs=170, agl=agl)
        self.assertFalse([t for t in f.texts if "stable" in t])

    def test_unstable_reasons(self):
        self.assertEqual(self.through_500(ias=122), ["five hundred, unstable, speed"])
        self.assertEqual(self.through_500(ias=98), ["five hundred, unstable, speed"])
        self.assertEqual(self.through_500(fpm=1300), ["five hundred, unstable, sink rate"])
        self.assertEqual(self.through_500(flaps=0), ["five hundred, unstable, flaps"])
        self.assertEqual(self.through_500(gear_down=False), ["unstable, gear"])   # no "500" call gear-up
        self.assertEqual(self.through_500(gear_down=False, ias=150), ["unstable, gear, speed"])

    def test_no_vref_skips_the_speed_check(self):
        self.assertEqual(self.through_500(ias=140, vref_kt=0), ["five hundred, stable"])

    def test_standalone_when_heights_are_off(self):
        self.assertEqual(self.through_500(height_callouts=False), ["stable"])

    def test_can_be_off(self):
        self.assertEqual(self.through_500(stable_check=False), [])


class TestLandingCheck(unittest.TestCase):
    def approach(self, flaps=50, gear_down=True, dc6=True, to=250, **cfg):
        f = Flight(Config(**cfg), agl=1500, ias=110, flaps=flaps, gear_down=gear_down)
        f.engine.dc6 = dc6
        f.fly_to(to, fpm=700)
        return f, [t for t, k in zip(f.texts, f.said) if k == "landing_check"]

    def test_all_set_at_300_ft(self):
        f, said = self.approach(to=350)
        self.assertEqual(said, [])                          # not yet at 350 ft
        f.fly_to(250, fpm=700)
        self.assertEqual([t for t, k in zip(f.texts, f.said) if k == "landing_check"], ["flaps full, three green"])

    def test_flaps_not_full(self):
        self.assertEqual(self.approach(flaps=40)[1], ["check flaps"])

    def test_flaps_and_gear(self):
        self.assertEqual(self.approach(flaps=20, gear_down=False)[1], ["check flaps, check gear"])

    def test_only_in_dc6_mode_and_can_be_off(self):
        self.assertEqual(self.approach(dc6=False)[1], [])
        self.assertEqual(self.approach(landing_check=False)[1], [])

    def test_once_per_approach_and_again_after_a_go_around(self):
        f, said = self.approach(to=200)
        self.assertEqual(len(said), 1)
        f.fly_to(1700, fpm=1500)                            # go-around
        f.fly_to(250, fpm=700)
        self.assertEqual([k for k in f.said if k == "landing_check"], ["landing_check", "landing_check"])

    def test_not_on_climb_out(self):
        f = Flight(Config(), agl=0, ias=120, flaps=20)
        f.engine.dc6 = True
        f.fly_to(1400, fpm=1000)
        self.assertNotIn("landing_check", f.said)


class TestFromDc6Recording(unittest.TestCase):
    """Fixes from the 2026-10-06 DC-6 recording."""

    def test_no_too_low_gear_on_a_fast_low_pass(self):
        f = Flight(agl=450, ias=230, gear_down=False)
        f.fly_to(90, fpm=1200)
        self.assertNotIn("too_low_gear", f.said)
        self.assertIn("sink_rate", f.said)           # still warns about the descent rate

    def test_too_low_gear_still_works_at_landing_speeds(self):
        f = Flight(agl=450, ias=150, gear_down=False)
        f.fly_to(200, fpm=700)
        self.assertIn("too_low_gear", f.said)

    def test_one_thousand_called_after_a_low_circuit(self):
        f = Flight(airbus(), agl=0, ias=0)
        f.fly_to(1069, fpm=900, vs=900)              # circuit only climbed to 1069 ft
        f.fly_to(990, fpm=400)
        self.assertIn("1000", f.said)


class TestOverspeed(unittest.TestCase):
    def test_flap_speed_repeats_while_exceeded_then_stops(self):
        f = Flight(agl=3000)
        f.hold(1)
        f.hold(6, flap_over=True)                   # t = 0 and 5 s
        self.assertEqual(f.said.count("flap_speed"), 2)
        self.assertEqual(f.texts[0], "flap speed")
        f.hold(10, flap_over=False)
        self.assertEqual(f.said.count("flap_speed"), 2)

    def test_gear_speed(self):
        f = Flight(agl=3000, gear_down=False)
        f.hold(1, gear_over=True)
        self.assertEqual(f.said, ["gear_speed"])

    def test_works_at_any_height_but_not_on_the_ground(self):
        self.assertIn("flap_speed", Flight(agl=9000).hold(1, flap_over=True).said)
        g = Flight(agl=0)
        g.hold(1, flap_over=True)
        self.assertEqual(g.said, [])

    def test_switch_off_and_no_data(self):
        self.assertEqual(Flight(Config(overspeed_warnings=False), agl=3000).hold(1, flap_over=True).said, [])
        self.assertEqual(Flight(agl=3000).hold(1, flap_over=None, gear_over=None).said, [])


class TestFlaps(unittest.TestCase):
    def move_flaps(self, f: Flight, to: float, deg_per_s: float = 10):
        step = deg_per_s * DT
        while abs(f.flaps - to) > 1e-9:
            f.feed(flaps=min(to, f.flaps + step) if to > f.flaps else max(to, f.flaps - step))

    def test_confirms_new_setting_once_it_settles(self):
        f = Flight(agl=3000, flaps=20)
        f.hold(2)
        self.assertEqual(f.said, [])                 # no call for the starting position
        self.move_flaps(f, 30)
        self.assertEqual(f.said, [])                 # not while moving
        f.hold(0.5)
        self.assertEqual(f.said, [])                 # not before it has settled for a second
        f.hold(2)
        self.assertEqual(f.said, ["flaps_30"])
        self.assertEqual(f.texts, ["flaps thirty"])
        self.move_flaps(f, 50)
        f.hold(2)
        self.move_flaps(f, 0, deg_per_s=20)
        f.hold(2)
        self.assertEqual(f.texts, ["flaps thirty", "flaps fifty", "flaps up"])

    def test_slow_flaps_not_announced_while_travelling(self):
        f = Flight(agl=3000, flaps=30)
        f.hold(1)
        self.move_flaps(f, 40, deg_per_s=5)          # 0.5 deg per reading, like a DC-6
        self.assertEqual(f.said, [])
        f.hold(1.5)
        self.assertEqual(f.said, ["flaps_40"])

    def test_small_wobble_is_ignored(self):
        f = Flight(agl=3000, flaps=40)
        f.hold(1)
        f.feed(flaps=42)
        f.hold(2)
        self.assertEqual(f.said, [])

    def test_rounds_to_nearest_five(self):
        f = Flight(agl=3000, flaps=0)
        f.hold(1)
        self.move_flaps(f, 14.6)                    # e.g. a jet's flaps 15 reading slightly off
        f.hold(2)
        self.assertEqual(f.texts, ["flaps fifteen"])

    def test_switch_off(self):
        f = Flight(Config(flap_callouts=False), agl=600, flaps=20, throttle=0)
        f.hold(1)
        f.feed(flaps=30)
        f.hold(2)
        self.assertFalse([k for k in f.said if "flaps" in k])

    def test_no_flap_data_no_flap_calls(self):
        f = Flight(agl=600, flaps=None, throttle=0)
        f.fly_to(100)
        self.assertFalse([k for k in f.said if "flaps" in k])


class TestThresholds(unittest.TestCase):
    def test_minimums_replace_plain_height(self):
        cfg = airbus(minimums_ft=200)
        table = build_thresholds(cfg)
        self.assertEqual(table[200], "minimums")
        self.assertEqual(table[300], "approaching_minimums")
        self.assertEqual(table[10], "retard")
        f = Flight(cfg, throttle=0)
        f.fly_to(0)
        self.assertEqual(f.said[:6], ["1000", "500", "400", "approaching_minimums", "minimums", "100"])

    def test_minimums_beat_retard(self):
        self.assertEqual(build_thresholds(airbus(minimums_ft=10))[10], "minimums")


class TestRetard(unittest.TestCase):
    def test_repeats_while_throttle_above_idle_then_stops(self):
        f = Flight(agl=35, throttle=40)
        f.fly_to(8, fpm=300)
        self.assertEqual(f.said, ["20", "retard"])
        f.hold(3.2, vs=-10, agl=8)          # floating with power on
        self.assertEqual(f.said.count("retard"), 3)
        f.hold(3, throttle=0)
        self.assertEqual(f.said.count("retard"), 3)

    def test_no_repeat_without_throttle_reading(self):
        f = Flight(agl=30, throttle=None)
        f.fly_to(8, fpm=300).hold(4, vs=-10)
        self.assertEqual(f.said.count("retard"), 1)


class TestConditions(unittest.TestCase):
    def test_too_low_gear_on_descent_not_after_takeoff(self):
        f = Flight(agl=0, gear_down=False)
        f.fly_to(800, fpm=1000)
        self.assertNotIn("too_low_gear", f.said)
        f.fly_to(300, fpm=600)
        self.assertIn("too_low_gear", f.said)

    def test_no_too_low_gear_easing_over_a_ridge_after_takeoff(self):
        # 2026-10-08: climbing over rising ground, then a one-second nose-over at the crest
        f = Flight(agl=0, ias=170, gear_down=False)
        f.fly_to(450, fpm=1200)
        f.hold(1, vs=-250, agl=480)
        f.fly_to(1000, fpm=1200)
        self.assertNotIn("too_low_gear", f.said)

    def test_speed_low_respects_repeat_interval(self):
        f = Flight(Config(vref_kt=110, speed_callouts="warnings"), agl=900, ias=110)
        f.hold(3.5, vs=-500)                              # established on final, on speed
        f.hold(12, ias=100)
        self.assertEqual(f.said.count("speed_low"), 3)   # t = 0, 5, 10 (from slowing down)

    def test_speed_high_respects_repeat_interval(self):
        f = Flight(Config(vref_kt=110, speed_callouts="warnings"), agl=900, ias=110)
        f.hold(3.5, vs=-500)                              # established on final, on speed
        f.hold(7, ias=140)
        self.assertEqual(f.said.count("speed_high"), 2)

    def test_speed_ok_says_nothing(self):
        f = Flight(Config(vref_kt=110, speed_callouts="warnings"), agl=900, ias=115)
        f.hold(7, vs=-500)
        self.assertEqual(f.said, [])

    def test_sink_rate(self):
        f = Flight(agl=900)
        f.hold(1, vs=-1500)
        self.assertEqual(f.said, ["sink_rate"])
        f2 = Flight(agl=40)
        f2.hold(1, vs=-1500)
        self.assertNotIn("sink_rate", f2.said)

    def test_rollout_only_after_landing(self):
        f = Flight(Config(rollout_callouts_kt=[80, 60]), agl=0, ias=0, throttle=0)
        for ias in range(0, 120, 3):        # takeoff roll
            f.feed(ias=ias)
        for ias in range(120, 40, -3):      # rejected takeoff
            f.feed(ias=ias)
        self.assertEqual(f.said, [])


if __name__ == "__main__":
    unittest.main()
