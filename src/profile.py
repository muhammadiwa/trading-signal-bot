"""Pair profile computation — 4-dimension characteristic analysis.

Determines which strategy best matches a trading pair's character.
"""

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PairProfile:
    """4-dimension profile for a trading pair (0-100 each)."""

    symbol: str
    trendiness: float  # 0-100: how strongly trending
    volatility: float  # 0-100: how volatile
    mean_reversion: float  # 0-100: tendency to revert to mean
    volume_quality: float  # 0-100: volume stability + price correlation

    # Explanatory detail (for audit/debug)
    detail: dict = field(default_factory=dict)


def compute(ohlcv: pd.DataFrame, indicators: dict[str, pd.Series],
            symbol: str = "unknown", window: int = 90) -> PairProfile:
    """Compute pair profile from OHLCV and indicators.

    Uses a rolling window for current characterization.

    Args:
        ohlcv: OHLCV DataFrame.
        indicators: Pre-computed indicators dict.
        symbol: Trading pair symbol.
        window: Rolling window in days for current profile.

    Returns:
        PairProfile with 0-100 scores.
    """
    if len(ohlcv) < window:
        window = len(ohlcv)

    recent = ohlcv.iloc[-window:]
    adx = indicators["adx_14"].iloc[-window:]
    atr = indicators["atr_14"].iloc[-window:]
    rsi = indicators["rsi_14"].iloc[-window:]
    bb_lower = indicators["bb_lower"].iloc[-window:]
    bb_upper = indicators["bb_upper"].iloc[-window:]
    vol_ratio = indicators["volume_ratio"].iloc[-window:]

    close = ohlcv["close"].iloc[-window:]
    volume = ohlcv["volume"].iloc[-window:]

    # 1. Trendiness: ADX average + % time in trending regime (ADX > 25)
    adx_avg = float(adx.mean()) if not adx.isna().all() else 0.0
    adx_trending_pct = float((adx > 25).mean()) * 100
    trendiness = min(100.0, (adx_avg / 50) * 60 + adx_trending_pct * 0.4)

    # 2. Volatility: ATR/close ratio normalized
    atr_pct = (atr / close.replace(0, np.nan)).mean()
    vol_score = min(100.0, float(atr_pct) * 10000)

    # 3. Mean-reversion: % of RSI bounces (RSI < 30 or > 70 that revert within 5 bars)
    rsi_oversold = rsi < 30
    rsi_overbought = rsi > 70
    bounces = 0
    total_extremes = 0
    for i in range(len(rsi) - 5):
        if rsi_oversold.iloc[i]:
            total_extremes += 1
            # Check if price reverted (moved up) within 5 bars
            if close.iloc[i + 1:i + 6].max() > close.iloc[i] * 1.02:
                bounces += 1
        elif rsi_overbought.iloc[i]:
            total_extremes += 1
            if close.iloc[i + 1:i + 6].min() < close.iloc[i] * 0.98:
                bounces += 1
    mean_rev = min(100.0, (bounces / max(total_extremes, 1)) * 100)

    # 4. Volume quality: stability (1 − CV) + price-volume correlation
    vol_cv = float(volume.std() / max(volume.mean(), 1e-10)) if volume.mean() > 0 else 1.0
    vol_stability = max(0.0, 100 * (1 - min(vol_cv, 1.0)))
    # Price-volume correlation (absolute, both positive and negative are useful)
    pv_corr = abs(float(close.corr(volume))) if len(close) > 5 and volume.std() > 0 else 0.0
    volume_quality = min(100.0, vol_stability * 0.6 + pv_corr * 40)

    detail = {
        "adx_avg": round(adx_avg, 1),
        "adx_trending_pct": round(adx_trending_pct, 1),
        "atr_pct": round(float(atr_pct) * 100, 3),
        "rsi_bounces": bounces,
        "rsi_extremes": total_extremes,
        "vol_cv": round(vol_cv, 3),
        "pv_corr": round(pv_corr, 3),
        "window_days": window,
    }

    logger.debug(
        "%s profile: trend=%.0f vol=%.0f mean_rev=%.0f vol_q=%.0f",
        symbol, trendiness, vol_score, mean_rev, volume_quality,
    )

    return PairProfile(
        symbol=symbol,
        trendiness=round(trendiness, 1),
        volatility=round(vol_score, 1),
        mean_reversion=round(mean_rev, 1),
        volume_quality=round(volume_quality, 1),
        detail=detail,
    )
