#!/usr/bin/env python3
"""Dataset runner and production gate for the 1-3 day swing mode.

Usage:
    python3 run_research_3d.py --universe watchlist_core.txt \
        --start 2024-09-01 --end 2026-08-31 \
        --out artifacts/swing-3d-report.json

The runner downloads completed daily bars (universe + SPY benchmark) and
completed first-hour (H1) bars, rebuilds the swing_3d daily-regime +
H1-trigger signal chain strictly out of bars that were completed at each
decision moment, simulates every signal through backtest_3d.simulate_trade
(entry on the next session's H1 close, exit by the third session's close,
slippage on every fill), walks the train span forward with an anchored
expanding window (backtest_3d.walk_forward) and leaves the most recent
20% of sessions untouched as the holdout.  The production gate
(backtest_3d.production_gate) then compares the holdout against
conservative thresholds; a failed gate means mode="PAPER".

Honesty rules:
  * H1 history only reaches ~2 years back on the free provider.  When the
    requested window starts before the earliest H1 bar, the runner exits
    non-zero with INSUFFICIENT_POINT_IN_TIME_H1_DATA instead of trimming
    the window silently or synthesizing trades.  Rerun with the shorter,
    documented window.
  * A signal day's H1 confirmation happens on the next completed session
    (the bar the signal could act on), never the same session.
  * The last session of the dataset is the last completed session; a live
    partial daily bar is never used as a signal or exit bar.
  * ETF universe members (VOO/QQQ) have no earnings calendar; their rows
    carry isin=false and skip the earnings filter, which is recorded.
  * Daily bars are auto-adjusted (yfinance default): a documented
    approximation for a 1-3 day swing and recorded in the report.

Data plumbing is honest and lazy: `market_data` functions are reused for
daily bars; H1 and earnings history come straight from yfinance because
market_data exposes only the live-freshness filtered views (a documented
ponytail: move both behind market_data when a point-in-time API lands).
"""

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import backtest_3d as bt
import market_data as md
import swing_3d as s

# Daily bars needed before the first researchable signal (the features
# module hard-requires 60).
WARMUP_DAILY_BARS = 60
# Walk-forward fold geometry in trading sessions.
TRAIN_SESSIONS = 252      # ~1 year of sessions
TEST_SESSIONS = 63        # ~3 months of sessions
HOLD_SESSIONS = 3         # swing_3d hold cap
EARNINGS_BUFFER_SESSIONS = 3  # matches swing_3d RESEARCH_EARNINGS_HOLD_BUFFER_DAYS
FIRST_HOUR_VOLUME_SHARE = 0.35  # ~09:30-10:30 ET share of daily volume
FIRST_HOUR_LOOKBACK = 20        # sessions for the expected-volume profile
ETF_UNIVERSE = {"VOO", "QQQ"}   # no earnings calendar on the free provider
SLIPPAGE_BPS = 5                # CLI default; multiplicative on every fill
# ET offsets: yfinance H1 bars come back with -04:00 (EDT) / -05:00 (EST).
_ET_OFFSETS = (timedelta(hours=-4), timedelta(hours=-5))


def fail(reason, message):
    """Exit non-zero with a documented failure reason."""
    print("ERROR %s: %s" % (reason, message), file=sys.stderr)
    sys.exit(reason)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run the 1-3 day swing research window and production gate")
    parser.add_argument("--universe", required=True,
                        help="path to ticker list, one per line, # comments")
    parser.add_argument("--start", required=True,
                        help="research window start YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True,
                        help="research window end YYYY-MM-DD (inclusive)")
    parser.add_argument("--out", required=True,
                        help="path to write the JSON report (artifacts/...)")
    parser.add_argument("--slippage-bps", type=float, default=SLIPPAGE_BPS,
                        help="one-way slippage in basis points (default 5)")
    parser.add_argument("--holdout-fraction", type=float, default=0.2,
                        help="most-recent fraction of sessions kept untouched "
                             "(default 0.2)")
    args = parser.parse_args(argv)
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError:
        parser.error("--start/--end must be YYYY-MM-DD dates")
    if start >= end:
        parser.error("--start must be before --end")
    args.start_date = start
    args.end_date = end
    if not (0 < args.holdout_fraction < 1):
        parser.error("--holdout-fraction must be in (0, 1)")
    return args


def read_universe(path):
    tickers = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tickers.append(line)
    if not tickers:
        fail("EMPTY_UNIVERSE", "no tickers in %s" % path)
    return tickers


def align_series(daily, benchmark):
    """Pair (key, value) series on common keys, preserving order.

    Used to align a ticker's daily bars with SPY's so relative-strength
    features compare the same trading days.  Keys may be dates or strings;
    they are matched on their string form.
    """
    bench = {str(key): value for key, value in benchmark}
    return (
        [(key, value) for key, value in daily if str(key) in bench],
        [(key, bench[str(key)]) for key, value in daily
         if str(key) in bench],
    )


def _parse_day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _sessions_between(day, anchor):
    """Trading sessions from `day` up to `anchor` (weekdays only).

    ponytail: no exchange-holiday calendar; weekday counting is the
    documented ceiling already used by swing_3d and market_data.
    """
    n = 0
    cursor = day
    while cursor < anchor:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            n += 1
    return n


def build_dataset(ticker, daily, spy, earnings_dates, universe):
    """Chronological researchable daily rows for one ticker.

    daily: list of row dicts {"day", "open", "high", "low", "close",
    "volume"}; spy: list of {"day", "close"} rows.  earnings_dates: ISO
    date strings, empty for ETFs.  A row is researchable only when at
    least EARNINGS_BUFFER_SESSIONS+1 sessions remain until the next
    earnings date (a position held up to 3 sessions is never carried
    across earnings) and the day carries a completed daily bar with a
    matched benchmark bar.

    Rows carry the daily OHLCV, benchmark close, ISO "day", "isin"
    (True = earnings calendar exists and was applied) and "ticker".
    """
    if not daily or not spy:
        return []
    is_etf = ticker in universe
    spy_by_day = {
        str(r["day"]): r.get("close") for r in spy if isinstance(r, dict)
    }
    earnings = {date.fromisoformat(d) for d in earnings_dates}
    rows = []
    for r in sorted(daily, key=lambda x: str(x["day"])):
        day = _parse_day(r["day"])
        close = r.get("close")
        benchmark_close = spy_by_day.get(str(day))
        if close is None or benchmark_close is None:
            continue
        if not is_etf and earnings:
            # Signal sessions must leave >= EARNINGS_BUFFER_SESSIONS+1
            # sessions to the next earnings date so the 3-session hold
            # never straddles it (matches swing_3d's <= 3 rejection,
            # applied point-in-time).
            blocked = False
            for e in sorted(e for e in earnings if e >= day):
                if _sessions_between(day, e) <= EARNINGS_BUFFER_SESSIONS:
                    blocked = True
                break
            if blocked:
                continue
        rows.append({
            "ticker": ticker,
            "day": day.isoformat(),
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": close,
            "volume": r.get("volume", 0),
            "benchmark_close": benchmark_close,
            "isin": not is_etf,
        })
    return rows


def trim_to_h1_coverage(rows, h1_first_day):
    """Drop rows whose session predates the first available H1 bar.

    A daily signal on such a session could never have been confirmed by a
    completed H1 close, so the session is not researchable.
    """
    return [row for row in rows if row["day"] >= h1_first_day]


def epoch_seconds(iso_stamp):
    """UTC epoch seconds of an ISO-8601 timestamp (any offset)."""
    parsed = datetime.fromisoformat(iso_stamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def weekday_close_time(day):
    """Session close timestamp (15:50 ET) for a day, ET offset attached."""
    return "%sT15:50:00-04:00" % day


def align_h1_to_daily(h1, daily_rows):
    """Enrich daily rows with their same-session first-hour bar.

    h1 rows are (iso_stamp, open, high, low, close, volume) tuples; only
    the bar stamped 09:30 ET (09:30-10:30, the completed first hour) of a
    session is used.  daily_rows are the runner's researchable rows.  The
    output keeps only sessions that carry both a daily row and an H1 bar,
    in chronological order, with the H1 fields attached and the daily
    fields preserved.
    """
    by_day = {}
    for stamp, open_, high, low, close, volume in h1:
        try:
            parsed = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if parsed.hour != 9 or parsed.minute != 30:
            continue  # not the 09:30-10:30 first-hour bar
        if parsed.tzinfo is None or parsed.utcoffset() not in _ET_OFFSETS:
            continue  # only ET-stamped bars map to ET session dates
        day = parsed.date().isoformat()
        if day in by_day:
            continue  # duplicate bar for the session: keep the first
        by_day[day] = (open_, high, low, close, volume)
    out = []
    for row in daily_rows:
        h1_row = by_day.get(row["day"])
        if not h1_row:
            continue
        open_, high, low, close, volume = h1_row
        merged = dict(row)
        merged.update({
            "session_date": row["day"],
            "h1_open": open_,
            "h1_high": high,
            "h1_low": low,
            "h1_close": close,
            "h1_volume": volume,
        })
        out.append(merged)
    return out


def expected_first_hour_volume(daily_volumes, lookback=FIRST_HOUR_LOOKBACK):
    """Expected first-hour volume: mean daily volume over the trailing
    `lookback` sessions times the first-hour share of a typical session.
    """
    window = daily_volumes[-lookback:]
    if not window:
        return None
    return float(sum(window) / len(window)) * FIRST_HOUR_VOLUME_SHARE


def _h1_bar_list(rows):
    """Ordered list of (stamp, o, h, l, c, v) first-hour bars for a
    ticker's enriched rows (only rows carrying an H1 bar)."""
    return [
        ("%sT09:30:00-04:00" % row["day"], row["h1_open"], row["h1_high"],
         row["h1_low"], row["h1_close"], row["h1_volume"])
        for row in rows if row.get("h1_close") is not None
    ]


def _daily_features_at(rows, index):
    """swing_3d daily features from the 60 completed bars ending at
    rows[index].  Strictly causal: nothing after the signal day is read.
    Returns None when fewer than WARMUP_DAILY_BARS are available.
    """
    hist = rows[max(0, index - WARMUP_DAILY_BARS + 1):index + 1]
    if len(hist) < WARMUP_DAILY_BARS:
        return None
    return s.daily_features(
        [r["high"] for r in hist],
        [r["low"] for r in hist],
        [r["close"] for r in hist],
        [r["volume"] or 0 for r in hist],
        [r["benchmark_close"] for r in hist],
    )


def _h1_context(rows, index):
    """H1 features over the completed first-hour bars up to rows[index],
    plus the freshness/verification flags the confirmation gate demands.
    rows[index] must carry an H1 bar (it is the entry session).
    """
    hist = [r for r in rows[:index + 1] if r.get("h1_close") is not None]
    bars = _h1_bar_list(hist)
    opens = [b[1] for b in bars]
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    closes = [b[4] for b in bars]
    volumes = [b[5] or 0 for b in bars]
    asofs = [b[0] for b in bars]
    vwaps = [(o + h + l + c) / 4 for o, h, l, c in
             zip(opens, highs, lows, closes)]
    ctx = s.h1_features(opens, highs, lows, closes, volumes, vwaps,
                        expected_first_hour_volume(
                            [r["volume"] or 0 for r in hist]), asofs)
    ctx.update({
        "fresh": True,
        "complete": True,
        "data_fresh": True,
        "earnings_verified": True,
        "asof": asofs[-1] if asofs else None,
    })
    return ctx


def simulate_series(rows, slippage_bps=SLIPPAGE_BPS):
    """Simulate every signal over a ticker's enriched rows.

    rows are chronological daily rows (warmup + researchable); each row
    that carries an H1 bar can act as the entry session for a signal
    emitted on the previous session's daily close.  Returns
    (trades, tally, errors):
      trades: list of result dicts, one per filled signal, each with
              "day" (signal day), "ticker", "r_multiple", "exit_reason",
              "trigger", "entry", "stop", "target", "holding_sessions";
      tally:  signal outcome counts per status for the coverage report;
      errors: per-session messages for data anomalies (never fatal).
    """
    ticker = rows[0].get("ticker", "UNKNOWN")
    trades = []
    tally = {}
    errors = []
    for i in range(len(rows) - 1):
        signal_row = rows[i]
        entry_row = rows[i + 1]
        if entry_row.get("h1_close") is None:
            continue  # no completed H1 bar: the signal cannot fill
        features = _daily_features_at(rows, i)
        if features is None:
            tally["INSUFFICIENT_HISTORY"] = tally.get(
                "INSUFFICIENT_HISTORY", 0) + 1
            continue
        daily_ctx = {
            "atr": features["atr"],
            "resistance": features["resistance"],
            "trigger_low": signal_row["low"],
            "data_fresh": True,
            "earnings_verified": True,
            "earnings_in_trading_days": None,
        }
        candidate = s.evaluate_daily(features, True, None, True)
        if candidate["status"] != "CANDIDATE":
            tally[candidate["status"]] = tally.get(candidate["status"], 0) + 1
            continue
        tally["CANDIDATE"] = tally.get("CANDIDATE", 0) + 1
        h1_ctx = _h1_context(rows, i + 1)
        now_iso = "%sT10:31:00-04:00" % entry_row["day"]
        decision = s.confirm_h1(candidate, h1_ctx, daily_ctx, now_iso)
        if decision["status"] != "READY":
            for blocker in decision.get("blockers") or []:
                tally[blocker] = tally.get(blocker, 0) + 1
            continue
        entry = decision["entry"]
        stop = decision["stop"]
        target = decision["target"]
        future = []
        for j, offset in enumerate((1, 2, 3), start=1):
            if i + offset >= len(rows):
                break
            bar = rows[i + offset]
            future.append({"open": bar["open"], "high": bar["high"],
                           "low": bar["low"], "close": bar["close"],
                           "session": j})
        if not future:
            continue  # entry session would be the last bar: no hold data
        result = bt.simulate_trade(None, future, entry, stop, target,
                                   max_sessions=HOLD_SESSIONS,
                                   slippage_bps=slippage_bps)
        if result["r_multiple"] is None:
            errors.append("no-fill signal on %s" % signal_row["day"])
            continue
        result.update({
            "ticker": ticker,
            "day": signal_row["day"],
            "entry_session": entry_row["day"],
            "trigger": decision.get("trigger"),
        })
        trades.append(result)
    return trades, tally, errors


def walk_forward_block(rows, h1_first_day, train_sessions=TRAIN_SESSIONS,
                       test_sessions=TEST_SESSIONS, holdout_fraction=0.2,
                       slippage_bps=SLIPPAGE_BPS, train_days=None,
                       test_days=None):
    """Walk-forward folds + untouched holdout for one ticker's rows.

    rows are the ticker's full chronological daily rows (warmup history
    included); h1_first_day is the first session with a completed H1 bar
    (the trimmed window starts there).  Signals are simulated once over
    the whole trimmed window (simulate_series), then bucketed by their
    signal session into anchored walk-forward folds on the development
    span and the chronological holdout tail.  Returns
    {"ticker", "h1_first_day", "folds": [...], "holdout": {metrics...},
     "trades": all trades, "tally": ..., "errors": [...]} or exits
    non-zero with INSUFFICIENT_POINT_IN_TIME_H1_DATA when the window
    cannot be researched honestly.

    train_days/test_days are accepted as aliases of train_sessions/
    test_sessions (the fold geometry is measured in trading sessions;
    "days" is the research shorthand).
    """
    if train_days is not None:
        train_sessions = train_days
    if test_days is not None:
        test_sessions = test_days
    ticker = rows[0].get("ticker", "UNKNOWN")
    trimmed = trim_to_h1_coverage(rows, h1_first_day)
    if len(trimmed) < 2:
        fail("INSUFFICIENT_POINT_IN_TIME_H1_DATA",
             "%s: no researchable session at/after H1 start %s" % (
                 ticker, h1_first_day))
    trades, tally, errors = simulate_series(trimmed, slippage_bps)
    trades_by_day = {}
    for trade in trades:
        trades_by_day.setdefault(trade["day"], []).append(trade)

    train_rows, holdout_rows = bt.split_holdout(trimmed, holdout_fraction)
    if len(train_rows) < train_sessions + test_sessions:
        fail("INSUFFICIENT_POINT_IN_TIME_H1_DATA",
             "%s: %d development sessions cannot host one %d+%d fold" % (
                 ticker, len(train_rows), train_sessions, test_sessions))

    def evaluator(train, test):
        test_days = {row["day"] for row in test}
        out = []
        for day in test_days:
            out.extend(trades_by_day.get(day, []))
        return out

    folds = bt.walk_forward(train_rows, train_sessions, test_sessions,
                            evaluator)
    holdout_days = {row["day"] for row in holdout_rows}
    holdout_trades = []
    for day in holdout_days:
        holdout_trades.extend(trades_by_day.get(day, []))

    def fold_summary(fold):
        fold_trades = [dict(t) for t in fold["trades"]]
        for t in fold_trades:
            t.pop("open", None)  # trades carry no bar data in summaries
        return {
            "train_from": fold["train"][0]["day"],
            "train_to": fold["train"][-1]["day"],
            "test_from": fold["test"][0]["day"],
            "test_to": fold["test"][-1]["day"],
            "metrics": bt.metrics(fold_trades),
            "trades": fold_trades,
            "failed": False,
        }

    return {
        "ticker": ticker,
        "h1_first_day": h1_first_day,
        "folds": [fold_summary(f) for f in folds],
        "holdout_metrics": bt.metrics(holdout_trades),
        "holdout_trades": holdout_trades,
        "all_trades": trades,
        "tally": tally,
        "errors": errors,
        "n_sessions": len(trimmed),
    }


def _json_safe(value):
    """JSON-safe rendering: floats inf/nan become strings."""
    if isinstance(value, float):
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        if math.isnan(value):
            return "nan"
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def write_report(report, path):
    """Write the report atomically with JSON-safe floats."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_json_safe(report), f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _fetch_daily(ticker, start_date, end_date):
    """Completed daily rows [start-9mo .. end] as (day, dict) pairs."""
    bars = md.get_daily_bars(ticker, "max")
    if bars is None or len(bars) < WARMUP_DAILY_BARS:
        return []
    rows = []
    for i in range(len(bars.closes)):
        day = _parse_day(bars.asofs[i] if i < len(bars.asofs)
                         else bars.asofs[-1])
        # Drop any bar whose session has not closed yet (yfinance can
        # include a live partial bar for today).
        if day > end_date or day < start_date - timedelta(days=270):
            continue
        rows.append((day, {
            "open": bars.opens[i] if i < len(bars.opens) else None,
            "high": bars.highs[i],
            "low": bars.lows[i],
            "close": bars.closes[i],
            "volume": bars.volumes[i] if i < len(bars.volumes) else 0,
        }))
    return rows


def _fetch_h1(ticker, end_date):
    """Completed 09:30-10:30 bars for sessions <= end_date as tuples."""
    bars = md._yahoo_bars(ticker, "2y", "60m")
    if bars is None or len(bars) < 2:
        return []
    out = []
    for i in range(len(bars.closes)):
        stamp = bars.asofs[i]
        try:
            parsed = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if parsed.hour != 9 or parsed.minute != 30:
            continue
        if _parse_day(stamp) > end_date:
            continue
        out.append((stamp,
                    bars.opens[i] if i < len(bars.opens) else None,
                    bars.highs[i], bars.lows[i], bars.closes[i],
                    bars.volumes[i] if i < len(bars.volumes) else 0))
    return out


def _fetch_earnings(ticker):
    """ISO dates of known earnings sessions (past + scheduled)."""
    try:
        import yfinance as yf  # noqa: PLC0415
        dates = yf.Ticker(ticker).earnings_dates
        if dates is None or len(dates) == 0:
            return []
        return [ts.date().isoformat() for ts in dates.index]
    except Exception:  # noqa: BLE001
        return []


def _last_completed_session():
    """Most recent fully closed session date (ET), or today when past
    close.  ponytail: weekday-only, matching market_data.is_session_complete
    without importing the live clock."""
    now = datetime.now()
    return now.date()


def main(argv=None):
    args = parse_args(argv)
    universe = read_universe(args.universe)
    end_date = args.end_date

    print("Fetching SPY benchmark...", file=sys.stderr)
    spy_rows = _fetch_daily("SPY", args.start_date, end_date)
    if not spy_rows:
        fail("INSUFFICIENT_DAILY_DATA", "SPY benchmark unavailable")
    spy_payload = [{"day": str(day), "close": row["close"]}
                   for day, row in spy_rows]

    payloads = []
    for ticker in universe:
        print("Fetching %s..." % ticker, file=sys.stderr)
        daily = _fetch_daily(ticker, args.start_date, end_date)
        if len(daily) < WARMUP_DAILY_BARS:
            payloads.append({"ticker": ticker, "error": "INSUFFICIENT_DAILY_DATA"})
            continue
        h1 = _fetch_h1(ticker, end_date)
        earnings = _fetch_earnings(ticker) if ticker not in ETF_UNIVERSE else []
        payloads.append({
            "ticker": ticker,
            "daily": daily,
            "spy": spy_payload,
            "h1": h1,
            "earnings": earnings,
        })

    h1_first_days = []
    for payload in payloads:
        if payload.get("h1"):
            h1_first_days.append(payload["h1"][0][0][:10])
    if not h1_first_days:
        fail("INSUFFICIENT_POINT_IN_TIME_H1_DATA",
             "no H1 bars for any universe member")
    research_start = max(args.start_date.isoformat(), min(h1_first_days))
    if research_start != args.start_date.isoformat():
        fail(
            "INSUFFICIENT_POINT_IN_TIME_H1_DATA",
            "requested window starts %s but the earliest free H1 bar is %s; "
            "rerun with --start >= %s (recorded in docs/swing-3d-validation.md)"
            % (args.start_date.isoformat(), min(h1_first_days),
               min(h1_first_days)),
        )

    blocks = []
    per_ticker = {}
    for payload in payloads:
        ticker = payload["ticker"]
        if "error" in payload:
            per_ticker[ticker] = {"error": payload["error"]}
            continue
        h1_first = payload["h1"][0][0][:10] if payload["h1"] else None
        rows = build_dataset(ticker, [
            {"day": str(day), **row} for day, row in payload["daily"]
        ], payload["spy"], payload["earnings"], ETF_UNIVERSE)
        if not rows:
            per_ticker[ticker] = {"error": "NO_RESEARCHABLE_DAYS"}
            continue
        h1_sessions = {row[0][:10] for row in payload["h1"]}
        enriched = align_h1_to_daily(payload["h1"],
                                     [r for r in rows if r["day"] >= research_start])
        if not enriched:
            per_ticker[ticker] = {
                "error": "INSUFFICIENT_POINT_IN_TIME_H1_DATA",
                "h1_first": h1_first,
            }
            continue
        block = walk_forward_block(
            enriched, research_start, TRAIN_SESSIONS,
            TEST_SESSIONS, args.holdout_fraction, args.slippage_bps)
        blocks.append(block)
        per_ticker[ticker] = {
            "n_sessions": block["n_sessions"],
            "signals": block["tally"],
            "trades": len(block["all_trades"]),
            "errors": block["errors"],
            "h1_first": h1_first,
            "h1_last": payload["h1"][-1][0][:10],
            "daily_first": payload["daily"][0][0].isoformat()
            if hasattr(payload["daily"][0][0], "isoformat")
            else str(payload["daily"][0][0]),
            "daily_last": str(payload["daily"][-1][0])[:10],
        }

    if not blocks:
        fail("INSUFFICIENT_POINT_IN_TIME_H1_DATA",
             "no ticker produced a researchable window")

    holdout_trades = []
    fold_summaries = []
    dev_trades = []
    for block in blocks:
        holdout_trades.extend(block["holdout_trades"])
        dev_trades.extend(
            t for fold in block["folds"] for t in fold["trades"])
        for fold in block["folds"]:
            fold_summaries.append({"ticker": block["ticker"], **fold})

    holdout_metrics = bt.metrics(holdout_trades)
    report = {
        "meta": {
            "strategy": "1-3 day swing long (daily regime + H1 trigger)",
            "universe": universe,
            "requested_window": [args.start_date.isoformat(),
                                 end_date.isoformat()],
            "research_start": research_start,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "costs": {
                "slippage_bps": args.slippage_bps,
                "model": "multiplicative slippage on every entry/exit fill",
                "commission": "not modeled (documented ponytail)",
            },
            "holdout_fraction": args.holdout_fraction,
            "geometry": {"train_sessions": TRAIN_SESSIONS,
                         "test_sessions": TEST_SESSIONS,
                         "hold_sessions": HOLD_SESSIONS},
            "data_sources": {"daily": "yahoo (auto_adjust=True)",
                             "h1": "yahoo 60m 09:30-10:30 bars, ~2y depth"},
            "notes": [
                "signal day D confirms on session D+1's completed H1 close",
                "exit bars are the daily bars of the 3 held sessions",
                "holdout is the untouched most-recent tail",
                "ETF rows (VOO/QQQ) skip the earnings filter (no calendar)",
                "VWAP approximated by the 09:30 bar's mean price",
                "daily bars auto-adjusted (split/dividend approximation)",
                "weekday-only session calendar (no holiday calendar)",
            ],
        },
        "coverage": per_ticker,
        "walk_forward": {
            "folds": fold_summaries,
            "development_metrics": bt.metrics(dev_trades),
            "n_folds": len(fold_summaries),
            "n_dev_trades": len(dev_trades),
        },
        "holdout": {
            **holdout_metrics,
            "trades": [
                {"ticker": t["ticker"], "day": t["day"], "r_multiple": t["r_multiple"],
                 "exit_reason": t["exit_reason"], "trigger": t.get("trigger")}
                for t in holdout_trades
            ],
        },
        "per_ticker_holdout": {
            block["ticker"]: bt.metrics(block["holdout_trades"])
            for block in blocks
        },
    }
    gate = bt.production_gate(report)
    report["gate"] = gate
    report["mode"] = "PRODUCTION" if gate["eligible"] else "PAPER"
    write_report(report, args.out)
    print(json.dumps(_json_safe(gate), indent=2))
    print("Report written to %s" % args.out)
    print("Mode: %s (holdout trades: %d)" % (
        report["mode"], len(holdout_trades)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
