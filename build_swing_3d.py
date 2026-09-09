#!/usr/bin/env python3
"""build_swing_3d.py — two-phase PAPER pipeline for 1-3 day swing signals.

Phase `daily` runs after the US close: SPY and every watchlist ticker are
refreshed once and each ticker is gated by the daily regime
(swing_3d.evaluate_daily). A daily run may reach CANDIDATE at most; it
never emits READY (the H1 confirmation phase owns that).

Phase `h1` runs ~60 minutes after the open and processes ONLY the items
the daily phase left as CANDIDATE: the completed first-hour bar confirms
the setup (swing_3d.confirm_h1) into READY/WAIT/REJECT. Every other item
is carried over untouched.

The result merges into data.json: a top-level `swing_3d` section and a
per-item `swing_3d` decision record. Writes go to a temporary file, run
schema validation (validate_swing_data.validate_payload), and only then
os.replace() atomically — a corrupt or invalid artifact never lands.
All existing dashboard fields (items, macro, legacy swing) are preserved.

Honesty rules from the plan and Tasks 1-6:
  * data freshness gates are preserved: the daily evaluation happens on
    the latest completed session; bars whose last completed session
    predates it reject as STALE_DAILY_DATA;
  * earnings unverified blocks READY (WAIT at daily, never CANDIDATE);
  * READY only after an H1 confirmation on a fresh, complete bar;
  * a fetch failure keeps the prior record's values but clears READY
    (explicit REJECT DATA_FETCH_FAILED);
  * signals carry an explicit PAPER label and disclaimer (bukan
    rekomendasi; untuk monitoring/belajar);
  * mode stays PAPER whenever the research production gate has not
    passed. This task wires PAPER mode only.

Data plumbing is injectable: `run_daily_phase` / `run_h1_phase` take a
provider object with `daily_bars(ticker)`, `h1_bars(ticker)` and
`earnings(ticker)`; the CLI builds the real one from market_data and the
tests pass fakes.

ponytail: live data fetches go straight through market_data against
Yahoo. No portfolio-level simultaneous-position allocator yet (add when
more than one READY setup may be acted on concurrently).
"""

import argparse
import copy
import json
import math
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

import exchange_sessions as xs
import market_data as md
import swing_3d as s
import validate_swing_data as v
import watchlist

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(BASE_DIR, "data.json")

MIN_DAILY_BARS = 60  # swing_3d RESEARCH_MIN_DAILY_BARS
HOLD_BUFFER_DAYS = s.RESEARCH_EARNINGS_HOLD_BUFFER_DAYS
# Research production gate: mode stays PAPER unless a passed gate record
# is supplied. Task 7 wires PAPER only.
PAPER_MODE = "PAPER"
DISCLAIMER = (
    "PAPER MODE - bukan rekomendasi; untuk monitoring/belajar. "
    "Belum ada order nyata; backtest/holdout belum lolos gate produksi."
)




# --------------------------------------------------------------------------
# clock / session helpers (injectable for tests)
# --------------------------------------------------------------------------

def et_now(iso_now=None):
    """Now in America/New_York. Pass iso_now (aware) to fix the clock."""
    if iso_now is not None:
        parsed = datetime.fromisoformat(iso_now)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(md.ET)
    return datetime.now(md.ET)


def latest_session_date(now):
    """Latest completed XNYS session, including holidays and early closes."""
    return xs.latest_completed(now)

def earnings_in_trading_days(earnings_date_iso, session_date):
    """Exchange sessions from session_date (exclusive) to earnings (inclusive).

    None when the earnings date is missing or falls before the session.
    """
    if not earnings_date_iso:
        return None
    try:
        target = date.fromisoformat(earnings_date_iso[:10])
    except ValueError:
        return None
    if target <= session_date:
        return None
    return xs.sessions_between(session_date, target)


# --------------------------------------------------------------------------
# per-ticker evaluation
# --------------------------------------------------------------------------

def bars_arrays(bars):
    """OHLCV/asof lists from a Bars object, aligned and validated."""
    if bars is None or len(bars) == 0:
        return None
    n = len(bars.closes)
    arrays = [bars.highs, bars.lows, bars.closes, bars.volumes]
    if any(len(a) != n for a in arrays):
        return None
    if bars.asofs and len(bars.asofs) != n:
        return None
    return {
        "opens": list(bars.opens) if bars.opens else [],
        "highs": bars.highs, "lows": bars.lows, "closes": bars.closes,
        "volumes": bars.volumes,
        "asofs": list(bars.asofs) if bars.asofs else [],
    }


def eval_daily_record(ticker, bars, spy_bars, session, earnings_status):
    """One ticker's swing_3d record for the daily phase (pure).

    Returns a dict with status CANDIDATE/WAIT/REJECT plus the full
    decision contract. A READY from an earlier run is never produced here
    — the caller preserves or clears it.
    """
    session_iso = xs.session_close(session).isoformat()
    arrays = bars_arrays(bars)
    spy_arrays = bars_arrays(spy_bars)
    if arrays is None or spy_arrays is None:
        out = s.reject("DATA_FETCH_FAILED", "DAILY")
        out["asof"] = session_iso
        return _pad_contract(out)
    if arrays["asofs"] and str(arrays["asofs"][-1])[:10] != session.isoformat():
        out = s.reject("STALE_DAILY_DATA", "DAILY", arrays["asofs"][-1])
        out["data_fresh"] = False
        return _pad_contract(out)
    # The SPY benchmark must be equally fresh: a stale SPY series truncates
    # the alignment below and would price CANDIDATE off misaligned sessions.
    if spy_arrays["asofs"] and str(spy_arrays["asofs"][-1])[:10] != session.isoformat():
        out = s.reject("STALE_DAILY_DATA", "DAILY", spy_arrays["asofs"][-1])
        out["data_fresh"] = False
        return _pad_contract(out)
    closes = arrays["closes"]
    if len(closes) < MIN_DAILY_BARS:
        out = s.reject("INSUFFICIENT_DAILY_BARS", "DAILY", session_iso)
        return _pad_contract(out)
    # Align the ticker's bars to the SPY benchmark's trading days so
    # relative-strength compares the same sessions.
    spy_dates = [str(a)[:10] for a in spy_arrays["asofs"]]
    spy_close_by_date = dict(zip(spy_dates, spy_arrays["closes"]))
    aligned_spy = []
    own = []
    for i, a in enumerate(arrays["asofs"]):
        spy_c = spy_close_by_date.get(str(a)[:10])
        if spy_c is not None:
            aligned_spy.append(spy_c)
            own.append(i)
    if len(own) < MIN_DAILY_BARS:
        out = s.reject("INSUFFICIENT_DAILY_BARS", "DAILY", session_iso)
        return _pad_contract(out)
    features = s.daily_features(
        [arrays["highs"][i] for i in own],
        [arrays["lows"][i] for i in own],
        [closes[i] for i in own],
        [arrays["volumes"][i] for i in own],
        aligned_spy,
    )
    earnings_verified = bool(
        earnings_status and earnings_status.get("verified"))
    earnings_date = (earnings_status or {}).get("date")
    in_days = (earnings_in_trading_days(earnings_date, session)
               if earnings_verified else None)
    decision = s.evaluate_daily(
        features, earnings_verified, in_days, data_fresh=True)
    rec = _contract_from(decision, features, earnings_verified)
    if rec.get("asof") is None:
        # daily_features carries no asof; the signal session's close is
        # the decision timestamp.
        rec["asof"] = session_iso
    return rec


def _contract_from(decision, features, earnings_verified=False):
    """Normalize a swing_3d decision into the full record contract."""
    rec = dict(decision)
    for key in ("entry", "stop", "target", "shares", "position_value",
                "risk_dollars", "risk_pct_account", "trigger",
                "reward_risk", "exit_deadline"):
        rec.setdefault(key, None)
    rec.setdefault("reasons", [])
    rec.setdefault("blockers", [])
    rec.setdefault("earnings_verified", earnings_verified)
    rec["data_fresh"] = bool(decision.get("data_fresh", True))
    rec["asof"] = decision.get("asof")
    rec["features"] = features
    return rec


def next_session_date(day):
    """Next exchange session strictly after day."""
    return xs.next_session(day)

def eval_h1_record(candidate, bars, session):
    """H1 confirmation record for one daily CANDIDATE (pure).

    bars: this ticker's 60m bars; only completed 09:30-10:30 first-hour
    bars may be used. `session` is the session the candidate enters: the
    first trading day after the daily signal (its asof). A candidate
    whose asof is not the immediately previous trading session of
    `session` is stale and can never confirm.
    """
    cand_asof = candidate.get("asof")
    cand_day = str(cand_asof)[:10] if cand_asof else None
    if not cand_day or cand_day != _prev_trading_day_iso(session):
        return _stale(candidate, cand_asof)
    arrays = bars_arrays(bars)
    if arrays is None:
        # No H1 bars at all: the confirmation cannot run. Preserve the
        # candidate's values but never READY.
        out = s.reject("DATA_FETCH_FAILED", "H1", cand_asof)
        return _preserve(out, candidate)
    asofs = arrays["asofs"]
    # keep bars whose calendar day <= session and hour == 09:30 ET
    indices = []
    for i, a in enumerate(asofs):
        try:
            parsed = xs.aware(str(a))
        except (ValueError, TypeError):
            continue
        if not xs.is_session(parsed.date()) or parsed != xs.session_open(parsed.date()):
            continue
        if parsed.date().isoformat() > session.isoformat():
            continue
        indices.append(i)
    if not indices:
        out = s.reject("DATA_FETCH_FAILED", "H1", cand_asof)
        return _preserve(out, candidate)
    h1_close_day = xs.aware(asofs[indices[-1]]).date().isoformat()
    if h1_close_day != session.isoformat():
        # The newest completed first-hour bar is not from the session the
        # candidate enters; today's H1 is incomplete or missing.
        out = s.reject("STALE_H1_DATA", "H1", asofs[indices[-1]])
        return _preserve(out, candidate)
    opens = [arrays["opens"][i] for i in indices] if arrays.get("opens") else []
    highs = [arrays["highs"][i] for i in indices]
    lows = [arrays["lows"][i] for i in indices]
    closes = [arrays["closes"][i] for i in indices]
    volumes = [arrays["volumes"][i] for i in indices]
    h1_asofs = [asofs[i] for i in indices]
    if not opens or len(opens) != len(highs):
        out = s.reject("DATA_FETCH_FAILED", "H1", cand_asof)
        return _preserve(out, candidate)
    vwaps = [(o + h + l + c) / 4 for o, h, l, c in
             zip(opens, highs, lows, closes)]
    # Expected first-hour volume: mean of the prior sessions' first-hour
    # volumes (the h1 phase only has H1 bars), matching the research
    # runner's per-session expected-volume profile. The entry session
    # itself is excluded so the bar under test never sets its own bar.
    prior_volumes = volumes[:-1] if len(volumes) > 1 else []
    if not prior_volumes:
        # Only the entry session's own first-hour bar exists: no prior
        # session profile to build an expected-volume baseline from.
        # h1_features would raise on expected_first_hour=None, so reject
        # fail-closed instead of crashing the phase.
        out = s.reject("INSUFFICIENT_H1_HISTORY", "H1", cand_asof)
        return _preserve(out, candidate)
    expected_first_hour = sum(prior_volumes) / len(prior_volumes)
    features = s.h1_features(opens, highs, lows, closes, volumes, vwaps,
                             expected_first_hour, h1_asofs)
    daily_ctx = {
        "atr": candidate["features"].get("atr"),
        "resistance": candidate["features"].get("resistance"),
        "trigger_low": candidate.get("trigger_low"),
    }
    h1_ctx = dict(features)
    h1_ctx["complete"] = True
    h1_ctx["fresh"] = True
    h1_ctx["data_fresh"] = True
    h1_ctx["earnings_verified"] = candidate.get("earnings_verified", False)
    decision = s.confirm_h1(candidate, h1_ctx, daily_ctx,
                            (xs.session_open(session) + timedelta(minutes=61)).isoformat())
    rec = dict(decision)
    for key in ("entry", "stop", "target", "shares", "position_value",
                "risk_dollars", "risk_pct_account"):
        rec.setdefault(key, None)
    rec["asof"] = decision.get("asof") or h1_asofs[-1]
    rec["data_fresh"] = bool(decision.get("data_fresh", True))
    rec["earnings_verified"] = bool(decision.get("earnings_verified", False))
    return rec


def _now_for(session):
    """A safe decision clock: 10:31 ET on the candidate's entry session."""
    return (xs.session_open(session) + timedelta(minutes=61)).isoformat()


def _prev_trading_day_iso(session):
    return xs.previous_session(session).isoformat()

def _preserve(out, candidate):
    """Keep the candidate's execution values but force status off READY."""
    for key in ("entry", "stop", "target", "shares", "position_value",
                "risk_dollars", "risk_pct_account", "trigger",
                "reward_risk", "exit_deadline"):
        if key in candidate and candidate[key] is not None:
            out[key] = candidate[key]
    out["features"] = candidate.get("features")
    out["data_fresh"] = False
    return out


def _stale(candidate, cand_asof):
    """Downgrade a candidate from a prior session (never confirms)."""
    out = s.reject("STALE_CANDIDATE_DATA", "H1", cand_asof)
    for key in ("entry", "stop", "target", "shares", "position_value",
                "risk_dollars", "risk_pct_account", "trigger",
                "reward_risk", "exit_deadline"):
        if key in candidate and candidate[key] is not None:
            out[key] = candidate[key]
    out["features"] = candidate.get("features")
    out["data_fresh"] = False
    return out


# --------------------------------------------------------------------------
# phases over the watchlist
# --------------------------------------------------------------------------

def run_daily_phase(now_iso, tickers, provider, prior=None, session=None):
    """Evaluate every ticker's daily regime for the latest session.

    provider must expose daily_bars(ticker), earnings(ticker) and
    h1_bars(ticker); prior maps ticker -> previous swing_3d record.
    Returns (records, session_date). A prior READY that cannot be
    re-evaluated (fetch failure) is preserved in values but never keeps
    its READY status.
    """
    now = et_now(now_iso)
    session = session or latest_session_date(now)
    prior = prior or {}
    spy_bars = provider.daily_bars("SPY")
    records = {}
    for ticker in tickers:
        bars = provider.daily_bars(ticker)
        earnings = provider.earnings(ticker)
        old = prior.get(ticker) or {}
        if bars is None:
            rec = s.reject("DATA_FETCH_FAILED", "DAILY",
                           xs.session_close(session).isoformat())
            _preserve_levels(rec, old)
            rec["data_fresh"] = False
            records[ticker] = _pad_contract(rec)
            continue
        rec = eval_daily_record(ticker, bars, spy_bars, session, earnings)
        if rec["status"] == "CANDIDATE":
            rec["trigger_low"] = _trigger_low(bars, session)
        records[ticker] = rec
    return records, session


def _preserve_levels(rec, old):
    for key in ("entry", "stop", "target", "shares", "position_value",
                "risk_dollars", "risk_pct_account", "trigger",
                "reward_risk", "exit_deadline", "features"):
        if old.get(key) is not None:
            rec[key] = old[key]


def _trigger_low(bars, session):
    """Low of the signal session (the latest completed daily bar)."""
    arrays = bars_arrays(bars)
    if arrays is None or not arrays["lows"]:
        return None
    return arrays["lows"][-1]


def _pad_contract(rec):
    """Guarantee the full decision-contract keys on any record.

    Raw reject/_wait dicts from swing_3d carry only status/phase/blockers;
    every record that lands in data.json must expose the complete contract
    so validate_swing_data passes on REJECT/WAIT paths too.
    """
    for key in ("trigger", "reward_risk", "exit_deadline"):
        rec.setdefault(key, None)
    rec.setdefault("earnings_verified", False)
    rec.setdefault("data_fresh", True)
    return rec


def run_h1_phase(now_iso, provider, prior):
    """Confirm the daily CANDIDATE records on their completed H1 bar.

    Each candidate's entry session is the first trading day after its
    daily signal asof. Only records with status CANDIDATE are processed;
    everything else is carried over untouched (identity preserved).
    Returns (records, h1_asofs) where h1_asofs maps ticker -> newest H1
    bar asof for the records that were confirmed.
    """
    now = et_now(now_iso)
    records = {}
    h1_asofs = {}
    for ticker, old in (prior or {}).items():
        if old.get("status") != "CANDIDATE":
            records[ticker] = old
            continue
        cand_asof = old.get("asof")
        try:
            cand_day = date.fromisoformat(str(cand_asof)[:10])
        except (TypeError, ValueError):
            cand_day = None
        if cand_day is None:
            records[ticker] = _pad_contract(_stale(old, cand_asof))
            continue
        session = next_session_date(cand_day)
        if now.date() > session:
            # The candidate's confirmation session (the first trading day
            # after its signal) is already behind us: the entry window
            # passed unconfirmed, so the candidate is stale.
            records[ticker] = _pad_contract(_stale(old, cand_asof))
            continue
        entry_open = xs.session_open(session) + timedelta(minutes=61)
        if now < entry_open:
            # The first hour of the entry session has not completed yet;
            # the candidate is simply not processable. Carry it over
            # untouched so a later run can still confirm it to READY.
            records[ticker] = old
            continue
        bars = provider.h1_bars(ticker)
        if bars is None:
            rec = s.reject("DATA_FETCH_FAILED", "H1", cand_asof)
            _preserve_levels(rec, old)
            rec["data_fresh"] = False
            records[ticker] = _pad_contract(rec)
            continue
        rec = eval_h1_record(old, bars, session)
        records[ticker] = _pad_contract(rec)
        if rec.get("asof"):
            h1_asofs[ticker] = rec["asof"]
    return records, h1_asofs


# --------------------------------------------------------------------------
# merge + commit
# --------------------------------------------------------------------------

def resolve_mode(requested, gate):
    """PAPER unless a passed research gate backs a production request."""
    if requested != "production":
        return PAPER_MODE
    if gate and gate.get("eligible") is True and not gate.get("reasons"):
        return "PRODUCTION"
    return PAPER_MODE


def build_meta(mode, phase, daily_asof, h1_asof, generated_at):
    """Top-level swing_3d section."""
    return {
        "mode": mode,
        "phase": phase,
        "daily_asof": daily_asof,
        "h1_asof": h1_asof,
        "validation_report": {"valid": True, "errors": []},
        "disclaimer": DISCLAIMER,
        "generated_at": generated_at,
    }


def merge(payload, records, meta):
    """Merge per-ticker swing_3d records into the dashboard payload."""
    out = copy.deepcopy(payload)
    out["swing_3d"] = meta
    mode = meta.get("mode")
    by_ticker = {i.get("ticker"): i for i in out.get("items", [])
                 if isinstance(i, dict) and i.get("ticker")}
    for ticker, rec in records.items():
        item_rec = copy.deepcopy(rec)
        if mode is not None:
            item_rec.setdefault("mode", mode)
        if ticker in by_ticker:
            by_ticker[ticker]["swing_3d"] = item_rec
        else:
            out.setdefault("items", []).append(
                {"ticker": ticker, "swing_3d": item_rec})
    return out


def commit(payload, out_path):
    """Validate then atomically replace. Returns the list of errors."""
    errors = v.validate_payload(payload)
    if errors:
        return errors
    with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8",
            dir=os.path.dirname(out_path) or ".",
            prefix=os.path.basename(out_path) + ".", suffix=".tmp",
            delete=False) as f:
        tmp_name = f.name
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_name, out_path)
    return []


def load_payload(path):
    """Existing data.json payload, or an empty shell when absent/corrupt."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"items": []}


def read_validation_gate():
    """Research gate record if a report artifact exists, else None.

    ponytail: the paper pipeline ships without reading a gate artifact —
    Task 7 wires PAPER only; production wiring lands with the runner.
    """
    return None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

class LiveProvider:
    """Real market_data plumbing for the CLI runs."""
    @staticmethod
    def daily_bars(ticker):
        return md.get_daily_bars(ticker)

    @staticmethod
    def h1_bars(ticker):
        return md.get_h1_bars(ticker)

    @staticmethod
    def earnings(ticker):
        return md.get_earnings_status(ticker)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Two-phase PAPER pipeline for 1-3 day swing signals")
    parser.add_argument("--phase", choices=("daily", "h1"), required=True)
    parser.add_argument("--mode", choices=("paper", "production"),
                        default="paper")
    parser.add_argument("--out", default=OUT_FILE,
                        help="data.json path (default: repo data.json)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        reason = xs.phase_skip_reason(args.phase, now_iso)
    except (xs.CalendarUnavailable, ValueError) as exc:
        print("ERROR CALENDAR_UNAVAILABLE: %s" % exc, file=sys.stderr)
        return 2
    if reason:
        print("SKIP %s phase=%s now=%s" % (reason, args.phase, now_iso))
        return 0
    payload = load_payload(args.out)
    tickers = watchlist.resolve()
    mode = resolve_mode(args.mode, read_validation_gate())
    provider = LiveProvider()

    old_records = {
        item.get("ticker"): item.get("swing_3d")
        for item in payload.get("items", [])
        if isinstance(item, dict) and item.get("ticker")
        and isinstance(item.get("swing_3d"), dict)
    }

    if args.phase == "daily":
        records, session = run_daily_phase(now_iso, tickers, provider,
                                           prior=old_records)
        daily_asof = xs.session_close(session).isoformat()
        meta = build_meta(mode, "daily", daily_asof, None, now_iso)
        for item in payload.get("items", []):
            if isinstance(item, dict) and item.get("ticker"):
                item.pop("swing_3d", None)
        merged = merge(payload, records, meta)
    else:
        if not old_records:
            print("ERROR NO_CANDIDATES: h1 phase needs a prior daily run",
                  file=sys.stderr)
            return 2
        records, h1_asofs = run_h1_phase(now_iso, provider, old_records)
        h1_asof = (max(h1_asofs.values()) if h1_asofs else None)
        meta = build_meta(mode, "h1", _daily_asof(payload), h1_asof, now_iso)
        for item in payload.get("items", []):
            if isinstance(item, dict) and item.get("ticker"):
                item.pop("swing_3d", None)
        merged = merge(payload, records, meta)

    errors = commit(merged, args.out)
    if errors:
        for error in errors:
            print("ERROR INVALID: %s" % error, file=sys.stderr)
        return 1
    ready = [t for t, r in records.items() if r.get("status") == "READY"]
    print("data.json written — phase=%s mode=%s ready=%s" % (
        args.phase, mode, ready or "none"))
    return 0


def _daily_asof(payload):
    """Keep the previous daily asof across the h1 run."""
    top = payload.get("swing_3d")
    if isinstance(top, dict) and top.get("daily_asof"):
        return top["daily_asof"]
    return None


if __name__ == "__main__":
    sys.exit(main())
