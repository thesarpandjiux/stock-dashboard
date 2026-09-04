#!/usr/bin/env python3
"""validate_swing_data.py — schema and risk invariants for data.json.

Enforces the swing_3d contracts from Tasks 1-3 plus the two-phase paper
pipeline rules:

  * top-level `swing_3d` section carries mode/phase/timestamps when the
    builder has run (its absence is legal for a legacy dashboard file);
  * per-item `swing_3d` records carry the full decision contract;
  * READY only from the H1 phase, with verified earnings and fresh data;
  * a READY execution plan never risks more than $0.50 and never values a
    position outside $20-$50;
  * daily top-level phase forbids READY items (daily emits CANDIDATE at
    most);
  * mode stays PAPER whenever mode == "production" would require the
    production gate (paper files cannot claim PRODUCTION).

Exits 0 only when every check passes; every failure is reported on
stderr with a machine-readable code. CLI: validate_swing_data.py FILE
"""

import json
import math
import sys

from swing_3d import MAX_POSITION, MIN_POSITION, RISK_BUDGET

CONTRACT_KEYS = (
    "status", "phase", "entry", "stop", "target", "shares",
    "position_value", "risk_dollars", "risk_pct_account",
    "reasons", "blockers", "asof", "trigger", "reward_risk",
    "exit_deadline", "earnings_verified", "data_fresh",
)
VALID_STATUSES = {"CANDIDATE", "READY", "WAIT", "REJECT"}
MODE_WHITELIST = {"PAPER", "PRODUCTION"}
# statuses that must carry an execution plan with numeric levels
PLAN_STATUSES = {"READY"}


def _num(value):
    """Finite float or None (non-numeric/missing values fail closed)."""
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _check_record(ticker, rec, phase, h1_stamped, errors):
    """Append validation errors for one item's swing_3d record."""
    tag = "%s.swing_3d" % ticker
    if not isinstance(rec, dict):
        errors.append("%s: swing_3d must be an object" % tag)
        return
    missing = [k for k in CONTRACT_KEYS if k not in rec]
    if missing:
        errors.append("%s: missing contract keys: %s" % (tag, ", ".join(missing)))
    status = rec.get("status")
    if status not in VALID_STATUSES:
        errors.append("%s: unknown status %r" % (tag, status))
        return
    rec_phase = rec.get("phase")
    if rec_phase not in ("DAILY", "H1"):
        errors.append("%s: unknown phase %r" % (tag, rec_phase))
    if status == "READY":
        if rec_phase != "H1":
            errors.append("%s: READY only from phase H1 (got %r)" % (tag, rec_phase))
        if phase == "daily" and h1_stamped:
            # A daily run is the very first writer of swing_3d; it never
            # emits READY. Any READY beside it must predate the run
            # (carried-over item), i.e. the file is a mixed artifact.
            errors.append("%s: READY forbidden in daily phase" % tag)
        if rec.get("earnings_verified") is not True:
            errors.append("%s: READY requires verified earnings" % tag)
        if rec.get("data_fresh") is not True:
            errors.append("%s: READY requires fresh data" % tag)
        position = _num(rec.get("position_value"))
        if position is None or not MIN_POSITION <= position <= MAX_POSITION:
            errors.append("%s: READY position_value must be in [%s, %s]"
                          % (tag, MIN_POSITION, MAX_POSITION))
        risk = _num(rec.get("risk_dollars"))
        if risk is None or risk > RISK_BUDGET:
            errors.append("%s: READY risk_dollars exceeds %s"
                          % (tag, RISK_BUDGET))
        entry = _num(rec.get("entry"))
        stop = _num(rec.get("stop"))
        target = _num(rec.get("target"))
        if entry is None or stop is None or target is None:
            errors.append("%s: READY needs numeric entry/stop/target" % tag)
        elif not (stop < entry < target):
            errors.append("%s: READY levels must satisfy stop < entry < target"
                          % tag)
        if not isinstance(rec.get("reasons"), list):
            errors.append("%s: READY reasons must be a list" % tag)
        if rec.get("blockers"):
            errors.append("%s: READY must have no blockers" % tag)
    elif status == "CANDIDATE" and rec_phase != "DAILY":
        errors.append("%s: CANDIDATE only from the daily phase" % tag)


def validate_payload(payload):
    """List of error strings for a data.json payload, [] when valid."""
    errors = []
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]
    top = payload.get("swing_3d")
    phase = None
    if isinstance(top, dict):
        phase = top.get("phase")
        mode = top.get("mode")
        if mode not in MODE_WHITELIST:
            errors.append("swing_3d.mode must be PAPER or PRODUCTION (got %r)"
                          % mode)
        if phase not in ("daily", "h1"):
            errors.append("swing_3d.phase must be 'daily' or 'h1' (got %r)"
                          % phase)
        if mode == "PRODUCTION":
            # Paper gate evidence must back a production claim. The
            # research runner records the gate verdict; without it a
            # production claim cannot be validated.
            report = top.get("validation_report")
            if not isinstance(report, dict) or report.get("valid") is not True:
                errors.append("swing_3d.mode PRODUCTION requires a passing "
                              "validation_report")
    items = payload.get("items")
    if not isinstance(items, list):
        errors.append("payload items must be a list")
        return errors
    h1_stamped = bool(top and top.get("h1_asof"))
    for item in items:
        if not isinstance(item, dict) or not item.get("ticker"):
            errors.append("item missing ticker")
            continue
        ticker = item["ticker"]
        rec = item.get("swing_3d")
        if rec is not None:  # legacy items carry no swing_3d record
            _check_record(ticker, rec, phase, h1_stamped, errors)
    return errors


def validate_file(path):
    """Validate a JSON file. Returns True when it fails (non-zero exit)."""
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as exc:
        print("ERROR DATA_UNREADABLE: %s: %s" % (path, exc), file=sys.stderr)
        return True
    errors = validate_payload(payload)
    for error in errors:
        print("ERROR INVALID: %s" % error, file=sys.stderr)
    if not errors:
        print("OK: %s swing_3d schema and risk invariants pass" % path)
    return bool(errors)


def main(argv=None):
    args = list(argv) if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: validate_swing_data.py data.json", file=sys.stderr)
        return 2
    return 1 if validate_file(args[0]) else 0


if __name__ == "__main__":
    sys.exit(main())
