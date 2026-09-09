"""Exchange-calendar regressions against production functions, offline."""
import datetime as dt
import unittest
import build_swing_3d as b
import market_data as md
import swing_3d as s


class ExchangeSessions(unittest.TestCase):
    def test_labor_day_uses_friday(self):
        self.assertEqual(b.latest_session_date(b.et_now('2026-09-07T20:25:00-04:00')), dt.date(2026, 9, 4))
        self.assertFalse(md.is_session_complete('2026-09-07T20:25:00-04:00'))
        self.assertEqual(b.next_session_date(dt.date(2026, 9, 4)), dt.date(2026, 9, 8))
        self.assertEqual(b._prev_trading_day_iso(dt.date(2026, 9, 8)), '2026-09-04')

    def test_real_close_and_early_close(self):
        self.assertFalse(md.is_session_complete('2026-09-04T15:50:00-04:00'))
        self.assertTrue(md.is_session_complete('2026-09-04T16:00:00-04:00'))
        self.assertFalse(md.is_session_complete('2026-11-27T12:59:59-05:00'))
        self.assertTrue(md.is_session_complete('2026-11-27T13:00:00-05:00'))

    def test_deadline_counts_sessions_and_dst(self):
        self.assertEqual(s._nth_trading_day_close('2026-09-04T10:30:00-04:00', 3), '2026-09-09T16:00:00-04:00')
        self.assertEqual(s._nth_trading_day_close('2026-11-24T10:30:00-05:00', 3), '2026-11-27T13:00:00-05:00')
        self.assertEqual(s._nth_trading_day_close('2026-10-30T10:30:00-04:00', 3), '2026-11-03T16:00:00-05:00')

class ProductionPath(unittest.TestCase):
    def test_holiday_cli_does_not_touch_snapshot(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromisoformat('2026-09-07T20:25:00-04:00').astimezone(tz)
        with tempfile.TemporaryDirectory() as tmp, patch.object(b, 'datetime', Clock):
            path = Path(tmp) / 'data.json'
            path.write_bytes(b'{"items": [], "sentinel": true}\n')
            before = path.stat().st_mtime_ns
            for phase in ('daily', 'h1'):
                with patch.object(b.LiveProvider, 'daily_bars', side_effect=AssertionError('network on holiday')):
                    self.assertEqual(b.main(['--phase', phase, '--out', str(path)]), 0)
                self.assertEqual(path.stat().st_mtime_ns, before)

    def test_live_provider_keeps_completed_tuesday_h1(self):
        from unittest.mock import patch
        stamps = ['2026-09-04T13:30:00+00:00', '2026-09-07T13:30:00+00:00', '2026-09-08T13:30:00+00:00']
        bars = md.Bars([11]*3, [9]*3, [10]*3, [100]*3, 'fixture', opens=[9]*3, asofs=stamps, interval='60m')
        with patch.object(md, '_yahoo_bars', return_value=bars), patch.object(md, '_now_et', return_value=b.et_now('2026-09-08T10:35:00-04:00')):
            result = b.LiveProvider.h1_bars('TEST')
        self.assertEqual(result.asofs, [stamps[0], stamps[2]])

    def test_rotation_rejects_holiday_future_and_incomplete(self):
        import screener
        dates = [dt.date(2026, 9, d) for d in (4, 7, 8, 9)]
        self.assertEqual(screener._only_completed(dates, b.et_now('2026-09-08T15:50:00-04:00')), [dates[0]])
        self.assertEqual(screener._only_completed([dt.date(2026, 11, 27)], b.et_now('2026-11-27T12:59:00-05:00')), [])
        self.assertEqual(screener._only_completed([dt.date(2026, 11, 27)], b.et_now('2026-11-27T13:00:00-05:00')), [dt.date(2026, 11, 27)])

if __name__ == '__main__':
    unittest.main()
