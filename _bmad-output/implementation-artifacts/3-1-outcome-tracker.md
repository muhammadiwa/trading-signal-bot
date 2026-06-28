---
baseline_commit: d6ee6ee
story_key: 3-1-outcome-tracker
epic: 3
story: 1
title: Outcome Tracker
status: in-progress
created: 2026-06-28
---

# Story 3.1: Outcome Tracker

As the pipeline,
I want yesterday's pending signals to be resolved with realized returns,
So that the system knows which signals were profitable and can learn from outcomes.

---

## Acceptance Criteria

### AC1: Resolve pending signals with current prices
**Given** signals from the previous run have status="pending" in SQLite
**When** the next pipeline run starts (before generating new signals)
**Then** it fetches the current price for each pending signal's symbol via CCXT
**And** computes realized_return_pct = (current_price − entry_price) / entry_price × 100 for BUY
**And** computes realized_return_pct = (entry_price − current_price) / entry_price × 100 for SELL
**And** the signal's status is updated to "resolved"

### AC2: Write outcomes to SQLite
**Given** a resolved signal
**When** the outcome is written to the outcomes table
**Then** the row contains: signal_id (FK), realized_return_pct, price_at_resolution, resolved_at timestamp
**And** the original signal row's status changes from "pending" to "resolved"

### AC3: Correct return calculation
**Given** a signal whose entry price was 60200 and current price is 58500 (SELL signal)
**When** realized_return_pct is computed
**Then** return = (60200 − 58500) / 60200 × 100 = +2.82%
**And** the outcome is flagged as "win" if return > 0, "loss" if return < 0

### AC4: Graceful handling of no pending signals
**Given** no pending signals exist (first run or all resolved)
**When** the outcome tracker runs
**Then** it completes immediately with log: "No pending signals to resolve"
**And** does not raise an error

### AC5: Handle delisted/unreachable symbols
**Given** a pending signal's symbol can no longer be fetched (delisted)
**When** the outcome tracker attempts to resolve
**Then** it marks the signal as "unresolvable" with error: "Symbol {X} no longer available"
**And** the outcome is stored with realized_return_pct = None

### AC6: Rolling 7-day win rate
**Given** the outcomes table is updated
**When** the rolling 7-day win rate is computed
**Then** it queries: `SELECT AVG(CASE WHEN realized_return_pct > 0 THEN 1 ELSE 0 END) FROM outcomes WHERE resolved_at > date('now', '-7 days')`
**And** the result is cached for use in the Telegram summary message and reflection prompt

---

## Tasks/Subtasks

- [x] **T1: Create `src/outcome_tracker.py`** — Resolution engine
  - [x] T1.1 `resolve_pending_signals()` — fetch all pending signals from SQLite
  - [x] T1.2 Fetch current price per symbol via CCXT (reuse `src/exchange.py`)
  - [x] T1.3 Compute `realized_return_pct` using AC3 formula
  - [x] T1.4 Write outcomes to `outcomes` table, update signal status to "resolved"
  - [x] T1.5 Handle delisted symbols — mark as "unresolvable"
  - [x] T1.6 Handle no pending signals — log and return early

- [x] **T2: Wire into `main.py` pipeline** — Stage 0 before signal generation
  - [x] T2.1 Add outcome resolution as pipeline stage (before data_fetch)
  - [x] T2.2 Reuse existing `_compute_7day_win_rate()` — pass result to Telegram stage
  - [x] T2.3 Store `win_rate_7d` for use in Telegram summary + reflection prompt

- [x] **T3: DB schema validation** — Ensure `outcomes` table matches requirements
  - [x] T3.1 Verify `outcomes` table has: signal_id FK, realized_return_pct, price_at_resolution, resolved_at, reflection_text, llm_used
  - [x] T3.2 Add `resolved_at` index for AC6 query performance

- [x] **T4: Tests**
  - [x] T4.1 Unit test: BUY signal return calculation (entry 60200, current 61500 → +2.16%)
  - [x] T4.2 Unit test: SELL signal return calculation (entry 60200, current 58500 → +2.82%)
  - [x] T4.3 Unit test: no pending signals → returns empty, logs message
  - [x] T4.4 Unit test: delisted symbol → "unresolvable" status
  - [x] T4.5 Integration test: resolve → outcome written to DB

- [x] **T5: Acceptance validation**
  - [x] T5.1 Run `python3 -m pytest tests/ -v` — all existing + new tests pass
  - [x] T5.2 Manually verify AC1-AC6 against implementation

---

## Dev Notes

### Architecture Context

**AD-8 (SQLite Schema):** The `outcomes` table already exists with schema:
```sql
outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL UNIQUE REFERENCES signals(id),
    realized_return_pct REAL,
    price_at_resolution REAL,
    resolved_at TEXT NOT NULL,
    reflection_text TEXT,
    llm_used INTEGER DEFAULT 0
)
```
✅ No schema changes needed — the table is already correct for this story.

**AD-1 (LLM Boundary):** LLM is NOT used in this story. Outcome resolution is purely deterministic computation. LLM reflection is Story 3.2.

**Existing code reference:** `main.py:_compute_7day_win_rate()` already queries the outcomes table. This function is already wired into the pipeline. Story 3.1 makes it operational by populating the outcomes.

### Pipeline Integration

This stage runs **BEFORE** signal generation in the pipeline timeline:
```
23:00 UTC — Pipeline Start
  ├── Stage 0: Outcome Resolution ← THIS STORY
  │   └── Fetch yesterday's pending signals
  │   └── Resolve with current prices
  │   └── Write outcomes + update signal status
  ├── Stage 1: Data Fetch
  ├── Stage 2: Profile + Strategy Match
  ├── Stage 3: Research Context
  ├── Stage 4: Confidence + Filter
  └── Stage 5: Telegram Delivery
```

### Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/outcome_tracker.py` | **NEW** | Resolution engine |
| `main.py` | UPDATE | Add Stage 0 to pipeline |
| `tests/test_outcome_tracker.py` | **NEW** | Unit + integration tests |

### Existing Code to Reference

- `src/exchange.py:fetch_ohlcv()` — reuse for fetching current price (use `force_refresh=True, limit=1`)
- `src/db.py:get_connection()` — standard DB connection
- `main.py:_compute_7day_win_rate()` — already queries outcomes table
- `main.py:_send_weekly_digest()` — already uses outcomes for weekly stats

### Return Calculation (AC3)

```
BUY:  realized_return_pct = (current_price - entry_price) / entry_price * 100
SELL: realized_return_pct = (entry_price - current_price) / entry_price * 100

Win:  realized_return_pct > 0
Loss: realized_return_pct < 0
```

### Error Handling

| Scenario | Behavior |
|----------|----------|
| No pending signals | Log "No pending signals to resolve", return empty list |
| CCXT fetch fails (delisted) | Catch exception, mark as "unresolvable", store `realized_return_pct = None` |
| DB write fails | Catch exception, log error, continue pipeline (don't block) |
| Multiple signals same symbol | Deduplicate: fetch price once, apply to all signals for that symbol |

### Blockers / Dependencies

- Oracle: CCXT must be able to fetch current ticker price (already verified in health check)
- Oracle: `outcomes` table must exist (already created by `init_db()`)
- Oracle: `signals` table must have rows with `status = "pending"` (from previous pipeline runs)

### Risk Mitigation

- **First run:** No pending signals → outcome tracker is a no-op. Pipeline proceeds normally.
- **Partial resolution:** If price fetch fails for one symbol, other symbols still resolve. One failure does not block others.
- **Duplicate prevention:** `outcomes.signal_id UNIQUE` constraint prevents double-resolution.

---

## Dev Agent Record

### Implementation Plan
1. Create `src/outcome_tracker.py` with `compute_return()`, `resolve_pending_signals()`, `_compute_win_rate_7d()`
2. Wire as Stage 0 in `main.py` pipeline — before data_fetch
3. Cache `_win_rate_7d_cache` and pass to Telegram stage
4. 8 unit tests covering: BUY return, SELL return, loss, no pending, write outcome, delisted, 7d WR, dedup

### Debug Log
- Mock patch path needed `src.db.get_connection` (not `src.outcome_tracker.get_connection`) because import is inside function scope
- DB connection close() in resolve function required MagicMock no-op wrapper for tests

### Completion Notes
✅ All 5 task groups complete. 8 new tests + 28 existing = 36/36 passing.
Stage 0 runs before signal generation — resolves yesterday's pending signals,
fetches current prices via CCXT, writes outcomes to SQLite, updates signal status.
7-day win rate cached and passed to Telegram summary message.

---

## File List

| File | Action |
|------|--------|
| `src/outcome_tracker.py` | NEW — Resolution engine |
| `main.py` | UPDATE — Stage 0 + win_rate_7d caching |
| `tests/test_outcome_tracker.py` | NEW — 8 tests |

---

## Change Log

- 2026-06-28: Story created from Epic 3 requirements (Epics-Stories-Epic3.md)
- 2026-06-28: Implementation complete — 36/36 tests passing, AC1-AC6 verified

---

## Status

**review**
