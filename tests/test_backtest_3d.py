import unittest

import backtest_3d as bt


def bar(open_, high, low, close, session):
    return {"open": open_, "high": high, "low": low, "close": close,
            "session": session}


def rr(entry, stop, target, exit_price):
    """R-multiple for a fixed exit price (no slippage)."""
    return (exit_price - entry) / (entry - stop)


class TestSimulation(unittest.TestCase):
    def test_same_bar_stop_and_target_uses_stop(self):
        bars = [{"open": 100, "high": 103, "low": 98, "close": 102, "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 102, slippage_bps=0)
        self.assertEqual(r["exit_reason"], "STOP")
        self.assertEqual(r["r_multiple"], -1)

    def test_gap_through_stop_exits_at_open(self):
        bars = [{"open": 97, "high": 98, "low": 96, "close": 97, "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 103, slippage_bps=0)
        self.assertEqual(r["exit_price"], 97)
        self.assertLess(r["r_multiple"], -1)

    def test_time_exit_at_third_session(self):
        bars = [
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100.1, "session": 1},
            {"open": 100.1, "high": 100.6, "low": 99.6, "close": 100.2, "session": 2},
            {"open": 100.2, "high": 100.7, "low": 99.7, "close": 100.3, "session": 3},
        ]
        r = bt.simulate_trade(None, bars, 100, 98, 104, slippage_bps=0)
        self.assertEqual(r["exit_reason"], "TIME")
        self.assertEqual(r["holding_sessions"], 3)

    def test_gap_through_stop_pays_slippage_below_open(self):
        # Exit price = open (97) minus 10 bps slippage = 96.903.
        bars = [{"open": 97, "high": 98, "low": 96, "close": 97, "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 103, slippage_bps=10)
        self.assertEqual(r["exit_reason"], "STOP")
        self.assertAlmostEqual(r["exit_price"], 96.903, places=6)

    def test_same_bar_stop_wins_before_intrabar_target(self):
        # Bar hits target 102 (intrabar high) and stop 99; stop-first means
        # exit fills at the stop, not the target. Loss, not breakeven win.
        bars = [{"open": 100, "high": 102.5, "low": 98.9, "close": 102, "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 102, slippage_bps=0)
        self.assertEqual(r["exit_reason"], "STOP")
        self.assertAlmostEqual(r["exit_price"], 99)
        self.assertEqual(r["r_multiple"], -1)

    def test_high_touching_stop_is_stop_not_target(self):
        # Conservative fill: when both levels are exactly touched intrabar
        # (low == stop and high == target), the stop fill is assumed.
        bars = [{"open": 100, "high": 102, "low": 99, "close": 102, "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 102, slippage_bps=0)
        self.assertEqual(r["exit_reason"], "STOP")
        self.assertEqual(r["r_multiple"], -1)

    def test_target_hit_exits_with_r_of_cap(self):
        bars = [
            {"open": 100, "high": 102.5, "low": 99.6, "close": 102.3, "session": 1},
            {"open": 101, "high": 102.4, "low": 100.6, "close": 102.1, "session": 2},
        ]
        r = bt.simulate_trade(None, bars, 100, 99, 102, slippage_bps=0)
        self.assertEqual(r["exit_reason"], "TARGET")
        self.assertAlmostEqual(r["exit_price"], 102)
        self.assertAlmostEqual(r["r_multiple"], 2)
        self.assertEqual(r["holding_sessions"], 1)

    def test_no_touch_exits_at_third_session_close(self):
        bars = [
            {"open": 100, "high": 100.3, "low": 99.4, "close": 99.8, "session": 1},
            {"open": 99.9, "high": 100.4, "low": 99.2, "close": 100.0, "session": 2},
            {"open": 100.0, "high": 100.5, "low": 99.1, "close": 99.6, "session": 3},
        ]
        r = bt.simulate_trade(None, bars, 100, 99, 103, slippage_bps=0)
        self.assertEqual(r["exit_reason"], "TIME")
        self.assertAlmostEqual(r["exit_price"], 99.6)
        self.assertAlmostEqual(r["r_multiple"], -0.4)
        self.assertEqual(r["holding_sessions"], 3)

    def test_never_inspects_bars_beyond_sessions_cap(self):
        # Entry session bar alone caps at 1 session: later bars never seen.
        bars = [
            {"open": 100, "high": 100.2, "low": 99.8, "close": 100.1, "session": 1},
            {"open": 90, "high": 91, "low": 89, "close": 90, "session": 2},
        ]
        r = bt.simulate_trade(None, bars, 100, 99, 103, max_sessions=1,
                              slippage_bps=0)
        self.assertEqual(r["exit_reason"], "TIME")
        self.assertEqual(r["holding_sessions"], 1)
        self.assertEqual(r["exit_price"], 100.1)


class TestCosts(unittest.TestCase):
    def test_default_slippage_5bps_buys_high_sells_low(self):
        bars = [{"open": 100.0, "high": 100.3, "low": 99.8, "close": 100.2,
                 "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 103)  # slippage_bps=5
        self.assertEqual(r["exit_reason"], "TIME")
        # 5 bps on the close 100.2: exit = 100.2 * 0.9995 = 100.1499.
        self.assertAlmostEqual(r["exit_price"], 100.1499, places=4)
        self.assertAlmostEqual(r["entry_price"], 100.05, places=4)

    def test_stop_fill_after_slippage_r_below_minus_one(self):
        # Low == stop 99 exactly: stop fills at 99 minus 10 bps = 98.901.
        bars = [{"open": 100, "high": 100.4, "low": 99, "close": 99.5, "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 103, slippage_bps=10)
        self.assertEqual(r["exit_reason"], "STOP")
        self.assertAlmostEqual(r["exit_price"], 98.901, places=6)
        self.assertLess(r["r_multiple"], -1)

    def test_gap_exit_price_never_below_zero(self):
        # 15000 bps = 1.5x slippage factor: 0.5 * (1 - 1.5) would go
        # negative, so the fill is floored at zero, not reported negative.
        bars = [{"open": 0.5, "high": 1, "low": 0.5, "close": 0.5, "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 103, slippage_bps=15000)
        self.assertEqual(r["exit_reason"], "STOP")
        self.assertEqual(r["exit_price"], 0)
        # (0 - 100) / 1 risk per share = -100R. Extreme slippage can push
        # the loss far past -1R; the floor only guards the price, not R.
        self.assertAlmostEqual(r["r_multiple"], -100)


class TestSimulationContract(unittest.TestCase):
    def test_result_keys(self):
        bars = [{"open": 100, "high": 103, "low": 98, "close": 102, "session": 1}]
        r = bt.simulate_trade(None, bars, 100, 99, 102, slippage_bps=0)
        self.assertEqual(
            set(r),
            {"entry_price", "exit_price", "exit_reason", "r_multiple",
             "holding_sessions", "gap_through_stop"},
        )

    def test_gap_flag_set_only_when_open_below_stop(self):
        gap = bt.simulate_trade(None, [bar(97, 98, 96, 97, 1)], 100, 99, 103,
                                slippage_bps=0)
        self.assertIs(gap["gap_through_stop"], True)
        no_gap = bt.simulate_trade(None, [bar(100, 103, 98, 102, 1)], 100, 99,
                                   102, slippage_bps=0)
        self.assertIs(no_gap["gap_through_stop"], False)

    def test_empty_or_missing_bars_fail_closed(self):
        for bad in ([], None):
            with self.subTest(bars=bad):
                r = bt.simulate_trade(None, bad, 100, 99, 102)
                self.assertEqual(r["exit_reason"], "NO_BARS")
                self.assertIsNone(r["exit_price"])
                self.assertEqual(r["holding_sessions"], 0)


class TestMetrics(unittest.TestCase):
    def test_metric_contract_and_values(self):
        trades = [
            {"exit_reason": "TARGET", "r_multiple": 2.0,
             "holding_sessions": 2, "gap_through_stop": False},
            {"exit_reason": "STOP", "r_multiple": -1.0,
             "holding_sessions": 1, "gap_through_stop": False},
            {"exit_reason": "STOP", "r_multiple": -1.0,
             "holding_sessions": 2, "gap_through_stop": False},
            {"exit_reason": "TARGET", "r_multiple": 1.5,
             "holding_sessions": 1, "gap_through_stop": False},
        ]
        m = bt.metrics(trades)
        self.assertEqual(
            set(m),
            {"n", "win_rate", "avg_win_r", "avg_loss_r", "expectancy_r",
             "profit_factor", "max_drawdown_r", "median_holding_sessions",
             "gap_stop_rate"},
        )
        self.assertEqual(m["n"], 4)
        self.assertEqual(m["win_rate"], 0.5)
        self.assertEqual(m["expectancy_r"], 0.375)
        self.assertEqual(m["avg_win_r"], 1.75)
        self.assertEqual(m["avg_loss_r"], -1.0)
        self.assertEqual(m["profit_factor"], 3.5 / 2.0)
        self.assertAlmostEqual(m["median_holding_sessions"], 1.5)
        self.assertAlmostEqual(m["gap_stop_rate"], 0.0)

    def test_max_drawdown_in_r_units_finite(self):
        trades = [
            {"r_multiple": 2.0, "holding_sessions": 1, "gap_through_stop": False},
            {"r_multiple": -1.0, "holding_sessions": 1, "gap_through_stop": False},
            {"r_multiple": -1.0, "holding_sessions": 1, "gap_through_stop": False},
            {"r_multiple": 1.5, "holding_sessions": 1, "gap_through_stop": False},
        ]
        m = bt.metrics(trades)
        self.assertAlmostEqual(m["max_drawdown_r"], 2.0)

    def test_gap_stop_rate_counts_gap_exits(self):
        trades = [
            {"exit_reason": "STOP", "r_multiple": -2.0,
             "holding_sessions": 1, "gap_through_stop": True},
            {"exit_reason": "STOP", "r_multiple": -1.0,
             "holding_sessions": 1, "gap_through_stop": False},
            {"exit_reason": "TARGET", "r_multiple": 1.0,
             "holding_sessions": 1, "gap_through_stop": False},
        ]
        m = bt.metrics(trades)
        self.assertAlmostEqual(m["gap_stop_rate"], 1 / 3)

    def test_all_losses_profit_factor_zero(self):
        trades = [
            {"exit_reason": "STOP", "r_multiple": -1.0,
             "holding_sessions": 1, "gap_through_stop": False},
            {"exit_reason": "TIME", "r_multiple": -0.5,
             "holding_sessions": 3, "gap_through_stop": False},
        ]
        m = bt.metrics(trades)
        self.assertEqual(m["win_rate"], 0.0)
        self.assertEqual(m["profit_factor"], 0.0)

    def test_all_wins_profit_factor_infinite(self):
        trades = [
            {"exit_reason": "TARGET", "r_multiple": 1.0,
             "holding_sessions": 1, "gap_through_stop": False},
            {"exit_reason": "TARGET", "r_multiple": 2.0,
             "holding_sessions": 1, "gap_through_stop": False},
        ]
        m = bt.metrics(trades)
        self.assertEqual(m["profit_factor"], float("inf"))

    def test_breakeven_ignored_in_win_loss_ratios(self):
        trades = [
            {"exit_reason": "TARGET", "r_multiple": 1.0,
             "holding_sessions": 1, "gap_through_stop": False},
            {"exit_reason": "STOP", "r_multiple": -1.0,
             "holding_sessions": 1, "gap_through_stop": False},
            {"exit_reason": "TIME", "r_multiple": 0.0,
             "holding_sessions": 3, "gap_through_stop": False},
        ]
        m = bt.metrics(trades)
        self.assertEqual(m["n"], 3)
        self.assertEqual(m["win_rate"], 1 / 3)
        self.assertEqual(m["expectancy_r"], 0.0)
        self.assertEqual(m["avg_win_r"], 1.0)
        self.assertEqual(m["avg_loss_r"], -1.0)

    def test_drawdown_tracked_on_equity_curve_not_cumulative_r(self):
        # +1 then -1: cumulative-R never negative, but equity goes 1 -> 0,
        # so drawdown is 1R. Naive min(cumsum, 0) would report 0.
        trades = [
            {"r_multiple": 1.0, "holding_sessions": 1, "gap_through_stop": False},
            {"r_multiple": -1.0, "holding_sessions": 1, "gap_through_stop": False},
        ]
        m = bt.metrics(trades)
        self.assertAlmostEqual(m["max_drawdown_r"], 1.0)

    def test_drawdown_peak_to_trough_after_recovery(self):
        # +1, -2, +1.5, -1: peak 1.0 at t1, trough -1.0 at t2 (DD 2.0),
        # recovery to 0.5, then trough -0.5 (DD 1.5). Max stays 2.0.
        trades = [
            {"r_multiple": 1.0, "holding_sessions": 1, "gap_through_stop": False},
            {"r_multiple": -2.0, "holding_sessions": 1, "gap_through_stop": False},
            {"r_multiple": 1.5, "holding_sessions": 1, "gap_through_stop": False},
            {"r_multiple": -1.0, "holding_sessions": 1, "gap_through_stop": False},
        ]
        m = bt.metrics(trades)
        self.assertAlmostEqual(m["max_drawdown_r"], 2.0)

    def test_single_trade_drawdown_zero_when_never_negative(self):
        trades = [{"r_multiple": 0.5, "holding_sessions": 1,
                   "gap_through_stop": False}]
        m = bt.metrics(trades)
        self.assertEqual(m["n"], 1)
        self.assertAlmostEqual(m["max_drawdown_r"], 0.0)

    def test_empty_trades_metrics_fail_closed(self):
        m = bt.metrics([])
        self.assertEqual(m["n"], 0)
        for key in ("win_rate", "avg_win_r", "avg_loss_r", "expectancy_r",
                    "profit_factor", "max_drawdown_r",
                    "median_holding_sessions", "gap_stop_rate"):
            self.assertIsNone(m[key], key)


class TestWalkForward(unittest.TestCase):
    def test_walk_forward_outputs_trade_lists_per_fold(self):
        def evaluator(train, test):
            # An evaluator sees the folds; returning one dummy trade per
            # tested sample is the documented protocol.
            return [
                {"exit_reason": "TIME", "r_multiple": 0.1,
                 "holding_sessions": 3, "gap_through_stop": False}
                for _ in test
            ]

        samples = [{"sample": i} for i in range(10)]
        folds = bt.walk_forward(samples, train_sessions=3, test_sessions=2,
                                evaluator=evaluator)
        # Anchored expanding window: train grows by the previous test span,
        # so folds are (0:3, 3:5), (0:5, 5:7), (0:7, 7:9).
        self.assertEqual(len(folds), 3)
        self.assertEqual([len(fold["train"]) for fold in folds], [3, 5, 7])
        self.assertEqual([len(fold["test"]) for fold in folds], [2, 2, 2])
        self.assertEqual(len(folds[0]["trades"]), 2)
        self.assertEqual(len(folds[2]["trades"]), 2)

    def test_walk_forward_slides_only_when_enough_for_next_fold(self):
        def evaluator(train, test):
            return [{"exit_reason": "TIME", "r_multiple": 0.0,
                     "holding_sessions": 1, "gap_through_stop": False}
                    for _ in test]

        samples = list(range(8))
        folds = bt.walk_forward(samples, train_sessions=3, test_sessions=2,
                                evaluator=evaluator)
        # Folds: (0:3, 3:5), (0:5, 5:7); a third fold would need test 7:9,
        # past the last sample, so two folds only.
        self.assertEqual(len(folds), 2)
        self.assertEqual([len(f["train"]) for f in folds], [3, 5])
        self.assertEqual([len(f["test"]) for f in folds], [2, 2])

    def test_evaluator_never_sees_test_data_in_train_fold(self):
        seen = []

        def evaluator(train, test):
            seen.append((list(train), list(test)))
            return []

        bt.walk_forward(list(range(6)), train_sessions=2, test_sessions=1,
                        evaluator=evaluator)
        # Anchored expanding window: each fold's train grows by the previous
        # test span; the next test window starts where the last one ended.
        self.assertEqual(seen[0], ([0, 1], [2]))
        self.assertEqual(seen[1], ([0, 1, 2], [3]))
        self.assertEqual(seen[2], ([0, 1, 2, 3], [4]))
        self.assertEqual(seen[3], ([0, 1, 2, 3, 4], [5]))

    def test_requires_evaluator_callable(self):
        with self.assertRaises(TypeError):
            bt.walk_forward(list(range(6)), 2, 1, evaluator=None)


class TestSplitHoldout(unittest.TestCase):
    def test_split_preserves_order_and_splits_by_count(self):
        samples = list(range(10))
        train, holdout = bt.split_holdout(samples, holdout_fraction=0.20)
        self.assertEqual(holdout, [8, 9])
        self.assertEqual(train, list(range(8)))
        self.assertEqual(sorted(train + holdout), samples)

    def test_holdout_never_empty_for_small_sets(self):
        # A 2-sample set still splits 1/1; 0- and 1-sample sets cannot be
        # split at all and raise rather than silently returning an empty
        # holdout (which would read as a clean pass).
        train, holdout = bt.split_holdout(list(range(2)),
                                          holdout_fraction=0.20)
        self.assertEqual(train, [0])
        self.assertEqual(holdout, [1])
        for size in (0, 1):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    bt.split_holdout(list(range(size)), holdout_fraction=0.20)

    def test_min_holdout_one_overrides_fraction(self):
        train, holdout = bt.split_holdout(list(range(5)), holdout_fraction=0.01)
        self.assertEqual(len(holdout), 1)

    def test_all_but_one_when_fraction_large(self):
        train, holdout = bt.split_holdout(list(range(5)), holdout_fraction=0.99)
        self.assertEqual(len(holdout), 4)
        self.assertEqual(len(train), 1)

    def test_large_fraction_keeps_one_training_sample(self):
        train, holdout = bt.split_holdout(list(range(10)),
                                          holdout_fraction=0.9)
        self.assertEqual(len(holdout), 9)
        self.assertEqual(train, [0])
        self.assertEqual(holdout, list(range(1, 10)))

    def test_rejects_bad_fraction(self):
        for bad in (-0.1, 1.5, float("nan")):
            with self.subTest(fraction=bad):
                with self.assertRaises(ValueError):
                    bt.split_holdout(list(range(10)), holdout_fraction=bad)

    def test_returns_new_lists(self):
        samples = [{"i": 1}, {"i": 2}]
        train, holdout = bt.split_holdout(samples)
        holdout[0]["i"] = 99  # shared dicts mutate, but the lists are fresh
        self.assertIsNot(train, samples)
        self.assertIsNot(holdout, samples)
        self.assertEqual(len(samples), 2)


if __name__ == "__main__":
    unittest.main()
