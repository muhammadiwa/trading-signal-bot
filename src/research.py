"""Research data fetchers — Story 2.1: Sentiment Data (FR1.2).

Fear & Greed Index + Reddit RSS sentiment. Twitter/X unavailable (free API dead).
Auto-normalizes weights when sources are missing. Never crashes — returns neutral.
"""

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# Sentiment keywords for Reddit title analysis
BULLISH_WORDS = {"buy", "bullish", "long", "moon", "pump", "green", "breakout",
                 "rally", "surge", "up", "gain", "ath", "bull", "approve", "etf"}
BEARISH_WORDS = {"sell", "bearish", "short", "dump", "red", "crash", "correction",
                 "drop", "down", "loss", "fear", "liquidat", "blood", "ban", "reject"}
BEARISH_WORDS = {"sell", "bearish", "short", "dump", "red", "crash", "correction",
                 "drop", "down", "loss", "fear", "liquidat", "blood"}
REDDIT_SUBREDDITS = ["CryptoCurrency", "bitcoin"]
REDDIT_KEYWORDS = ["bitcoin", "ethereum", "crypto"]
REDDIT_USER_AGENT = "TradingSignalBot/1.0 (research pipeline)"


def fetch_fear_greed() -> Optional[dict]:
    """Fetch Fear & Greed Index from Alternative.me (free, no API key).

    Returns dict with keys: value (0-100), value_classification, timestamp.
    Returns None on any failure.
    """
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if data:
            return {
                "value": int(data[0]["value"]),
                "classification": data[0].get("value_classification", ""),
                "timestamp": data[0].get("timestamp", ""),
            }
    except Exception as e:
        logger.warning("Fear & Greed fetch failed: %s", e)
    return None


def _parse_reddit_feed(rss_text: str) -> tuple[int, int, int]:
    """Parse a Reddit RSS/Atom feed and count bullish vs bearish titles.

    Handles both Atom (xmlns:atom) and RSS 2.0 (<item>/<title>) formats.

    Returns (bullish_count, bearish_count, total_posts).
    """
    root = ET.fromstring(rss_text)
    bullish = bearish = total = 0

    # Try Atom namespace first
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//atom:entry", ns)
    if entries:
        for entry in entries:
            title_el = entry.find("atom:title", ns)
            if title_el is not None and title_el.text:
                words = set(re.findall(r'\w+', title_el.text.lower()))
                if words & BULLISH_WORDS:
                    bullish += 1
                elif words & BEARISH_WORDS:
                    bearish += 1
                total += 1
        return bullish, bearish, total

    # Fallback: RSS 2.0 <item> elements
    for item in root.findall(".//item"):
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            words = set(re.findall(r'\w+', title_el.text.lower()))
            if words & BULLISH_WORDS or "all time high" in title_el.text.lower():
                bullish += 1
            elif words & BEARISH_WORDS:
                bearish += 1
            total += 1

    return bullish, bearish, total


def fetch_reddit_sentiment() -> Optional[dict]:
    """Fetch Reddit RSS from crypto subreddits and compute keyword sentiment.

    Searches r/CryptoCurrency and r/bitcoin. Counts bullish vs bearish
    keyword mentions in post titles. No API key required.

    Note: sort=new&limit=25 approximates "last 24h" — active subreddits
    produce 25+ posts within hours. Full 24h window requires Reddit API key.

    Returns dict with: bullish_count, bearish_count, total_posts, ratio (0-1).
    Returns None if all feeds fail.
    """
    bullish_total = bearish_total = posts_total = 0

    for subreddit in REDDIT_SUBREDDITS:
        for keyword in REDDIT_KEYWORDS:
            url = (
                f"https://www.reddit.com/r/{subreddit}/search.rss"
                f"?q={keyword}&sort=new&restrict_sr=on&limit=25"
            )
            try:
                resp = requests.get(url, timeout=15, headers={"User-Agent": REDDIT_USER_AGENT})
                if resp.status_code == 429:
                    logger.warning("Reddit rate-limited (429) — quota may be exhausted for %s/%s", subreddit, keyword)
                    continue
                if resp.status_code != 200:
                    continue
                b, s, t = _parse_reddit_feed(resp.text)
                bullish_total += b
                bearish_total += s
                posts_total += t
            except Exception as e:
                logger.warning("Reddit %s/%s fetch failed: %s", subreddit, keyword, e)

    if posts_total == 0:
        logger.warning("Reddit sentiment: 0 posts parsed across all feeds")
        return None

    ratio = bullish_total / max(bullish_total + bearish_total, 1)
    return {
        "bullish_count": bullish_total,
        "bearish_count": bearish_total,
        "total_posts": posts_total,
        "ratio": round(ratio, 3),
    }


def fetch_sentiment_composite() -> dict:
    """Fetch all sentiment sources and compute weighted composite.

    Formula: 0.4 × FG + 0.3 × Reddit + 0.3 × Twitter
    Twitter/X free API is dead → weight auto-redistributed to available sources.
    Returns dict with: fear_greed (0-100), reddit_ratio (0-1),
    twitter_ratio (None), active_sources, composite (0-100).
    """
    fg = fetch_fear_greed()
    reddit = fetch_reddit_sentiment()

    fear_greed_val = fg["value"] if fg else None
    reddit_ratio = reddit["ratio"] if reddit else None
    twitter_ratio = None  # Free API dead — not available

    # Count active sources
    active = sum(1 for v in [fear_greed_val, reddit_ratio, twitter_ratio] if v is not None)

    if active == 0:
        logger.warning("Sentiment: 0 of 3 sources active — returning neutral 50")
        return {
            "fear_greed": None, "reddit_ratio": None, "twitter_ratio": None,
            "active_sources": 0, "composite": 50.0,
        }

    # Build weighted sum with auto-normalization
    # When a source fails: use neutral 0.5 for weight redistribution
    weights = {"fg": 0.4, "reddit": 0.3, "twitter": 0.3}

    if fear_greed_val is not None:
        fg_contrib = fear_greed_val / 100
    else:
        fg_contrib = 0.5  # AC1: default to neutral 50 when FG fails

    if reddit_ratio is not None:
        reddit_contrib = reddit_ratio
        reddit_weight = weights["reddit"]
    else:
        reddit_contrib = 0.5
        reddit_weight = 0.0  # Exclude from active count

    # Twitter always unavailable
    twitter_weight = 0.0

    composite = (
        weights["fg"] * fg_contrib + reddit_weight * reddit_contrib
    ) / max(weights["fg"] + reddit_weight + twitter_weight, 0.01) * 100

    composite = round(max(0, min(100, composite)), 1)

    logger.info(
        "Sentiment: FG=%s Reddit=%s → composite=%.1f (active=%d/3)",
        fear_greed_val, reddit_ratio, composite, active,
    )

    return {
        "fear_greed": fear_greed_val,
        "reddit_ratio": reddit_ratio,
        "twitter_ratio": twitter_ratio,
        "active_sources": active,
        "composite": composite,
    }


# ============================================================
# Story 2.2: On-chain Data Fetcher (FR1.3)
# ============================================================

WHALE_ALERT_API_KEY = os.getenv("WHALE_ALERT_API_KEY", "")


def fetch_whale_transactions(min_value_usd: int = 1_000_000) -> Optional[dict]:
    """Fetch whale transactions from Whale Alert API, last 24h only.

    Separates buy-side vs sell-side based on transaction metadata.

    Args:
        min_value_usd: Minimum transaction value in USD (default $1M per AC).

    Returns:
        dict with buy_count, sell_count, buy_volume, sell_volume, net_flow.
        Returns None if API key missing or fetch fails.
    """
    if not WHALE_ALERT_API_KEY:
        logger.debug("Whale Alert API key not configured — skipping on-chain")
        return None

    since_ts = int((time.time() - 86400))  # 24 hours ago

    for attempt in range(3):
        try:
            resp = requests.get(
                "https://api.whale-alert.io/v1/transactions",
                params={
                    "api_key": WHALE_ALERT_API_KEY,
                    "min_value": min_value_usd,
                    "start": since_ts,
                    "limit": 100,
                },
                timeout=15,
            )
            resp.raise_for_status()
            txs = resp.json().get("transactions", [])
            break  # Success — exit retry loop
        except Exception as e:
            if attempt < 2:
                logger.warning("Whale Alert attempt %d/3 failed: %s — retrying", attempt + 1, e)
                time.sleep(2 ** attempt)
            else:
                logger.warning("Whale Alert unavailable after 3 attempts — on-chain signal skipped: %s", e)
                return None

    if not txs:
        return None

    buy_count = sell_count = 0
    buy_volume = sell_volume = 0.0

    for tx in txs:
        amount = tx.get("amount_usd", 0) or 0
        # Guard: None/null values in JSON deserialize to None
        if not isinstance(amount, (int, float)):
            amount = 0
        # Heuristic: transactions TO exchanges are sells, FROM are buys.
        # Whale Alert API may return "to"/"from" as dict, str, or None.
        # Normalize: if string → treat as owner_type directly; if None/dict → extract
        def _safe_owner(field):
            val = tx.get(field)
            if isinstance(val, str):
                return val
            if isinstance(val, dict):
                return val.get("owner_type", "")
            return ""

        to_owner = _safe_owner("to")
        from_owner = _safe_owner("from")

        if to_owner == "exchange":
            sell_count += 1
            sell_volume += amount
        elif from_owner == "exchange":
            buy_count += 1
            buy_volume += amount
        else:
            # Unknown direction: classify by transaction type if available
            tx_type = (tx.get("transaction_type", "") or "").lower()
            if "sell" in tx_type:
                sell_count += 1
                sell_volume += amount
            elif "buy" in tx_type:
                buy_count += 1
                buy_volume += amount
            # else: unclassified, skip

    return {
        "buy_count": buy_count, "sell_count": sell_count,
        "buy_volume": buy_volume, "sell_volume": sell_volume,
        "total_count": buy_count + sell_count,
        # AC1: outflow (from exchanges = buy) is bullish, inflow (to exchanges = sell) is bearish
        # Positive net_flow = net outflow = more buying from exchanges → bullish
        "net_flow": buy_volume - sell_volume,
    }


def fetch_coingecko_active_addresses(symbol: str) -> Optional[dict]:
    """Fetch active address data from CoinGecko API (free tier).

    Uses /coins/{id}/market_chart endpoint with vs_currency=usd, days=7.

    Returns dict with: current_active, avg_7d, trend ("rising"/"declining"/"flat").
    Returns None on failure.
    """
    # CoinGecko requires full coin IDs, not symbols
    COINGECKO_IDS = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2",
        "LINK": "chainlink", "UNI": "uniswap",
    }
    base = symbol.split("-")[0].upper() if symbol else ""
    coin_id = COINGECKO_IDS.get(base)
    if not coin_id:
        return None

    try:
        # CoinGecko free API: /coins/{id}/market_chart?vs_currency=usd&days=7
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": "7"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract active addresses (deprecated in free tier — use total_volumes as proxy)
        addresses = [d[1] for d in data.get("total_volumes", [])]
        if len(addresses) < 2:
            return None

        current = addresses[-1]
        avg_7d = sum(addresses) / len(addresses)
        if current > avg_7d * 1.05:
            trend = "rising"
        elif current < avg_7d * 0.95:
            trend = "declining"
        else:
            trend = "flat"

        return {
            "current_active": current,
            "avg_7d": round(avg_7d, 0),
            "trend": trend,
        }
    except Exception as e:
        logger.warning("CoinGecko active address fetch for %s failed: %s", symbol, e)
    return None


def compute_onchain(whale_data: Optional[dict],
                    active_addr: Optional[dict],
                    symbol: str = "") -> tuple[str, float]:
    """Compute composite on-chain signal.

    Classification (deterministic per FR3.2):
      1. Whale metrics (AC1-2):
         - net_flow > 0 → more outflow = bullish
         - net_flow < 0 → more inflow = bearish
         - whale_ratio = buy_count / (buy+sell)
         - ratio > 0.6 → bullish, < 0.4 → bearish, else neutral
      2. Active addresses (AC3):
         - trend rising → bullish, declining → bearish
      3. Combined: strongest signal wins

    Args:
        whale_data: Output from fetch_whale_transactions().
        active_addr: Output from fetch_coingecko_active_addresses().
        symbol: Trading pair symbol.

    Returns:
        (onchain_signal: str, multiplier: float)
        Signal ∈ {"bullish", "neutral", "bearish"}.
        Multiplier: 1.15 bullish, 1.0 neutral, 0.85 bearish per FR3.2.
    """
    signals = []

    # Whale signals
    if whale_data and whale_data.get("total_count", 0) > 0:
        buy = whale_data["buy_count"]
        sell = whale_data["sell_count"]
        total = buy + sell
        if total > 0:
            ratio = buy / total
            if ratio > 0.6:
                signals.append("bullish")
            elif ratio < 0.4:
                signals.append("bearish")
            else:
                signals.append("neutral")

        # Net flow: outflow (positive) = bullish, inflow (negative) = bearish
        net_flow = whale_data.get("net_flow", 0)
        if net_flow > 1_000_000:
            signals.append("bullish")
        elif net_flow < -1_000_000:
            signals.append("bearish")

    # Active address trend
    if active_addr:
        trend = active_addr.get("trend", "")
        if trend == "rising":
            signals.append("bullish")
        elif trend == "declining":
            signals.append("bearish")

    # Deterministic classification: strongest signal wins
    if "bullish" in signals:
        return "bullish", 1.15
    elif "bearish" in signals:
        return "bearish", 0.85
    else:
        return "neutral", 1.0


# ============================================================
# Story 2.3: Macro Calendar Overlay (FR1.4)
# ============================================================

def load_macro_calendar() -> list[dict]:
    """Load macro event calendar from config/macro_calendar.json.

    Returns list of event dicts: {date, event, impact}.
    Returns empty list if file missing or unparseable.
    """
    calendar_path = Path(__file__).resolve().parent.parent / "config" / "macro_calendar.json"
    if not calendar_path.exists():
        logger.warning("Macro calendar file not found at %s — macro overlay disabled", calendar_path)
        return []

    try:
        with open(calendar_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.error("Macro calendar JSON must be a list, got %s — ignoring", type(data).__name__)
            return []
        # Validate each entry
        valid = []
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                logger.warning("Macro calendar entry %d is not a dict — skipping", i)
                continue
            if "date" not in entry or "event" not in entry:
                logger.warning("Macro calendar entry %d missing date/event — skipping", i)
                continue
            valid.append(entry)
        return valid
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Macro calendar JSON parse error: %s — macro overlay disabled", e)
        return []


def get_upcoming_macro_events(days_ahead: int = 7) -> list[dict]:
    """Get macro events within the next N days, sorted by date."""
    calendar = load_macro_calendar()
    if not calendar:
        return []

    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=days_ahead)
    upcoming = []
    for ev in calendar:
        try:
            ev_date = datetime.strptime(ev["date"], "%Y-%m-%d").date()
            if today <= ev_date <= cutoff:
                upcoming.append(ev)
        except (ValueError, KeyError):
            continue
    return sorted(upcoming, key=lambda e: e["date"])


def macro_flag_for_date(target_date: Optional[datetime] = None,
                        look_ahead_days: int = 7) -> tuple[bool, float, Optional[str]]:
    """Check for macro events near target date.

    Returns (has_event: bool, penalty: float, warning: str|None):
      - High impact ≤24h → penalty 0.20, warning with event name
      - High impact ≤48h → penalty 0.10
      - Medium impact → half of high-impact penalty
      - No event → (False, 0.0, None)
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc)

    events = get_upcoming_macro_events(look_ahead_days)
    if not events:
        return False, 0.0, None

    best_penalty = 0.0
    best_warning = None
    has_event = False

    for ev in events:
        try:
            ev_date = datetime.strptime(ev["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            # Treat calendar-date events as active until end of day (23:59 UTC)
            # Prevents midnight boundary from making same-day events invisible
            ev_date = ev_date.replace(hour=23, minute=59)
            hours_away = (ev_date - target_date).total_seconds() / 3600
            if hours_away < 0:
                continue  # Past event (yesterday or earlier)
            impact = ev.get("impact", "medium")
            event_name = ev.get("event", "Unknown")

            if hours_away <= 24:
                penalty = 0.20 if impact == "high" else 0.10
                warning = f"{event_name} in {hours_away:.0f}h — elevated volatility expected"
            elif hours_away <= 48:
                penalty = 0.10 if impact == "high" else 0.05
                hours_text = "tomorrow" if hours_away <= 36 else "2 days"
                warning = f"{event_name} {hours_text} — elevated volatility expected"
            else:
                continue

            if penalty > best_penalty:
                best_penalty = penalty
                best_warning = warning
                has_event = True
        except (ValueError, KeyError):
            continue

    return has_event, best_penalty, best_warning


# ============================================================
# Story 2.4: Prediction Markets Fetcher (FR1.5)
# ============================================================

# Crypto-relevant keywords for filtering Polymarket events
POLYMARKET_CRYPTO_KEYWORDS = [
    "btc", "bitcoin", "eth", "ethereum", "crypto", "rate", "fed",
    "regulation", "sec", "etf", "halving", "defi", "stablecoin",
]

# Last successful fetch timestamp for freshness check
_polymarket_last_fetch: Optional[float] = None
_polymarket_cache: Optional[list[dict]] = None


def fetch_polymarket(category: str = "crypto", limit: int = 20) -> Optional[list[dict]]:
    """Fetch top Polymarket prediction probabilities from Gamma API.

    Filters to crypto-relevant markets, stores top 5 by 24h volume.

    Returns list of market dicts: {question, probability, volume_24h, slug}.
    Returns None on failure.
    """
    global _polymarket_last_fetch, _polymarket_cache

    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={
                "tag": category,
                "limit": limit,
                "closed": "false",
                "order": "volume24hr",
            },
            timeout=15,
        )
        resp.raise_for_status()
        markets = resp.json()
    except Exception as e:
        logger.warning("Polymarket API unavailable — prediction markets skipped: %s", e)
        # Invalidate cache on failure — stale data must not report as fresh
        global _polymarket_last_fetch
        _polymarket_last_fetch = None
        return None

    if not markets:
        return None

    # Filter to crypto-relevant markets
    relevant = []
    for m in markets:
        question = (m.get("question", "") or "").lower()
        if any(kw in question for kw in POLYMARKET_CRYPTO_KEYWORDS):
            prob = m.get("probability")
            relevant.append({
                "question": m.get("question", ""),
                "probability": float(prob) if prob is not None else None,
                "volume_24h": m.get("volume24hr", 0),
                "slug": m.get("slug", ""),
            })

    # Top 5 by volume
    relevant.sort(key=lambda x: x["volume_24h"], reverse=True)
    top5 = relevant[:5]

    # Cache for freshness check
    _polymarket_last_fetch = time.time()
    _polymarket_cache = top5

    logger.info("Polymarket: %d crypto-relevant markets, top 5 selected", len(relevant))
    return top5


def polymarket_prediction_adjustment(symbol: str,
                                     markets: Optional[list[dict]]) -> float:
    """Compute prediction market adjustment for a symbol (FR4).

    Rules:
      - Find markets matching the symbol
      - If probability > 70% for bullish context → +0.05
      - If probability > 70% for bearish context → −0.05
      - No clear direction → 0.0

    Context classification is heuristic: keyword-based sentiment.
    """
    if not markets:
        return 0.0

    base = symbol.split("-")[0].lower() if symbol else ""
    if not base:
        return 0.0  # Guard: empty symbol → no prediction adjustment
    bullish_keywords = {"buy", "bull", "up", "rise", "rally", "approve", "etf", "halving"}
    bearish_keywords = {"sell", "bear", "down", "crash", "ban", "reject", "decline", "tighten"}

    for m in markets:
        question = (m.get("question", "") or "").lower()
        prob = m.get("probability")
        # Guard: None probability from API null values
        if prob is None:
            continue
        try:
            prob = float(prob)
        except (TypeError, ValueError):
            continue
        if prob <= 0.70:
            continue
        if base not in question:
            continue

        # Determine direction
        words = set(question.split())
        if words & bullish_keywords:
            logger.debug("Polymarket: bullish %s at %.0f%% → +0.05", base, prob * 100)
            return 0.05
        elif words & bearish_keywords:
            logger.debug("Polymarket: bearish %s at %.0f%% → −0.05", base, prob * 100)
            return -0.05

    return 0.0


def polymarket_is_fresh(max_age_hours: int = 12) -> bool:
    """Check if cached Polymarket data is fresh (≤12 hours old per AC)."""
    if _polymarket_last_fetch is None:
        return False
    fresh = (time.time() - _polymarket_last_fetch) < max_age_hours * 3600
    if not fresh:
        logger.warning("Polymarket data stale (>%dh) — reducing prediction weight", max_age_hours)
    return fresh
