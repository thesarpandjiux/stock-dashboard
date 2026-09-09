# Calendar Fix & Verification — stock-dashboard

Branch `fix/us-exchange-calendar`. Scope: US exchange session/calendar fixes +
regression tests + paper-journal audit. No cron edits, no real orders, PAPER only.

## Root cause

`market_data.py`, `build_swing_3d.py`, `swing_3d.py`, `screener.py` all derived
the US session calendar from **weekday-only rules** and a hardcoded close of
**15:50 ET**. `exchange_sessions.py` (new) replaces this with the verified
`exchange-calendars` XNYS calendar (close 16:00 ET, early close 13:00 ET,
DST-aware, Labor Day etc.).

Prior bug reproduced and fixed: on Labor Day **2026-09-07 (Monday)** at
20:25 ET, `latest_session_date` returned **2026-09-07** as a "completed
session" and every ticker's daily bars ended 2026-09-04, so all 17 items
rejected `STALE_DAILY_DATA`. New behavior: `latest_completed("2026-09-07T20:25-04:00")`
→ `2026-09-04`, and holiday CLI invocations **SKIP before any fetch or write**.

## Changes

| Path | Change |
|---|---|
| `exchange_sessions.py` | **new** — XNYS session module. Close 16:00, early-close 13:00, DST-correct, fail-closed `CalendarUnavailable`. |
| `market_data.py` | `is_session_complete`, `get_h1_bars` use real close / session open; removed `SESSION_CLOSE=(15,50)` and weekday logic. |
| `build_swing_3d.py` | `latest_session_date`, `next_session_date`, `_prev_trading_day_iso`, `earnings_in_trading_days`, `_nth_trading_day_close`, session-open/close timestamps all calendar-backed; `main()` SKIPs `NON_TRADING_SESSION`/`SESSION_INCOMPLETE` before load/fetch/write, `CALENDAR_UNAVAILABLE` exits 2. |
| `swing_3d.py` | `_nth_trading_day_close` → real exchange sessions (holidays + early close + DST). |
| `screener.py` | session-date filters and `_only_completed` use the calendar. |
| `requirements.txt` | `exchange-calendars==4.13.2`. |
| `tests/test_exchange_sessions.py` | **new** — Labor Day, real/early close, 3-session deadline (DST), holiday CLI no-write, Tue-H1 after holiday, rotation holiday rejection, calendar-unavailable fail-closed. |
| `tests/*` | fixture assertions updated from 15:50→16:00 real close; rotation bootstrap test now crosses Labor Day. |

## Tests (all run against production functions, offline fixtures)

- Python: `python -m unittest discover -s tests -p 'test_*.py'` → **218 tests, OK**
  (includes new `test_exchange_sessions.py`, updated `test_swing_3d`,
  `test_build_swing_3d`, `test_market_data_3d`, `test_rotation`).
- JS: `node --test tests/refresh.test.js tests/swing-3d-ui.test.js
  tests/swing-workflow.test.js tests/watchlist-manage.test.js` → **3/4 pass**.
  `swing-workflow.test.js` fails on legacy `swing` BUY→READY mapping
  (`NVDA swing.verdict=BUY` but READY mapping assert) — **pre-existing, unrelated
  to calendar**; see Unresolved.

## Production-path verification (real functions, not fakes)

- `exchange_sessions.latest_completed("2026-09-07T20:25-04:00")` → `2026-09-04`
- `next_session(2026-09-04)` → `2026-09-08` (skips Labor Day)
- `session_close(2026-11-27)` → `13:00-05:00` (early close)
- `nth_close("2026-09-04T10:30-04:00", 3)` → `2026-09-09T16:00-04:00` (3 real sessions)
- Holiday CLI: `build_swing_3d.py --phase daily --out /tmp/…` at
  `2026-09-09T14:24Z` (pre-open) → `SKIP SESSION_INCOMPLETE`, exit 0,
  repo `data.json` **byte-identical** (skip did not overwrite).
- Calendar unavailable (module hidden): `is_session_complete` → `False`;
  CLI → exit 2, no file written. Fail-closed confirmed.

## Workflow / GitHub evidence (live `gh`)

- Daily workflow cron `15 22 * * 1-5`; H1 `35 14 * * 1-5` + `35 15 * * 1-5`.
- Daily workflow: exactly **1 schedule run** on record —
  `34173225617` 2026-09-08T00:24:45Z (success, wrote data.json).
- H1 workflow: **2 schedule runs** — `34155310252` 2026-09-07T19:22:10Z and
  `34153164826` 2026-09-07T18:49:13Z (both success; both fired on Labor Day,
  guard `weekday()<5` passed the holiday).
- **No Sept 8 22:15 UTC daily run exists** and no Sept 9 daily run yet (now
  2026-09-09T14:23Z). The calendar bug alone does **not** explain the absent
  Sept 8 session: the daily cron did not fire a run for it. This is a
  **scheduling/absence issue**, out of this child's scope, for the parent.

## Paper journal audit — truthful capability report

- **No paper entry/exit/P&L ledger exists.** Repo has no journal/ledger
  artifact, no realized-P&L tracking, no fill/execution log.
- `data.json` `swing_3d` records are **signal snapshots** (status/plan per
  ticker), **not trade outcomes**. They are not executions, fills, or P&L.
- `swing_3d.py` `position_plan` computes a *hypothetical* sizing plan only;
  nothing records an actual paper fill.
- Reporting cron (parent, `a9c1bbab8c7b`) can only report these snapshots.
  It **cannot generate trades**. Claiming signal snapshots as trade outcomes
  would be fabrication. Outcome tracking (paper fills + realized P&L) is a
  missing feature — **blocker for any "paper trading results" claim**.

## Unresolved

1. **Daily cron absence** — Sept 8 22:15 UTC daily run missing (no run record).
   Calendar fix cannot fix scheduling. Parent must investigate schedule/run
   absence separately.
2. **H1 YAML guard** (`swing-3d-h1.yml`) still uses `weekday()<5` + 10:31–11:29
   window; it passes on holidays but is now harmless because the Python phase
   SKIPs authoritatively (no write). Update it to calendar when touching
   workflows, out of this scope.
3. **`swing-workflow.test.js`** legacy `swing.verdict=BUY`→READY mapping assert
   fails against current `data.json` — pre-existing, not calendar-related.
4. **No paper outcome ledger** — must be built before any P&L reporting.
