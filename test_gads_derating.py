
import unittest
from datetime import datetime

from appendix_g_examples import EXAMPLES, NDC, run, t
from gads_derating import Event, analyze, round_half_up


class AppendixGExamples(unittest.TestCase):
    """Published values, compared at the two decimals the appendix prints."""

    def test_equivalent_derated_hours(self):
        for ex_id, ex in EXAMPLES.items():
            res = run(ex_id)
            for num, published in ex["edh"].items():
                with self.subTest(example=ex_id, event=num):
                    self.assertEqual(round_half_up(res[num].equivalent_hours), published)

    def test_outage_and_reserve_shutdown_hours(self):
        for ex_id, ex in EXAMPLES.items():
            res = run(ex_id)
            for num, published in ex["hours"].items():
                with self.subTest(example=ex_id, event=num):
                    self.assertEqual(round_half_up(res[num].duration_hours), published)

    def test_unit_nac_profile(self):
        for ex_id, ex in EXAMPLES.items():
            if "nac_profile" not in ex:
                continue
            with self.subTest(example=ex_id):
                self.assertEqual([mw for _, _, mw in run(ex_id).nac_profile()],
                                 ex["nac_profile"])

    def test_no_example_raises_warnings(self):
        for ex_id in EXAMPLES:
            with self.subTest(example=ex_id):
                self.assertEqual(run(ex_id).warnings, [])

    def test_no_double_counting(self):
        """Sum of event MWh lost == area between NDC and the unit NAC curve
        outside full outages."""
        for ex_id in EXAMPLES:
            res = run(ex_id)
            by_event = sum(r.mwh_lost for r in res.results.values())
            by_curve = sum((NDC - s.unit_nac) * s.hours
                           for s in res.segments if s.status != "OUTAGE")
            with self.subTest(example=ex_id):
                self.assertAlmostEqual(by_event, by_curve, places=6)


class AppendixGDetails(unittest.TestCase):
    def test_3a_second_derating_sized_from_first(self):
        r = run("3A")["0004"]
        self.assertEqual((r.reference_nac, r.reduction_mw), (525, 75))

    def test_3b_shadowed_hours(self):
        r = run("3B")["0005"]
        self.assertAlmostEqual(r.duration_hours, 477.25)
        self.assertAlmostEqual(r.shadowed_hours, 0.75)
        self.assertEqual(r.shadowed_by, ["0006"])

    def test_3d_first_derating_charged_only_before_dominant(self):
        r = run("3D")["0009"]
        self.assertAlmostEqual(r.charged_hours, 1.25)
        self.assertAlmostEqual(r.shadowed_hours, 2.75)

    def test_4_shadowed_derating_sized_as_if_alone(self):
        r = run("4")["0013"]
        self.assertEqual(r.reduction_mw, 100)       # "if it had occurred alone"
        self.assertEqual(r.charged_hours, 0)
        self.assertAlmostEqual(r.shadowed_hours, 7.25)

    def test_5_derating_during_reserve_shutdown(self):
        res = run("5")
        r = res["0015"]
        self.assertAlmostEqual(r.equivalent_hours_rs, 10.5)
        self.assertAlmostEqual(r.equivalent_hours_in_service, 0.0)
        s = res.summary()
        self.assertAlmostEqual(s["EMDH"], 10.5)
        self.assertAlmostEqual(s["EMDHRS"], 10.5)
        self.assertAlmostEqual(s["RSH"], 61.0)

    def test_6b_outage_interrupts_derating(self):
        r = run("6B")["0018"]
        self.assertAlmostEqual(r.charged_hours, 57.0 + 35.8)
        self.assertAlmostEqual(r.shadowed_hours, 79.95)
        self.assertAlmostEqual(r.repair_hours, 172.75)   # blank work times -> event times

    def test_summary_categories(self):
        s = run("3C").summary()
        self.assertEqual(round_half_up(s["EPDH"]), 0.42)
        self.assertEqual(round_half_up(s["EFDH"]), 11.33)
        self.assertEqual(round_half_up(s["EDH"]), 11.75)
        self.assertAlmostEqual(run("9").summary()["FOH"], 134.0)

    def test_repair_hours_from_work_times(self):
        self.assertAlmostEqual(run("1")["0001"].repair_hours, 121.5)
        self.assertAlmostEqual(run("3B")["0005"].repair_hours, 243.75)
        self.assertAlmostEqual(run("7")["0022"].repair_hours, 17.0)
        self.assertAlmostEqual(run("9")["0023"].repair_hours, 38.5)


class EngineEdgeCases(unittest.TestCase):
    def test_round_half_up(self):
        self.assertEqual(round_half_up(0.125), 0.13)   # built-in round() gives 0.12
        self.assertEqual(round_half_up(1.15625), 1.16)

    def test_nmc_differs_from_ndc(self):
        e = Event("1", "D1", t(1, 1, "00:00"), t(1, 1, "10:00"), nac=480)
        r = analyze([e], ndc=580, nmc=600)["1"]
        self.assertEqual(r.reduction_mw, 100)
        self.assertAlmostEqual(r.equivalent_hours, 100 * 10 / 600)

    def test_three_stacked_additive_deratings(self):
        evs = [Event("A", "D1", t(1, 1, "00:00"), t(1, 1, "10:00"), nac=550),
               Event("B", "D4", t(1, 1, "02:00"), t(1, 1, "08:00"), nac=500),
               Event("C", "PD", t(1, 1, "04:00"), t(1, 1, "06:00"), nac=420)]
        res = analyze(evs, 600)
        self.assertEqual([res[k].reduction_mw for k in "ABC"], [50, 50, 80])
        self.assertEqual([mw for *_, mw in res.nac_profile()], [550, 500, 420, 500, 550])

    def test_missing_dominant_flag_is_flagged(self):
        # Example 3D reported WITHOUT the "D": B looks additive (500 -> 360 = 140 MW)
        # and the unit would wrongly return to 460 MW when A ends.
        evs = [Event("A", "D1", t(3, 10, "06:30"), t(3, 10, "10:30"), nac=500),
               Event("B", "D1", t(3, 10, "07:45"), t(3, 10, "19:30"), nac=360)]
        res = analyze(evs, 600)
        self.assertEqual(res["B"].reduction_mw, 140)
        self.assertEqual([mw for *_, mw in res.nac_profile()], [500, 360, 460])

    def test_nac_not_below_current_capacity_warns(self):
        evs = [Event("A", "D1", t(1, 1, "00:00"), t(1, 1, "10:00"), nac=400),
               Event("B", "D1", t(1, 1, "02:00"), t(1, 1, "04:00"), nac=500)]
        res = analyze(evs, 600)
        self.assertEqual(res["B"].equivalent_hours, 0)
        self.assertTrue(any("event B" in w for w in res.warnings))

    def test_bad_input_rejected(self):
        e = Event("1", "D1", t(1, 2, "00:00"), t(1, 1, "00:00"), nac=500)
        with self.assertRaises(ValueError):
            analyze([e], 600)
        with self.assertRaises(ValueError):
            analyze([Event("1", "D1", t(1, 1, "00:00"), t(1, 2, "00:00"), 500)] * 2, 600)


if __name__ == "__main__":
    unittest.main()
