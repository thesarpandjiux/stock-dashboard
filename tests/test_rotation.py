"""Rotation scheduling tests — five *distinct completed US trading dates*,
never calendar `*/5`, state-based, pinned tickers immutable.

Trading-date lists are explicit (holiday-free August/early-September 2026
windows), per the task brief: no helper may infer holidays by skipping
calendar days. Session dates come only from observed market data
(exchange-verified); when the calendar cannot be verified the gate fails
closed and rotation is skipped.
"""

import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

import market_data as md
import screener as s


AUG21 = dt.date(2026, 8, 21)   # Friday
AUG24 = dt.date(2026, 8, 24)   # Monday
AUG25 = dt.date(2026, 8, 25)
AUG26 = dt.date(2026, 8, 26)
AUG27 = dt.date(2026, 8, 27)
AUG28 = dt.date(2026, 8, 28)   # Friday
AUG31 = dt.date(2026, 8, 31)   # Monday
SEP01 = dt.date(2026, 9, 1)
SEP02 = dt.date(2026, 9, 2)
SEP03 = dt.date(2026, 9, 3)
SEP04 = dt.date(2026, 9, 4)    # Friday (Labor Day 9/7 excluded: explicit list)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _workflow(name):
    path = os.path.join(REPO, ".github", "workflows", name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _h1_guard_source():
    """Runnable python from the `python3 - <<'PY' ... PY` guard heredoc."""
    body = re.search(r"<<'PY'\n(.*?)\n\s*PY\n", _workflow("swing-3d-h1.yml"),
                     re.S).group(1)
    return textwrap.dedent(body)


class TestFiveSessionGate(unittest.TestCase):
    def test_friday_to_next_friday_is_five_sessions_no_holiday(self):
        # Rotated Fri 8/21; Mon-Fri 8/24-8/28 = five distinct sessions.
        new = s.new_sessions(AUG21, [AUG24, AUG25, AUG26, AUG27, AUG28])
        self.assertEqual(new, [AUG24, AUG25, AUG26, AUG27, AUG28])
        self.assertTrue(s.rotation_due(AUG21, [AUG24, AUG25, AUG26,
                                               AUG27, AUG28]))

    def test_four_sessions_not_due(self):
        self.assertFalse(s.rotation_due(AUG21, [AUG24, AUG25, AUG26, AUG27]))

    def test_due_date_is_fifth_new_session(self):
        due = s.due_on(AUG21, [AUG24, AUG25, AUG26, AUG27, AUG28])
        self.assertEqual(due, AUG28)

    def test_month_boundary_does_not_shorten_interval(self):
        # Rotated Fri 8/28; the next five sessions span Aug->Sep.
        observed = [AUG31, SEP01, SEP02, SEP03, SEP04]
        self.assertTrue(s.rotation_due(AUG28, observed))
        # Only four sessions across the boundary -> not due yet.
        self.assertFalse(s.rotation_due(AUG28, observed[:-1]))

    def test_duplicate_dates_do_not_advance(self):
        self.assertFalse(s.rotation_due(AUG21, [AUG24] * 5))
        self.assertFalse(s.rotation_due(AUG21, [AUG24, AUG24, AUG25,
                                                AUG25, AUG26]))

    def test_sessions_must_be_strictly_after_anchor(self):
        # Re-observing the rotation session itself does not count.
        observed = [AUG21, AUG24, AUG25, AUG26, AUG27, AUG28]
        self.assertEqual(s.new_sessions(AUG21, observed),
                         [AUG24, AUG25, AUG26, AUG27, AUG28])
        self.assertTrue(s.rotation_due(AUG21, observed))

    def test_out_of_order_and_older_dates_ignored(self):
        observed = [AUG28, AUG24, dt.date(2026, 8, 3), AUG25, AUG26, AUG27]
        self.assertEqual(s.new_sessions(AUG21, observed),
                         [AUG24, AUG25, AUG26, AUG27, AUG28])


class TestFailClosed(unittest.TestCase):
    def test_no_observed_calendar_skips_rotation(self):
        # Market calendar unverifiable -> fail closed, never rotate.
        self.assertFalse(s.rotation_due(AUG21, None))
        self.assertFalse(s.rotation_due(AUG21, []))
        self.assertIsNone(s.due_on(AUG21, None))

    def test_missing_anchor_counts_nothing(self):
        # Unknown anchor with no calendar -> skip. With a calendar the
        # counting helper is anchor-free by design, but the *gate*
        # (rotate_if_due) must never rotate on an unknown anchor: it
        # bootstraps to the newest observed session and skips (see
        # TestBootstrapAnchor). Counting the whole observed window as
        # "new" would otherwise hold the gate open for years.
        self.assertFalse(s.rotation_due(None, []))
        self.assertEqual(s.new_sessions(None, [AUG24, AUG25]), [AUG24, AUG25])


class TestBootstrapAnchor(unittest.TestCase):
    """F-2: empty state + one year of history must NOT rotate."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"last_rotation": None, "sessions": []}, f)
        self._run = s.run
        s.run = self._boom
        self.addCleanup(self._restore)

    def _restore(self):
        s.run = self._run
        os.unlink(self.path)

    def _boom(self, *a, **kw):
        raise AssertionError("run() must not screen/write during bootstrap")

    def _year(self):
        # ~245 weekday sessions ending 2026-09-04, as a live SPY 1y pull.
        out, d = [], SEP04
        while len(out) < 245:
            if d.weekday() < 5:
                out.append(d)
            d -= dt.timedelta(days=1)
        return sorted(out)

    def test_first_fire_bootstraps_anchor_and_skips(self):
        observed = self._year()
        st = s.RotationState(self.path)
        self.assertIsNone(st.anchor)
        ok, msg = s.rotate_if_due(observed, ["NVDA"], state=st)
        self.assertFalse(ok)
        self.assertIn("SKIP", msg)
        # anchor persisted at the newest observed session, not a year ago
        self.assertEqual(s.RotationState(self.path).anchor, max(observed))

    def test_after_bootstrap_gate_needs_five_more_sessions(self):
        observed = self._year()
        s.rotate_if_due(observed, ["NVDA"], state=s.RotationState(self.path))
        anchor = s.RotationState(self.path).anchor
        four = observed + [anchor + dt.timedelta(days=i) for i in (3, 4, 5, 6)]
        ok, _ = s.rotate_if_due(four, ["NVDA"], state=s.RotationState(self.path))
        self.assertFalse(ok)                    # only 4 new sessions
        self.assertEqual(s.RotationState(self.path).anchor, anchor)

    def test_five_sessions_after_bootstrap_rotates(self):
        observed = self._year()
        s.rotate_if_due(observed, ["NVDA"], state=s.RotationState(self.path))
        anchor = s.RotationState(self.path).anchor
        # anchor+3 (Sep 7) is Labor Day: a real exchange holiday, so it must
        # not advance the gate. Five new sessions = Sep 8,9,10,11,14.
        five = observed + [anchor + dt.timedelta(days=i)
                           for i in (3, 4, 5, 6, 7, 10)]
        s.run = lambda *a, **kw: [{"ticker": "ABNB", "score": 80}]
        ok, msg = s.rotate_if_due(five, ["NVDA"], state=s.RotationState(self.path))
        self.assertTrue(ok, msg)
        self.assertEqual(s.RotationState(self.path).anchor,
                         anchor + dt.timedelta(days=10))


class TestOnlyCompleted(unittest.TestCase):
    """C-1: keep the last session unless its bar is still intraday."""

    def _now(self, day, hh, mm):
        return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=md.ET)

    def test_keeps_last_session_after_close(self):
        dates = [AUG31, SEP01, SEP02, SEP03, SEP04]
        # caller (refresh.yml cron) runs 23:00 UTC = 19:00 ET, after close
        self.assertEqual(s._only_completed(dates, now=self._now(SEP04, 19, 0)),
                         dates)

    def test_drops_last_session_while_still_open(self):
        dates = [AUG31, SEP01, SEP02, SEP03, SEP04]
        self.assertEqual(s._only_completed(dates, now=self._now(SEP04, 11, 0)),
                         dates[:-1])

    def test_older_last_session_is_complete(self):
        dates = [AUG31, SEP01]
        self.assertEqual(s._only_completed(dates, now=self._now(SEP04, 11, 0)),
                         dates)

    def test_weekend_dates_dropped_and_empty_safe(self):
        self.assertEqual(s._only_completed([]), [])
        self.assertEqual(
            s._only_completed([AUG28, dt.date(2026, 8, 29)],
                              now=self._now(SEP04, 19, 0)),
            [AUG28])


class TestWorkflowWiring(unittest.TestCase):
    def test_refresh_invokes_rotation_mode(self):
        # F-1: calendar cron is a trigger; the 5-session gate must govern.
        wf = _workflow("refresh.yml")
        self.assertIn("python screener.py --rotation", wf)
        self.assertNotRegex(wf, r"run: python screener\.py\s*$")

    def test_refresh_commits_rotation_state(self):
        # anchor must survive between fires or the gate re-bootstraps forever
        wf = _workflow("refresh.yml")
        add = [ln for ln in wf.splitlines() if ln.strip().startswith("git add")]
        self.assertTrue(add)
        self.assertIn("rotation_state.json", add[0])

    def test_unittest_step_sets_pipefail(self):
        # F-3: `| tail -5` otherwise swallows a red suite's exit status
        for name in ("swing-3d-daily.yml", "swing-3d-h1.yml"):
            wf = _workflow(name)
            for m in re.finditer(r"^.*unittest discover.*\|\s*tail.*$",
                                 wf, re.M):
                before = wf[:m.start()].rsplit("run: |", 1)[-1]
                self.assertIn("set -o pipefail", before,
                              "%s: unittest pipeline without pipefail" % name)

    def test_h1_guard_has_no_dead_import(self):
        self.assertNotIn("import sys", _workflow("swing-3d-h1.yml"))

    def test_h1_guard_writes_real_github_output(self):
        # Heredoc is quoted ('PY'), so "$GITHUB_OUTPUT" stays literal: the
        # guard would write a file named `$GITHUB_OUTPUT`, leaving
        # steps.guard.outputs.ok unset and every later step skipped forever.
        guard = _h1_guard_source()
        self.assertNotIn('open("$GITHUB_OUTPUT"', guard)
        self.assertIn('os.environ["GITHUB_OUTPUT"]', guard)

    def test_h1_guard_writes_ok_flag_to_github_output(self):
        guard = _h1_guard_source()
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "gh_output")
            open(out, "w").close()
            env = dict(os.environ, GITHUB_OUTPUT=out, TZ="America/New_York")
            proc = subprocess.run([sys.executable, "-c", guard], env=env,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as f:
                written = f.read()
        self.assertRegex(written, r"^ok=(true|false)\n$")
        self.assertFalse(os.path.exists("$GITHUB_OUTPUT"))

    def test_rotate_if_due_has_no_dead_now_param(self):
        import inspect
        self.assertNotIn("now", inspect.signature(s.rotate_if_due).parameters)

    def test_daily_comment_hours_are_correct(self):
        # C-4: 22:15 UTC = 18:15 ET (EDT) / 17:15 ET (EST)
        wf = _workflow("swing-3d-daily.yml")
        self.assertIn("18:15 ET", wf)
        self.assertNotIn("17:15 ET saat EDT", wf)


class TestRotationState(unittest.TestCase):
    def _state(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        return path

    def test_load_missing_or_corrupt_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "nope.json")
            st = s.RotationState(missing)
            self.assertIsNone(st.anchor)
            self.assertEqual(st.sessions, [])
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "bad.json")
            with open(bad, "w") as f:
                f.write("{not json")
            st = s.RotationState(bad)
            self.assertIsNone(st.anchor)

    def test_save_then_reload_roundtrip(self):
        path = self._state()
        st = s.RotationState(path)
        st.last_rotation = AUG28
        st.sessions = [AUG24, AUG25, AUG26, AUG27, AUG28]
        st.save()
        again = s.RotationState(path)
        self.assertEqual(again.last_rotation, AUG28)
        self.assertEqual(again.sessions, [AUG24, AUG25, AUG26, AUG27, AUG28])
        os.unlink(path)

    def test_reset_after_rotation(self):
        path = self._state()
        st = s.RotationState(path)
        st.last_rotation = AUG21
        st.reset(AUG28)
        self.assertEqual(st.last_rotation, AUG28)
        self.assertEqual(st.sessions, [])
        os.unlink(path)


class TestPinnedImmutability(unittest.TestCase):
    def test_auto_candidates_never_contain_pinned(self):
        pinned = ["NVDA", "AVGO", "AMD", "AMZN", "GOOGL", "LLY",
                  "VOO", "QQQ", "MU", "WMT", "TSM"]
        candidates = [
            {"ticker": "NVDA", "score": 99},      # pinned, must be dropped
            {"ticker": "ABNB", "score": 87},
            {"ticker": "MSFT", "score": 85},
            {"ticker": "TSM", "score": 84},       # pinned, must be dropped
        ]
        out = s.auto_candidates(candidates, pinned)
        tickers = [e["ticker"] for e in out]
        self.assertTrue(set(tickers).isdisjoint(pinned))
        self.assertEqual(tickers, ["ABNB", "MSFT"])

    def test_rotation_logic_never_references_core_file(self):
        # Rotation writes watchlist_auto.json + rotation_state.json only.
        self.assertNotIn("watchlist_core", s.ROTATION_WRITES)
        self.assertIn("watchlist_auto.json", s.ROTATION_WRITES)
        self.assertIn("rotation_state.json", s.ROTATION_WRITES)


if __name__ == "__main__":
    unittest.main()
