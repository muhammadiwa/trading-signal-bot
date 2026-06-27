# Brainstorm Intent: Trading Signal Bot (Telegram)

**Date:** 2026-06-27
**User:** Kumaha-sia
**Mode:** Creative Partner
**Techniques:** Five Whys, What If?, Inversion, Analogy Mapping
**Research:** Vibe-Trading (68 tools, 79 skills) + TradingAgents (13 agents, 2 debate loops)

---

## 1. Product Vision

A **research-first Telegram signal generator** for crypto. Every night, the system analyzes the top 100 coins through a multi-layer pipeline: technical backtest with strategy-pair matching, sentiment analysis from social/news/on-chain sources, macro event overlay, and prediction market probabilities. Only signals that pass all quality gates are delivered to Telegram at 07:00 WIB — typically ~20 signals from 100 analyzed. No trading execution. Pure signal.

---

## 2. Core Problem (Five Whys Root Cause)

**Problem:** Existing signal bots are inaccurate and users ignore them.

1. **Why inaccurate?** → Use 1 generic strategy for all pairs.
2. **Why 1 strategy?** → Strategy-pair matching is complex to build.
3. **Why complex?** → Requires automated backtest pipeline + metric comparison + auto-selection.
4. **Why rare?** → Needs data management (100 pairs × 12 months), reliable backtest engine, objective metrics.
5. **Root cause:** No standardized framework for: auto-profile pair characteristics → auto-match strategy → auto-validate with backtest → auto-select valid → generate signal. **PLUS: no research context layer** (sentiment, on-chain, macro, prediction markets) to adjust technical confidence.

---

## 3. Target User

Crypto trader who wants curated daily signals delivered to Telegram. Values accuracy over quantity, transparency over blind signals, and wants research context (not just "BUY" but "BUY because X, Y, Z with on-chain confirmation"). No trading execution — user executes themselves.

---

## 4. Key Innovation: Research Pipeline + Strategy-Pair Matching

**Dual-layer confidence:**
- **Technical Confidence** = backtest win rate × strategy score × signal strength
- **Research Confidence** = f(sentiment, on-chain data, macro overlay, prediction markets)
- **Final Confidence** = Technical × Research Multiplier

**Strategy-pair matching:** Each pair profiled on 4 dimensions (trendiness, volatility, mean-reversion tendency, volume quality). Rolling 90-day profile → auto-match to best strategy → backtest validate → if pass, generate signal.

---

## 5. Daily Flow

```
23:00 UTC — Nightly Batch Starts
├── Phase 0: Identity Resolution (ticker → name/sector, filter stablecoins)
├── Phase 1: Data Fetch
│   ├── OHLCV 6-12 bulan (CCXT: Binance→OKX fallback)
│   ├── On-chain (Glassnode/Whale Alert: exchange flow, whale moves, MVRV)
│   ├── Sentiment (Fear & Greed API, Reddit RSS, Twitter/X trending)
│   ├── Macro (FRED, economic calendar: FOMC, CPI, NFP dates)
│   └── Prediction Markets (Polymarket probabilities)
├── Phase 2: Strategy-Pair Matching + Backtest
│   ├── 4D profile per pair
│   ├── Auto-match to best strategy (from 5 candidates)
│   └── Backtest validate: win rate ≥ 40%, Sharpe ≥ 0.5
├── Phase 3: Research Context Scoring
│   ├── Sentiment score (0-100)
│   ├── On-chain signal (bullish/bearish/neutral)
│   ├── Macro overlay (event risk: high/medium/low)
│   └── Research Confidence Multiplier
├── Phase 4: Confidence Adjustment
│   └── Final Confidence = Technical × Research Multiplier
├── Phase 5: Signal Filter
│   └── Keep only: Final Confidence ≥ 60%
│
07:00 WIB — Telegram Delivery
├── Summary: "20 sinyal dari 100 pair dianalisa"
└── Per signal: action, confidence, SL/TP, strategy used, research context
```

---

## 6. Strategy-Pair Matching Method

### 4-Dimension Profile (0-100 each):
- **Trendiness**: ADX(14) average + % time ADX > 25
- **Volatility**: ATR(14)/close ratio + max drawdown frequency
- **Mean-reversion**: % RSI bounce frequency (RSI<30 or >70 returning to mean in 5 bars)
- **Volume quality**: Volume stability (CV) + volume-price correlation

### Matching Rules:
| Profile | Matched Strategy |
|---------|-----------------|
| High trendiness + high volume | Trend Following (MA crossover) |
| High volatility + high volume | Momentum Breakout |
| High mean-rev + low trend | Mean Reversion (RSI + Bollinger) |
| High volatility + low volume | Volatility Breakout (ATR channel) |
| Unclear profile | Ensemble (5 strategy voting) |

### 5 Strategies:
1. Momentum Breakout (N=20, k=0.005)
2. Trend Following (MA20/50 crossover + ADX filter)
3. Mean Reversion (RSI 14 + Bollinger Bands)
4. Volatility Breakout (ATR channel)
5. Volume-Price Divergence

### Confidence Scoring:
- **Strategy score** = backtest win rate × profit factor normalized
- **Signal strength** = how far price is from threshold (0-1)
- **Research multiplier** = sentiment_score × onchain_score × (1 - macro_risk_penalty)
- **Final Confidence** = (0.7 × strategy_score + 0.3 × signal_strength) × research_multiplier

---

## 7. MVP Scope (MUST — 13 items)

| # | Capability | From |
|---|-----------|------|
| M1 | OHLCV data fetch (CCXT multi-source fallback) | Base |
| M2 | 4-Dimension pair profile + rolling 90-day window | Brainstorm |
| M3 | 5 strategy implementations | Brainstorm |
| M4 | Strategy-pair auto-matching | Brainstorm |
| M5 | Backtest validator (win rate ≥ 40%, Sharpe ≥ 0.5) | Brainstorm |
| M6 | Minimum data gate (6 months) | What If |
| M7 | Data freshness gate (max 4 hours old) | What If |
| M8 | **Sentiment multi-source** (Fear & Greed + Reddit + Twitter) | Vibe/TA |
| M9 | **On-chain data** (exchange inflow/outflow, whale moves) | Vibe |
| M10 | **Macro calendar overlay** (FOMC, CPI, NFP) | Vibe/TA |
| M11 | **Prediction markets** (Polymarket probabilities) | TA |
| M12 | Confidence scoring (Technical × Research Multiplier) | Brainstorm |
| M13 | Telegram delivery (summary + per-signal detail with research context) | Base |

---

## 8. Architecture Outline (6 Pipeline Stages)

```
┌──────────────────────────────────────────────────────────┐
│ STAGE 0: IDENTITY RESOLUTION                             │
│ Ticker → name/sector/market cap (cache)                  │
│ Filter: skip stablecoins, skip delisted                  │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ STAGE 1: DATA FETCH (parallel per source)                │
│ OHLCV │ On-chain │ Sentiment │ Macro │ Prediction Mkts   │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ STAGE 2: PROFILE + STRATEGY MATCH (per pair)             │
│ 4D profile → strategy match → backtest validate          │
│ Output: (pair, strategy, technical_confidence)           │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ STAGE 3: RESEARCH CONTEXT (per pair)                     │
│ Sentiment score │ On-chain signal │ Macro overlay        │
│ Prediction market probability                            │
│ Output: research_confidence_multiplier (0.5-1.5)         │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ STAGE 4: CONFIDENCE ADJUSTMENT                           │
│ Final = Technical × Research Multiplier                  │
│ Gate: Final Confidence ≥ 60%                             │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ STAGE 5: TELEGRAM DELIVERY                               │
│ Summary + per-signal detail + research context           │
│ Store: signal_history + deferred outcome pending         │
└──────────────────────────────────────────────────────────┘
```

---

## 9. Research Layer Detail

### Sentiment Multi-Source:
- **Fear & Greed Index** (Alternative.me API): 0-100 score
- **Reddit** (r/cryptocurrency, r/bitcoin RSS): mention volume, bullish/bearish keyword ratio
- **Twitter/X trending**: crypto hashtag volume, sentiment polarity
- **Weight**: 0.4 × F&G + 0.3 × Reddit + 0.3 × Twitter

### On-Chain Data:
- **Exchange inflow/outflow**: net flow to exchanges (inflow = bearish, outflow = bullish)
- **Whale transactions**: count + volume of >$1M transactions
- **MVRV ratio**: over/under-valuation signal
- **Active addresses**: trend (rising = bullish network activity)
- **Score**: bullish if outflow > inflow AND MVRV < 3 AND active addresses rising

### Macro Calendar:
- **High-impact events**: FOMC, CPI, NFP → auto-reduce confidence by 20%
- **Medium-impact**: GDP, PPI, retail sales → auto-reduce confidence by 10%
- **Event proximity**: within 24h → full penalty, 24-48h → half penalty

### Prediction Markets:
- **Polymarket**: rate decisions, recession probability, crypto-specific events
- **Integration**: if market-implied probability > 70% for a bearish event → reduce confidence

### Deferred Outcome Reflection:
- Yesterday's signals stored as pending
- Next run: fetch realized return for each pending signal
- LLM generates 1-2 sentence reflection: "BTC SELL signal at $60,200 → price dropped to $58,500 (+2.8%). Death cross signal was correct. However, on-chain data showed accumulation — next time weight on-chain signal higher."
- Reflection injected into confidence adjustment formula (incrementally adjusts weights)

---

## 10. Telegram Output Format

```
╔══════════════════════════════════════════╗
║  📊 SINYAL HARIAN — 27 Jun 2026        ║
╠══════════════════════════════════════════╣
║  20/100 pair menghasilkan sinyal        ║
║  Avg confidence: 72% | Win 7-hari: 51%  ║
╠══════════════════════════════════════════╣
║                                          ║
║  🔴 SELL — BTC/USDT $60,200            ║
║  Trend Following | Conf 73%             ║
║  SL: 61,500 | TP: 56,000               ║
║  ─────────────────────────────────      ║
║  📊 Sentiment: Fear 25/100             ║
║  🔗 On-chain: $200M inflow (bearish)   ║
║  📅 FOMC: 2 hari lagi ⚠️               ║
║  🗳️ Polymarket: 68% rate hold          ║
║  ─────────────────────────────────      ║
║  📈 Track record: 4/5 profit (+2.1%)   ║
║                                          ║
║  🟢 BUY — ETH/USDT $3,420             ║
║  Momentum Breakout | Conf 68%           ║
║  SL: 3,280 | TP: 3,680                ║
║  📊 Sentiment: Neutral 52/100           ║
║  🔗 On-chain: $50M outflow (bullish)   ║
║  📈 Track record: 3/5 profit (+1.8%)   ║
║                                          ║
║  ... (18 lainnya)                       ║
╚══════════════════════════════════════════╝
```

---

## 11. Key Decisions Made

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Research-first: analyze 100 pair, deliver ~20 signals | Quality > quantity |
| 2 | Strategy-pair matching (not 1 strategy for all) | Different pair = different characteristics |
| 3 | 4-D profile: trendiness, volatility, mean-rev, volume quality | Objective, measurable, backtestable |
| 4 | Rolling 90-day profile window | Adapts to regime changes |
| 5 | Dual confidence: Technical × Research Multiplier | Technical alone misses context |
| 6 | Research multiplier = f(sentiment, on-chain, macro, prediction markets) | Multi-source confirmation |
| 7 | No trading execution — pure signal only | User preference, simpler, safer |
| 8 | Batch nightly, deliver morning | One clear delivery time, research takes hours |
| 9 | Multi-source data with fallback chains | Reliability, no single point of failure |
| 10 | Deferred outcome reflection | Continuous learning, self-improving accuracy |
| 11 | Minimum data gate (6 months) | Avoid new coins with insufficient history |
| 12 | Data freshness gate (max 4 hours) | Avoid stale signals |
| 13 | Circuit breaker: macro event proximity → reduce confidence | Avoid trading into high volatility |
| 14 | Track record display per signal | Transparency, trust building |
| 15 | LLM only for reflection + optional explainer (NOT decision) | Cost control, deterministic backbone |

---

## 12. Open Questions for Product Brief

1. Which specific on-chain data providers to use? (Glassnode API vs free alternatives like Whale Alert)
2. Exact confidence threshold? (60% baseline — adjustable?)
3. Telegram delivery: single channel or per-user subscription?
4. How many strategies to start? (5 or more?)
5. Which prediction market topics are relevant beyond rate decisions?
6. What language for signal messages? (Indonesian or English?)
7. Should LLM explainer be optional per signal or always-on?
8. How to handle weekends/low-volume periods?
9. Public track record page? (Website or Telegram only?)
10. Multi-timeframe signal? (1H for scalping + 4H for swing + 1D for trend)
