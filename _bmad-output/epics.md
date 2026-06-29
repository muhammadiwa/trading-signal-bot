---
stepsCompleted: ["step-01", "step-02"]
status: final
inputDocuments:
  - _bmad-output/prd/trading-signal-bot/prd.md
  - _bmad-output/architecture/ARCHITECTURE-SPINE.md
  - _bmad-output/specs/spec-trading-signal-bot/SPEC.md
---

# Trading Signal Bot - Epic Breakdown

## Requirements Inventory

### Functional Requirements

FR1.1: Fetch 6-12 months OHLCV for top 100 coins via CCXT with Binance→OKX→CoinGecko fallback. Cache Parquet. Reject data > 4h old.

FR1.2: Fetch Fear & Greed Index, Reddit crypto RSS, Twitter trending. Compute composite sentiment score 0-100.

FR1.3: Fetch exchange net flow, whale tx (>$1M), active address trend via Whale Alert + CoinGecko. Output bullish/neutral/bearish signal.

FR1.4: Maintain economic calendar JSON. Flag high-impact events (FOMC, CPI, NFP) within 24h/48h. Medium-impact within 24h.

FR1.5: Fetch Polymarket probabilities for crypto events via Gamma API. Score as forward-looking risk overlay.

FR2.1: Compute 4D profile per pair: trendiness (ADX), volatility (ATR/close), mean-reversion (RSI bounce freq), volume quality (CV + price corr). Rolling 90-day.

FR2.2: Implement 5 strategies: Momentum Breakout, Trend Following, Mean Reversion, Volatility Breakout, Volume-Price Divergence.

FR2.3: Match profile to strategy via deterministic rules. Unclear profile → try all strategies in priority order.

FR2.4: Backtest matched strategy on 6-month data. Gate: win rate ≥ 40%, Sharpe ≥ 0.5. Fail → next strategy or drop.

FR2.5: Technical Confidence = 0.7 × strategy_score + 0.3 × signal_strength. Strategy score = win_rate × profit_factor normalized.

FR3.1: Sentiment multiplier: composite score >60 → 1.2, 40-60 → 1.0, <40 → 0.8.

FR3.2: On-chain multiplier: bullish → 1.15, neutral → 1.0, bearish → 0.85. Based on exchange flow + whale + addresses.

FR3.3: Macro penalty: high-impact within 24h → −20%, 48h → −10%. Medium-impact half penalty.

FR3.4: Research Multiplier = sentiment × onchain × (1 − macro_penalty). Clamped [0.5, 1.5].

FR4.1: Final Confidence = Technical × Research Multiplier. Display as 0-100%.

FR4.2: Signal structure: symbol, action, entry, SL, TP, confidence, strategy name, research context summary.

FR4.3: SL = entry ± ATR×1.5. TP = entry ± ATR×3.0. Computed deterministically.

FR4.4: Filter: Final Confidence ≥ 60%. Max 30 signals/day. If >30 → top 30 by confidence.

FR4.5: Cooldown: same pair no new signal within 24h unless confidence >80%.

FR5.1: Single Telegram message at 07:00 WIB. Summary + per-signal blocks. Mixed EN/ID.

FR5.2: Message: summary line + per-signal: emoji, pair, entry, strategy, conf, SL, TP, sentiment, on-chain, macro warning, track record.

FR5.3: Private channel, single user. python-telegram-bot sender only.

FR6.1: Store signals as pending with entry price/timestamp. Next run: fetch current price → realized return.

FR6.2: LLM generate 1-2 sentence reflection per resolved signal. 3s timeout, skip on fail.

FR6.3: Reflection adjusts research multiplier weights via EMA over last 30 days.

FR7.1: Cron trigger at 23:00 UTC (06:00 WIB). System cron or APScheduler.

FR7.2: Max runtime 60 min. Timeout → deliver partial results with warning.

FR7.3: Health check all data sources before pipeline. Critical source down → skip, Telegram alert.

### Non-Functional Requirements

NFR1: Reliability — Multi-source fallback chains. Graceful degradation: one source fails → use remaining. All fail → skip day, alert.

NFR2: Performance — Per-pair backtest <5s. Pipeline <60 min for 100 pairs. 10 workers parallel.

NFR3: Accuracy — Confidence monotonic with actual win rate. No look-ahead bias. Data freshness enforced.

NFR4: Cost — LLM only for reflection (1-2/signal, ~20/day = $0.20-0.60/day). No LLM in signal path. Free data sources preferred.

NFR5: Security — API keys in .env only. Telegram token as env var.

NFR6: Observability — Structured logging per stage. Per-source error counting. Signal accuracy tracking.

### Additional Requirements (Architecture)

AR1: LLM Boundary — LLM ONLY for deferred reflection. Must NOT in signal generation, confidence, or matching. (AD-1)

AR2: Parquet + SQLite — OHLCV as Parquet, signal metadata as SQLite. Parquet append-only. (AD-2)

AR3: Pipeline Stages — Pure functions, filesystem I/O between stages. No cross-stage imports. (AD-3)

AR4: CCXT Router — ExchangeRouter class with Binance→OKX→CoinGecko fallback. No direct API calls. (AD-4)

AR5: Strategy Protocol — StrategyProtocol with evaluate(ohlcv, indicators) → StrategySignal. No side effects. (AD-5)

AR6: Pre-Compute Indicators — All indicators computed once via pandas-ta, shared across strategies. (AD-6)

AR7: Telegram Sender — Single function send_daily_signals(). No webhook, no buttons. (AD-7)

AR8: SQLite 3 Tables — signals, outcomes, run_log. No migrations framework. (AD-8)

AR9: No Redis, no Postgres, no Docker required. In-memory processing. (AD-3)

AR10: No trading execution — Must NOT place orders or connect broker APIs. (PRD scope)

### FR Coverage Map

| FR | Epic | Description |
|----|------|-------------|
| FR1.1 | Epic 1 | OHLCV fetch via CCXT |
| FR2.1 | Epic 1 | 4D pair profile |
| FR2.2 | Epic 1 | 5 strategy implementations |
| FR2.3 | Epic 1 | Strategy-pair matching |
| FR2.4 | Epic 1 | Backtest validator |
| FR2.5 | Epic 1 | Technical confidence scoring |
| FR4.1 | Epic 1 | Final confidence formula |
| FR4.2 | Epic 1 | Signal structure |
| FR4.3 | Epic 1 | SL/TP computation |
| FR4.4 | Epic 1 | Signal filter (conf ≥ 60%) |
| FR4.5 | Epic 1 | Cooldown rule |
| FR5.1 | Epic 1 | Telegram daily message |
| FR5.2 | Epic 1 | Message formatting |
| FR5.3 | Epic 1 | Single user channel |
| FR7.1 | Epic 1 | Cron scheduling |
| FR7.2 | Epic 1 | Runtime budget 60 min |
| FR7.3 | Epic 1 | Health check |
| FR1.2 | Epic 2 | Sentiment fetcher |
| FR1.3 | Epic 2 | On-chain fetcher |
| FR1.4 | Epic 2 | Macro calendar |
| FR1.5 | Epic 2 | Prediction markets |
| FR3.1 | Epic 2 | Sentiment scoring |
| FR3.2 | Epic 2 | On-chain scoring |
| FR3.3 | Epic 2 | Macro penalty overlay |
| FR3.4 | Epic 2 | Research multiplier |
| FR6.1 | Epic 3 | Outcome tracking |
| FR6.2 | Epic 3 | LLM reflection |
| FR6.3 | Epic 3 | Weight adjustment EMA |

## Epic List

### Epic 1: Daily Signal Pipeline
User receives daily trading signals in Telegram with backtest-validated confidence scoring. Full end-to-end pipeline: data fetch → strategy matching → backtest → confidence → filter → Telegram delivery. Standalone — after Epic 1, signals are delivered daily.

**FRs covered:** FR1.1, FR2.1-2.5, FR4.1-4.5, FR5.1-5.3, FR7.1-7.3 (18 FRs)

### Epic 2: Research Context Integration
Signals include sentiment analysis, on-chain data, macro event warnings, and prediction market probabilities. Confidence is adjusted by research multiplier.

**FRs covered:** FR1.2-1.5, FR3.1-3.4 (8 FRs)

### Epic 3: Self-Improving Accuracy
Signal accuracy improves over time through outcome tracking, LLM-generated reflections, and automatic weight adjustment via EMA.

**FRs covered:** FR6.1-6.3 (3 FRs)

### Epic 4: Interactive Telegram Bot
Full interactive Telegram bot with command suggestions, inline keyboards, multi-timeframe signal generation, and on-demand backtesting. Bot runs 24/7 in parallel with cron scheduler — user can trigger pipeline, backtest any pair, view signals, and check performance from Telegram chat. Multi-timeframe (1h/4h/1d) signals generated independently per timeframe.

**New FRs:** FR5.4-5.8, FR7.4 (5 FRs)

**FRs in Epic 4:**
FR5.4: Register bot commands via BotCommand API — suggestions appear when user types `/` in chat
FR5.5: Interactive backtest wizard — user selects pair → timeframe → strategy → range via inline keyboard
FR5.6: On-demand pipeline trigger — `/run` with confirmation dialog
FR5.7: Signal viewer with filters (today/pending/all) + performance stats (7D/30D/all)
FR5.8: Status dashboard — last run, pairs analyzed, signals generated, win rate
FR7.4: CLI args for run modes — `--bot` (interactive), `--no-scheduler`, `--pipeline` (single run)

### Story 1.1: Project Setup

As a developer,
I want a working project skeleton with all dependencies installed and configuration loaded,
So that all subsequent stories have a solid foundation to build upon.

**Acceptance Criteria:**

**Given** a fresh checkout of the repository
**When** I run `pip install -r requirements.txt`
**Then** all dependencies install without errors
**And** `python -c "import ccxt, numpy, pandas, pyarrow, requests, telegram, apscheduler, yaml; print('OK')"` succeeds

**Given** the project directory exists
**When** I create `config/settings.yaml` with watchlist, thresholds, and API endpoints
**And** I create `.env` with TOKENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
**Then** `src/config.py` loads both files and exposes a `Settings` dataclass
**And** `.env` is listed in `.gitignore`

**Given** SQLite database does not exist
**When** the application initializes
**Then** `data/signals.db` is created with tables: `signals`, `outcomes`, `run_log`
**And** each table has correct schema per AD-8

**Given** the project structure follows ARCHITECTURE-SPINE.md seed
**When** I inspect `src/`, `data/`, `config/`
**Then** all directories exist with `__init__.py` files

---

### Story 1.2: OHLCV Data Fetcher

As the pipeline,
I want to fetch 6-12 months of OHLCV data for any crypto symbol via CCXT with automatic fallback,
So that every subsequent stage has reliable historical data to work with.

**Acceptance Criteria:**

**Given** a symbol "BTC-USDT" and timeframe "1d"
**When** `ExchangeRouter.fetch_ohlcv("BTC-USDT", "1d", since_ms)` is called
**Then** returns a pandas DataFrame with columns: timestamp, open, high, low, close, volume
**And** data spans at least 6 months from `since_ms` (minimum requirement per FR1.1)

**Given** Binance API returns error or timeout
**When** the router retries 3 times
**Then** it falls back to OKX automatically
**And** if OKX also fails, falls back to CoinGecko
**And** logs each attempt with exchange name and error

**Given** OHLCV data is successfully fetched
**When** the router saves to `data/ohlcv/{symbol}.parquet`
**Then** the file is written atomically (write to temp, then rename)
**And** saved to `data/ohlcv/{symbol}.parquet` (one file per symbol, contains OHLCV + indicators once Story 1.3 appends them)

**Given** the data freshness gate is active
**When** cached Parquet data is older than 4 hours
**Then** the router fetches fresh data from exchange
**And** if exchange fetch fails, uses cached data with a stale-data warning logged

**Given** cached Parquet data exists and exchange fetch succeeds
**When** the router saves
**Then** existing data is appended, not overwritten (preserves history)
**And** duplicate timestamps are dropped before save

**Given** a symbol has less than 6 months of available data
**When** the router attempts to fetch
**Then** it logs a warning: "Insufficient history for {symbol}"
**And** returns a DataFrame with whatever data is available (caller decides)

---

### Story 1.3: Indicator Engine

As the pipeline,
I want to pre-compute all technical indicators once per pair and store them alongside OHLCV data,
So that all 5 strategies can share the same indicator values without redundant computation.

**Acceptance Criteria:**

**Given** an OHLCV DataFrame loaded from Parquet
**When** `indicators.compute_all(ohlcv)` is called
**Then** returns a dict with keys: rsi_14, macd, macd_signal, macd_histogram, ma_20, ma_50, ma_200, atr_14, adx_14, bb_upper, bb_lower, bb_middle, volume_sma_20, volume_ratio
**And** each value is a pandas Series aligned with the OHLCV index

**Given** OHLCV data has fewer rows than the indicator lookback period
**When** `compute_all` is called
**Then** returns NaN for periods where calculation is impossible
**And** does not raise an exception

**Given** indicators are computed
**When** appended to the same Parquet file as OHLCV (`data/ohlcv/{symbol}.parquet`)
**Then** the file contains all indicator columns plus the original OHLCV columns in one file
**And** strategies load a single Parquet file to get both OHLCV and indicators

**Given** a strategy requests an indicator by key (e.g., "rsi_14")
**When** the indicator dict is accessed
**Then** the value is a pandas Series, not a scalar
**And** the strategy can slice by date index

---

### Story 1.4: Strategy Library

As the pipeline,
I want 5 trading strategies implemented as pure functions following a shared protocol,
So that the strategy-pair matcher can evaluate each pair against the best-fit strategy.

**Acceptance Criteria:**

**Given** any strategy module (e.g., `momentum.py`)
**When** imported
**Then** it exports a class implementing `StrategyProtocol` with: `name: str`, `weight: float`, `evaluate(ohlcv, indicators) -> StrategySignal`

**Given** Momentum Breakout strategy with N=20, k=0.005
**When** `evaluate(ohlcv, indicators)` is called on trending data
**Then** returns BUY when price > highest(N) × (1+k)
**And** returns SELL when price < lowest(N) × (1−k)
**And** confidence is proportional to breakout strength

**Given** Trend Following strategy with short=20, long=50, adx_threshold=25
**When** `evaluate` is called
**Then** returns BUY when MA20 crosses above MA50 AND ADX > 25
**And** returns SELL when MA20 crosses below MA50 AND ADX > 25
**And** returns HOLD when ADX < 25 (no trend)

**Given** Mean Reversion strategy with rsi_length=14, bb_length=20
**When** `evaluate` is called
**Then** returns BUY when RSI < 30 AND price < BB_lower AND volume > 1.3× SMA(volume,20)
**And** returns SELL when RSI > 70 AND price > BB_upper AND volume > 1.3× SMA(volume,20)
**And** only triggers when ADX < 20 (ranging market — avoiding strong trend whipsaws)

**Given** Volatility Breakout strategy with atr_length=14, k=1.5
**When** `evaluate` is called
**Then** returns BUY when price > SMA20 + ATR×k
**And** returns SELL when price < SMA20 − ATR×k

**Given** Volume-Price Divergence strategy
**When** `evaluate` is called on data with bullish divergence (new low + declining volume)
**Then** returns BUY
**And** bearish divergence (new high + declining volume) returns SELL

**Given** any strategy evaluates on data with no clear signal
**When** strategy conditions are not met
**Then** returns HOLD with confidence 0.0
**And** does not raise an exception

---

### Story 1.5: Profile + Strategy Matching + Backtest

As the pipeline,
I want to compute a 4-dimension profile for each pair and auto-match it to the best strategy validated by backtesting,
So that each pair uses the strategy most suited to its characteristics.

**Acceptance Criteria:**

**Given** OHLCV and indicators for a pair
**When** `profile.compute(ohlcv, indicators)` is called
**Then** returns a `PairProfile` with: trendiness (0-100), volatility (0-100), mean_reversion (0-100), volume_quality (0-100)
**And** profile uses rolling 90-day window for current values
**And** each dimension is explainable (e.g., "trendiness=72: ADX avg=28, ADX>25 for 65% of window")

**Given** a PairProfile with high trendiness (>60) and high volume_quality (>50)
**When** the matcher runs
**Then** selects Trend Following strategy
**And** the matching rule is deterministic (same profile → same strategy)

**Given** a PairProfile with high mean_reversion (>60) and low trendiness (<40)
**When** the matcher runs
**Then** selects Mean Reversion strategy

**Given** a PairProfile with no clear dominant characteristic
**When** the matcher runs
**Then** selects Ensemble mode (try all strategies in priority order)
**And** logs: "Unclear profile for {symbol} — using ensemble"

**Given** a matched strategy
**When** `backtest.run(strategy, ohlcv, indicators)` is called on 6-month historical data
**Then** returns `BacktestResult` with: win_rate, sharpe_ratio, max_drawdown, profit_factor, total_trades
**And** backtest uses walk-forward validation (train 80%, test 20%) if data > 12 months

**Given** backtest result with win_rate < 40% OR sharpe < 0.5
**When** the validator checks the gate
**Then** the pair is flagged as FAILED for this strategy
**And** the system tries the second-best matched strategy
**And** if all strategies fail → pair is dropped with log: "{symbol} dropped — no strategy passed backtest"

**Given** backtest result passes all gates
**When** the validator accepts
**Then** the pair proceeds to signal generation
**And** the matched strategy + backtest metrics are stored for this run

---

### Story 1.6: Signal Generation + Filter

As the pipeline,
I want to convert validated strategy matches into structured trading signals with confidence scoring, stop-loss/take-profit, and quality filters,
So that only high-confidence, actionable signals reach the user.

**Acceptance Criteria:**

**Given** a matched strategy and its backtest result
**When** signal generation runs
**Then** `Technical_Confidence = 0.7 × strategy_score + 0.3 × signal_strength`
**And** `strategy_score = win_rate × profit_factor / max_possible` (normalized 0-1)
**And** `signal_strength` = normalized distance of current price from trigger threshold (0-1)

**Given** a SELL signal on BTC-USDT with entry_price=60200
**When** SL/TP are computed
**Then** SL = entry + ATR×1.5 = 60200 + 867 = 61067
**And** TP = entry − ATR×3.0 = 60200 − 2600 = 57600
**And** both are rounded to 2 decimal places for USD pairs, 0 decimal for JPY/KRW pairs
**And** rounding is done via standard `round()` — no exchange-specific tick size needed in MVP

**Given** a Final Confidence of 73% for a signal
**When** the filter runs
**Then** the signal passes (≥ 60% threshold)
**And** is included in today's output

**Given** a Final Confidence of 45% for a signal
**When** the filter runs
**Then** the signal is dropped
**And** logged with reason: "Confidence 45% < 60% threshold"

**Given** 35 signals pass the confidence filter
**When** the cap is applied
**Then** only the top 30 by confidence are kept
**And** a warning is logged: "Signal cap reached — 5 signals dropped"

**Given** BTC-USDT generated a signal yesterday at 07:00
**When** the same pair generates a signal today with confidence 65%
**Then** the cooldown filter blocks it (24h rule)
**And** logs: "{symbol} in cooldown — last signal < 24h ago"

**Given** BTC-USDT generated a signal yesterday but today's confidence is 85%
**When** the cooldown filter runs
**Then** the cooldown is overridden (>80% exception)
**And** the signal passes

**Given** a passing signal
**When** the `Signal` dataclass is constructed
**Then** it contains all fields from AD-8: id (uuid), symbol, action, confidence, entry_price, sl, tp, strategy, timestamp_utc, status="pending"
**And** the signal is written to SQLite signals table

---

### Story 1.7: Telegram Delivery

As a user (Kumaha-sia),
I want to receive a single formatted Telegram message every morning with all daily signals and their details,
So that I can review trading opportunities from my phone without opening any app.

**Acceptance Criteria:**

**Given** a list of 20 validated Signal objects
**When** `telegram_sender.send_daily_signals(signals)` is called
**Then** exactly ONE Telegram message is sent to the configured chat_id
**And** the message starts with a summary line: "📊 {N}/{total} pair analyzed | Avg Conf: {avg}% | 7-day win: {win_rate}%"
**And** signal fields (symbol, action, price, confidence, SL, TP) are in English
**And** commentary text (summary, caveats) is in Indonesian (Bahasa Indonesia) per FR5.1

**Given** a SELL signal for BTC-USDT with confidence 73%, 7-day track record "4/5 win (+2.1%)"
**When** the message is formatted
**Then** the signal block contains:
```
🔴 SELL — BTC/USDT $60,200
Trend Following | Conf 73%
SL: 61,500 | TP: 56,000
📈 Track: 4/5 win (+2.1%)
```
**And** for BUY signals, uses 🟢 emoji
**And** the track record line is omitted if no historical data exists (new pair)

**Given** the Telegram API returns an error (network, rate limit)
**When** `send_daily_signals` catches the exception
**Then** retries up to 3 times with exponential backoff (1s, 2s, 4s)
**And** if all retries fail → logs error: "Telegram send failed after 3 retries"
**And** does NOT crash the pipeline

**Given** a signal with missing optional fields (e.g., no macro_flag)
**When** the message is formatted
**Then** those lines are simply omitted (no empty labels, no "—")
**And** the message remains valid and readable

**Given** the Telegram bot token is invalid or not configured
**When** `send_daily_signals` is called
**Then** logs an error and returns False
**And** the pipeline continues (signals are saved to DB, just not delivered)

---

### Story 1.8: Pipeline Orchestrator + Scheduling

As the system operator,
I want the pipeline to run automatically every night via cron, execute all stages in sequence, and handle failures gracefully,
So that signals are delivered reliably at 07:00 WIB without manual intervention.

**Acceptance Criteria:**

**Given** the system time reaches 23:00 UTC (06:00 WIB)
**When** the APScheduler cron trigger fires (configured via `apscheduler` library per FR7.1)
**Then** `main.py` executes `run_pipeline()`
**And** each stage function receives input_dir and returns output_dir
**And** artifacts from each stage are written to `data/pipeline/{date}/stage_{N}/`

**Given** Stage 1 (Data Fetch) completes successfully
**When** Stage 2 (Profile + Matching) starts
**Then** it reads artifacts from `stage_1/` output directory
**And** writes to `stage_2/` directory
**And** so on for all stages

**Given** a stage raises an unhandled exception
**When** the pipeline catches it
**Then** the error is logged with full traceback
**And** subsequent stages are skipped
**And** a Telegram alert is sent: "Pipeline failed at Stage {N}: {error}"
**And** the run_log table records: status="failed", stage_failed=N, error_summary

**Given** the pipeline has been running for 55 minutes
**When** the runtime budget check triggers (60 min max)
**Then** a warning is logged: "Pipeline approaching timeout — 5 min remaining"
**And** if 60 min is exceeded → pipeline terminates, delivers partial results with warning

**Given** it's 22:55 UTC (5 min before pipeline start)
**When** the health check runs
**Then** it verifies: CCXT connectivity, Alternative.me API reachable, Telegram token valid
**And** if any critical source fails → skips pipeline, sends Telegram alert
**And** if only non-critical source fails → logs warning, continues pipeline

**Given** the pipeline completes successfully
**When** `run_log` is updated
**Then** the record shows: status="completed", pairs_analyzed=N, signals_generated=M, duration_seconds=X
**And** a completion message is sent to Telegram: "✅ Pipeline complete — {M} signals from {N} pairs in {X}s"
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
