"""US cash-equity sessions: XNYS, exchange-calendars. Never weekday fallback."""
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')


class CalendarUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def calendar():
    try:
        import exchange_calendars
        return exchange_calendars.get_calendar('XNYS')
    except Exception as exc:
        raise CalendarUnavailable('XNYS calendar unavailable') from exc


def _call(method, *args, **kwargs):
    try:
        return getattr(calendar(), method)(*args, **kwargs)
    except CalendarUnavailable:
        raise
    except Exception as exc:
        raise CalendarUnavailable('XNYS schedule unavailable: %s' % exc) from exc


def aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('aware timestamp required')
    return value.astimezone(ET)


def is_session(day):
    return bool(_call('is_session', day))


def session_open(day):
    return _call('session_open', day).to_pydatetime().astimezone(ET)


def session_close(day):
    return _call('session_close', day).to_pydatetime().astimezone(ET)


def next_session(day):
    return _call('date_to_session', day + timedelta(days=1), direction='next').date()


def previous_session(day):
    return _call('date_to_session', day - timedelta(days=1), direction='previous').date()


def latest_completed(now):
    now = aware(now)
    day = _call('date_to_session', now.date(), direction='previous').date()
    return previous_session(day) if session_close(day) > now else day


def sessions_between(start, end):
    if end <= start:
        return 0
    return len(_call('sessions_in_range', start + timedelta(days=1), end))


def nth_close(start, number):
    start = aware(start)
    if not isinstance(number, int) or number < 1 or not is_session(start.date()):
        raise ValueError('positive session count and valid entry session required')
    day = _call('session_offset', start.date(), number - 1).date()
    return session_close(day).isoformat()


def phase_skip_reason(phase, now):
    """Calendar errors raise; closed/incomplete windows SKIP before any writes."""
    now = aware(now)
    if not is_session(now.date()):
        return 'NON_TRADING_SESSION'
    if phase == 'daily':
        return None if now >= session_close(now.date()) else 'SESSION_INCOMPLETE'
    if phase != 'h1':
        raise ValueError('unknown phase')
    opening = session_open(now.date())
    return (None if opening + timedelta(minutes=61) <= now
            < min(opening + timedelta(hours=2), session_close(now.date()))
            else 'OUTSIDE_H1_WINDOW')
