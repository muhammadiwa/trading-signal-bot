"""Research data fetchers — Story 2.1: Sentiment Data (FR1.2).

Fear & Greed Index + Reddit RSS sentiment. Twitter/X unavailable (free API dead).
Auto-normalizes weights when sources are missing. Never crashes — returns neutral.
"""

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Sentiment keywords for Reddit title analysis
BULLISH_WORDS = {"buy", "bullish", "long", "moon", "pump", "green", "breakout",
                 "rally", "surge", "up", "gain", "ath"}
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
                if words & BULLISH_WORDS or "all time high" in title_el.text.lower():
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
                if resp.status_code != 200:
                    continue
                b, s, t = _parse_reddit_feed(resp.text)
                bullish_total += b
                bearish_total += s
                posts_total += t
            except Exception as e:
                logger.debug("Reddit %s/%s fetch failed: %s", subreddit, keyword, e)

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
    """Fetch whale transactions from Whale Alert API.

    Separates buy-side vs sell-side based on transaction metadata.

    Args:
        min_value_usd: Minimum transaction value in USD (default $1M per AC).

    Returns:
        dict with buy_count, sell_count, buy_volume, sell_volume.
        Returns None if API key missing or fetch fails.
    """
    if not WHALE_ALERT_API_KEY:
        logger.debug("Whale Alert API key not configured — skipping on-chain")
        return None

    try:
        resp = requests.get(
            "https://api.whale-alert.io/v1/transactions",
            params={"api_key": WHALE_ALERT_API_KEY, "min_value": min_value_usd, "limit": 100},
            timeout=15,
        )
        resp.raise_for_status()
        txs = resp.json().get("transactions", [])
    except Exception as e:
        logger.warning("Whale Alert fetch failed: %s", e)
        return None

    if not txs:
        return None

    buy_count = sell_count = 0
    buy_volume = sell_volume = 0.0

    for tx in txs:
        amount = tx.get("amount_usd", 0)
        # Heuristic: transactions TO exchanges are sells, FROM are buys
        to_owner = (tx.get("to", {}) or {}).get("owner_type", "")
        from_owner = (tx.get("from", {}) or {}).get("owner_type", "")

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
    }


def fetch_coingecko_trending() -> Optional[list[str]]:
    """Fetch trending coins from CoinGecko (free, no API key)."""
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        resp.raise_for_status()
        coins = resp.json().get("coins", [])
        return [c["item"]["symbol"].upper() for c in coins[:10]]
    except Exception as e:
        logger.warning("CoinGecko trending fetch failed: %s", e)
    return None


def compute_onchain(whale_data: Optional[dict],
                    trending: Optional[list[str]],
                    symbol: str = "") -> tuple[str, float]:
    """Compute composite on-chain signal.

    Classification (deterministic):
      1. Whale buy/sell ratio: buy_count / (buy_count + sell_count)
         > 0.6 → bullish, < 0.4 → bearish, else neutral
      2. CoinGecko trending: if symbol in trending → bullish
      3. Combined: strongest signal wins (bullish > neutral > bearish)

    Args:
        whale_data: Output from fetch_whale_transactions().
        trending: List of trending symbols from CoinGecko.
        symbol: Trading pair symbol.

    Returns:
        (onchain_signal: str, multiplier: float)
        Signal is "bullish", "neutral", or "bearish".
        Multiplier: 1.15 bullish, 1.0 neutral, 0.85 bearish per FR3.2.
    """
    base = symbol.split("-")[0].upper() if symbol else ""
    signals = []

    # Whale ratio signal
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

    # Trending signal
    if trending and base in trending:
        signals.append("bullish")

    # Deterministic classification: strongest signal wins
    if "bullish" in signals:
        return "bullish", 1.15
    elif "bearish" in signals:
        return "bearish", 0.85
    else:
        return "neutral", 1.0
