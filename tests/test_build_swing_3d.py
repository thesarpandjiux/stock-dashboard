import copy
import datetime as dt
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

import market_data as md
import swing_3d as s

import build_swing_3d as b
import validate_swing_data as v

EDT = dt.timezone(dt.timedelta(hours=-4))


def iso(d, hh=15, mm=50):
    return "%sT%02d:%02d:00-04:00" % (d.isoformat(), hh, mm)


def weekday_dates(end, n):
    """n weekday dates ascending, ending on `end` (inclusive)."""
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def uptrend_closes():
    """80 closes on the signal day (last bar) at a window high: ramp
    60->100 over 50 bars, slow grind 100->104.2 to bar 78, then a final
    up-day to 104.75 that clears every prior high (with its wick), so
    the daily features see price 104.75 > SMA50, positive slopes, RS vs
    SPY positive, extension within 2 ATR, and resistance = the single
    old spike (108.0) far above the +2R target window."""
    closes = []
    for i in range(50):
        closes.append(round(60 + 0.8 * i, 4))
    for i in range(50, 79):
        closes.append(round(100 + 0.155 * (i - 50), 4))
    closes.append(round(104.75, 4))
    return closes


def daily_bars(end=dt.date(2026, 9, 1), n=80, closes=None):
    dates = weekday_dates(end, n)
    closes = closes if closes is not None else uptrend_closes()
    highs = [round(c + 0.4, 4) for c in closes]
    highs[30] = 108.0  # old spike: the only prior high above the close
    lows = [round(c - 0.9, 4) for c in closes]
    opens = [round(c - 0.2, 4) for c in closes]
    vols = [1_000_000] * n
    return md.Bars(
        opens=opens, highs=highs, lows=lows, closes=closes, volumes=vols,
        asofs=[iso(d, 0, 0) for d in dates], interval="1d", source="fake",
    )


def spy_bars(end=dt.date(2026, 9, 1), n=80):
    dates = weekday_dates(end, n)
    closes = [100 + i * 0.1 for i in range(n)]
    return md.Bars(
        opens=closes, highs=[c + 0.1 for c in closes],
        lows=[c - 0.1 for c in closes], closes=closes,
        volumes=[5_000_000] * n,
        asofs=[iso(d, 0, 0) for d in dates], interval="1d", source="fake",
    )


def h1_bars(today=dt.date(2026, 9, 2), prior_sessions=6):
    """09:30 ET bars for prior sessions + today's completed first hour."""
    dates = weekday_dates(today, prior_sessions + 1)
    opens, highs, lows, closes, vols, asofs = [], [], [], [], [], []
    for d in dates:
        asofs.append(iso(d, 9, 30))
        if d == today:
            # bullish breakout hour above every prior-session high
            opens.append(100.0); highs.append(101.6)
            lows.append(100.2); closes.append(101.3); vols.append(550_000.0)
        else:
            opens.append(round(100.1, 1)); highs.append(101.0)
            lows.append(99.4); closes.append(100.5); vols.append(250_000.0)
    return md.Bars(
        opens=opens, highs=highs, lows=lows, closes=closes, volumes=vols,
        asofs=asofs, interval="60m", source="fake",
    )


def earnings_far():
    return {"verified": True, "date": "2026-10-15", "source": "yahoo-calendar"}


def make_provider(daily=None, spy=None, h1=None, earnings=None,
                  fail_daily=(), h1_override=None):
    """h1_override: dict ticker -> Bars|None replacing default h1 bars."""
    def fetch_daily(tk):
        if tk in fail_daily:
            return None
        if tk == "SPY":
            return spy if spy is not None else spy_bars()
        return daily if daily is not None else daily_bars()

    def fetch_h1(tk):
        if h1_override is not None and tk in h1_override:
            return h1_override[tk]
        return h1 if h1 is not None else h1_bars()

    return SimpleNamespace(
        daily_bars=fetch_daily,
        earnings=lambda tk: earnings if earnings is not None
        else dict(earnings_far()),
        h1_bars=fetch_h1,
    )


def full_record(**overrides):
    """A normalized READY record for a ticker (contract-complete)."""
    rec = {
        "status": "READY", "phase": "H1", "entry": 101.2, "stop": 99.6,
        "target": 104.4, "shares": 0.3125, "position_value": 31.6,
        "risk_dollars": 0.5, "risk_pct_account": 0.05, "reasons": ["H1_CONFIRMED"],
        "blockers": [], "asof": iso(dt.date(2026, 9, 2), 9, 30),
        "trigger": "BREAKOUT_5D", "reward_risk": 2.0,
        "exit_deadline": iso(dt.date(2026, 9, 4)), "earnings_verified": True,
        "data_fresh": True,
    }
    rec.update(overrides)
    return rec


class TestPhaseHelpers(unittest.TestCase):
    def test_latest_session_after_close_is_today(self):
        now = iso(dt.date(2026, 9, 2), 16, 0)
        self.assertEqual(b.latest_session_date(b.et_now(now)),
                         dt.date(2026, 9, 2))

    def test_latest_session_before_close_is_previous_weekday(self):
        now = iso(dt.date(2026, 9, 2), 10, 31)  # Wed morning
        self.assertEqual(b.latest_session_date(b.et_now(now)),
                         dt.date(2026, 9, 1))

    def test_latest_session_monday_morning_is_friday(self):
        now = iso(dt.date(2026, 9, 7), 9, 0)  # Mon pre-open
        self.assertEqual(b.latest_session_date(b.et_now(now)),
                         dt.date(2026, 9, 4))

    def test_latest_session_saturday_is_friday(self):
        now = iso(dt.date(2026, 9, 5), 12, 0)  # Sat
        self.assertEqual(b.latest_session_date(b.et_now(now)),
                         dt.date(2026, 9, 4))

    def test_resolve_mode_stays_paper_when_gate_fails(self):
        self.assertEqual(b.resolve_mode("production", None), "PAPER")
        self.assertEqual(
            b.resolve_mode("production",
                           {"eligible": False, "reasons": ["X"]}), "PAPER")
        self.assertEqual(b.resolve_mode("paper", {"eligible": True}), "PAPER")
        self.assertEqual(
            b.resolve_mode("production", {"eligible": True, "reasons": []}),
            "PRODUCTION")


class TestDailyPhase(unittest.TestCase):
    def test_daily_never_emits_ready(self):
        p = make_provider()
        records, upto = b.run_daily_phase(
            iso(dt.date(2026, 9, 2), 10, 31), ["AAA"], p)
        self.assertEqual(upto, dt.date(2026, 9, 1))
        self.assertNotEqual(records["AAA"]["status"], "READY")

    def test_daily_emits_candidate_with_full_contract(self):
        p = make_provider()
        records, _ = b.run_daily_phase(
            iso(dt.date(2026, 9, 2), 10, 31), ["AAA"], p)
        rec = records["AAA"]
        self.assertEqual(rec["status"], "CANDIDATE")
        self.assertEqual(rec["phase"], "DAILY")
        for key in ("entry", "stop", "target", "shares", "position_value",
                    "risk_dollars", "risk_pct_account", "trigger",
                    "reward_risk", "exit_deadline"):
            self.assertIn(key, rec)
        self.assertIs(rec["earnings_verified"], True)
        self.assertIs(rec["data_fresh"], True)
        self.assertEqual(rec["asof"], iso(dt.date(2026, 9, 1)))
        self.assertIn("features", rec)
        self.assertAlmostEqual(rec["features"]["resistance"], 108.0)
        self.assertIn("trigger_low", rec)

    def test_failed_fetch_preserves_prior_values_but_clears_ready(self):
        prior = {"AAA": full_record()}
        p = make_provider(fail_daily=("AAA",))
        records, _ = b.run_daily_phase(
            iso(dt.date(2026, 9, 2), 10, 31), ["AAA"], p, prior)
        rec = records["AAA"]
        self.assertNotEqual(rec["status"], "READY")
        self.assertEqual(rec["status"], "REJECT")
        self.assertEqual(rec["entry"], 101.2)     # prior values preserved
        self.assertEqual(rec["stop"], 99.6)
        self.assertEqual(rec["target"], 104.4)
        self.assertIn("DATA_FETCH_FAILED", rec["blockers"])
        self.assertIs(rec["data_fresh"], False)

    def test_failed_fetch_without_prior_rejects_explicitly(self):
        p = make_provider(fail_daily=("AAA",))
        records, _ = b.run_daily_phase(
            iso(dt.date(2026, 9, 2), 10, 31), ["AAA"], p)
        rec = records["AAA"]
        self.assertEqual(rec["status"], "REJECT")
        self.assertIn("DATA_FETCH_FAILED", rec["blockers"])

    def test_stale_daily_bars_reject(self):
        # bars end 2026-08-31 but the latest completed session is 09-01
        stale = daily_bars(end=dt.date(2026, 8, 31))
        p = make_provider(daily=stale)
        records, _ = b.run_daily_phase(
            iso(dt.date(2026, 9, 2), 10, 31), ["AAA"], p)
        self.assertEqual(records["AAA"]["status"], "REJECT")
        self.assertIn("STALE_DAILY_DATA", records["AAA"]["blockers"])

    def test_stale_spy_rejects_even_when_ticker_fresh(self):
        # ticker bars end 09-01 (fresh) but SPY ends 08-27: the alignment
        # would otherwise price CANDIDATE off a mismatched session.
        stale_spy = spy_bars(end=dt.date(2026, 8, 27))
        p = make_provider(spy=stale_spy)
        records, _ = b.run_daily_phase(
            iso(dt.date(2026, 9, 2), 10, 31), ["AAA"], p)
        rec = records["AAA"]
        self.assertEqual(rec["status"], "REJECT")
        self.assertIn("STALE_DAILY_DATA", rec["blockers"])
        self.assertIs(rec["data_fresh"], False)

    def test_unverified_earnings_never_candidate(self):
        p = make_provider(earnings={"verified": False, "date": None,
                                    "source": None})
        records, _ = b.run_daily_phase(
            iso(dt.date(2026, 9, 2), 10, 31), ["AAA"], p)
        self.assertEqual(records["AAA"]["status"], "WAIT")
        self.assertIn("EARNINGS_UNVERIFIED", records["AAA"]["blockers"])


class TestH1Phase(unittest.TestCase):
    def now(self):
        return iso(dt.date(2026, 9, 2), 10, 31)

    def daily_run(self):
        p = make_provider()
        records, _ = b.run_daily_phase(self.now(), ["AAA"], p)
        return p, records

    def test_h1_can_emit_ready_with_bounded_plan(self):
        p, prior = self.daily_run()
        self.assertEqual(prior["AAA"]["status"], "CANDIDATE")
        records, h1_asofs = b.run_h1_phase(self.now(), p, prior)
        rec = records["AAA"]
        self.assertEqual(rec["status"], "READY")
        self.assertEqual(rec["phase"], "H1")
        self.assertGreaterEqual(rec["position_value"], 20)
        self.assertLessEqual(rec["position_value"], 50)
        self.assertLessEqual(rec["risk_dollars"], 0.50)
        self.assertIs(rec["earnings_verified"], True)
        self.assertIs(rec["data_fresh"], True)
        self.assertIn(rec["trigger"], ("BREAKOUT_5D", "HIGHER_LOW_PULLBACK"))
        self.assertLess(rec["stop"], rec["entry"])
        self.assertGreater(rec["target"], rec["entry"])
        self.assertEqual(rec["exit_deadline"], iso(dt.date(2026, 9, 4)))
        self.assertTrue(h1_asofs)

    def test_h1_processes_only_candidates(self):
        p, prior = self.daily_run()
        prior["BBB"] = {"status": "WAIT", "phase": "DAILY",
                        "blockers": ["EARNINGS_UNVERIFIED"]}
        prior["CCC"] = {"status": "REJECT", "phase": "DAILY",
                        "blockers": ["DAILY_TREND_DOWN"]}
        prior["DDD"] = full_record()
        records, _ = b.run_h1_phase(self.now(), p, prior)
        self.assertIs(records["BBB"], prior["BBB"])   # untouched objects
        self.assertIs(records["CCC"], prior["CCC"])
        self.assertIs(records["DDD"], prior["DDD"])
        self.assertEqual(records["AAA"]["status"], "READY")

    def test_stale_candidate_is_downgraded(self):
        p = make_provider()
        # candidate signalled 2026-08-31; today's H1 run is for 09-01 signals
        prior = {"AAA": {
            "status": "CANDIDATE", "phase": "DAILY",
            "asof": iso(dt.date(2026, 8, 31)),
            "features": {"atr": 1.0, "resistance": 105.0},
            "trigger_low": 99.2, "reasons": [], "blockers": [],
            "earnings_verified": True, "data_fresh": True,
        }}
        records, _ = b.run_h1_phase(self.now(), p, prior)
        rec = records["AAA"]
        self.assertEqual(rec["status"], "REJECT")
        self.assertIn("STALE_CANDIDATE_DATA", rec["blockers"])
        self.assertEqual(rec["asof"], iso(dt.date(2026, 8, 31)))  # preserved

    def test_missing_today_h1_bar_never_readies(self):
        _, prior = self.daily_run()
        old = h1_bars(today=dt.date(2026, 9, 1))  # yesterday only
        p2 = make_provider(h1=old)
        records, _ = b.run_h1_phase(self.now(), p2, prior)
        self.assertNotEqual(records["AAA"]["status"], "READY")

    def test_early_h1_run_carries_candidate_and_later_run_readies(self):
        # 09:00 ET: the entry session's first hour has not completed, so
        # the run must NOT downgrade the candidate to WAIT H1_INCOMPLETE.
        p, prior = self.daily_run()
        early = iso(dt.date(2026, 9, 2), 9, 0)
        records1, _ = b.run_h1_phase(early, p, prior)
        self.assertEqual(records1["AAA"]["status"], "CANDIDATE")
        # Same record re-run at 10:45 (after the window) can still confirm.
        records2, h1_asofs = b.run_h1_phase(
            iso(dt.date(2026, 9, 2), 10, 45), p, records1)
        self.assertEqual(records2["AAA"]["status"], "READY")
        self.assertTrue(h1_asofs)

    def test_all_readies_respect_risk_caps(self):
        p, prior = self.daily_run()
        records, _ = b.run_h1_phase(self.now(), p, prior)
        for tk, rec in records.items():
            if rec["status"] == "READY":
                self.assertLessEqual(rec["risk_dollars"], 0.50)
                self.assertGreaterEqual(rec["position_value"], 20)
                self.assertLessEqual(rec["position_value"], 50)


class TestMergeAndCommit(unittest.TestCase):
    def legacy_item(self, ticker):
        return {"ticker": ticker, "ok": True, "price": 217.44,
                "swing": {"verdict": "BUY", "score": 86}}

    def payload(self):
        return {"updated": "legacy", "macro": [], "items": [
            self.legacy_item("AAA"), self.legacy_item("BBB")]}

    def meta(self):
        return b.build_meta("PAPER", "daily", iso(dt.date(2026, 9, 1)),
                            None, "2026-09-02T03:00:00+00:00")

    def test_merge_preserves_legacy_and_adds_section(self):
        payload = self.payload()
        rec = full_record(status="CANDIDATE", phase="DAILY", entry=None,
                          stop=None, target=None, shares=None,
                          position_value=None, risk_dollars=None,
                          risk_pct_account=None, trigger=None,
                          reward_risk=None, exit_deadline=None,
                          asof=iso(dt.date(2026, 9, 1)))
        merged = b.merge(payload, {"AAA": rec, "ZZZ": rec}, self.meta())
        aaa = next(i for i in merged["items"] if i["ticker"] == "AAA")
        self.assertEqual(aaa["price"], 217.44)       # legacy untouched
        self.assertEqual(aaa["swing"]["verdict"], "BUY")
        self.assertEqual(aaa["swing_3d"]["status"], "CANDIDATE")
        self.assertEqual(aaa["swing_3d"]["mode"], "PAPER")
        self.assertEqual(merged["swing_3d"]["mode"], "PAPER")
        for key in ("mode", "phase", "daily_asof", "h1_asof",
                    "validation_report", "disclaimer"):
            self.assertIn(key, merged["swing_3d"])
        self.assertIn("bukan rekomendasi", merged["swing_3d"]["disclaimer"])
        self.assertEqual(len(merged["items"]), 3)    # ZZZ appended

    def test_commit_replaces_atomically_and_keeps_original_on_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "data.json")
            with open(out, "w") as f:
                json.dump({"original": True}, f)
            payload = self.payload()
            payload["swing_3d"] = self.meta()
            payload["items"][0]["swing_3d"] = full_record(
                status="READY", phase="DAILY")   # invalid: READY in DAILY
            errors = b.commit(payload, out)
            self.assertTrue(errors)
            with open(out) as f:
                self.assertEqual(json.load(f), {"original": True})
            # now valid
            payload["items"][0]["swing_3d"] = full_record()
            self.assertEqual(b.commit(payload, out), [])
            with open(out) as f:
                saved = json.load(f)
            self.assertEqual(saved["items"][0]["swing_3d"]["status"], "READY")
            self.assertEqual(saved["swing_3d"]["validation_report"]["valid"],
                             True)

    def test_commit_uses_unique_tmp_name_per_write(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "data.json")
            payload = self.payload()
            payload["swing_3d"] = self.meta()
            payload["items"][0]["swing_3d"] = full_record()
            self.assertEqual(b.commit(payload, out), [])
            leftovers1 = {f for f in os.listdir(td)
                          if f.endswith(".tmp") and f != "data.json"}
            self.assertEqual(b.commit(payload, out), [])
            leftovers2 = {f for f in os.listdir(td)
                          if f.endswith(".tmp") and f != "data.json"}
            # concurrent runs must never share a fixed tmp name
            self.assertEqual(leftovers1 & leftovers2, set())
            with open(out) as f:
                self.assertEqual(json.load(f)["items"][0]["swing_3d"]
                                 ["status"], "READY")


class TestH1RejectContract(unittest.TestCase):
    """REJECT/WAIT records from the H1 phase are validator-clean (C-1)."""

    def meta(self, phase="h1"):
        return b.build_meta("PAPER", phase, iso(dt.date(2026, 9, 1)),
                            iso(dt.date(2026, 9, 2), 9, 30),
                            "2026-09-02T03:00:00+00:00")

    def payload(self, rec):
        return {"updated": "x", "items": [
            {"ticker": "AAA", "swing_3d": rec}],
            "swing_3d": self.meta()}

    def assert_validator_clean(self, rec):
        self.assertEqual(v.validate_payload(self.payload(rec)), [])

    def daily_run(self, provider):
        records, _ = b.run_daily_phase(iso(dt.date(2026, 9, 2), 10, 31),
                                       ["AAA"], provider)
        return records

    def test_insufficient_h1_history_record_is_validator_clean(self):
        # F-3: h1 history holds only the entry session's own 09:30 bar
        # (no prior sessions) -> explicit REJECT, never a crash.
        p = make_provider()
        prior = self.daily_run(p)
        self.assertEqual(prior["AAA"]["status"], "CANDIDATE")
        solo = h1_bars(today=dt.date(2026, 9, 2), prior_sessions=0)
        p2 = make_provider(h1=solo)
        records, _ = b.run_h1_phase(self.now(), p2, prior)
        rec = records["AAA"]
        self.assertEqual(rec["status"], "REJECT")
        self.assertIn("INSUFFICIENT_H1_HISTORY", rec["blockers"])
        self.assertIs(rec["data_fresh"], False)
        self.assert_validator_clean(rec)

    def now(self):
        return iso(dt.date(2026, 9, 2), 10, 45)

    def test_wait_records_from_h1_are_validator_clean(self):
        # C-1: a WAIT (BELOW_SESSION_VWAP from confirm_h1's gate on the
        # entry session's completed bar) lands validator-clean.
        p = make_provider()
        prior = self.daily_run(p)
        base = h1_bars(today=dt.date(2026, 9, 2), prior_sessions=6)
        # last close below its own vwap -> BELOW_SESSION_VWAP WAIT
        closes = list(base.closes)
        closes[-1] = 100.2
        lows = list(base.lows)
        lows[-1] = 99.9
        below = md.Bars(
            opens=list(base.opens), highs=list(base.highs), lows=lows,
            closes=closes, volumes=list(base.volumes),
            asofs=list(base.asofs), interval="60m", source="fake")
        p2 = make_provider(h1=below)
        records, _ = b.run_h1_phase(self.now(), p2, prior)
        rec = records["AAA"]
        self.assertEqual(rec["status"], "WAIT")
        self.assertIn("BELOW_SESSION_VWAP", rec["blockers"])
        self.assert_validator_clean(rec)

    def test_daily_reject_record_is_validator_clean(self):
        # C-1: daily-phase REJECT records (stale SPY etc.) are
        # validator-clean too.
        p = make_provider(spy=spy_bars(end=dt.date(2026, 8, 27)))
        records, _ = b.run_daily_phase(iso(dt.date(2026, 9, 2), 10, 31),
                                       ["AAA"], p)
        rec = records["AAA"]
        self.assertEqual(rec["status"], "REJECT")
        self.assertEqual(v.validate_payload(self.payload(rec)), [])


class TestValidator(unittest.TestCase):
    def payload(self):
        return {"updated": "x", "items": [
            {"ticker": "AAA", "swing_3d": full_record()},
            {"ticker": "BBB", "swing_3d": full_record(
                status="CANDIDATE", phase="DAILY", entry=None, stop=None,
                target=None, shares=None, position_value=None,
                risk_dollars=None, risk_pct_account=None, trigger=None,
                reward_risk=None, exit_deadline=None,
                asof=iso(dt.date(2026, 9, 1)))},
            {"ticker": "LEGACY", "price": 1.0},
        ], "swing_3d": {
            "mode": "PAPER", "phase": "h1",
            "daily_asof": iso(dt.date(2026, 9, 1)),
            "h1_asof": iso(dt.date(2026, 9, 2), 9, 30),
            "validation_report": {"valid": True, "errors": []},
        }}

    def test_valid_payload_passes(self):
        self.assertEqual(v.validate_payload(self.payload()), [])

    def test_legacy_payload_without_swing_3d_passes(self):
        p = {"updated": "x", "items": [{"ticker": "AAA", "price": 1.0}]}
        self.assertEqual(v.validate_payload(p), [])

    def test_ready_position_above_cap_fails(self):
        p = self.payload()
        p["items"][0]["swing_3d"]["position_value"] = 50.01
        errs = v.validate_payload(p)
        self.assertTrue(any("position_value" in e for e in errs))

    def test_ready_risk_above_budget_fails(self):
        p = self.payload()
        p["items"][0]["swing_3d"]["risk_dollars"] = 0.51
        errs = v.validate_payload(p)
        self.assertTrue(any("risk_dollars" in e for e in errs))

    def test_ready_requires_h1_phase(self):
        p = self.payload()
        p["items"][0]["swing_3d"]["phase"] = "DAILY"
        errs = v.validate_payload(p)
        self.assertTrue(any("READY" in e for e in errs))

    def test_ready_requires_verified_earnings_and_fresh_data(self):
        for key, value in (("earnings_verified", False),
                           ("data_fresh", False)):
            with self.subTest(key=key):
                p = self.payload()
                p["items"][0]["swing_3d"][key] = value
                self.assertTrue(v.validate_payload(p))

    def test_unknown_status_fails(self):
        p = self.payload()
        p["items"][1]["swing_3d"]["status"] = "BUY"
        errs = v.validate_payload(p)
        self.assertTrue(any("status" in e for e in errs))

    def test_missing_contract_key_fails(self):
        p = self.payload()
        del p["items"][1]["swing_3d"]["position_value"]
        self.assertTrue(v.validate_payload(p))

    def test_daily_top_phase_forbids_ready(self):
        p = self.payload()
        p["swing_3d"]["phase"] = "daily"
        errs = v.validate_payload(p)
        self.assertTrue(errs)

    def test_candidate_requires_daily_phase(self):
        p = self.payload()
        p["items"][1]["swing_3d"]["phase"] = "H1"
        self.assertTrue(v.validate_payload(p))

    def test_validate_file_reports_corrupt_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            f.write("{not json")
            name = f.name
        try:
            self.assertTrue(v.validate_file(name))
        finally:
            os.unlink(name)


if __name__ == "__main__":
    unittest.main()
