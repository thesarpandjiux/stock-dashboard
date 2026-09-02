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
    except (TypeError, ValueError):
        return {"eligible": False, "reason": "INVALID_LEVELS"}
    try:
        capital, risk_budget, min_value, max_value = map(
            float, (capital, risk_budget, min_value, max_value)
        )
    except (TypeError, ValueError):
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
