## Epic 2: Research Context Integration

### Story 2.1: Sentiment Data Fetcher

As the pipeline,
I want to fetch Fear & Greed Index, Reddit crypto sentiment, and Twitter trending data,
So that each signal is enriched with market-wide sentiment context.

**Acceptance Criteria:**

**Given** the pipeline reaches the research fetch stage
**When** the sentiment fetcher runs for all active pairs
**Then** Fear & Greed Index is fetched from `https://api.alternative.me/fng/?limit=1`
**And** the value (0-100) and classification (Fear/Neutral/Greed) are stored
**And** if the API fails → sentiment_score defaults to 50 (neutral) with warning logged

**Given** Reddit RSS feeds are reachable
**When** the sentiment fetcher parses r/cryptocurrency and r/bitcoin
**Then** it counts bullish vs bearish keyword mentions from post titles in last 24h
**And** computes a ratio: bullish_mentions / (bullish + bearish) from post titles
**And** keyword counting is on titles only (limited accuracy — noted in log; upvote-weighted sentiment deferred to v2)
**And** if Reddit is unreachable → skips, weight redistributed to Fear & Greed

**Given** Twitter/X trending is configured
**When** the sentiment fetcher checks crypto hashtag volume
**Then** it computes a polarity score based on tweet volume change vs 7-day average
**And** if Twitter API is unavailable → skips, weight redistributed to remaining sources

**Given** all sentiment sources are fetched
**When** the composite score is computed
**Then** composite = 0.4 × FearGreed_norm + 0.3 × Reddit_ratio + 0.3 × Twitter_polarity
**And** weights auto-adjust if a source is unavailable (remaining weights normalized to sum 1.0)
**And** the result is stored as `sentiment_score` (0-100) in the pair's research metadata

---

### Story 2.2: On-Chain Data Fetcher

As the pipeline,
I want to fetch exchange net flow, whale transaction data, and active address trends,
So that signals are validated against actual on-chain capital movements.

**Acceptance Criteria:**

**Given** the pipeline reaches the research fetch stage
**When** the on-chain fetcher runs for all active pairs
**Then** exchange net flow is fetched from Whale Alert API (free tier)
**And** computes: inflow_volume − outflow_volume for last 24h
**And** positive net flow (more inflow) → bearish signal
**And** negative net flow (more outflow) → bullish signal

**Given** whale transaction data is available
**When** the fetcher counts transactions > $1M in last 24h
**Then** it separates buy-side vs sell-side whale activity
**And** computes whale_buy_ratio = buy_count / (buy_count + sell_count)
**And** ratio > 0.6 → bullish, ratio < 0.4 → bearish, else neutral

**Given** CoinGecko API is reachable
**When** the fetcher checks active address trend
**Then** it compares current active addresses vs 7-day moving average
**And** rising trend → adds to bullish signal
**And** declining trend → adds to bearish signal

**Given** Whale Alert API is unreachable
**When** the on-chain fetcher attempts to fetch
**Then** it logs a warning: "Whale Alert unavailable — on-chain signal skipped"
**And** sets onchain_signal to "neutral" with multiplier 1.0 (no adjustment)
**And** does NOT crash the pipeline

**Given** all on-chain sources are fetched
**When** the composite on-chain signal is computed
**Then** onchain_signal ∈ {"bullish", "neutral", "bearish"}
**And** the classification rules are deterministic (documented in FR3.2)
**And** the result is stored in the pair's research metadata

---

### Story 2.3: Macro Calendar Overlay

As the pipeline,
I want to check an economic calendar for high-impact events (FOMC, CPI, NFP) near the signal date,
So that confidence is automatically reduced when trading into known volatility events.

**Acceptance Criteria:**

**Given** `config/macro_calendar.json` exists and is maintained
**When** the macro overlay runs for the current date
**Then** it loads the JSON calendar file
**And** checks for events within 24h (high-impact penalty) and 48h (medium-impact penalty)
**And** the JSON format is: `[{"date": "2026-07-01", "event": "FOMC Minutes", "impact": "high"}, ...]`

**Given** a high-impact event (FOMC, CPI, NFP) is within 24h of signal date
**When** the macro overlay computes the penalty
**Then** macro_penalty = 0.20 (20% confidence reduction)
**And** macro_flag = True for all signals on that date

**Given** a high-impact event is within 48h but not 24h
**When** the macro overlay computes the penalty
**Then** macro_penalty = 0.10 (10% confidence reduction)
**And** the signal carries a ⚠️ warning: "FOMC in 2 days — elevated volatility expected"

**Given** a medium-impact event (GDP, retail sales) is within 24h
**When** the macro overlay computes the penalty
**Then** macro_penalty = 0.10 (half of high-impact)

**Given** the macro calendar file is missing or unparseable
**When** the overlay runs
**Then** it logs a warning and sets macro_penalty = 0.0, macro_flag = False
**And** the pipeline continues without macro adjustment

**Given** macro_penalty is computed
**When** it feeds into the research multiplier
**Then** research_multiplier = sentiment_mult × onchain_mult × (1 − macro_penalty)
**And** the multiplier is clamped to [0.5, 1.5] per FR3.4

---

### Story 2.4: Prediction Markets Fetcher

As the pipeline,
I want to fetch Polymarket probabilities for crypto-relevant events,
So that forward-looking market expectations are factored into signal confidence.

**Acceptance Criteria:**

**Given** the pipeline reaches the research fetch stage
**When** the prediction markets fetcher runs
**Then** it queries Polymarket Gamma API: `https://gamma-api.polymarket.com/markets?tag=crypto&limit=20`
**And** extracts markets with crypto-relevant keywords (BTC, ETH, rate, regulation, ETF)
**And** stores the top 5 markets by volume with their probabilities

**Given** Polymarket shows "Fed rate cut by July" at 72% probability
**When** the predictor scores this for signal relevance
**Then** if probability > 70% for a bullish event → research multiplier +0.05
**And** if probability > 70% for a bearish event → research multiplier −0.05
**And** if no clear direction → neutral (no adjustment)

**Given** Polymarket API is unreachable or returns no crypto markets
**When** the fetcher fails
**Then** it logs a warning and sets prediction_multiplier = 1.0
**And** the pipeline continues without prediction market adjustment

**Given** prediction market data is stale (older than 12 hours for active markets)
**When** the freshness check runs
**Then** it logs a warning and reduces the weight of prediction markets in the multiplier
**And** the pipeline continues with reduced prediction influence

---

### Story 2.5: Research Multiplier Engine

As the pipeline,
I want all research dimensions (sentiment, on-chain, macro, prediction markets) combined into a single research multiplier,
So that the final signal confidence reflects both technical and research-driven factors.

**Acceptance Criteria:**

**Given** sentiment_score (0-100), onchain_signal (bullish/neutral/bearish), macro_penalty (0.0-0.20), prediction_adjustment (-0.05 to +0.05)
**When** the research multiplier is computed
**Then** sentiment_mult = 1.2 if score > 60, 1.0 if 40-60, 0.8 if < 40
**And** onchain_mult = 1.15 if bullish, 1.0 if neutral, 0.85 if bearish
**And** base_multiplier = sentiment_mult × onchain_mult × (1 − macro_penalty) + prediction_adjustment
**And** final multiplier is clamped to [0.5, 1.5] per FR3.4

**Given** any research source failed and produced default/neutral values
**When** the multiplier is computed
**Then** the failed source contributes 1.0 (no bias) and the remaining sources carry full influence
**And** a warning is logged: "Research multiplier: {N} of 4 sources active"

**Given** the multiplier is applied to a signal's technical confidence of 0.73
**When** Final Confidence = Technical_Confidence × Research_Multiplier
**Then** if multiplier = 0.85 → Final = 0.62 (62%)
**And** if multiplier = 1.15 → Final = 0.84 (84%)
**And** the result is rounded to 2 decimal places

**Given** the research multiplier is computed
**When** stored in the signal metadata
**Then** each contributing dimension is stored individually for transparency:
**And** `research_metadata = {sentiment_score, sentiment_mult, onchain_signal, onchain_mult, macro_flag, macro_penalty, prediction_adjustment, final_multiplier}`
**And** all values are logged for auditability

**Given** all 4 research sources are completely unavailable
**When** the multiplier is computed
**Then** it defaults to 1.0 (no adjustment)
**And** the signal is flagged: "research_unavailable=true"
**And** a Telegram alert is sent: "⚠️ Research data unavailable — signals using technical confidence only"

---

### Story 2.6: Signal Enhancement with Research Context

As the pipeline,
I want each signal to include research context in its Telegram message,
So that the user understands WHY the confidence is what it is, not just the number.

**Acceptance Criteria:**

**Given** this story extends the Telegram formatter built in Story 1.7
**When** research context injection is implemented
**Then** the formatter module supports adding research lines without modifying core signal formatting logic
**And** Story 1.7's existing ACs continue to pass unchanged

**Given** a signal with full research metadata from Story 2.5
**When** the signal is formatted for Telegram (extending Story 1.7's formatter)
**Then** the signal block includes research context lines after SL/TP:

```
🔴 SELL — BTC/USDT $60,200
Trend Following | Conf 62%
SL: 61,500 | TP: 56,000
📊 Sentiment: Fear 25/100
🔗 On-chain: Bearish ($200M inflow)
📅 Macro: FOMC in 2 days ⚠️
🗳️ Polymarket: 68% rate hold
📈 Track: 4/5 win (+2.1%)
```

**Given** a signal with partial research data (e.g., on-chain available but prediction markets failed)
**When** the message is formatted
**Then** only available research lines are shown (no "On-chain: —" or empty placeholders)
**And** the message remains compact and readable

**Given** a signal where all research sources defaulted (research_unavailable=true)
**When** the message is formatted
**Then** the research context section is omitted entirely
**And** the signal shows: "(Technical confidence only — research data unavailable)"

**Given** the Signal is saved to SQLite
**When** the research_metadata JSON field is populated
**Then** it includes the full breakdown from Story 2.5
**And** can be queried later for analytics (which source improved/diminished accuracy)
