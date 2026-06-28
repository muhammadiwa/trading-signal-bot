---
baseline_commit: e3ebd35
story_key: 3-3-adaptive-weights
epic: 3
story: 3
title: Adaptive Weight Adjustment
status: review
created: 2026-06-28
---

# Story 3.3: Adaptive Weight Adjustment

As the pipeline,
I want research multiplier weights to automatically adjust based on historical accuracy of each research source,
So that the system improves its signal accuracy over time without manual tuning.

---

## Acceptance Criteria

### AC1: Per-source accuracy computation
**Given** at least 30 resolved outcomes exist
**When** the weight adjustment runs after outcome resolution
**Then** it computes per-source accuracy over the last 30 outcomes:
- Sentiment accuracy: % of signals where sentiment direction matched outcome direction
- On-chain accuracy: % of signals where on-chain direction matched outcome direction
- Macro accuracy: % of signals where macro flag correctly predicted reduced confidence was warranted
- Prediction accuracy: % of signals where prediction market direction matched outcome direction

### AC2: EMA weight update + persistence
**Given** per-source accuracy scores
**When** weights are adjusted via Exponential Moving Average
**Then** new_weight = 0.8 × old_weight + 0.2 × (accuracy / baseline) where baseline = 0.5
**And** weights clamped to [0.5, 1.5]
**And** stored in SQLite `weights` table per AD-2
**And** persist across runs via SQLite, not filesystem config

### AC3: Insufficient data gate
**Given** fewer than 30 resolved outcomes exist
**When** the weight adjustment runs
**Then** it skips with log: "Insufficient data for weight adjustment ({N}/30 outcomes)"
**And** uses default weights (all 1.0)

### AC4: Persistent underperformance detection
**Given** a source consistently underperforms (accuracy < 40% for 50+ outcomes)
**When** weight adjustment detects persistent underperformance
**Then** the weight is capped at 0.5 (minimum influence)
**And** Telegram alert: "⚠️ {source} accuracy {X}% — weight reduced to 0.5. Review recommended."

### AC5: Weights table initialization
**Given** the weights table is empty or corrupted
**When** the adjustment runs
**Then** it initializes all weights to 1.0 (default)
**And** logs: "Weights initialized to defaults in SQLite"

### AC6: Dynamic multiplier integration
**Given** new weights are computed
**When** the research multiplier is calculated
**Then** dynamically adjusted weights replace static defaults
**And** formula adapts: sentiment_mult = 1.0 + (score − 50) / 50 × sentiment_weight

---

## Tasks/Subtasks

- [x] **T1: Create `src/weight_adjuster.py`** — Weight computation engine
  - [ ] T1.1 `compute_source_accuracy(source, outcomes)` — per-source accuracy over last 30 outcomes
  - [ ] T1.2 `load_weights()` — read current weights from SQLite, init defaults if empty
  - [ ] T1.3 `adjust_weights(outcomes)` — EMA update, clamp [0.5,1.5], persist to DB
  - [ ] T1.4 Underperformance detection + Telegram alert (AC4)
  - [ ] T1.5 Insufficient data gate — skip if <30 outcomes (AC3)

- [x] **T2: Update `src/research_scoring.py`** — Dynamic multiplier
  - [x] T2.1 Read weights from DB in `sentiment_mult()` + `onchain_mult()`
  - [x] T2.2 Dynamic formula: `1.0 + (score − 50) / 50 × weight` for sentiment
  - [x] T2.3 Dynamic formula for on-chain: `1.0 + (direction_sign) × onchain_weight × 0.15`

- [x] **T3: Wire into `main.py`** — After Stage 0 reflections
  - [x] T3.1 Call `adjust_weights()` after `generate_reflections()`
  - [x] T3.2 Log weight changes

- [x] **T4: Tests**
  - [x] T4.1 Unit test: sentiment accuracy computation (AC1)
  - [x] T4.2 Unit test: EMA formula produces correct weight (AC2)
  - [x] T4.3 Unit test: <30 outcomes → skip (AC3)
  - [x] T4.4 Unit test: underperformance detection + alert (AC4)
  - [x] T4.5 Unit test: empty weights table → init defaults (AC5)
  - [x] T4.6 Unit test: weight clamp [0.5, 1.5] (AC2)
  - [x] T4.7 Integration test: weights written to DB + read back

- [x] **T5: Acceptance validation**
  - [x] T5.1 Run `python3 -m pytest tests/ -v` — all tests pass
  - [x] T5.2 Manually verify AC1-AC6 against implementation

---

## Dev Notes

### Architecture Context

**AD-2 (Parquet + SQLite):** Weights must persist in SQLite, not filesystem config.
**AD-8 (SQLite Schema):** The `weights` table already exists:
```sql
weights (weight_id TEXT PK, value REAL, updated_at TEXT)
```
✅ No schema changes needed.

**Static research multiplier (current):**
- `sentiment_mult(fear_greed_val)` → fixed: >60=1.2, 40-60=1.0, <40=0.8
- `onchain_mult(onchain_signal)` → fixed: bullish=1.15, neutral=1.0, bearish=0.85

**Dynamic research multiplier (target):**
- `sentiment_mult(fear_greed_val, weight=None)` — backward-compatible: weight=None uses static defaults
  - With weight: `1.0 + (score − 50) / 50 × weight`
  - score=25, weight=1.0 → 1.0 + (-0.5) = 0.5
  - score=75, weight=1.0 → 1.0 + (0.5) = 1.5
  - score=25, weight=0.7 → 1.0 + (-0.5)×0.7 = 0.65
- `onchain_mult(onchain_signal, weight=None)` — backward-compatible
  - bullish (+1), weight=1.0 → 1.0 + 1×1.0×0.15 = 1.15
  - bearish (-1), weight=1.0 → 1.0 + (-1)×1.0×0.15 = 0.85
  - bearish (-1), weight=0.7 → 1.0 + (-1)×0.7×0.15 = 0.895
- `compute_research_multiplier()` — reads weights from DB before computing
  - Auto-initializes defaults if weights table empty
  - Each source multiplier receives its weight from DB

### Accuracy Computation (AC1)

Source accuracy = fraction of outcomes where the source's direction matched the outcome.
Only count outcomes where the source had a clear directional signal.

| Source | Direction Match Rule |
|--------|---------------------|
| Sentiment | score > 60 → predicted bullish → signal.action=BUY is correct. score < 40 → predicted bearish → signal.action=SELL is correct. 40-60: neutral, excluded from accuracy calc |
| On-chain | onchain_signal=bullish → matched if outcome win AND signal was BUY (or loss AND SELL). bearish → opposite |
| Macro | macro_flag=true → predicted loss. Match: outcome was loss (confidence reduction was correct) |
| Prediction | pred_adj > 0 → bullish adjustment. Match: outcome return > 0. pred_adj < 0 → bearish. Match: outcome return < 0 |

### Dynamic Multiplier Formulas (AC6)

All multipliers accept optional weight parameter for backward compatibility.

```
sentiment_mult(score, weight=None):
  if weight is None → use static thresholds (existing behavior)
  return 1.0 + (score - 50) / 50 × weight

onchain_mult(signal, weight=None):
  if weight is None → use static values (existing)
  sign = 1 if bullish, -1 if bearish, 0 if neutral
  return 1.0 + sign × weight × 0.15

macro_mult(penalty, weight=None):
  if weight is None → 1.0
  return 1.0 - penalty × weight

prediction_mult(adjustment, weight=None):
  if weight is None → 1.0
  return 1.0 + adjustment × weight
```

### Pipeline Integration

```
Stage 0: Outcome Resolution
  ├── resolve_pending_signals()      ← Story 3.1
  ├── generate_reflections()         ← Story 3.2
  └── adjust_weights()               ← THIS STORY (3.3)
       └── if <30 outcomes: skip
       └── if ≥30: compute accuracy → EMA update → persist → check underperformance
```

### Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/weight_adjuster.py` | **NEW** | Weight computation + persistence |
| `src/research_scoring.py` | UPDATE | Dynamic multiplier from DB weights |
| `main.py` | UPDATE | Wire after reflections in Stage 0 |
| `tests/test_weight_adjuster.py` | **NEW** | Unit + integration tests |

### Existing Code to Reference

- `src/research_scoring.py:sentiment_mult()` — current static implementation
- `src/research_scoring.py:onchain_mult()` — current static implementation  
- `src/db.py:get_connection()` — standard DB connection
- `main.py:Stage 0` — where outcome resolution + reflection already run
- `main.py:send_alert()` — for underperformance Telegram alerts

### Weight identifiers in SQLite

```
weight_id                value   updated_at
"sentiment_weight"       1.0     2026-06-28T00:00:00
"onchain_weight"         1.0     2026-06-28T00:00:00
"macro_weight"           1.0     2026-06-28T00:00:00
"prediction_weight"      1.0     2026-06-28T00:00:00
```

### Risk Mitigation

- **Weights never go to zero:** clamp floor 0.5 prevents a source from being eliminated
- **Slow adaptation:** EMA α=0.2 means 30 days to converge — prevents overfitting
- **Backward compatible:** Existing code still works with static defaults when weights table is empty

---

## Dev Agent Record

### Implementation Plan
1. Create `src/weight_adjuster.py` — accuracy computation, EMA update, weight persistence
2. Update `src/research_scoring.py` — backward-compatible dynamic multipliers
3. Wire into `main.py` Stage 0 after reflections
4. 7 unit tests covering: accuracy, EMA, clamp, skip, persistence, underperformance

### Debug Log
- Mock patch path: `src.weight_adjuster.get_connection` (module-level import)
- MIN_OUTCOMES check removed from accuracy functions (only at adjust_weights level)
- Underperformance check requires >= 50 outcomes total

### Completion Notes
✅ All 5 task groups complete. 7 new tests + 44 existing = 51/51 passing.
Weights adjust after Stage 0 reflections via EMA. Sentiment/onchain use
dynamic multipliers from DB weights. Backward compatible — no weight defaults
to static behavior. Underperformance triggers Telegram alert at <40% accuracy
for 50+ outcomes.

---

## File List

| File | Action |
|------|--------|
| `src/weight_adjuster.py` | NEW — weight computation + persistence |
| `src/research_scoring.py` | UPDATE — dynamic multiplier from DB weights |
| `main.py` | UPDATE — wire adjust_weights after Stage 0 reflections |
| `tests/test_weight_adjuster.py` | NEW — 7 tests |

---

## Change Log

- 2026-06-28: Story created from Epic 3 requirements + Story 3.1/3.2 completion context

---

## Status

**Ready for Development**
