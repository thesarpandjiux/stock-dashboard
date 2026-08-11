# Beginner Swing Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape the static decision cockpit into a beginner swing workflow with market benchmarks, a stable equity universe, and a maximum five-name focus list.

**Architecture:** Keep the zero-build GitHub Pages architecture. `data.json` remains the immutable snapshot source; `index.html` separates ETF benchmarks from equity candidates, derives execution statuses, enforces the semiconductor cap, and renders focus and fixed-watchlist surfaces. Node contract tests inspect the static artifact and execute pure ranking behavior where possible.

**Tech Stack:** HTML, CSS, vanilla JavaScript, Node built-in assertions, existing `data.json`.

## Global Constraints

- Fixed equity membership is `NVDA`, `AVGO`, `AMD`, `MU`, `AMZN`, `GOOGL`, `MELI`, and `LLY`.
- `QQQ` and `VOO` are benchmarks and never equity candidates.
- Focus list maximum is five equities and two semiconductor names.
- Missing earnings data always produces `CHECK EARNINGS`; the page does not invent a date.
- No scheduled refresh, interval timer, margin, options, shorting, or order placement.
- Manual refresh preserves the previous snapshot on failure.

---

### Task 1: Lock the swing workflow contract

**Files:**
- Modify: `tests/refresh.test.js`
- Create: `tests/swing-workflow.test.js`

**Interfaces:**
- `executionStatus(decision)` produces `READY`, `WAIT`, or `REJECT`.
- `buildFocusList(decisions)` excludes ETFs and returns at most five rows with at most two semiconductor tickers.

- [ ] **Step 1: Add failing static contract assertions** for benchmark, guardrail, focus-list, fixed-watchlist, and earnings-warning elements.
- [ ] **Step 2: Add failing ranking assertions** for ETF exclusion, five-name capacity, and two-semiconductor capacity.
- [ ] **Step 3: Run `node tests/refresh.test.js && node tests/swing-workflow.test.js`** and confirm failure because the new surfaces and ranking functions do not exist.

### Task 2: Implement candidate rules and separated data flows

**Files:**
- Modify: `index.html`

**Interfaces:**
- `executionStatus(d)` consumes the existing decision object.
- `buildFocusList(items)` returns qualified `READY` and `WAIT` decisions.
- `renderBenchmarks(items)`, `renderFocus(items)`, and `renderFixedWatchlist(items)` write their dedicated surfaces.

- [ ] **Step 1: Add execution status and focus-list functions** using the exact thresholds from the specification.
- [ ] **Step 2: Split ETFs from equities after each snapshot load** without changing fixed-watchlist membership.
- [ ] **Step 3: Enforce the five-name and two-semiconductor limits** before rendering.
- [ ] **Step 4: Run both Node tests** and confirm the behavior assertions pass.

### Task 3: Build the beginner workflow surfaces

**Files:**
- Modify: `index.html`

**Interfaces:**
- Elements `#guardrails`, `#benchmarks`, `#focusList`, and `#fixedWatchlist` are populated by Task 2 render functions.

- [ ] **Step 1: Add the guardrail and benchmark strips** above the decision card.
- [ ] **Step 2: Add the focus list beside or immediately below the decision card** with text statuses and earnings warning.
- [ ] **Step 3: Rename the lower ranking surface to fixed watchlist** and keep all eight equities visible.
- [ ] **Step 4: Add responsive styling and empty states** for narrow screens and no-qualified-setup outcomes.
- [ ] **Step 5: Run both Node tests and JavaScript syntax validation.**

### Task 4: Verify and commit

**Files:**
- Modify: `index.html` only if verification exposes a defect.

- [ ] **Step 1: Run `node tests/refresh.test.js && node tests/swing-workflow.test.js`.**
- [ ] **Step 2: Extract the inline script and validate it with `new Function(script)`.**
- [ ] **Step 3: Run `git diff --check` and confirm the page contains no interval or schedule language.**
- [ ] **Step 4: Commit the implementation with `feat: add beginner swing focus workflow`.**
