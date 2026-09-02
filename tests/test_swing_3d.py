import unittest

import swing_3d as s


class TestPositionPlan(unittest.TestCase):
    def test_caps_risk_and_position(self):
        p = s.position_plan(entry=100, stop=99)
        self.assertEqual(p["eligible"], True)
        self.assertAlmostEqual(p["shares"], 0.5)
        self.assertAlmostEqual(p["position_value"], 50.0)
        self.assertLessEqual(p["risk_dollars"], 0.50)

    def test_rejects_when_structural_stop_makes_position_too_small(self):
        p = s.position_plan(entry=100, stop=97)
        self.assertEqual(p["eligible"], False)
        self.assertEqual(p["reason"], "POSITION_BELOW_MIN")

    def test_never_tightens_stop(self):
        p = s.position_plan(entry=100, stop=98)
        self.assertEqual(p["stop"], 98)
        self.assertLessEqual(p["risk_dollars"], 0.50)

    def test_rejects_invalid_configuration(self):
        cases = (
            {"capital": 0},
            {"capital": -1000},
            {"risk_budget": -0.50},
            {"min_value": -20},
            {"max_value": -50},
            {"min_value": 50, "max_value": 20},
            {"capital": float("nan")},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                p = s.position_plan(entry=100, stop=99, **overrides)
                self.assertFalse(p["eligible"])
                self.assertEqual(p["reason"], "INVALID_CONFIGURATION")
                self.assertNotIn("shares", p)

    def test_rejects_non_finite_levels(self):
        for levels in (
            {"entry": float("nan"), "stop": 99},
            {"entry": 100, "stop": float("nan")},
        ):
            with self.subTest(levels=levels):
                p = s.position_plan(**levels)
                self.assertFalse(p["eligible"])
                self.assertEqual(p["reason"], "INVALID_LEVELS")
                self.assertNotIn("shares", p)

    def test_reject_has_domain_contract(self):
        p = s.reject("NO_SETUP", "screen", "2026-09-02")
        self.assertEqual(
            set(p),
            {
                "status", "phase", "entry", "stop", "target", "shares",
                "position_value", "risk_dollars", "risk_pct_account",
                "reasons", "blockers", "asof",
            },
        )
        self.assertEqual(p["status"], "REJECT")
        self.assertEqual(p["blockers"], ["NO_SETUP"])
        self.assertEqual(p["asof"], "2026-09-02")


if __name__ == "__main__":
    unittest.main()
