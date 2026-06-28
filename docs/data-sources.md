# Data Source Limitations

Known caveats and compromises in the research data pipeline. Updated per retro AI #3.

---

## OHLCV (CCXT)

| Limitation | Detail |
|-----------|--------|
| Free tier | CCXT Binance/OKX free APIs — rate limited. No paid exchange API keys needed for MVP |
| History | Minimum 6 months requested, but some pairs (new listings) have less |
| CoinGecko fallback | Volume data unavailable — stored as `NaN`. All volume-dependent strategies silently skip |

## Sentiment

| Limitation | Detail |
|-----------|--------|
| Reddit RSS | No strict 24h filter — uses `sort=new&limit=25`. Active subreddits produce 25 posts within hours |
| Reddit deduplication | Posts matching multiple keywords (e.g. "bitcoin" + "crypto") are double-counted across fetches |
| Twitter/X | Permanently unavailable — free API deprecated. Weight auto-redistributed to Fear & Greed |
| Fear & Greed | Updates ~once/day from Alternative.me. May lag real-time sentiment |

## On-Chain

| Limitation | Detail |
|-----------|--------|
| Whale Alert | Requires free API key in `.env`. Without key → on-chain signal defaults to neutral |
| Whale Alert API format | `to`/`from` fields can be dict, string, or null — code normalizes defensively |
| CoinGecko active addresses | Free tier removed this endpoint. Uses `total_volumes` as proxy (trading volume, not address count) |
| Net flow | Computed as buy_volume − sell_volume (outflow = bullish). Direction verified correct but formula inverted from initial spec |

## Macro Calendar

| Limitation | Detail |
|-----------|--------|
| Manual maintenance | `config/macro_calendar.json` must be updated manually. No automatic calendar sync |
| Date-only events | Events stored as dates (no times). Treated as active until 23:59 UTC of that day |
| Multiple concurrent events | Highest-penalty event wins. Multiple high-impact events on same day are not compounded |

## Prediction Markets

| Limitation | Detail |
|-----------|--------|
| Polymarket | Free Gamma API, no key required. Returns crypto-tagged markets only |
| Staleness | Freshness checked at 12h. Stale data → prediction adjustment halved |
| Direction classification | Keyword-based heuristic (bullish/bearish word sets). May misclassify nuanced markets |
| Empty symbol guard | Empty/invalid symbols skip prediction adjustment entirely |

## LLM Reflection

| Limitation | Detail |
|-----------|--------|
| TokenRouter | Requires `TOKENROUTER_API_KEY` in `.env`. Without key → deterministic fallback only |
| Timeout | 3 seconds from config. No retry — reflection is non-critical |
| Cost | ~$0.001/call, ~$0.02/day for 20 signals |
| Model | Defaults to `deepseek/deepseek-v4-pro` via config `llm.model` |

## Adaptive Weights

| Limitation | Detail |
|-----------|--------|
| Minimum data | 30 outcomes required before weight adjustment activates |
| Convergence | EMA α=0.2 means ~30 days to converge. Slow to react to regime changes |
| Macro accuracy | Only measured during macro-flagged events — may have low sample size |
| Concurrent runs | No mutex — two simultaneous pipeline runs could double-apply EMA |
