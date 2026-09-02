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
