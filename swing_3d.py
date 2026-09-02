import math


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
