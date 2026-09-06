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


class TestH1Confirmation(unittest.TestCase):
    def candidate(self):
        return {"status": "CANDIDATE", "phase": "DAILY", "blockers": []}

    def h1(self, **overrides):
        base = {"complete": True, "fresh": True, "close": 100, "open": 98.8,
                "high": 100.4, "low": 98.5, "vwap": 99.2,
                "relative_volume": 1.4, "higher_low": True,
                "breakout_5d": True, "asof": "2026-09-01T14:30:00-04:00"}
        base.update(overrides)
        return base

    def daily(self, **overrides):
        base = {"atr": 2.0, "resistance": 105.0, "trigger_low": 98.5}
        base.update(overrides)
        return base

    def now(self):
        return "2026-09-01T14:31:00-04:00"

    def test_ready_requires_complete_first_hour(self):
        r = s.confirm_h1(self.candidate(), self.h1(complete=False), self.daily(), self.now())
        self.assertEqual(r["status"], "WAIT")

    def test_ready_has_bounded_execution_plan(self):
        r = s.confirm_h1(self.candidate(), self.h1(), self.daily(), self.now())
        self.assertEqual(r["status"], "READY")
        self.assertGreaterEqual(r["position_value"], 20)
        self.assertLessEqual(r["position_value"], 50)
        self.assertLessEqual(r["risk_dollars"], 0.50)
        self.assertGreater(r["target"], r["entry"])
        self.assertLess(r["stop"], r["entry"])

    def test_below_vwap_waits(self):
        r = s.confirm_h1(self.candidate(), self.h1(close=98.9), self.daily(), self.now())
        self.assertEqual(r["status"], "WAIT")

    def test_poor_structural_reward_rejects(self):
        r = s.confirm_h1(self.candidate(), self.h1(), self.daily(resistance=100.5), self.now())
        self.assertEqual(r["status"], "REJECT")
        self.assertIn("INSUFFICIENT_STRUCTURAL_RR", r["blockers"])

    def test_stale_h1_rejects(self):
        r = s.confirm_h1(self.candidate(), self.h1(fresh=False), self.daily(), self.now())
        self.assertEqual(r["status"], "REJECT")
        self.assertIn("STALE_H1_DATA", r["blockers"])

    def test_h1_asof_in_future_waits(self):
        r = s.confirm_h1(self.candidate(), self.h1(), self.daily(), "2026-09-01T14:00:00-04:00")
        self.assertEqual(r["status"], "WAIT")

    def test_gap_up_does_not_tighten_structural_stop(self):
        flat = s.confirm_h1(self.candidate(), self.h1(), self.daily(), self.now())
        gapped = s.confirm_h1(self.candidate(), self.h1(low=99.9), self.daily(), self.now())
        self.assertEqual(gapped["status"], "READY")
        self.assertAlmostEqual(gapped["stop"], flat["stop"])
        self.assertAlmostEqual(gapped["stop"], 98.3)

    def test_no_resistance_caps_target_at_two_r(self):
        r = s.confirm_h1(self.candidate(), self.h1(), self.daily(resistance=None), self.now())
        self.assertEqual(r["status"], "READY")
        self.assertAlmostEqual(r["target"], 103.4)
        self.assertAlmostEqual(r["reward_risk"], 2.0)

    def test_wide_structural_stop_rejects_budget(self):
        r = s.confirm_h1(self.candidate(), self.h1(), self.daily(trigger_low=90.0), self.now())
        self.assertEqual(r["status"], "REJECT")
        self.assertIn("POSITION_BELOW_MIN", r["blockers"])

    def test_ready_carries_execution_contract(self):
        r = s.confirm_h1(self.candidate(), self.h1(), self.daily(), self.now())
        self.assertEqual(r["status"], "READY")
        self.assertEqual(r["trigger"], "BREAKOUT_5D")
        self.assertAlmostEqual(r["reward_risk"], 2.0)
        self.assertEqual(r["exit_deadline"], "2026-09-03T15:50:00-04:00")
        self.assertIs(r["earnings_verified"], True)
        self.assertIs(r["data_fresh"], True)

    def test_friday_entry_deadline_skips_weekend(self):
        r = s.confirm_h1(
            self.candidate(),
            self.h1(asof="2026-09-04T14:30:00-04:00"),
            self.daily(),
            "2026-09-04T14:31:00-04:00",
        )
        self.assertEqual(r["status"], "READY")
        self.assertEqual(r["exit_deadline"], "2026-09-08T15:50:00-04:00")

    def test_non_candidate_is_not_confirmed(self):
        c = self.candidate()
        c["status"] = "WAIT"
        c["blockers"] = ["EARNINGS_UNVERIFIED"]
        r = s.confirm_h1(c, self.h1(), self.daily(), self.now())
        self.assertEqual(r["status"], "WAIT")
        self.assertIn("EARNINGS_UNVERIFIED", r["blockers"])
        self.assertIsNone(r["trigger"])
        self.assertIs(r["earnings_verified"], False)

    def test_all_wait_results_carry_contract_fields(self):
        for label, kw in (
            ("incomplete", {"complete": False}),
            ("below_vwap", {"close": 98.9}),
            ("low_volume", {"relative_volume": 1.2}),
            ("bearish", {"close": 98.6}),
            ("no_trigger", {"higher_low": False, "breakout_5d": False}),
        ):
            with self.subTest(case=label):
                r = s.confirm_h1(self.candidate(), self.h1(**kw), self.daily(), self.now())
                self.assertEqual(r["status"], "WAIT")
                for key in ("trigger", "reward_risk", "exit_deadline",
                            "earnings_verified", "data_fresh"):
                    self.assertIn(key, r)

    def test_malformed_h1_numbers_reject_instead_of_crashing(self):
        for label, kw in (
            ("close", {"close": "oops"}),
            ("vwap", {"vwap": None}),
            ("missing_close", {"close": None}),
        ):
            with self.subTest(case=label):
                r = s.confirm_h1(self.candidate(), self.h1(**kw), self.daily(), self.now())
                self.assertEqual(r["status"], "REJECT")
                self.assertIn("INVALID_H1_INPUT", r["blockers"])

    def test_malformed_daily_levels_reject_instead_of_crashing(self):
        for label, kw in (
            ("trigger_low", {"trigger_low": None}),
            ("atr", {"atr": "x"}),
        ):
            with self.subTest(case=label):
                r = s.confirm_h1(self.candidate(), self.h1(), self.daily(**kw), self.now())
                self.assertEqual(r["status"], "REJECT")
                self.assertIn("INVALID_STRUCTURAL_LEVELS", r["blockers"])
        r = s.confirm_h1(self.candidate(), self.h1(low=None), self.daily(), self.now())
        self.assertEqual(r["status"], "REJECT")
        self.assertIn("INVALID_STRUCTURAL_LEVELS", r["blockers"])


class TestH1Features(unittest.TestCase):
    def bars(self):
        # Two completed first-hour bars: prior session then current session.
        return {
            "opens": [99.0, 100.2],
            "highs": [101.0, 102.5],
            "lows": [98.0, 99.9],
            "closes": [99.5, 102.0],
            "volumes": [1_000_000, 1_400_000],
            "vwaps": [99.4, 101.8],
            "asofs": ["2026-08-31T14:30:00-04:00", "2026-09-01T14:30:00-04:00"],
        }

    def test_h1_features_use_last_completed_bar_only(self):
        f = s.h1_features(**self.bars(), expected_first_hour_volume=1_000_000)
        self.assertEqual(f["bars"], 2)
        self.assertEqual(f["close"], 102.0)
        self.assertEqual(f["open"], 100.2)
        self.assertEqual(f["high"], 102.5)
        self.assertEqual(f["low"], 99.9)
        self.assertEqual(f["vwap"], 101.8)
        self.assertEqual(f["asof"], "2026-09-01T14:30:00-04:00")
        self.assertAlmostEqual(f["relative_volume"], 1.4)
        self.assertIs(f["bullish_close"], True)
        self.assertIs(f["higher_low"], True)
        self.assertIs(f["breakout_5d"], True)

    def test_breakout_needs_strict_prior_high_break(self):
        b = self.bars()
        b["highs"] = [500.0, 100.5]  # prior spike far above last high
        f = s.h1_features(**b, expected_first_hour_volume=1_000_000)
        self.assertIs(f["breakout_5d"], False)

    def test_breakout_lookback_capped_at_five_prior_bars(self):
        opens = [100.0] * 7
        # 500 six bars back must not count; last high 105 beats the prior-5
        # window max (101) and must count only if the window excludes bar 0.
        highs = [500.0] + [101.0] * 5 + [105.0]
        lows = [99.0] * 7
        closes = [100.5] * 6 + [105.5]
        volumes = [1_000_000] * 7
        vwaps = [100.0] * 7
        asofs = ["2026-08-2%dT14:30:00-04:00" % i for i in range(1, 8)]
        f = s.h1_features(opens, highs, lows, closes, volumes, vwaps, 1_000_000, asofs)
        self.assertIs(f["breakout_5d"], True)

    def test_single_bar_has_no_trigger_history(self):
        b = self.bars()
        for key in ("opens", "highs", "lows", "closes", "volumes", "vwaps", "asofs"):
            b[key] = [b[key][-1]]
        f = s.h1_features(**b, expected_first_hour_volume=1_000_000)
        self.assertIs(f["higher_low"], False)
        self.assertIs(f["breakout_5d"], False)

    def test_h1_features_require_equal_nonempty_arrays(self):
        b = self.bars()
        b["highs"] = [101.0]  # shorter than the rest
        with self.assertRaises(ValueError):
            s.h1_features(**b, expected_first_hour_volume=1_000_000)
        with self.assertRaises(ValueError):
            s.h1_features([], [], [], [], [], [], 1_000_000, [])

    def test_h1_features_reject_nonpositive_expected_volume(self):
        for bad in (0, -5):
            with self.subTest(expected=bad):
                with self.assertRaises(ValueError):
                    s.h1_features(**self.bars(), expected_first_hour_volume=bad)


if __name__ == "__main__":
    unittest.main()
