"""Conservative event simulator and research harness for 1-3 day longs.

Every function here is a pure decision over the bars it is handed. The
simulator never inspects a bar after the session it decides on, never
peeks at bars beyond the holding cap, and breaks same-bar ambiguity in
favor of the loss (stop before target). R multiples are gross of
commission; costs enter as a fixed per-share slippage on entry and exit
fills. The harness walks a sample series forward in time (anchored,
expanding training window) and splits an untouched chronological holdout,
so research can quantify a hypothesis before any production claim.

ponytail: the position cap ($50 budget, $0.50 risk) is not enforced here;
the plan sizes positions at signal time (swing_3d.position_plan) and this
harness evaluates setups only after that plan exists. Wire commission in
per share when a broker fee model is chosen.
"""

import math
import statistics


def _bps_factor(slippage_bps):
    try:
        slippage_bps = float(slippage_bps)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("slippage_bps must be numeric")
    if not math.isfinite(slippage_bps) or slippage_bps < 0:
        raise ValueError("slippage_bps must be finite and non-negative")
    return slippage_bps / 10_000


def _numeric(value, name):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("%s must be numeric" % name)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("%s must be finite and positive" % name)
    return value


def simulate_trade(entry_bar, future_bars, entry, stop, target,
                   max_sessions=3, slippage_bps=5):
    """Simulate one long swing trade bar by bar, conservatively.

    entry_bar is the completed trigger candle; the fill is modeled on the
    session that follows it (session 1 of the holding period) or, when no
    future bar exists, at the entry level itself. future_bars are the
    completed daily bars in chronological order that cover the holding
    window; only sessions 1..max_sessions are ever inspected.

    Conservative rules, in priority order per bar:
      * open gaps at or below the stop -> filled at the open, slippage on
        top, immediately (gap_through_stop True);
      * otherwise stop and target compete intrabar and the stop wins
        (a bar touching both settles as the stop);
      * otherwise the position is carried to the next session;
      * a position still open when the supplied window runs out exits at
        the close of its last carried session (TIME).

    entry_bar is informational (the completed trigger candle) and is not
    inspected; the runner numbers future_bars 1..max_sessions starting at
    the first post-trigger session. With no usable future bar the trade
    never fills and fails closed as NO_BARS (exit_price None) rather than
    guessing a fill.

    Bars are dicts with float-like "open", "high", "low", "close" and an
    int "session" (1 = first holding session). Entry/stop/target are
    absolute prices known at signal time. Slippage is paid on both the
    entry and exit fills in basis points of the modeled fill price. R
    multiples are gross of commission.

    Result: {"entry_price", "exit_price", "exit_reason", "r_multiple",
    "holding_sessions", "gap_through_stop"} with exit_reason STOP, TARGET,
    TIME, or NO_BARS (fail-closed: nothing to trade against). exit_price
    and r_multiple are None on NO_BARS.
    """
    entry = _numeric(entry, "entry")
    stop = _numeric(stop, "stop")
    target = _numeric(target, "target")
    try:
        max_sessions = int(max_sessions)
    except (TypeError, ValueError):
        raise ValueError("max_sessions must be an int")
    if max_sessions < 1:
        raise ValueError("max_sessions must be at least 1")
    slip = _bps_factor(slippage_bps)
    if not (0 < stop < entry < target):
        raise ValueError("need 0 < stop < entry < target")

    def fails(bar):
        try:
            return tuple(float(bar[k]) for k in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    entry_price = entry * (1 + slip)
    # Only bars inside the holding window can move the position. Bars that
    # are malformed (unparseable levels, high < low) are skipped; a fully
    # unmanageable series fails closed as NO_BARS rather than guessing.
    managed = []
    for bar in future_bars or []:
        ohlc = fails(bar)
        if ohlc is None:
            continue
        try:
            session = int(bar["session"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 1 <= session <= max_sessions:
            continue
        open_, high, low, close = ohlc
        if not (high >= low >= 0):
            continue
        managed.append((open_, high, low, close))
    if not managed:
        return {
            "entry_price": round(entry_price, 6),
            "exit_price": None,
            "exit_reason": "NO_BARS",
            "r_multiple": None,
            "holding_sessions": 0,
            "gap_through_stop": False,
        }

    seen = 0
    for open_, high, low, close in managed:
        seen += 1
        if open_ <= stop:
            # Open gaps at or through the stop: settle at the actual open,
            # slippage on top, never below zero.
            exit_price = max(0.0, open_ * (1 - slip))
            return {
                "entry_price": round(entry_price, 6),
                "exit_price": round(exit_price, 6),
                "exit_reason": "STOP",
                "r_multiple": round((exit_price - entry) / (entry - stop), 6),
                "holding_sessions": seen,
                "gap_through_stop": True,
            }
        # Stop checked before target: same-bar ambiguity resolves to the
        # loss. Touching either level counts as filling it.
        if low <= stop:
            exit_price = max(0.0, stop * (1 - slip))
            return {
                "entry_price": round(entry_price, 6),
                "exit_price": round(exit_price, 6),
                "exit_reason": "STOP",
                "r_multiple": round((exit_price - entry) / (entry - stop), 6),
                "holding_sessions": seen,
                "gap_through_stop": False,
            }
        if high >= target:
            exit_price = target * (1 - slip)
            return {
                "entry_price": round(entry_price, 6),
                "exit_price": round(exit_price, 6),
                "exit_reason": "TARGET",
                "r_multiple": round((exit_price - entry) / (entry - stop), 6),
                "holding_sessions": seen,
                "gap_through_stop": False,
            }
    # No exit inside the window: the position is sold at the close of the
    # last session it was carried into (managed is non-empty here).
    exit_price = max(0.0, managed[-1][3] * (1 - slip))
    return {
        "entry_price": round(entry_price, 6),
        "exit_price": round(exit_price, 6),
        "exit_reason": "TIME",
        "r_multiple": round((exit_price - entry) / (entry - stop), 6),
        "holding_sessions": seen,
        "gap_through_stop": False,
    }


def metrics(trades):
    """Summarize a list of trade result dicts in R units.

    Returns n, win_rate, avg_win_r, avg_loss_r, expectancy_r, profit_factor,
    max_drawdown_r, median_holding_sessions, gap_stop_rate. Drawdown is
    measured on the cumulative equity curve (1R per trade) as the largest
    peak-to-trough drop in R; it stays zero while the curve never dips
    below its running high. All ratios are None when there is no trade in
    the denominator; empty input yields all-None metrics with n == 0.
    Trades may omit exit_reason/holding_sessions/gap_through_stop; only
    r_multiple is required.
    """
    if not trades:
        return {key: None for key in (
            "n", "win_rate", "avg_win_r", "avg_loss_r", "expectancy_r",
            "profit_factor", "max_drawdown_r", "median_holding_sessions",
            "gap_stop_rate")} | {"n": 0}

    def r_of(trade):
        try:
            r = float(trade["r_multiple"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("every trade needs a numeric r_multiple")
        if not math.isfinite(r):
            raise ValueError("r_multiple must be finite")
        return r

    rs = [r_of(trade) for trade in trades]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)

    drawdown = 0.0
    peak = 0.0
    equity = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)

    sessions = [
        trade["holding_sessions"] for trade in trades
        if isinstance(trade.get("holding_sessions"), (int, float))
    ]
    gaps = sum(
        1 for trade in trades if trade.get("gap_through_stop") is True
    )

    def ratio(num, den):
        if not den:
            return None
        return num / den

    return {
        "n": n,
        "win_rate": len(wins) / n,
        "avg_win_r": ratio(sum(wins), len(wins)),
        "avg_loss_r": ratio(sum(losses), len(losses)),
        "expectancy_r": sum(rs) / n,
        "profit_factor": gross_win / gross_loss if gross_loss else (
            float("inf") if gross_win else 0.0),
        "max_drawdown_r": drawdown,
        "median_holding_sessions": (
            statistics.median(sessions) if sessions else None),
        "gap_stop_rate": ratio(gaps, n),
    }


def walk_forward(samples, train_sessions, test_sessions, evaluator):
    """Fold an ordered sample series into an anchored walk-forward.

    samples must be in chronological order. The first fold trains on
    samples[:train_sessions] and tests on the next test_sessions; every
    later fold appends the previous test span to the training window
    (training data only ever grows, never slides). The window advances
    until the next test span would run past the last sample.

    evaluator(train, test) is called with the two sample sublists and must
    return the list of trade result dicts its hypothesis produces on test.
    Each fold dict is {"train": [...], "test": [...], "trades": [...]};
    samples are passed through by reference, never copied.
    """
    if not callable(evaluator):
        raise TypeError("evaluator must be callable(train, test)")
    if train_sessions < 1 or test_sessions < 1:
        raise ValueError("train_sessions and test_sessions must be >= 1")
    n = len(samples)
    folds = []
    train_end = train_sessions
    while train_end + test_sessions <= n:
        train = samples[:train_end]
        test = samples[train_end:train_end + test_sessions]
        folds.append({
            "train": train,
            "test": test,
            "trades": evaluator(train, test),
        })
        train_end += test_sessions
    return folds


def split_holdout(samples, holdout_fraction=0.20):
    """Chronological train/holdout split of an ordered sample series.

    The holdout is always the most recent samples: the last
    ceil(holdout_fraction * len(samples)) of the series, at least one
    sample (so an empty holdout can never be mistaken for a pass), and the
    training side keeps everything before it. Fraction must be in
    (0, 1); 1.0 would leave nothing to fit on, 0.0 would leave nothing to
    test on. Both returned lists are new.
    """
    try:
        holdout_fraction = float(holdout_fraction)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("holdout_fraction must be numeric")
    if not math.isfinite(holdout_fraction) or not (0 < holdout_fraction < 1):
        raise ValueError("holdout_fraction must be in (0, 1)")
    n = len(samples)
    if n < 2:
        raise ValueError("split_holdout needs at least 2 samples")
    k = max(1, int(math.ceil(holdout_fraction * n)))
    if k >= n:
        k = n - 1
    cut = n - k
    return list(samples[:cut]), list(samples[cut:])


# Production gate thresholds for the 1-3 day swing mode.  Conservative by
# design: a report that cannot prove every rule fails closed.  A failed
# gate means mode="PAPER"; it never triggers parameter hunting on the
# holdout.
GATE_MIN_HOLDOUT_TRADES = 30       # brief: holdout n >= 30
GATE_MAX_TICKER_POSITIVE_R = 0.35  # brief: no ticker > 35% of positive R


def _holdout_number(holdout, key):
    """Finite float of a holdout metric, else None (fail closed)."""
    try:
        value = float(holdout.get(key))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    return value


def production_gate(report):
    """Decide whether the researched 1-3 day swing mode may leave PAPER.

    report must carry the aggregated walk-forward report produced by the
    research runner (backtest_3d.metrics on the full-sample train span,
    per-fold summaries, and the untouched chronological holdout metrics).
    Only the holdout decides eligibility: the development sample is where
    parameters were chosen and cannot vouch for itself.

    Rules (all must hold):
      * holdout metrics exist with finite numerics (HOLDOUT_METRICS_MISSING);
      * holdout n >= 30 (HOLDOUT_SAMPLE_TOO_SMALL);
      * holdout expectancy after costs > 0 (HOLDOUT_EXPECTANCY_NOT_POSITIVE);
      * holdout profit factor after costs > 1 (HOLDOUT_PROFIT_FACTOR_NOT_ABOVE_ONE);
      * no single ticker contributes over 35% of gross positive holdout R
        (HOLDOUT_TICKER_CONCENTRATION);
      * no walk-forward fold failed explicitly (HIDDEN_FAILED_FOLD).

    Returns {"eligible": bool, "reasons": [str, ...]} with reasons empty on
    a pass.
    """
    reasons = []
    holdout = report.get("holdout")
    if not isinstance(holdout, dict):
        return {"eligible": False, "reasons": ["HOLDOUT_METRICS_MISSING"]}
    n = _holdout_number(holdout, "n")
    if n is None:
        return {"eligible": False, "reasons": ["HOLDOUT_METRICS_MISSING"]}
    if n < GATE_MIN_HOLDOUT_TRADES:
        # Too few trades to judge anything; the profitability rules are
        # not even consulted (no arithmetic on an empty/unrepresentative
        # sample).
        return {"eligible": False, "reasons": ["HOLDOUT_SAMPLE_TOO_SMALL"]}

    expectancy = _holdout_number(holdout, "expectancy_r")
    profit_factor = _holdout_number(holdout, "profit_factor")
    if expectancy is None:
        return {"eligible": False, "reasons": ["HOLDOUT_METRICS_MISSING"]}
    if expectancy <= 0:
        reasons.append("HOLDOUT_EXPECTANCY_NOT_POSITIVE")
    if profit_factor is None or profit_factor <= 1:
        # A report without a cost-adjusted profit factor cannot prove the
        # >1 rule; fail closed under the rule's own reason code.
        reasons.append("HOLDOUT_PROFIT_FACTOR_NOT_ABOVE_ONE")

    trades = holdout.get("trades")
    if not isinstance(trades, list):
        # Without per-trade records the 35% concentration cap cannot be
        # verified; a report that cannot prove the rule fails closed.
        reasons.append("HOLDOUT_TICKER_CONCENTRATION")
    else:
        per_ticker = {}
        total = 0.0
        for trade in trades:
            try:
                r = float(trade.get("r_multiple"))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(r) or r <= 0:
                continue
            ticker = trade.get("ticker")
            per_ticker[ticker] = per_ticker.get(ticker, 0.0) + r
            total += r
        if total > 0 and any(
            share > GATE_MAX_TICKER_POSITIVE_R
            for share in (per_ticker[t] / total for t in per_ticker)
        ):
            reasons.append("HOLDOUT_TICKER_CONCENTRATION")

    folds = report.get("folds")
    if not isinstance(folds, list):
        reasons.append("HIDDEN_FAILED_FOLD")
    elif any(fold.get("failed") for fold in folds
             if isinstance(fold, dict)):
        reasons.append("HIDDEN_FAILED_FOLD")

    return {"eligible": not reasons, "reasons": reasons}
