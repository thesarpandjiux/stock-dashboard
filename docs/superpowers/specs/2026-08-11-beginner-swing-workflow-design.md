# Beginner swing workflow — final specification

## Objective

Turn the current decision cockpit into a beginner-safe swing workflow. The dashboard must help the user narrow a stable watchlist into three to five candidates without implying that every day requires a trade.

## Operating model

The dashboard uses three layers:

1. **Market benchmarks** — `QQQ` and `VOO` provide market-regime context and are not ranked as individual swing candidates.
2. **Fixed watchlist** — `NVDA`, `AVGO`, `AMD`, `MU`, `AMZN`, `GOOGL`, `MELI`, and `LLY` remain the learning universe. Membership does not rotate automatically.
3. **Focus list** — up to five equities selected from the fixed watchlist by the swing rules. Ranking can change whenever the snapshot is manually refreshed.

The fixed watchlist is reviewed monthly, with no more than one or two replacements in a review. Daily refresh changes data, scores, statuses, and ranking—not membership.

## Beginner mode

- The first learning phase assumes paper trading or one open position at a time.
- The dashboard states a maximum risk budget of 0.25% of account equity per trade and no margin, options, or short positions.
- An empty focus list is a valid outcome and must display `NO TRADE` rather than lowering the qualification rules.
- The dashboard does not track a brokerage account or place orders.

## Candidate rules

Every equity receives one execution status:

- `READY`: composite score at least 70, upside/risk at least 2.0, price above MA200, RSI below 70, and no hard-risk flag.
- `WAIT`: score at least 50 but entry, trend, or risk/reward is not ready.
- `REJECT`: score below 50, price below MA200, or a hard-risk flag is present.

Because the current snapshot has no next-earnings date, every candidate must display `CHECK EARNINGS`. The UI must explain that a trade should not be opened until the user verifies that earnings are more than five trading days away. The dashboard must not invent an earnings date or claim that this check has passed.

## Ranking and sector concentration

- Benchmarks are removed before candidate scoring and ranking.
- The focus list shows at most five equities.
- A maximum of two semiconductor names may appear in the focus list. Semiconductor tickers in the current universe are `NVDA`, `AVGO`, `AMD`, and `MU`.
- Within each status, candidates rank by composite score and then upside/risk.
- `READY` ranks above `WAIT`; `REJECT` remains visible only in the fixed-watchlist table.

## User interface

### Header

- Keep the manual `Refresh data` action.
- Show the underlying snapshot time as `Data snapshot`, not as a schedule.
- State that there is no automatic daily rotation.

### Additional information

Show the beginner rules as regular reference information at the bottom of the page:

- `1 position while learning`
- `Risk ≤ 0.25% per trade`
- `No earnings hold`

### Benchmark information

Show `QQQ` and `VOO` in the bottom additional-information section with price, weekly move, trend versus MA200, and a simple regime label: `RISK ON`, `MIXED`, or `RISK OFF`.

### Focus list

Show three to five candidates immediately above the primary decision card, arranged as two horizontal-scroll rows. Each candidate shows ticker, `READY` or `WAIT`, score, weekly move, risk/reward, and the first reason. Selecting a candidate updates the primary decision card.

If no equity qualifies for `READY` or `WAIT`, show `NO TRADE — no setup meets the rules`.

### Fixed watchlist

Keep all eight equities visible below the focus list. Show status, score, price, weekly move, and reason. Explain that this list is stable and reviewed monthly.

### Primary decision card

Keep verdict, confidence, entry, target, risk line, upside/risk, signal breakdown, and invalidation. Add the execution status and `CHECK EARNINGS` warning.

## Refresh behavior

- Load `data.json` once on page open.
- Refresh only when the user presses `Refresh data` or reloads the page.
- Use a cache-busting query and `cache: no-store`.
- After refresh, recompute benchmarks, candidate statuses, focus list, and fixed-watchlist ranking.
- Preserve the selected ticker if it remains in the fixed watchlist.
- On failure, retain the previous snapshot and display a non-blocking error.

## Accessibility and responsive behavior

- All candidate rows and refresh controls are keyboard accessible.
- Status must be communicated with text, not color alone.
- Mobile order: focus list, decision card, signals, fixed watchlist, additional information.
- Honor `prefers-reduced-motion`.

## Acceptance criteria

- `QQQ` and `VOO` never appear in the equity focus list.
- The focus list contains no more than five equities and no more than two semiconductor names.
- Fixed-watchlist membership does not change during refresh.
- Every candidate displays an earnings verification warning.
- No automatic interval or 22:00 schedule exists in page code.
- Manual refresh, search, candidate selection, empty states, and mobile layout remain functional.
