---
title: "Trading Signal Bot Telegram — Research-First Crypto Signal Generator"
status: final
created: 2026-06-27
updated: 2026-06-27
version: 1.0
author: "[ASSUMPTION: Kumaha-sia]"
stakeholder: Personal use
stakes: hobby—but—perfect
---

# PRD: Research-First Crypto Signal Generator

## Problem Statement

Crypto signal bots generate inaccurate signals because they apply one generic strategy to all coins and lack research context. Traders need signals backed by: (a) strategy-pair matching with backtest validation, (b) multi-source research context (sentiment, on-chain, macro, prediction markets), and (c) transparent confidence scoring — delivered once daily via Telegram. No trading execution.

## Product Definition

A nightly batch pipeline that analyzes the top 100 coins through technical backtest with strategy-pair matching and multi-layer research context. Delivers ~20 high-confidence signals to Telegram at 07:00 WIB. Pure signal — user executes trades independently.

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Signal accuracy (7-day rolling) | Win rate ≥ 45% | signal_history JOIN realized returns |
| Profit factor (paper tracked) | ≥ 1.2 | Gross profit / gross loss |
| Daily coverage | ≥ 15 signals from 100 analyzed | Per-run count |
| Confidence accuracy | High-confidence (≥70%) signals outperform low-confidence | Segmented analysis |
| Runtime | < 60 minutes nightly batch | Perf log |
| Uptime | 29/30 days | Cron monitor |

## Counter-Metrics (what we guard against)
- False confidence: >70% confidence signals with <40% win rate
- Signal clustering: >50% signals on same action (BUY/SELL imbalance signals regime bias)
- Coverage drop: <10 signals for 3 consecutive days

---

## Features (FR — Functional Requirements)

### FR1: Data Ingestion Pipeline

**FR1.1 — OHLCV Fetcher**: Fetch 6-12 months of OHLCV data for top 100 coins using CCXT with Binance→OKX→CoinGecko fallback chain. Cache to Parquet. Freshness gate: reject data older than 4 hours.

**FR1.2 — Sentiment Fetcher**: Fetch Fear & Greed Index (Alternative.me API), Reddit crypto threads (RSS: r/cryptocurrency, r/bitcoin), Twitter/X trending crypto hashtags. Compute composite sentiment score 0-100.

**FR1.3 — On-Chain Fetcher**: Fetch exchange net flow, whale transaction count (>$1M), active address trend via free APIs (Whale Alert, CoinGecko). Compute on-chain signal: bullish/neutral/bearish.

**FR1.4 — Macro Calendar**: Maintain economic calendar with high-impact events (FOMC, CPI, NFP) and medium-impact (GDP, retail sales). Flag events within 24h and 48h windows. [ASSUMPTION: Calendar manually maintained JSON, updated monthly]

**FR1.5 — Prediction Markets**: Fetch Polymarket probabilities for crypto-relevant events via public Gamma API. Score as forward-looking event risk overlay.

### FR2: Technical Analysis Engine

**FR2.1 — 4D Profile**: Compute per-pair profile: trendiness (ADX-based), volatility (ATR/close ratio), mean-reversion tendency (RSI bounce frequency), volume quality (CV + price correlation). Rolling 90-day window.

**FR2.2 — Strategy Library**: Implement 5 strategies as pure functions: Momentum Breakout, Trend Following (MA crossover), Mean Reversion (RSI + Bollinger), Volatility Breakout (ATR channel), Volume-Price Divergence.

**FR2.3 — Strategy-Pair Matching**: Match profile to strategy using deterministic rules. Unclear profiles → ensemble voting (all 5 strategies).

**FR2.4 — Backtest Validator**: Run matched strategy on 6-month historical data. Gate: win rate ≥ 40%, Sharpe ≥ 0.5. If fail → try next best strategy. If all fail → drop pair for today.

**FR2.5 — Technical Confidence**: `Technical_Confidence = (0.7 × strategy_score + 0.3 × signal_strength)` where `strategy_score = win_rate × profit_factor / max_possible`, `signal_strength = normalized distance from trigger threshold`.

### FR3: Research Context Engine

**FR3.1 — Sentiment Scoring**: Composite score = 0.4 × FearGreed_norm + 0.3 × Reddit_sentiment + 0.3 × Twitter_sentiment. Map to multiplier: score >60 → 1.2, 40-60 → 1.0, <40 → 0.8.

**FR3.2 — On-Chain Scoring**: Bullish if: (a) net exchange outflow, (b) whale buy count > sell count, (c) active addresses rising. Bearish if opposite. Mixed → neutral. Multiplier: bullish → 1.15, neutral → 1.0, bearish → 0.85.

**FR3.3 — Macro Overlay**: High-impact event within 24h → −20% confidence penalty. Within 48h → −10%. Medium-impact → half penalty.

**FR3.4 — Research Multiplier**: `Research_Multiplier = sentiment_mult × onchain_mult × (1 − macro_penalty)`. Clamped to [0.5, 1.5].

### FR4: Signal Generation

**FR4.1 — Final Confidence**: `Final_Confidence = Technical_Confidence × Research_Multiplier`. Displayed as percentage (0-100).

**FR4.2 — Signal Structure**: Each signal contains: symbol, action (BUY/SELL), entry price, stop loss, take profit, confidence, matched strategy name, research context summary.

**FR4.3 — Stop Loss / Take Profit**: SL = entry ± (ATR × 1.5). TP = entry ± (ATR × 3.0). Computed deterministically. [ASSUMPTION: 1.5x ATR SL, 3x ATR TP — adjustable]

**FR4.4 — Signal Filter**: Keep only signals with Final Confidence ≥ 60%. Cap maximum 30 signals per day. If >30, take top 30 by confidence.

**FR4.5 — Cooldown**: Same pair cannot generate new signal within 24 hours of previous signal (unless confidence >80%).

### FR5: Telegram Delivery

**FR5.1 — Daily Message**: Single formatted message at 07:00 WIB with summary header + per-signal detail blocks. Mixed language: signal fields in English, commentary in Indonesian.

**FR5.2 — Message Format**: Summary line (X/N pair analyzed) + avg confidence + 7-day win rate. Then per-signal blocks: emoji action, pair name, entry price, strategy name, confidence %, SL, TP, sentiment score, on-chain signal, macro warning (if any), track record.

**FR5.3 — Channel**: Private Telegram channel. [ASSUMPTION: Single bot, single user — personal use]

### FR6: Deferred Outcome Reflection

**FR6.1 — Outcome Tracking**: After each signal, store as pending with entry price and timestamp. On next run, fetch current price → compute realized return.

**FR6.2 — LLM Reflection**: For each resolved signal, generate 1-2 sentence reflection via LLM: "BTC SELL at $60,200 → $58,500 (+2.8%). Death cross correct. On-chain showed accumulation — next time weight on-chain higher."

**FR6.3 — Reflection Injection**: Resolved reflections injected into Research Multiplier calculation. Incrementally adjusts sentiment/on-chain weights based on historical accuracy. [ASSUMPTION: Simple exponential moving average of per-source accuracy over last 30 days]

### FR7: Scheduling

**FR7.1 — Nightly Cron**: Trigger at 23:00 UTC (06:00 WIB). [ASSUMPTION: System cron or scheduler like APScheduler]

**FR7.2 — Execution Time Budget**: Max 60 minutes. If timeout → deliver partial results with warning.

**FR7.3 — Health Check**: Validate all data sources reachable before starting pipeline. If critical source down → skip pipeline, send Telegram alert.

---

## Non-Functional Requirements

### NFR1: Reliability
- Multi-source data fallback chains for every fetcher
- Graceful degradation: if one research source fails, use remaining sources
- If all data sources fail → skip day, send alert

### NFR2: Performance
- Per-pair backtest: < 5 seconds
- Total pipeline: < 60 minutes for 100 pairs
- Parallel processing: 10 workers concurrent

### NFR3: Accuracy
- Confidence score must be monotonic with actual win rate (high confidence → higher win rate)
- No look-ahead bias in backtesting
- Data freshness enforced at gate level

### NFR4: Cost
- LLM calls only for deferred reflection (1-2 per signal outcome, ~20/day = $0.20-0.60/day)
- No LLM in real-time signal generation path
- Free data sources preferred; paid optional and configurable

### NFR5: Security
- API keys in `.env` only, never in repo
- Telegram bot token stored as env var

### NFR6: Observability
- Structured logging per pipeline stage
- Per-source error counting
- Signal accuracy tracking over time

---

## User Journey

**Daily experience (07:00 WIB):**
1. Kumaha-sia opens Telegram
2. Sees one message from "Trading Signal Bot"
3. Header: "📊 20/100 pair analyzed | Avg Conf: 72% | 7-day win: 51%"
4. Scrolls through signal blocks
5. For each: reads action + confidence + research context
6. Decides which signals to act on independently
7. (Optional) Opens performance dashboard link

**Weekly review (Saturday morning):**
1. Bot sends weekly digest: "This week: 95 signals, 49 wins (51.6%), +3.2% aggregate"
2. Best strategy: Mean Reversion (58% win rate on SOL/ETH)
3. Worst strategy: Momentum Breakout (42% — under review)
4. Recommendation: "On-chain signals improved accuracy by 12% — keep weighting"

---

## Out of Scope (Explicit)

- ❌ Trading execution of any kind
- ❌ Multi-user support
- ❌ Web dashboard (future phase)
- ❌ Real-time signals (only daily batch)
- ❌ Custom strategy creation UI
- ❌ Portfolio tracking
- ❌ Mobile app (Telegram only)
- ❌ Live LLM debate per signal (cost prohibitive for personal use)

---

## Open Items

1. Exact LLM model for deferred reflection? [ASSUMPTION: deepseek-v4-pro via TokenRouter]
2. Historical data storage: local Parquet files. DB needed for signal history? [ASSUMPTION: SQLite for MVP]
3. Weekend behavior: run as usual or skip? [ASSUMPTION: Run daily including weekends — crypto never sleeps]
4. Alerts for extreme signals? (Confidence >85%) [ASSUMPTION: Not MVP, add later]

---

## Appendix: Research Source Detail

| Layer | Source | Freq | Auth | Fallback |
|-------|--------|------|------|----------|
| OHLCV | CCXT (Binance) | Nightly | None | OKX → CoinGecko |
| Sentiment | Alternative.me | Nightly | None | Static last-known |
| Social | Reddit RSS | Nightly | None | Skip |
| On-Chain | Whale Alert API | Nightly | None | Skip |
| Macro | Manual JSON calendar | Monthly update | None | Skip |
| Prediction | Polymarket Gamma API | Nightly | None | Skip |
| Reflection LLM | TokenRouter | Per resolved signal | API Key | Skip reflection |
