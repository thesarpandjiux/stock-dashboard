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
import tempfile
import unittest

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
        # Unknown anchor with no calendar -> skip; with calendar, the
        # window starts fresh from the earliest observed session.
        self.assertFalse(s.rotation_due(None, []))
        self.assertEqual(s.new_sessions(None, [AUG24, AUG25]), [AUG24, AUG25])


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
