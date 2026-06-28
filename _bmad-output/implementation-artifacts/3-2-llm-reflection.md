---
baseline_commit: 2ed66c7
story_key: 3-2-llm-reflection
epic: 3
story: 2
title: LLM Reflection Generator
status: in-progress
created: 2026-06-28
---

# Story 3.2: LLM Reflection Generator

As the pipeline,
I want resolved signal outcomes to be reflected upon by an LLM, generating 1-2 sentence insights,
So that the system accumulates qualitative lessons that improve future confidence weighting.

---

## Acceptance Criteria

### AC1: Full prompt construction
**Given** a resolved outcome with realized_return_pct and the original signal's full context
**When** the LLM reflection is triggered
**Then** a prompt is constructed containing:
- Symbol, action, entry_price, exit_price, return_pct
- Strategy name and technical confidence
- Research context at signal time (sentiment, on-chain, macro)
- Whether the signal was a win or loss

### AC2: LLM call configuration
**Given** the reflection prompt is constructed
**When** ChatLLM is called with model from config `settings.yaml` (llm.model, default: "deepseek/deepseek-v4-pro") via the provider defined in config (llm.provider, default: "tokenrouter")
**Then** the call has a 3-second timeout (llm.timeout_seconds)
**And** max_tokens is limited to 150 (llm.max_tokens)
**And** the prompt explicitly instructs: "Return ONLY 1-2 sentences. No markdown, no analysis, no recommendations."

### AC3: Successful reflection storage
**Given** the LLM returns a valid response
**When** the reflection is parsed
**Then** example output: "SELL signal at $60,200 captured the death cross correctly (+2.8%). However, on-chain outflow suggested accumulation — the research multiplier's on-chain weight may need reduction when exchange flow and price action diverge."
**And** the reflection text is stored in the outcomes table (reflection_text column)
**And** the LLM call is logged to llm_call_log with tokens used and cost

### AC4: LLM failure → deterministic fallback
**Given** the LLM call times out (3 seconds) or returns an error
**When** the reflection fails
**Then** a fallback reflection is generated deterministically:
**And** "BTC-USDT SELL: +2.8% — signal aligned with price movement."
**And** the outcome is still stored with reflection_text = fallback text
**And** `llm_used = False` is flagged in the outcome

### AC5: Response truncation
**Given** the LLM returns a response longer than 300 characters
**When** the reflection is parsed
**Then** it is truncated to 300 characters with "..." appended
**And** the truncation is logged

### AC6: TokenRouter API failure handling
**Given** TokenRouter API key is invalid or credit exhausted
**When** the LLM reflection is attempted
**Then** it falls back to deterministic reflection immediately
**And** logs: "LLM unavailable — using deterministic reflection"
**And** does NOT crash the pipeline

---

## Tasks/Subtasks

- [x] **T1: Create `src/reflection.py`** — LLM reflection engine
  - [x] T1.1 `build_reflection_prompt(outcome, signal)` — construct the prompt per AC1
  - [x] T1.2 `call_llm(prompt, config)` — call TokenRouter API with timeout + max_tokens
  - [x] T1.3 `generate_reflection(outcome, signal, config)` — main orchestrator
  - [x] T1.4 Parse + truncate LLM response (AC5)
  - [x] T1.5 Deterministic fallback on any failure (AC4+AC6)

- [x] **T2: Wire into `main.py` pipeline** — After Stage 0 outcome resolution
  - [x] T2.1 Call `generate_reflection()` for each resolved outcome
  - [x] T2.2 Update `outcomes` row with `reflection_text` + `llm_used` flag
  - [x] T2.3 Pass LLM config from `Settings` to the reflection module

- [x] **T3: DB changes** — llm_call_log table
  - [x] T3.1 Add `llm_call_log` table to `src/db.py` schema (id, prompt, response, tokens_used, cost, called_at, model, status)
  - [x] T3.2 Log each LLM call (success + failure) to llm_call_log

- [x] **T4: Tests**
  - [x] T4.1 Unit test: prompt contains all required fields (AC1)
  - [x] T4.2 Unit test: deterministic fallback format matches spec (AC4)
  - [x] T4.3 Unit test: response truncated at 300 chars (AC5)
  - [x] T4.4 Unit test: LLM failure → fallback + llm_used=False
  - [x] T4.5 Integration test: resolved outcome → reflection stored in DB

- [x] **T5: Acceptance validation**
  - [x] T5.1 Run `python3 -m pytest tests/ -v` — all tests pass
  - [x] T5.2 Manually verify AC1-AC6 against implementation

---

## Dev Notes

### Architecture Context

**AD-1 (LLM Boundary):** This story IS the LLM boundary. It is the ONLY place where LLM is used in the entire pipeline. Rules:
- LLM MUST ONLY be used for deferred outcome reflection
- 3s timeout, fallback to skip on failure
- No LLM in signal generation, confidence scoring, or strategy matching

**AD-8 (SQLite Schema):** The `outcomes` table already has these columns:
```sql
outcomes (
    ...
    reflection_text TEXT,
    llm_used INTEGER DEFAULT 0
)
```
✅ The `reflection_text` and `llm_used` columns already exist. Story 3.1 writes them as NULL/0.

**New table — llm_call_log:**
```sql
CREATE TABLE IF NOT EXISTS llm_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT,        -- First 500 chars of prompt
    response TEXT,       -- Truncated to 500 chars
    tokens_used INTEGER,
    cost REAL,
    called_at TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL  -- "success" | "timeout" | "error"
);
```

### Pipeline Integration

```
Stage 0: Outcome Resolution
  ├── resolve_pending_signals() — Story 3.1
  └── generate_reflections(resolved_outcomes) — THIS STORY
       └── For each resolved outcome:
            ├── Fetch original signal from signals table
            ├── Build reflection prompt
            ├── Call LLM (with timeout)
            └── Store reflection_text in outcomes
```

### Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/reflection.py` | **NEW** | LLM reflection engine |
| `main.py` | UPDATE | Wire reflection after Stage 0 |
| `src/db.py` | UPDATE | Add llm_call_log table |
| `tests/test_reflection.py` | **NEW** | Unit + integration tests |

### Existing Code to Reference

- `src/outcome_tracker.py:resolve_pending_signals()` — returns list of resolved outcomes
- `src/config.py:Settings` — llm_provider, llm_model, llm_timeout_seconds, llm_max_tokens
- `src/db.py:get_connection()` — standard DB connection
- `main.py:_compute_7day_win_rate()` — pattern for DB query

### LLM API Call Pattern

TokenRouter is an OpenAI-compatible API proxy. Use the standard `requests` library (no SDK needed):

```python
import requests, os, time

api_key = os.getenv("TOKENROUTER_API_KEY", "")
base_url = os.getenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1")

response = requests.post(
    f"{base_url}/chat/completions",
    headers={"Authorization": f"Bearer {api_key}"},
    json={
        "model": config.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": config.llm_max_tokens,
        "temperature": 0.3,
    },
    timeout=config.llm_timeout_seconds,
)
```

### Deterministic Fallback (AC4)

```
"{symbol} {action}: {return_pct:+.1f}% — signal aligned with price movement."

- BUY win: "BTC-USDT BUY: +3.2% — price moved favorably after signal."
- SELL win: "BTC-USDT SELL: +2.8% — signal aligned with price movement."
- Loss: "ETH-USDT BUY: −1.5% — signal did not work out this time."
```

### Blockers / Dependencies

- Oracle: Story 3.1 must be complete (outcomes table populated)
- Oracle: `TOKENROUTER_API_KEY` must be set in `.env`
- Oracle: `TOKENROUTER_BASE_URL` defaults to `https://api.tokenrouter.com/v1`
- If API key is missing → immediate fallback, no crash

### Risk Mitigation

- **LLM down:** Deterministic fallback catches ALL exception types
- **Rate limited:** 3s timeout prevents pipeline hang
- **Cost control:** max_tokens=150, ~$0.001/call, ~$0.02/day for 20 signals
- **No retry:** Unlike data fetchers, single attempt only — reflection is non-critical

---

## Dev Agent Record

### Implementation Plan
_To be filled by dev agent during implementation._

### Debug Log
_To be filled by dev agent if issues encountered._

### Completion Notes
_To be filled by dev agent after completion._

---

## File List

_To be filled during implementation._

---

## Change Log

- 2026-06-28: Story created from Epic 3 requirements (Epics-Stories-Epic3.md) + Story 3.1 completion context

---

## Status

**Ready for Development**
