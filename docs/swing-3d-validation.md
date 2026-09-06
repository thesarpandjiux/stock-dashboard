# Swing 1-3 Day — Paper Validation Record

Status: **PAPER MODE — live production data not yet pushed**
Updated: 2026-09-06 (Step 1-2 complete; Step 3 deferred by user decision)

## Step 1 — Local verification (all exit 0)

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests` | 212 OK |
| `node --test tests/*.test.js` | 4/4 OK |
| `python3 build_swing_3d.py --phase daily --mode paper` | `data.json written — phase=daily mode=PAPER ready=none` |
| `python3 validate_swing_data.py data.json` | `OK: schema and risk invariants pass` |
| `py_compile` (6 modules) | OK |

## Step 2 — Production invariants (programmatic)

`OK: swing-3d invariants (mode=PAPER, READY=0, semua H1-phase)`

- `data.json['swing_3d']['mode'] == 'PAPER'` ✓
- 17 items: 3 CANDIDATE / 10 REJECT / 4 WAIT — **0 READY** in daily phase ✓
- No READY item exists outside H1 phase; position/risk bounds untestable until first READY (no H1 confirm run yet)

## Step 3 — Push + workflow dispatch: PENDING

Deferred by user (commit local first, verify more before push). Once pushed:
- dispatch `swing-3d-daily.yml`, verify commit + live data.json
- dispatch `swing-3d-h1.yml` only within ET 10:31-11:29 market-time gate
- confirm GitHub Pages still serves Codex UI + valid JSON

## Step 4 — Paper observation: PENDING

Needs ≥30 completed paper trades across tickers/regimes before any capital use. Compare live-paper vs backtest assumptions (H1 availability, delayed data, slippage, gaps, third-day exits). No silent retuning — model change = new version + new holdout.

## Step 5 — Release decision: PENDING

- Holdout + paper gate fail → retain PAPER MODE, document blockers
- Both pass → separate reviewed commit flips mode config only (thresholds untouched in same commit)

## Known residuals (non-blocking)

- First production fire no-ops by design (rotation bootstrap SKIP)
- `build_dashboard.py` legacy rebuild wipes `swing_3d` records per rotation fire (I-2 open)
- Weekday-only holiday handling (`ponytail:` — fail closed until verified market-session calendar)
- VOO/QQQ 404 fundamentals = non-fatal, ETF skipped by design
