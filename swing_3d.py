import math
from datetime import datetime, timedelta


ACCOUNT_CAPITAL = 1000.0
RISK_BUDGET = 0.50
MIN_POSITION = 20.0
MAX_POSITION = 50.0
VALID_STATUSES = {"CANDIDATE", "READY", "WAIT", "REJECT"}


def position_plan(
    entry,
    stop,
    capital=ACCOUNT_CAPITAL,
    risk_budget=RISK_BUDGET,
    min_value=MIN_POSITION,
    max_value=MAX_POSITION,
):
    try:
        entry, stop = map(float, (entry, stop))
    except (TypeError, ValueError, OverflowError):
        return {"eligible": False, "reason": "INVALID_LEVELS"}
    try:
        capital, risk_budget, min_value, max_value = map(
            float, (capital, risk_budget, min_value, max_value)
        )
    except (TypeError, ValueError, OverflowError):
        return {"eligible": False, "reason": "INVALID_CONFIGURATION"}

    if (
        not all(map(math.isfinite, (entry, stop)))
        or entry <= 0
        or stop <= 0
        or stop >= entry
    ):
        return {
            "eligible": False,
            "reason": "INVALID_LEVELS",
            "entry": entry,
            "stop": stop,
        }
    configuration = (capital, risk_budget, min_value, max_value)
    if (
        not all(map(math.isfinite, configuration))
        or any(value <= 0 for value in configuration)
        or min_value > max_value
    ):
        return {
            "eligible": False,
            "reason": "INVALID_CONFIGURATION",
            "entry": entry,
            "stop": stop,
        }
    risk_per_share = entry - stop
    shares = min(risk_budget / risk_per_share, max_value / entry)
    value = shares * entry
    risk = shares * risk_per_share
    eligible = value + 1e-9 >= min_value
    return {
        "eligible": eligible,
        "reason": None if eligible else "POSITION_BELOW_MIN",
        "entry": entry,
        "stop": stop,
        "shares": round(shares, 6),
        "position_value": round(value, 2),
        "risk_dollars": round(risk, 2),
        "risk_pct_account": round(risk / capital * 100, 4),
    }


def reject(reason, phase, asof=None):
    return {
        "status": "REJECT",
        "phase": phase,
        "entry": None,
        "stop": None,
        "target": None,
        "shares": None,
        "position_value": None,
        "risk_dollars": None,
        "risk_pct_account": None,
        "reasons": [],
        "blockers": [reason],
        "asof": asof,
    }


# Research hypotheses, not optimized parameters: minimum average dollar
# volume for liquid daily entries, and max distance from EMA20 in ATR units
# beyond which a long is chasing the move.
RESEARCH_MIN_DOLLAR_VOLUME = 20_000_000
RESEARCH_MAX_EXTENSION_ATR = 2.0
RESEARCH_MIN_DAILY_BARS = 60
RESEARCH_EARNINGS_HOLD_BUFFER_DAYS = 3


def _ema_series(values, span):
    alpha = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def daily_features(highs, lows, closes, volumes, spy_closes, sector_closes=None):
    """Causal features from completed daily bars only (oldest first).

    Every indicator uses only the supplied bars and their own past values;
    no network, no clock, no future data. Last bar is the most recent
    completed session.
    """
    n = len(closes)
    if not (n == len(highs) == len(lows) == len(volumes) == len(spy_closes)):
        raise ValueError("daily arrays must have equal length")
    if n < RESEARCH_MIN_DAILY_BARS:
        raise ValueError(
            "need at least %d daily bars, got %d" % (RESEARCH_MIN_DAILY_BARS, n)
        )
    if sector_closes is not None and len(sector_closes) != n:
        raise ValueError("sector_closes must match daily bar count")

    price = closes[-1]
    ema10 = _ema_series(closes, 10)[-1]
    ema20 = _ema_series(closes, 20)[-1]
    sma50 = sum(closes[-50:]) / 50
    ema20_now = _ema_series(closes, 20)
    sma50_series = [
        sum(closes[i - 49 : i + 1]) / 50 for i in range(49, n)
    ]
    span = 5  # slope lookback in trading days
    ema20_slope = (ema20_now[-1] - ema20_now[-1 - span]) / span
    sma50_slope = (sma50_series[-1] - sma50_series[-1 - span]) / span

    def pct_change_5d(series):
        return (series[-1] / series[-1 - span] - 1) * 100

    rs_spy_5d = pct_change_5d(closes) - pct_change_5d(spy_closes)

    atr14 = 14
    trs = []
    for i in range(1, n):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    atr = sum(trs[-atr14:]) / atr14
    atr_pct = atr / price * 100
    extension_atr = (price - ema20) / atr

    features = {
        "bars": n,
        "price": price,
        "ema10": ema10,
        "ema20": ema20,
        "sma50": sma50,
        "ema20_slope": ema20_slope,
        "sma50_slope": sma50_slope,
        "rs_spy_5d": rs_spy_5d,
        "atr": atr,
        "atr_pct": atr_pct,
        "extension_atr": extension_atr,
        "dollar_volume": price * volumes[-1],
        "resistance": next(
            (
                prior
                for prior in reversed(highs[:-1])
                if prior > price
            ),
            None,
        ),
    }
    if sector_closes is not None:
        features["rs_sector_5d"] = pct_change_5d(closes) - pct_change_5d(
            sector_closes
        )
    return features


# Research hypotheses, not optimized parameters: minimum first-hour relative
# volume, minimum structural reward per unit risk, and stop buffer under
# structure in daily ATR units.
RESEARCH_MIN_H1_RELATIVE_VOLUME = 1.3
RESEARCH_MIN_STRUCTURAL_RR = 1.5
RESEARCH_STOP_BUFFER_ATR = 0.10
RESEARCH_BREAKOUT_LOOKBACK_BARS = 5
RESEARCH_MAX_TARGET_R = 2.0  # cap on reward when no resistance caps it sooner


def _parse_iso(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string, got %r" % (value,))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("not an ISO-8601 timestamp: %r" % (value,))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must carry a timezone offset: %r" % (value,))
    return parsed


def _nth_trading_day_close(start_iso, session_number):
    """Close time (15:50) of the `session_number`-th trading session, where
    the session containing start_iso counts as session 1; weekdays only.

    A position entered during session 1 on its H1 close may be held through
    the close of session 3, so the deadline is the close of the session
    `session_number - 1` sessions later.

    ponytail: weekday-only calendar; real exchange holidays (and early
    closes) shift the true third-day deadline. Replace with a holiday
    calendar before production.
    """
    start = _parse_iso(start_iso)
    day = start.date()
    remaining = session_number - 1
    while remaining > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return datetime.combine(day, start.timetz().replace(hour=15, minute=50)).isoformat()


def h1_features(opens, highs, lows, closes, volumes, vwaps,
                expected_first_hour_volume, asofs):
    """Causal features from completed first-hour bars only (oldest first).

    Last bar is the most recent completed first hour. breakout_5d compares
    the last close only against highs of the prior bars inside the research
    lookback window; bars before that window are ignored, so an old spike
    cannot manufacture a breakout. No network, no clock, no future bars.
    """
    arrays = (opens, highs, lows, closes, volumes, vwaps, asofs)
    lengths = {len(a) for a in arrays}
    if len(lengths) != 1 or 0 in lengths:
        raise ValueError("h1 arrays must be equal length and non-empty")
    try:
        expected_first_hour_volume = float(expected_first_hour_volume)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("expected_first_hour_volume must be numeric")
    if not math.isfinite(expected_first_hour_volume) or expected_first_hour_volume <= 0:
        raise ValueError("expected_first_hour_volume must be positive")

    close, high, low, vwap = closes[-1], highs[-1], lows[-1], vwaps[-1]
    lookback = RESEARCH_BREAKOUT_LOOKBACK_BARS
    prior_highs = highs[-1 - lookback:-1]
    features = {
        "bars": len(closes),
        "open": opens[-1],
        "high": high,
        "low": low,
        "close": close,
        "vwap": vwap,
        "volume": volumes[-1],
        "asof": asofs[-1],
        "relative_volume": volumes[-1] / expected_first_hour_volume,
        "bullish_close": close > opens[-1],
        # Higher low: prior bar's low is the nearest completed structure.
        "higher_low": len(lows) >= 2 and low > lows[-2],
        "breakout_5d": len(prior_highs) > 0 and close > max(prior_highs),
    }
    return features


def _wait(reason, phase, asof=None):
    out = reject(reason, phase, asof)
    out["status"] = "WAIT"
    return out


def _confirm_gate(candidate, h1, daily, now_iso):
    """Status before structural sizing: WAIT/REJECT or None to proceed.

    Pure decision over supplied inputs. now_iso is the caller's clock read;
    an H1 bar stamped after it cannot have been completed yet.
    """
    now = _parse_iso(now_iso)
    if not h1.get("fresh"):
        return reject("STALE_H1_DATA", "H1", h1.get("asof"))
    if not h1.get("complete"):
        return _wait("H1_INCOMPLETE", "H1", h1.get("asof"))
    try:
        h1_asof = _parse_iso(h1["asof"])
    except ValueError:
        return _wait("H1_ASOF_UNPARSEABLE", "H1", h1.get("asof"))
    if h1_asof > now:
        # A completed bar stamped after "now" is a look-ahead; fail closed.
        return _wait("H1_ASOF_IN_FUTURE", "H1", h1.get("asof"))
    if not daily.get("data_fresh", True):
        return reject("STALE_DAILY_DATA", "H1", daily.get("asof"))
    if not daily.get("earnings_verified", True):
        return _wait("EARNINGS_UNVERIFIED", "H1", daily.get("asof"))
    earnings_days = daily.get("earnings_in_trading_days")
    if earnings_days is not None and earnings_days <= RESEARCH_EARNINGS_HOLD_BUFFER_DAYS:
        return reject("EARNINGS_WITHIN_HOLD", "H1", daily.get("asof"))
    return None


def _finalize(result, h1, daily):
    """Attach the confirmation contract fields to any terminal result.

    WAIT/REJECT carry the daily verification status (already True whenever
    the candidate reached the sizing stage); a produced plan keeps True.
    """
    result.setdefault("trigger", None)
    result.setdefault("reward_risk", None)
    result.setdefault("exit_deadline", None)
    result.setdefault("asof", h1.get("asof"))
    result.setdefault("earnings_verified", h1.get("earnings_verified", False))
    result["data_fresh"] = bool(h1.get("fresh")) and bool(h1.get("data_fresh", True))
    return result


def confirm_h1(candidate, h1, daily, now_iso):
    """Confirm a daily CANDIDATE on the completed first-hour bar and size it.

    Entry is the H1 close (only known after the bar completes). Structural
    stop is min(daily trigger low, H1 low) minus a fixed ATR buffer, taken
    before sizing; an entry gap away from the stop never moves it upward.
    Target is min(prior daily resistance, entry + 2 * risk_per_share).
    READY carries the bounded execution plan; anything short of every hard
    gate, trigger, and risk check stays WAIT or REJECT. Malformed numeric
    inputs fail closed with an explicit REJECT, never a crash.
    """
    if candidate.get("status") != "CANDIDATE":
        out = _finalize(dict(candidate), h1, daily)
        out.setdefault("phase", "H1")
        return out
    gate = _confirm_gate(candidate, h1, daily, now_iso)
    if gate is not None:
        h1 = dict(h1)
        h1["data_fresh"] = bool(h1.get("fresh")) and bool(daily.get("data_fresh", True))
        h1["earnings_verified"] = daily.get("earnings_verified", False)
        return _finalize(gate, h1, daily)
    # Past the gate: earnings verified and data fresh were required to get
    # here, so every downstream WAIT/REJECT/READY reports them as such.
    h1 = dict(h1)
    h1["earnings_verified"] = True
    h1["data_fresh"] = True

    try:
        close = float(h1["close"])
        vwap = float(h1["vwap"])
        relative_volume = float(h1["relative_volume"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return _finalize(reject("INVALID_H1_INPUT", "H1", h1.get("asof")), h1, daily)
    if close <= vwap:
        return _finalize(_wait("BELOW_SESSION_VWAP", "H1", h1.get("asof")), h1, daily)
    if relative_volume < RESEARCH_MIN_H1_RELATIVE_VOLUME:
        return _finalize(_wait("LOW_FIRST_HOUR_VOLUME", "H1", h1.get("asof")), h1, daily)
    bullish = h1.get("bullish_close")
    if bullish is None:  # hand-built h1 dicts may omit it; derive from bars
        try:
            bullish = close > float(h1["open"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return _finalize(
                reject("INVALID_H1_INPUT", "H1", h1.get("asof")), h1, daily
            )
    if not bullish:
        return _finalize(_wait("BEARISH_FIRST_HOUR", "H1", h1.get("asof")), h1, daily)
    breakout = bool(h1.get("breakout_5d"))
    pullback = bool(h1.get("higher_low"))
    if not (breakout or pullback):
        return _finalize(_wait("NO_H1_TRIGGER", "H1", h1.get("asof")), h1, daily)
    trigger = "BREAKOUT_5D" if breakout else "HIGHER_LOW_PULLBACK"

    try:
        entry = close
        stop = min(float(daily["trigger_low"]), float(h1["low"])) \
            - RESEARCH_STOP_BUFFER_ATR * float(daily["atr"])
        risk_per_share = entry - stop
        resistance = daily.get("resistance")
    except (KeyError, TypeError, ValueError, OverflowError):
        return _finalize(
            reject("INVALID_STRUCTURAL_LEVELS", "H1", h1.get("asof")), h1, daily
        )
    if risk_per_share <= 0:
        return _finalize(
            reject("INVALID_STRUCTURAL_LEVELS", "H1", h1.get("asof")), h1, daily
        )
    plan = position_plan(entry, stop)
    if not plan["eligible"]:
        out = reject(plan["reason"], "H1", h1.get("asof"))
        out["entry"], out["stop"] = entry, stop
        return _finalize(out, h1, daily)
    target = entry + RESEARCH_MAX_TARGET_R * risk_per_share
    if resistance is not None:
        target = min(float(resistance), target)
    reward_risk = (target - entry) / risk_per_share
    if reward_risk < RESEARCH_MIN_STRUCTURAL_RR:
        return _finalize(
            reject("INSUFFICIENT_STRUCTURAL_RR", "H1", h1.get("asof")), h1, daily
        )

    plan.update({
        "status": "READY",
        "phase": "H1",
        "target": round(target, 4),
        "reasons": ["H1_CONFIRMED", trigger],
        "blockers": [],
        "trigger": trigger,
        "reward_risk": round(reward_risk, 4),
        "exit_deadline": _nth_trading_day_close(h1["asof"], 3),
        "asof": h1.get("asof"),
    })
    return _finalize(plan, h1, daily)


def evaluate_daily(features, earnings_verified, earnings_in_trading_days, data_fresh):
    """Gate a 1-3 day long from supplied daily features.

    Pure decision over given inputs only. Daily phase may reach CANDIDATE
    at most; READY is emitted by later intraday/confirmation stages.
    """
    if not data_fresh:
        return reject("STALE_DAILY_DATA", "DAILY", features.get("asof"))
    if features.get("bars", 0) < RESEARCH_MIN_DAILY_BARS:
        return reject("INSUFFICIENT_DAILY_BARS", "DAILY", features.get("asof"))
    if not earnings_verified:
        out = reject("EARNINGS_UNVERIFIED", "DAILY", features.get("asof"))
        out["status"] = "WAIT"
        return out
    if (
        earnings_in_trading_days is not None
        and earnings_in_trading_days <= RESEARCH_EARNINGS_HOLD_BUFFER_DAYS
    ):
        return reject("EARNINGS_WITHIN_HOLD", "DAILY", features.get("asof"))
    if features["price"] <= features["sma50"] or features["sma50_slope"] <= 0:
        return reject("DAILY_TREND_DOWN", "DAILY", features.get("asof"))
    if features["rs_spy_5d"] <= 0:
        return reject("NEGATIVE_RELATIVE_STRENGTH", "DAILY", features.get("asof"))
    if features["dollar_volume"] < RESEARCH_MIN_DOLLAR_VOLUME:
        return reject("LOW_LIQUIDITY", "DAILY", features.get("asof"))
    if features["extension_atr"] > RESEARCH_MAX_EXTENSION_ATR:
        out = reject("EXTENDED_FROM_EMA20", "DAILY", features.get("asof"))
        out["status"] = "WAIT"
        return out
    return {
        "status": "CANDIDATE",
        "phase": "DAILY",
        "features": features,
        "reasons": ["DAILY_REGIME_UP", "RS_OUTPERFORMS_SPY"],
        "blockers": [],
        "asof": features.get("asof"),
    }
