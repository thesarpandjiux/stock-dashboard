# US Stock Decision Cockpit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current watchlist-first page with a static US stock decision cockpit that produces an auditable `BUY / WAIT / AVOID` signal per ticker.

**Architecture:** Keep the repository as a zero-build static GitHub Pages site. `data.json` remains the source snapshot; `index.html` owns the semantic shell, CSS, deterministic scoring, and interaction. The page renders one selected ticker into a decision surface and a compact ranked list, with CSS custom properties carrying the visual states.

**Tech Stack:** HTML, CSS custom properties, vanilla JavaScript, existing `data.json` snapshot.

## Global Constraints

- Preserve the static GitHub Pages deployment shape and existing data snapshot.
- Verdict weights are trend 25%, earnings 25%, valuation 20%, relative strength 15%, risk 15%.
- `BUY` requires score ≥ 70 and modeled upside/risk ≥ 2; `WAIT` covers score 50–69 or mixed/extended signals; `AVOID` covers score < 50 or hard risk flags.
- Do not add a runtime dependency or pretend that the snapshot is live.
- Every verdict must show evidence and a “what changes my mind” condition.
- Keep interactive controls keyboard accessible and honor `prefers-reduced-motion`.

### Task 1: Add decision data helpers and shell

**Files:**
- Modify: `index.html`
- Read: `data.json`

**Interfaces:**
- `scoreItem(item)` returns `{ score, verdict, confidence, upside, riskLine, entry, reasons, invalidation, signals }`.
- `renderDecision(item)` updates the selected-ticker surface.
- `renderWatchlist(items)` renders compact rows and stores ticker buttons.

- [ ] **Step 1: Implement deterministic scoring helpers** using the existing `rsi`, `vs_ma50`, `vs_ma200`, `rev_growth`, `roe`, `peg`, `d2e`, `fcf`, `beta`, `from_52w_high`, `target`, and `price` fields.
- [ ] **Step 2: Render the semantic header and selected decision surface** with explicit verdict, action text, entry, target, risk line, confidence, reasons, and invalidation.
- [ ] **Step 3: Render the five signal chips and compact watchlist rows** from `data.json`, including ETF fallback copy when fundamental values are unavailable.

### Task 2: Add the cockpit visual system and responsive layout

**Files:**
- Modify: `index.html`

**Interfaces:**
- CSS classes `.decision-card`, `.chart-card`, `.signal-grid`, `.watch-row`, `.verdict-*`, `.horizon-tabs`, and `.empty-state` are used by the render functions from Task 1.

- [ ] **Step 1: Replace the current mobile-only card styling** with a dark charcoal-blue, asymmetric two-column desktop layout and single-column mobile layout.
- [ ] **Step 2: Add chart annotations** for entry, target, and risk line using CSS-positioned guide lines over the existing sparkline data.
- [ ] **Step 3: Add focus, hover, pressed, reduced-motion, and narrow-screen states** without changing the static deployment shape.

### Task 3: Wire search, selection, filters, and horizon controls

**Files:**
- Modify: `index.html`

**Interfaces:**
- Search input filters rows and selects an exact ticker.
- Clicking a `.watch-row` calls `selectTicker(ticker)`.
- Horizon buttons update the decision copy without changing the snapshot’s numeric fields.

- [ ] **Step 1: Add event delegation** for search, watchlist rows, and horizon tabs.
- [ ] **Step 2: Add empty and no-data states** while preserving the decision shell.
- [ ] **Step 3: Validate the page locally** at desktop and mobile widths and confirm keyboard focus order.

### Task 4: Verify the static artifact

**Files:**
- Modify: `index.html` only if validation finds a defect.

- [ ] **Step 1: Run a local static server** and load the page.
- [ ] **Step 2: Exercise ticker selection, search, horizon changes, and empty search state.**
- [ ] **Step 3: Confirm the repository diff contains the spec, plan, and implementation only.**
- [ ] **Step 4: Commit the completed redesign with message `feat: add decision cockpit verdicts`.**
