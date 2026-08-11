# US stock decision cockpit

## Goal

Turn the existing US stock watchlist into a fast decision aid that answers “buy, wait, or avoid?” for each ticker while showing the evidence and invalidation condition behind the call.

## Audience and scope

- Audience: an investor scanning US stocks quickly, with enough context to avoid making a decision from a single chart.
- Scope: a static, client-rendered GitHub Pages dashboard using the repository’s existing `data.json` snapshot.
- Out of scope: brokerage integration, live order placement, user accounts, portfolio persistence, and pretending that the UI is personalized financial advice.

## Decision model

Each stock gets a score from five weighted signals:

| Signal | Weight | Inputs |
| --- | ---: | --- |
| Trend & momentum | 25 | weekly change, RSI, price vs. MA50/MA200 |
| Earnings quality | 25 | revenue growth, ROE, free cash flow, balance-sheet health |
| Valuation | 20 | PEG, forward P/E, analyst upside as a secondary input |
| Relative strength | 15 | price position in 52-week range, trend persistence |
| Risk | 15 | beta, drawdown from 52-week high, volatility flags |

Verdict rules:

- `BUY` when score is at least 70 and the modeled upside/risk ratio is at least 2:1.
- `WAIT` when score is 50–69, signals are mixed, or entry is extended.
- `AVOID` when score is below 50 or a hard risk flag is present.

The decision card must show the score, confidence, entry zone, target, risk line, three supporting reasons, and one “what changes my mind” condition. ETF rows use the same verdict language but are explicitly marked as technical-only when fundamental data is missing.

## Information architecture

1. Sticky header with dashboard name, data freshness, market status, and ticker search.
2. Primary decision card for the selected ticker, with the verdict as the strongest visual element.
3. Compact chart panel with price, entry zone, target zone, and risk line.
4. Five-signal rail showing bullish, neutral, or bearish states.
5. “Why this call” panel with evidence and invalidation.
6. Ranked watchlist with verdict, score, price, weekly move, and one-line reason.
7. Market context and methodology collapsed below the decision surface.

## Visual direction

- Dark charcoal-blue background with a restrained amber accent.
- Green is reserved for `BUY`/positive evidence; red is reserved for `AVOID`/risk; neutral uses cool gray.
- Typography uses a characterful display face for headings and a compact sans/monospace treatment for figures.
- Use asymmetry: a large verdict card, a smaller chart card, and a horizontally ranked watchlist rather than equal cards everywhere.
- Use sentence case copy and concrete labels such as “Entry ≤ $174” and “Risk line $162”.
- Maintain keyboard focus states, reduced-motion support, semantic headings, and mobile-first stacking.

## Interaction and states

- Clicking a watchlist row changes the selected ticker and updates the decision surface without a page reload.
- Search filters the watchlist and selects an exact ticker when entered.
- Horizon tabs change the framing text between swing, 3–6 months, and long-term; the current snapshot stays transparent about its data date.
- Empty search state explains how to clear the filter.
- No-data state preserves the decision shell and explains which data is missing.

## Data and trust

- Read the existing `data.json` snapshot; do not invent live freshness.
- Derive the verdict deterministically in the browser from the snapshot fields so users can inspect the rule inputs.
- Keep the data timestamp and source visible.
- Use “decision aid” language and avoid claims of certainty or guaranteed returns.

## Swing-first refresh behavior

- The focused view uses only the swing lens: 40% trend & momentum, 10% earnings quality, 5% valuation, 25% relative strength, and 20% risk.
- The page has no interval timer or 22:00 schedule. It loads the current `data.json` snapshot on open and provides a manual `Refresh data` button.
- Manual refresh requests `data.json` with a cache-busting query and `cache: no-store`, then recomputes and reranks the watchlist.
- The watchlist does not change continuously. It changes only when the underlying snapshot has changed and the user refreshes the page or presses `Refresh data`.

## Success criteria

- A user can identify the selected ticker’s verdict and next action above the fold.
- Every verdict has visible evidence and an invalidation condition.
- The watchlist is scannable without opening each card.
- Search, ticker selection, filters, and horizon controls work with mouse, keyboard, and touch.
- The page remains usable at mobile widths and builds as a static GitHub Pages site.
