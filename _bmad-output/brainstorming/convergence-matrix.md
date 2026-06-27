# Trading Signal Bot — Impact vs Effort Matrix
# Dari 15 keputusan desain hasil brainstorming

## HIGH IMPACT / LOW EFFORT (Do first)

| # | Decision | Impact | Effort |
|---|----------|--------|--------|
| 1 | Profile 4 dimensi sbg fondasi | 🔥🔥🔥 | Low |
| 4 | Circuit breaker pas market crash | 🔥🔥🔥 | Low |
| 6 | Minimum data gate (6 bulan) | 🔥🔥🔥 | Low |
| 9 | Confidence = win rate × strategy score | 🔥🔥 | Low |
| 11 | Data freshness gate (max 4 jam) | 🔥🔥 | Low |

## HIGH IMPACT / MEDIUM EFFORT (Do second)

| # | Decision | Impact | Effort |
|---|----------|--------|--------|
| 2 | Rolling 90-day profile (dinamis) | 🔥🔥🔥 | Medium |
| 7 | Parallel backtest + smart sampling | 🔥🔥🔥 | Medium |
| 12 | Rolling 30-day performance badge | 🔥🔥 | Medium |

## HIGH IMPACT / HIGH EFFORT (Do third)

| # | Decision | Impact | Effort |
|---|----------|--------|--------|
| 5 | Multi-source data fallback chain | 🔥🔥🔥 | High |
| 8 | Auto-audit win rate adjustment | 🔥🔥 | High |
| 15 | Quality gates pipeline | 🔥🔥 | High |

## MEDIUM IMPACT (Defer)

| # | Decision | Impact | Effort |
|---|----------|--------|--------|
| 3 | Confidence interval on profile | 🔥 | Medium |
| 10 | Regime-aware strategy selection | 🔥 | Medium |
| 13 | Weekly re-evaluation matching | 🔥 | Medium |
| 14 | SignalRank multi-factor scoring | 🔥 | High |
