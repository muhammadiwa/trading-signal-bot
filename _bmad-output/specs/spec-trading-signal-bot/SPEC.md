---
slug: trading-signal-bot
status: final
created: 2026-06-27
inputs: prd/trading-signal-bot/prd.md, architecture/ARCHITECTURE-SPINE.md
---

# SPEC: Trading Signal Bot (Telegram)

## Why

Crypto signal bots fail because they apply one generic strategy to all coins and ignore research context (sentiment, on-chain, macro). This spec defines a research-first batch pipeline that analyzes 100 coins nightly, matches each to its best strategy via backtest validation, overlays multi-source research, and delivers only high-confidence signals to Telegram. No trading execution — pure signal.

**Success signal:** After 30 days, rolling 7-day win rate ≥ 45%, profit factor ≥ 1.2, and user reports "I trust the signals enough to act on them daily."

---

## Capabilities

### CAP-1: Nightly Batch Pipeline
The system runs once daily at 23:00 UTC via cron. Pipeline stages execute sequentially: identity resolution → data fetch → profile + strategy match → research context → confidence + filter → Telegram delivery. Runtime budget: 60 minutes max.

### CAP-2: Top 100 Coin Analysis
Fetches top 100 coins by market cap from CoinGecko. Filters out stablecoins and coins with < 6 months of history. Each remaining coin proceeds through full pipeline.

### CAP-3: Multi-Source Data Fetch
Parallel data collection per coin: OHLCV (CCXT with Binance→OKX→CoinGecko fallback), sentiment (Fear & Greed + Reddit RSS), on-chain (Whale Alert exchange flows), macro calendar (pre-maintained JSON), prediction markets (Polymarket Gamma API). All data cached to Parquet.

### CAP-4: Strategy-Pair Matching
Computes 4-dimension profile per pair (trendiness, volatility, mean-reversion, volume quality). Matches profile to best strategy from library of 5. Backtest validates match: win rate ≥ 40%, Sharpe ≥ 0.5. Failed matches try next-best strategy or drop.

### CAP-5: Research Context Overlay
Computes research multiplier from 4 sources: sentiment score (Fear & Greed + social), on-chain signal (exchange flow direction), macro overlay (high-impact event proximity penalty), prediction market probabilities. Multiplier range: 0.5-1.5.

### CAP-6: Confidence Scoring
Final confidence = technical confidence × research multiplier. Technical confidence = 0.7 × strategy_score + 0.3 × signal_strength. Only signals with final confidence ≥ 60% are delivered. Cap: max 30 signals per day.

### CAP-7: Telegram Daily Delivery
Single formatted message at 07:00 WIB via python-telegram-bot. Contains summary header + per-signal blocks with action, entry, SL/TP, confidence, strategy name, and research context. Mixed language: signal fields in English, commentary in Indonesian.

### CAP-8: Deferred Outcome Reflection
Yesterday's signals stored as pending. On next run, fetch current price → compute realized return. LLM generates 1-2 sentence reflection per resolved signal. Reflections injected into research multiplier weights via EMA over last 30 days.

### CAP-9: Signal History Storage
SQLite database with 3 tables: signals (per-signal metadata), outcomes (realized returns + reflections), run_log (pipeline execution records). All artifacts reproducible from DB + Parquet files.

---

## Constraints

### CON-1: LLM Boundary
LLM may ONLY be used for deferred outcome reflection. MUST NOT participate in signal generation, confidence scoring, or strategy matching. Reflection LLM call has 3s timeout, falls back to skip on failure.

### CON-2: No Real-Time Dependency
No WebSocket connections. No streaming data. No request-response API server. The pipeline terminates when Telegram message is sent. State persists in filesystem + SQLite.

### CON-3: Data Freshness
OHLCV data must be ≤ 4 hours old at pipeline start. Sentiment and on-chain data fetched fresh each run. Stale data → skip signal for that pair.

### CON-4: No Trading Execution
System MUST NOT place orders, connect to broker APIs, or suggest position sizes. Pure signal output only.

### CON-5: Single User
One Telegram channel. One set of API keys. No multi-tenant architecture.

### CON-6: Deterministic Core
All signal-critical computation (indicators, strategy evaluation, backtesting, confidence scoring) is deterministic. Same input → same output. LLM only in reflection path.

### CON-7: Free Data Sources
OHLCV: CCXT (free). Sentiment: Alternative.me (free). On-chain: Whale Alert (free). Macro: manual JSON. Prediction: Polymarket (free). No paid API dependencies in MVP.

### CON-8: Environment
Python 3.14. Libraries: ccxt, numpy, pandas, pyarrow, python-telegram-bot, apscheduler, requests, pyyaml. Storage: Parquet + SQLite. No Redis, no Postgres, no Docker required.

---

## Non-Goals

- Real-time signal delivery (daily batch only)
- Trading execution or broker integration
- Multi-user support
- Web dashboard or GUI
- Custom strategy creation UI
- Portfolio tracking or P&L calculation
- Mobile app (Telegram only)
- Backtesting UI (CLI/script only)
- Multi-exchange arbitrage signals
- AI agent debate or multi-agent swarm

---

## Companion: Architecture Spine

The companion `ARCHITECTURE-SPINE.md` defines 8 architecture decisions (AD-1 through AD-8), 5 deferred items, the project structure seed, data flow conventions, and key Python interfaces. It is the binding technical contract for implementation.

Reference: `_bmad-output/architecture/ARCHITECTURE-SPINE.md`
