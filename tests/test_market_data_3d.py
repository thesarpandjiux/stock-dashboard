import datetime as dt
import unittest

import market_data as md


class TestFreshness(unittest.TestCase):
    def test_h1_freshness_is_explicit(self):
        now = dt.datetime(2026, 9, 1, 15, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(md.is_fresh("2026-09-01T14:30:00+00:00", dt.timedelta(hours=2), now))
        self.assertFalse(md.is_fresh("2026-09-01T10:00:00+00:00", dt.timedelta(hours=2), now))

    def test_exactly_at_age_limit_is_still_fresh(self):
        now = dt.datetime(2026, 9, 1, 15, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(md.is_fresh("2026-09-01T13:00:00+00:00", dt.timedelta(hours=2), now))

    def test_naive_and_zoned_timestamps_compare_safely(self):
        now = dt.datetime(2026, 9, 1, 15, 0)
        self.assertFalse(md.is_fresh("2026-09-01T10:00:00+00:00", dt.timedelta(hours=2), now))

    def test_unverified_earnings_is_not_safe(self):
        status = md.normalize_earnings_status(None, None)
        self.assertEqual(status, {"verified": False, "date": None, "source": None})

    def test_verified_earnings_status_contract(self):
        status = md.normalize_earnings_status(dt.date(2026, 9, 1), "yahoo-calendar")
        self.assertEqual(
            status,
            {"verified": True, "date": "2026-09-01", "source": "yahoo-calendar"},
        )

    def test_string_date_is_accepted(self):
        status = md.normalize_earnings_status("2026-09-01", "yahoo-calendar")
        self.assertEqual(status["verified"], True)
        self.assertEqual(status["date"], "2026-09-01")

    def test_earnings_gate_never_verified_inside_event_window(self):
        # Earnings two trading days from now -> already inside the 1-3 day hold
        # window (buffer of 3 sessions). Unverified must be the ONLY safe result.
        for raw in (
            None,
            "bukan-tanggal",
            dt.datetime(2026, 9, 1, 0, 0),
        ):
            with self.subTest(raw=raw):
                self.assertFalse(md.normalize_earnings_status(raw, "x")["verified"])

    def test_midday_weekday_has_no_completed_session_before_open(self):
        self.assertIs(md.is_session_complete("2026-09-02T08:00:00-04:00"), False)
        self.assertIs(md.is_session_complete("2026-09-02T09:30:00-04:00"), False)

    def test_weekday_afternoon_has_completed_session(self):
        self.assertIs(md.is_session_complete("2026-09-02T15:50:00-04:00"), True)

    def test_weekend_daytime_has_no_completed_session(self):
        self.assertIs(md.is_session_complete("2026-09-05T12:00:00-04:00"), False)

    def test_sunday_evening_has_no_completed_session(self):
        self.assertIs(md.is_session_complete("2026-09-06T18:00:00-04:00"), False)

    def test_weekday_ten_pm_has_completed_session(self):
        self.assertIs(md.is_session_complete("2026-09-01T22:00:00-04:00"), True)

    def test_monday_morning_after_weekend_has_no_session(self):
        self.assertIs(md.is_session_complete("2026-09-07T09:00:00-04:00"), False)


class TestBarsContract(unittest.TestCase):
    def test_bars_carry_opens_asofs_and_interval(self):
        bars = md.Bars(
            opens=[10.0, 11.0],
            highs=[11.0, 12.0],
            lows=[9.0, 10.0],
            closes=[10.5, 11.5],
            volumes=[100, 200],
            asofs=["2026-09-01T09:30:00-04:00", "2026-09-02T09:30:00-04:00"],
            interval="1d",
            source="yahoo",
        )
        self.assertEqual(bars.opens, [10.0, 11.0])
        self.assertEqual(bars.asofs[1], "2026-09-02T09:30:00-04:00")
        self.assertEqual(bars.interval, "1d")
        self.assertEqual(bars.closes, [10.5, 11.5])
        self.assertEqual(bars.source, "yahoo")
        self.assertEqual(len(bars), 2)

    def test_legacy_positional_bars_still_work(self):
        bars = md.Bars([1, 2], [3, 4], [5, 6], [7, 8], "stooq")
        self.assertEqual(bars.highs, [1, 2])
        self.assertEqual(bars.lows, [3, 4])
        self.assertEqual(bars.closes, [5, 6])
        self.assertEqual(bars.volumes, [7, 8])
        self.assertEqual(bars.source, "stooq")
        self.assertEqual(bars.opens, [])
        self.assertEqual(bars.asofs, [])
        self.assertEqual(bars.interval, "1d")


if __name__ == "__main__":
    unittest.main()
