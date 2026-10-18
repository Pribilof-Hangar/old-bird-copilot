import unittest

from landing_callouts.vref import DC6_VAT, DC6_WEIGHTS, is_dc6, suggest, takeoff

DC6 = "PMDG DC-6A Cargo"


class TestDc6Table(unittest.TestCase):
    def test_exact_table_rows(self):
        # PMDG POH p.318: full / 40 columns
        for i, w in enumerate(DC6_WEIGHTS):
            s = suggest(DC6, w * 1000)
            self.assertEqual((s.full, s.flaps40), (DC6_VAT["full"][i], DC6_VAT["40"][i]), w)
            self.assertEqual(s.note, "")

    def test_spot_checks_from_the_handbook(self):
        self.assertEqual(suggest(DC6, 80_000).full, 100)
        self.assertEqual(suggest(DC6, 88_000).flaps40, 111)
        self.assertEqual(suggest(DC6, 70_000).full, 94)

    def test_between_rows(self):
        self.assertEqual(suggest(DC6, 81_000).full, 101)     # between 100 and 102
        self.assertEqual(suggest(DC6, 84_900).full, 103)     # 103 -> 104, rounds to 103

    def test_outside_the_table(self):
        light = suggest(DC6, 64_000)
        self.assertEqual((light.full, light.note), (94, "below table"))
        heavy = suggest(DC6, 95_000)
        self.assertEqual((heavy.full, heavy.note), (106, "over max landing wt"))

    def test_only_for_the_dc6(self):
        for title in ("PMDG DC-6A", "PMDG DC-6B Cloudmaster", "dc6 freighter", "Douglas DC 6"):
            self.assertTrue(is_dc6(title), title)
        for title in ("Beechcraft King Air 350i", "Boeing 727-200", "", None, "DC-3"):
            self.assertFalse(is_dc6(title), title)
        self.assertIsNone(suggest("Beechcraft King Air 350i", 12_000))
        self.assertIsNone(suggest(DC6, None))


class TestDc6Takeoff(unittest.TestCase):
    def test_wet_rows_from_the_handbook(self):
        from landing_callouts.vref import DC6_TOW, DC6_V1_WET, DC6_V2
        for i, w in enumerate(DC6_TOW):
            if w * 1000 <= 87_600:
                continue                                  # dry weights checked below
            t = takeoff(DC6, w * 1000)
            self.assertEqual((t.v1, t.v2, t.vr, t.wet), (min(DC6_V1_WET[i], DC6_V2[i] - 5), DC6_V2[i],
                                                         DC6_V2[i] - 5, True), w)

    def test_dry_up_to_87600(self):
        t = takeoff(DC6, 86_000)
        self.assertEqual((t.v1, t.vr, t.v2, t.wet), (93, 101, 106, False))
        self.assertTrue(takeoff(DC6, 88_000).wet)

    def test_corrections(self):
        base = takeoff(DC6, 96_000)                       # V1 99
        self.assertEqual(takeoff(DC6, 96_000, elevation_ft=3000).v1, base.v1 + 3)
        self.assertEqual(takeoff(DC6, 96_000, headwind_kt=15).v1, base.v1 + 1)
        self.assertEqual(takeoff(DC6, 96_000, headwind_kt=-5).v1, base.v1 - 2)

    def test_v1_never_above_vr(self):
        t = takeoff(DC6, 103_000, elevation_ft=8000)
        self.assertLessEqual(t.v1, t.vr)

    def test_outside_and_other_planes(self):
        self.assertEqual(takeoff(DC6, 110_000).note, "over max take-off wt")
        self.assertIsNone(takeoff("Beechcraft King Air 350i", 14_000))


if __name__ == "__main__":
    unittest.main()
