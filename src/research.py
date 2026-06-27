"""Research data fetchers — Story 2.1: Sentiment Data (FR1.2).

Fear & Greed Index + Reddit RSS sentiment. Twitter/X unavailable (free API dead).
Auto-normalizes weights when sources are missing. Never crashes — returns neutral.
"""

import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Sentiment keywords for Reddit title analysis
BULLISH_WORDS = {"buy", "bullish", "long", "moon", "pump", "green", "breakout",
                 "rally", "surge", "up", "gain", "ath", "all time high"}
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
            if words & BULLISH_WORDS:
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
    weights = {"fg": 0.4, "reddit": 0.3, "twitter": 0.3}
    total_weight = 0.0
    weighted_sum = 0.0

    if fear_greed_val is not None:
        weighted_sum += weights["fg"] * (fear_greed_val / 100)
        total_weight += weights["fg"]
    if reddit_ratio is not None:
        weighted_sum += weights["reddit"] * reddit_ratio
        total_weight += weights["reddit"]
    # Twitter weight redistributes automatically (not added to total)

    composite = (weighted_sum / total_weight) * 100 if total_weight > 0 else 50.0
    composite = round(composite, 1)

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
