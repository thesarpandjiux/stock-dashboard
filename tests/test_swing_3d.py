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

    def test_rejects_huge_integer_entry(self):
        p = s.position_plan(entry=10**10000, stop=99)
        self.assertFalse(p["eligible"])
        self.assertEqual(p["reason"], "INVALID_LEVELS")
        self.assertNotIn("shares", p)

    def test_rejects_huge_integer_configuration(self):
        huge = 10**10000
        for overrides in (
            {"capital": huge},
            {"risk_budget": huge},
            {"min_value": huge},
            {"max_value": huge},
        ):
            with self.subTest(overrides=overrides):
                p = s.position_plan(entry=100, stop=99, **overrides)
                self.assertFalse(p["eligible"])
                self.assertEqual(p["reason"], "INVALID_CONFIGURATION")
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


class TestDailyRegime(unittest.TestCase):
    def features(self, **overrides):
        base = {"bars": 80, "price": 105, "ema10": 103, "ema20": 101,
                "sma50": 98, "ema20_slope": 1.2, "sma50_slope": 0.8,
                "rs_spy_5d": 2.0, "atr": 2.0, "atr_pct": 1.9,
                "extension_atr": 1.0, "dollar_volume": 80_000_000,
                "resistance": 112}
        base.update(overrides)
        return base

    def test_daily_can_only_create_candidate(self):
        d = s.evaluate_daily(self.features(), True, 8, True)
        self.assertEqual(d["status"], "CANDIDATE")
        self.assertEqual(d["phase"], "DAILY")

    def test_unknown_earnings_never_candidate(self):
        d = s.evaluate_daily(self.features(), False, None, True)
        self.assertEqual(d["status"], "WAIT")
        self.assertIn("EARNINGS_UNVERIFIED", d["blockers"])

    def test_negative_relative_strength_rejects(self):
        d = s.evaluate_daily(self.features(rs_spy_5d=-0.1), True, 8, True)
        self.assertEqual(d["status"], "REJECT")

    def test_stale_data_rejects(self):
        d = s.evaluate_daily(self.features(), True, 8, False)
        self.assertEqual(d["status"], "REJECT")

    def test_insufficient_data_rejects(self):
        d = s.evaluate_daily(self.features(bars=59), True, 8, True)
        self.assertEqual(d["status"], "REJECT")
        self.assertIn("INSUFFICIENT_DAILY_BARS", d["blockers"])

    def test_daily_features_use_only_completed_supplied_bars(self):
        closes = [100 + i for i in range(60)]
        f = s.daily_features(
            [price + 2 for price in closes],
            [price - 2 for price in closes],
            closes,
            [1_000_000] * 60,
            [100 + i / 2 for i in range(60)],
        )
        self.assertEqual(f["bars"], 60)
        self.assertEqual(f["price"], 159)
        self.assertGreater(f["ema10"], f["ema20"])
        self.assertGreater(f["ema20_slope"], 0)
        self.assertGreater(f["sma50_slope"], 0)
        self.assertGreater(f["rs_spy_5d"], 0)
        self.assertEqual(f["dollar_volume"], 159_000_000)
        self.assertEqual(f["resistance"], 160)

    def test_sector_relative_strength_included(self):
        closes = [100 + i for i in range(60)]
        f = s.daily_features(
            [price + 2 for price in closes],
            [price - 2 for price in closes],
            closes,
            [1_000_000] * 60,
            [100 + i / 2 for i in range(60)],
            [100 + i / 4 for i in range(60)],
        )
        self.assertIn("rs_sector_5d", f)
        self.assertGreater(f["rs_sector_5d"], f["rs_spy_5d"])

    def test_no_prior_high_above_price_gives_none_resistance(self):
        closes = [100 + i for i in range(60)]
        f = s.daily_features(
            [price - 1 for price in closes],
            [price - 3 for price in closes],
            closes,
            [1_000_000] * 60,
            [100 + i / 2 for i in range(60)],
        )
        self.assertIsNone(f["resistance"])


if __name__ == "__main__":
    unittest.main()
