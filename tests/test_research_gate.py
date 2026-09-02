import json
import math
import unittest
from datetime import datetime, timezone

import backtest_3d as bt
import run_research_3d as rr


def holdout_trades(n, wins, win_r=1.0, loss_r=-1.0,
                   tickers=("AAA", "BBB", "CCC")):
    """n trades, `wins` of them at +win_r, spread across tickers so no
    single ticker holds more than ~1/3 of the winners (below the 35%
    concentration cap)."""
    trades = []
    for k in range(wins):
        trades.append({"ticker": tickers[k % len(tickers)],
                       "r_multiple": win_r})
    for k in range(n - wins):
        trades.append({"ticker": tickers[k % len(tickers)],
                       "r_multiple": loss_r})
    return trades


def clean_holdout(n, expectancy=0.2, profit_factor=1.5, **extra):
    """Holdout dict that passes every gate rule on its own, so a test can
    isolate one rule.  Trades implied: wins = n*(1+e)/2 at +1R, rest at
    -1R, winners spread over three tickers (33.3% each <= 35% cap)."""
    wins = int(round(n * (1 + expectancy) / 2))
    h = {
        "n": n,
        "expectancy_r": expectancy,
        "profit_factor": profit_factor,
        "trades": holdout_trades(n, wins),
    }
    h.update(extra)
    return h


class TestProductionGate(unittest.TestCase):
    """Gate rules from the Task 6 brief, one reason code per rule."""

    def test_rejects_negative_holdout(self):
        g = bt.production_gate({"holdout": {"n": 100, "expectancy_r": -0.02},
                                "folds": []})
        self.assertFalse(g["eligible"])
        self.assertIn("HOLDOUT_EXPECTANCY_NOT_POSITIVE", g["reasons"])

    def test_rejects_small_sample(self):
        g = bt.production_gate({"holdout": {"n": 19, "expectancy_r": 0.2},
                                "folds": []})
        self.assertFalse(g["eligible"])
        self.assertIn("HOLDOUT_SAMPLE_TOO_SMALL", g["reasons"])

    def test_accepts_large_positive_holdout_without_folds(self):
        g = bt.production_gate({"holdout": clean_holdout(30, 0.2),
                                "folds": []})
        self.assertTrue(g["eligible"])
        self.assertEqual(g["reasons"], [])

    def test_rejects_profit_factor_below_or_at_one(self):
        for pf in (1.0, 0.9):
            with self.subTest(profit_factor=pf):
                h = clean_holdout(30, 0.2, pf)
                g = bt.production_gate({"holdout": h, "folds": []})
                self.assertFalse(g["eligible"])
                self.assertIn("HOLDOUT_PROFIT_FACTOR_NOT_ABOVE_ONE",
                              g["reasons"])

    def test_accepts_profit_factor_above_one(self):
        g = bt.production_gate(
            {"holdout": clean_holdout(30, 0.2, 1.01), "folds": []})
        self.assertTrue(g["eligible"])

    def test_rejects_missing_profit_factor(self):
        # Fail closed: a report without a cost-adjusted profit factor cannot
        # prove the >1 rule, so it is not eligible.
        h = clean_holdout(30, 0.2)
        del h["profit_factor"]
        g = bt.production_gate({"holdout": h, "folds": []})
        self.assertFalse(g["eligible"])
        self.assertIn("HOLDOUT_PROFIT_FACTOR_NOT_ABOVE_ONE", g["reasons"])

    def test_rejects_zero_trades_holdout_fail_closed(self):
        g = bt.production_gate({"holdout": {"n": 0}, "folds": []})
        self.assertFalse(g["eligible"])
        self.assertIn("HOLDOUT_SAMPLE_TOO_SMALL", g["reasons"])
        # No expectancy reason: no arithmetic on an empty set.
        self.assertNotIn("HOLDOUT_EXPECTANCY_NOT_POSITIVE", g["reasons"])

    def test_rejects_zero_expectancy(self):
        g = bt.production_gate(
            {"holdout": clean_holdout(30, 0.0), "folds": []})
        self.assertFalse(g["eligible"])
        self.assertIn("HOLDOUT_EXPECTANCY_NOT_POSITIVE", g["reasons"])

    def test_rejects_missing_holdout_section(self):
        g = bt.production_gate({"folds": []})
        self.assertFalse(g["eligible"])
        self.assertEqual(g["reasons"], ["HOLDOUT_METRICS_MISSING"])

    def test_rejects_non_numeric_holdout_metrics(self):
        g = bt.production_gate(
            {"holdout": {"n": "30", "expectancy_r": "x"}, "folds": []})
        self.assertFalse(g["eligible"])
        self.assertEqual(g["reasons"], ["HOLDOUT_METRICS_MISSING"])

    def test_rejects_single_ticker_over_concentration_cap(self):
        # 60% of gross positive R comes from AAA; the cap is >35%.
        trades = ([{"ticker": "AAA", "r_multiple": 1.0}] * 60
                  + [{"ticker": "BBB", "r_multiple": 1.0}] * 40)
        h = {"n": 100, "expectancy_r": 0.2, "profit_factor": 1.5,
             "trades": trades}
        g = bt.production_gate({"holdout": h, "folds": []})
        self.assertFalse(g["eligible"])
        self.assertIn("HOLDOUT_TICKER_CONCENTRATION", g["reasons"])

    def test_accepts_ticker_share_at_exactly_35_percent(self):
        trades = ([{"ticker": "AAA", "r_multiple": 1.0}] * 30
                  + [{"ticker": "BBB", "r_multiple": 1.0}] * 35
                  + [{"ticker": "CCC", "r_multiple": 1.0}] * 35)
        h = {"n": 100, "expectancy_r": 1.0, "profit_factor": 2.0,
             "trades": trades}
        g = bt.production_gate({"holdout": h, "folds": []})
        self.assertTrue(g["eligible"])

    def test_concentration_counts_positive_r_only(self):
        # Winners spread 20/20/20 over three tickers (33.3% each); the 40
        # losers all belong to BBB but losses never count toward the cap.
        trades = ([{"ticker": "AAA", "r_multiple": 1.0}] * 20
                  + [{"ticker": "BBB", "r_multiple": 1.0}] * 20
                  + [{"ticker": "CCC", "r_multiple": 1.0}] * 20
                  + [{"ticker": "BBB", "r_multiple": -1.0}] * 40)
        h = {"n": 100, "expectancy_r": 0.2, "profit_factor": 1.5,
             "trades": trades}
        g = bt.production_gate({"holdout": h, "folds": []})
        self.assertTrue(g["eligible"])

    def test_failed_fold_flag_rejects(self):
        g = bt.production_gate({
            "holdout": clean_holdout(30, 0.2),
            "folds": [{"test": [{"day": 1}], "trades": [],
                       "failed": True}],
        })
        self.assertFalse(g["eligible"])
        self.assertIn("HIDDEN_FAILED_FOLD", g["reasons"])

    def test_accepts_selective_folds_without_trades(self):
        # A picky strategy may produce zero trades in a fold; that is
        # selectivity, not a hidden failure.  Only an explicit failure flag
        # trips the rule.
        g = bt.production_gate({
            "holdout": clean_holdout(30, 0.2),
            "folds": [
                {"test": [{"day": 1}], "trades": []},
                {"test": [{"day": 2}], "trades": [{"r_multiple": 0.1}]},
            ],
        })
        self.assertTrue(g["eligible"])

    def test_rejects_non_list_folds(self):
        g = bt.production_gate({"holdout": clean_holdout(30, 0.2),
                                "folds": None})
        self.assertFalse(g["eligible"])
        self.assertIn("HIDDEN_FAILED_FOLD", g["reasons"])

    def test_rejects_missing_holdout_key_fail_closed(self):
        g = bt.production_gate({})
        self.assertFalse(g["eligible"])
        self.assertIn("HOLDOUT_METRICS_MISSING", g["reasons"])


def day_row(day, close=100.0):
    return {"day": day, "open": close - 0.2, "high": close + 1.0,
            "low": close - 1.0, "close": close, "volume": 1_000_000}


def spy_row(day, close=100.0):
    return {"day": day, "close": close}


class TestRunnerCore(unittest.TestCase):
    """run_research_3d pure functions (no network)."""

    def test_parse_args_defaults(self):
        args = rr.parse_args([
            "--universe", "u.txt",
            "--start", "2021-01-01",
            "--end", "2026-08-31",
            "--out", "artifacts/x.json",
        ])
        self.assertEqual(args.universe, "u.txt")
        self.assertEqual(args.out, "artifacts/x.json")
        self.assertEqual(args.slippage_bps, 5)
        self.assertEqual(args.holdout_fraction, 0.2)

    def test_parse_args_rejects_unparseable_dates(self):
        with self.assertRaises(SystemExit):
            rr.parse_args([
                "--universe", "u.txt",
                "--start", "not-a-date",
                "--end", "2026-08-31",
                "--out", "x.json",
            ])

    def test_parse_args_rejects_reversed_period(self):
        with self.assertRaises(SystemExit):
            rr.parse_args([
                "--universe", "u.txt",
                "--start", "2026-08-31",
                "--end", "2021-01-01",
                "--out", "x.json",
            ])

    def test_read_universe_skips_comments_and_blank_lines(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as f:
            f.write("# komentar\n\nNVDA\nAVGO\n\n")
            name = f.name
        try:
            self.assertEqual(rr.read_universe(name), ["NVDA", "AVGO"])
        finally:
            os.unlink(name)

    def test_read_universe_raises_on_empty(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as f:
            f.write("# hanya komentar\n")
            name = f.name
        try:
            with self.assertRaises(SystemExit):
                rr.read_universe(name)
        finally:
            os.unlink(name)

    def test_align_series_pairs_common_trading_days(self):
        daily = [("2026-08-03", 100.0), ("2026-08-04", 101.0),
                 ("2026-08-05", 102.0)]
        spy = [("2026-08-04", 500.0), ("2026-08-05", 501.0)]
        d, s = rr.align_series(daily, spy)
        self.assertEqual([row[0] for row in d],
                         ["2026-08-04", "2026-08-05"])
        self.assertEqual([row[0] for row in s],
                         ["2026-08-04", "2026-08-05"])

    def test_align_series_with_date_objects(self):
        from datetime import date
        daily = [(date(2026, 8, 3), 1.0), (date(2026, 8, 4), 2.0)]
        spy = [(date(2026, 8, 3), 9.0)]
        d, s = rr.align_series(daily, spy)
        self.assertEqual(d, [(date(2026, 8, 3), 1.0)])
        self.assertEqual(s, [(date(2026, 8, 3), 9.0)])

    def test_build_dataset_excludes_earnings_inside_hold(self):
        # NVDA earnings 2026-08-26 (Wed).  A row is researchable only when
        # the 3-session hold started from the next entry session cannot
        # straddle the earnings date: sessions leaving <= 3 sessions to it
        # (08-20..08-26) are dropped, the earnings date itself is never an
        # entry row, and rows after it resume immediately.
        days = ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25",
                "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31"]
        ds = rr.build_dataset(
            ticker="NVDA",
            daily=[day_row(d) for d in days],
            spy=[spy_row(d) for d in days],
            earnings_dates=["2026-08-26"],
            universe={"VOO", "QQQ"},  # NVDA is not an ETF: filter applies
        )
        dates = [row["day"] for row in ds]
        # 08-21..08-26 leave <= 3 sessions to earnings: the 3-session hold
        # from their next entry session would still be open at (or after)
        # the earnings date.  08-20's hold exits 08-25, before it, so it
        # stays; rows after the earnings date resume immediately.
        self.assertEqual(dates, ["2026-08-20", "2026-08-27", "2026-08-28",
                                 "2026-08-31"])
        self.assertNotIn("2026-08-26", dates)  # earnings date: no entry
        for row in ds:
            self.assertIs(row["isin"], True)

    def test_build_dataset_keeps_rows_far_from_earnings(self):
        # NVDA earnings 2026-08-15 falls on a Saturday (yfinance stamps
        # after-hours reports with the next calendar day).  Sessions
        # 08-11..08-14 leave <= 3 sessions to it and cannot host a hold;
        # 08-10 is far enough ahead to stay researchable and the sessions
        # after the weekend (08-17 onward) resume immediately.
        days = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13",
                "2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19",
                "2026-08-20", "2026-08-21"]
        ds = rr.build_dataset(
            ticker="NVDA",
            daily=[day_row(d) for d in days],
            spy=[spy_row(d) for d in days],
            earnings_dates=["2026-08-15"],  # Saturday: 08-17 is next session
            universe={"VOO", "QQQ"},  # NVDA is not an ETF: filter applies
        )
        dates = [row["day"] for row in ds]
        self.assertIn("2026-08-10", dates)   # 4 sessions before earnings
        self.assertNotIn("2026-08-11", dates)
        self.assertNotIn("2026-08-14", dates)
        # sessions after the earnings date resume
        self.assertIn("2026-08-17", dates)
        self.assertIn("2026-08-21", dates)

    def test_etf_universe_skips_earnings_filter(self):
        days = ["2026-08-10", "2026-08-11"]
        ds = rr.build_dataset(
            ticker="VOO",
            daily=[day_row(d) for d in days],
            spy=[spy_row(d) for d in days],
            earnings_dates=[],
            universe={"VOO", "QQQ"},
        )
        self.assertEqual(len(ds), 2)
        for row in ds:
            self.assertIs(row["isin"], False)

    def test_build_dataset_drops_universe_missing_daily(self):
        ds = rr.build_dataset(
            ticker="ZZZZ",
            daily=[],
            spy=[],
            earnings_dates=[],
            universe={"ZZZZ"},
        )
        self.assertEqual(ds, [])

    def test_walk_forward_block_rejects_insufficient_h1(self):
        # H1 first bar 2024-09-03 is after the last research day 2024-08-30:
        # no signal in the window could ever have been confirmed by an H1
        # close.  The runner must fail closed, not synthesize results.
        with self.assertRaises(SystemExit) as cm:
            rr.walk_forward_block(
                rows=[day_row("2024-08-29"), day_row("2024-08-30")],
                h1_first_day="2024-09-03",
                train_days=252,
                test_days=63,
                holdout_fraction=0.2,
                slippage_bps=5,
            )
        self.assertIn("INSUFFICIENT_POINT_IN_TIME_H1_DATA", str(cm.exception))

    def test_walk_forward_block_rejects_window_too_short(self):
        with self.assertRaises(SystemExit):
            rr.walk_forward_block(
                rows=[day_row("2024-09-03"), day_row("2024-09-04"),
                      day_row("2024-09-05")],
                h1_first_day="2024-09-03",
                train_days=252,
                test_days=63,
                holdout_fraction=0.2,
                slippage_bps=5,
            )

    def test_trim_to_h1_coverage_drops_early_rows(self):
        rows = [day_row("2024-08-28"), day_row("2024-08-29"),
                day_row("2024-08-30"), day_row("2024-09-03")]
        out = rr.trim_to_h1_coverage(rows, "2024-08-30")
        self.assertEqual([r["day"] for r in out],
                         ["2024-08-30", "2024-09-03"])

    def test_epoch_seconds_utc(self):
        expected = int(datetime(2024, 9, 3, 18, 30,
                                tzinfo=timezone.utc).timestamp())
        self.assertEqual(rr.epoch_seconds("2024-09-03T14:30:00-04:00"),
                         expected)

    def test_weekday_close_time(self):
        self.assertEqual(
            rr.weekday_close_time("2024-09-04"),
            "2024-09-04T15:50:00-04:00")

    def test_align_h1_to_daily_matches_session_dates(self):
        h1 = [
            # stamps are the first-hour bars (09:30 and 10:30 ET)
            ("2024-09-04T09:30:00-04:00", 10.0, 11.0, 9.9, 10.8, 500.0),
            ("2024-09-04T10:30:00-04:00", 10.9, 11.1, 10.7, 11.0, 400.0),
            ("2024-09-05T09:30:00-04:00", 11.1, 11.4, 11.0, 11.3, 600.0),
            ("2024-09-05T10:30:00-04:00", 11.3, 11.5, 11.2, 11.4, 350.0),
        ]
        daily = [day_row("2024-09-04", 10.2), day_row("2024-09-05", 11.0)]
        out = rr.align_h1_to_daily(h1, daily)
        self.assertEqual(len(out), 2)
        # the 09:30 bar is the first hour: 09:30-10:30 ET
        self.assertEqual(out[0]["session_date"], "2024-09-04")
        self.assertEqual(out[0]["h1_close"], 10.8)
        self.assertEqual(out[1]["session_date"], "2024-09-05")
        self.assertEqual(out[1]["h1_close"], 11.3)
        # daily open preserved from the daily row (day_row: open=close-0.2)
        self.assertEqual(out[1]["open"], 10.8)

    def test_align_h1_to_daily_only_keeps_sessions_with_h1(self):
        h1 = [("2024-09-04T09:30:00-04:00", 10.0, 11.0, 9.9, 10.8, 500.0)]
        daily = [day_row("2024-09-04", 10.2), day_row("2024-09-05", 11.0)]
        out = rr.align_h1_to_daily(h1, daily)
        self.assertEqual([r["session_date"] for r in out], ["2024-09-04"])

    def test_align_h1_uses_first_hour_bar_only(self):
        h1 = [
            ("2024-09-04T09:30:00-04:00", 10.0, 11.0, 9.9, 10.8, 500.0),
            ("2024-09-04T10:30:00-04:00", 10.9, 11.1, 10.7, 11.0, 400.0),
            ("2024-09-05T09:30:00-04:00", 11.1, 11.4, 11.0, 11.3, 600.0),
        ]
        daily = [day_row("2024-09-04", 10.2), day_row("2024-09-05", 11.0)]
        out = rr.align_h1_to_daily(h1, daily)
        self.assertEqual(out[0]["h1_high"], 11.0)
        self.assertEqual(out[0]["h1_low"], 9.9)
        self.assertEqual(out[0]["h1_volume"], 500.0)
        self.assertEqual(out[0]["h1_open"], 10.0)

    def test_expected_first_hour_volume_from_trailing_daily(self):
        daily_vols = [100.0, 110.0, 120.0]
        self.assertAlmostEqual(
            rr.expected_first_hour_volume(daily_vols, lookback=3),
            (110.0) * rr.FIRST_HOUR_VOLUME_SHARE,
        )
        self.assertAlmostEqual(
            rr.expected_first_hour_volume([100.0] * 30),
            100.0 * rr.FIRST_HOUR_VOLUME_SHARE,
        )


class TestRunnerSimulation(unittest.TestCase):
    """Signal simulation stays strictly causal: features come only from
    completed bars at or before the decision moment."""

    def test_evaluate_daily_signal_uses_only_supplied_bars(self):
        import swing_3d as s
        # Uptrend with a shallow 3-bar pause near EMA20.  The 80 supplied
        # bars keep price above SMA50 with a positive slope while the
        # extension from EMA20 stays under the 2.0-ATR cap, so the daily
        # regime gate can reach CANDIDATE on supplied bars alone.
        closes = [100 + i for i in range(77)] + [176] * 3
        highs = [c + 2 for c in closes]
        lows = [c - 2 for c in closes]
        vols = [1_000_000] * 80
        spy = [100 + i * 0.2 for i in range(80)]
        f = s.daily_features(highs, lows, closes, vols, spy)
        self.assertEqual(f["bars"], 80)
        self.assertEqual(f["price"], 176)
        self.assertLessEqual(f["extension_atr"], 2.0)
        d = s.evaluate_daily(f, True, None, True)
        self.assertEqual(d["status"], "CANDIDATE")

    def test_evaluate_daily_signal_waits_on_unverified_earnings(self):
        import swing_3d as s
        closes = [100 + i for i in range(80)]
        highs = [c + 2 for c in closes]
        lows = [c - 2 for c in closes]
        vols = [1_000_000] * 80
        spy = [100 + i / 2 for i in range(80)]
        f = s.daily_features(highs, lows, closes, vols, spy)
        d = s.evaluate_daily(f, earnings_verified=False,
                             earnings_in_trading_days=None, data_fresh=True)
        self.assertEqual(d["status"], "WAIT")
        self.assertIn("EARNINGS_UNVERIFIED", d["blockers"])

    def test_walk_forward_evaluator_protocol(self):
        rows = [day_row("2024-09-%02d" % (i + 1)) for i in range(10)]

        def evaluator(train, test):
            return [{"r_multiple": 0.5, "ticker": "NVDA"} for _ in test]

        folds = bt.walk_forward(rows, 3, 2, evaluator)
        self.assertEqual(len(folds), 3)
        self.assertEqual(len(folds[0]["trades"]), 2)


class TestReportJson(unittest.TestCase):
    def test_report_writer_is_json_safe(self):
        report = {
            "holdout": {"expectancy_r": None, "profit_factor": float("inf")},
            "gate": {"eligible": False, "reasons": []},
        }
        rr.write_report(report, "/tmp/rr-report-test.json")
        with open("/tmp/rr-report-test.json") as f:
            loaded = json.load(f)
        self.assertIsNone(loaded["holdout"]["expectancy_r"])
        self.assertEqual(loaded["holdout"]["profit_factor"], "inf")


if __name__ == "__main__":
    unittest.main()
