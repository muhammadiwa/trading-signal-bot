## Epic 3: Self-Improving Accuracy

### Story 3.1: Outcome Tracker

As the pipeline,
I want yesterday's pending signals to be resolved with realized returns,
So that the system knows which signals were profitable and can learn from outcomes.

**Acceptance Criteria:**

**Given** signals from the previous run have status="pending" in SQLite
**When** the next pipeline run starts (before generating new signals)
**Then** it fetches the current price for each pending signal's symbol via CCXT
**And** computes realized_return_pct = (current_price − entry_price) / entry_price × 100 for BUY
**And** computes realized_return_pct = (entry_price − current_price) / entry_price × 100 for SELL
**And** the signal's status is updated to "resolved"

**Given** a resolved signal
**When** the outcome is written to the outcomes table
**Then** the row contains: signal_id (FK), realized_return_pct, price_at_resolution, resolved_at timestamp
**And** the original signal row's status changes from "pending" to "resolved"

**Given** a signal whose entry price was 60200 and current price is 58500 (SELL signal)
**When** realized_return_pct is computed
**Then** return = (60200 − 58500) / 60200 × 100 = +2.82%
**And** the outcome is flagged as "win" if return > 0, "loss" if return < 0

**Given** no pending signals exist (first run or all resolved)
**When** the outcome tracker runs
**Then** it completes immediately with log: "No pending signals to resolve"
**And** does not raise an error

**Given** a pending signal's symbol can no longer be fetched (delisted)
**When** the outcome tracker attempts to resolve
**Then** it marks the signal as "unresolvable" with error: "Symbol {X} no longer available"
**And** the outcome is stored with realized_return_pct = None

**Given** the outcomes table is updated
**When** the rolling 7-day win rate is computed
**Then** it queries: `SELECT AVG(CASE WHEN realized_return_pct > 0 THEN 1 ELSE 0 END) FROM outcomes WHERE resolved_at > date('now', '-7 days')`
**And** the result is cached for use in the Telegram summary message and reflection prompt

---

### Story 3.2: LLM Reflection Generator

As the pipeline,
I want resolved signal outcomes to be reflected upon by an LLM, generating 1-2 sentence insights,
So that the system accumulates qualitative lessons that improve future confidence weighting.

**Acceptance Criteria:**

**Given** a resolved outcome with realized_return_pct = +2.82% and the original signal's full context
**When** the LLM reflection is triggered
**Then** a prompt is constructed containing:
- Symbol, action, entry_price, exit_price, return_pct
- Strategy name and technical confidence
- Research context at signal time (sentiment, on-chain, macro)
- Whether the signal was a win or loss

**Given** the reflection prompt is constructed
**When** ChatLLM is called with model from config `settings.yaml` (llm.model, default: "deepseek/deepseek-v4-pro") via the provider defined in config (llm.provider, default: "tokenrouter")
**Then** the call has a 3-second timeout
**And** max_tokens is limited to 150 (1-2 sentences only)
**And** the prompt explicitly instructs: "Return ONLY 1-2 sentences. No markdown, no analysis, no recommendations."

**Given** the LLM returns a valid response
**When** the reflection is parsed
**Then** example output: "SELL signal at $60,200 captured the death cross correctly (+2.8%). However, on-chain outflow suggested accumulation — the research multiplier's on-chain weight may need reduction when exchange flow and price action diverge."
**And** the reflection text is stored in the outcomes table (reflection_text column)
**And** the LLM call is logged to llm_call_log with tokens used and cost

**Given** the LLM call times out (3 seconds) or returns an error
**When** the reflection fails
**Then** a fallback reflection is generated deterministically:
**And** "BTC-USDT SELL: +2.8% — signal aligned with price movement."
**And** the outcome is still stored with reflection_text = fallback text
**And** `llm_used = False` is flagged in the outcome

**Given** the LLM returns a response longer than 300 characters
**When** the reflection is parsed
**Then** it is truncated to 300 characters with "..." appended
**And** the truncation is logged

**Given** TokenRouter API key is invalid or credit exhausted
**When** the LLM reflection is attempted
**Then** it falls back to deterministic reflection immediately
**And** logs: "LLM unavailable — using deterministic reflection"
**And** does NOT crash the pipeline

---

### Story 3.3: Adaptive Weight Adjustment

As the pipeline,
I want research multiplier weights to automatically adjust based on historical accuracy of each research source,
So that the system improves its signal accuracy over time without manual tuning.

**Acceptance Criteria:**

**Given** at least 30 resolved outcomes exist (enough data for statistical significance)
**When** the weight adjustment runs after outcome resolution
**Then** it computes per-source accuracy over the last 30 outcomes:
- Sentiment accuracy: % of signals where sentiment direction matched outcome direction
- On-chain accuracy: % of signals where on-chain direction matched outcome direction
- Macro accuracy: % of signals where macro flag correctly predicted reduced confidence was warranted (signal would have been a loss)
- Prediction accuracy: % of signals where prediction market direction matched outcome direction

**Given** per-source accuracy scores
**When** the weights are adjusted via Exponential Moving Average
**Then** new_weight = 0.8 × old_weight + 0.2 × (accuracy / baseline)
**And** baseline = 0.5 (random chance)
**And** weights are clamped to [0.5, 1.5]
**And** the new weights are stored in SQLite `weights` table (created if not exists: `weight_id TEXT PK, value REAL, updated_at TEXT`) per AD-2
**And** weights persist across runs via SQLite, not filesystem config

**Given** fewer than 30 resolved outcomes exist
**When** the weight adjustment runs
**Then** it skips with log: "Insufficient data for weight adjustment ({N}/30 outcomes)"
**And** uses default weights (all 1.0)

**Given** a source consistently underperforms (accuracy < 40% for 50+ outcomes)
**When** the weight adjustment detects persistent underperformance
**Then** the weight is capped at 0.5 (minimum influence)
**And** a Telegram alert is sent: "⚠️ {source} accuracy {X}% — weight reduced to 0.5. Review recommended."

**Given** the weights table is empty or corrupted
**When** the adjustment runs
**Then** it initializes all weights to 1.0 (default)
**And** logs: "Weights initialized to defaults in SQLite"

**Given** new weights are computed
**When** the research multiplier is calculated in Story 2.5
**Then** the dynamically adjusted weights replace the static defaults
**And** the multiplier formula adapts: sentiment_mult = 1.0 + (score − 50) / 50 × sentiment_weight
**And** similarly for on-chain and other sources

**Given** weights have been adjusted for 90+ days
**When** a weekly performance report is generated
**Then** it includes a section: "Weight Evolution" showing how each source's weight changed over time
**And** correlates weight changes with overall signal accuracy trends
