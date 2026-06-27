"""Trading strategy protocol and implementations.

Each strategy is a pure function: no network, no disk, no side effects.
Follows AD-5: StrategyProtocol with evaluate(ohlcv, indicators) -> StrategySignal.
"""

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd


@dataclass
class StrategySignal:
    """Output of a strategy evaluation."""

    action: str  # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 - 1.0
    entry_price: float
    stop_loss: float = 0.0  # 0.0 for HOLD (no trade)
    take_profit: float | None = None
    metadata: dict = field(default_factory=dict)


class StrategyProtocol(Protocol):
    """Protocol that all strategies must implement."""

    name: str
    weight: float

    def evaluate(
        self, ohlcv: pd.DataFrame, indicators: dict[str, pd.Series]
    ) -> StrategySignal: ...


# ============================================================
# Strategy 1: Momentum Breakout
# ============================================================


class MomentumBreakout:
    """Buy when price breaks above N-period high; sell below N-period low.

    Filter: volume must confirm the breakout (volume > 1.5x SMA).
    """

    name = "Momentum Breakout"
    weight = 0.25

    def __init__(self, n: int = 20, k: float = 0.005):
        self.n = n
        self.k = k

    def evaluate(
        self, ohlcv: pd.DataFrame, indicators: dict[str, pd.Series]
    ) -> StrategySignal:
        close = ohlcv["close"].iloc[-1]
        high_n = ohlcv["high"].rolling(self.n).max().iloc[-1]
        low_n = ohlcv["low"].rolling(self.n).min().iloc[-1]
        vol_ratio = indicators["volume_ratio"].iloc[-1]
        atr_14 = indicators["atr_14"].iloc[-1]

        if pd.isna(high_n) or pd.isna(low_n):
            return StrategySignal("HOLD", 0.0, close)

        if close > high_n * (1 + self.k) and vol_ratio > 1.5:
            strength = (close - high_n) / (atr_14 + 1e-10)
            conf = min(1.0, 0.5 + strength * 0.3)
            return StrategySignal(
                "BUY", conf, close,
                stop_loss=close - atr_14 * 1.5,
                take_profit=close + atr_14 * 3.0,
                metadata={"breakout": "bullish", "vol_ratio": vol_ratio},
            )

        if close < low_n * (1 - self.k) and vol_ratio > 1.5:
            strength = (low_n - close) / (atr_14 + 1e-10)
            conf = min(1.0, 0.5 + strength * 0.3)
            return StrategySignal(
                "SELL", conf, close,
                stop_loss=close + atr_14 * 1.5,
                take_profit=close - atr_14 * 3.0,
                metadata={"breakout": "bearish", "vol_ratio": vol_ratio},
            )

        return StrategySignal("HOLD", 0.0, close)


# ============================================================
# Strategy 2: Trend Following (MA Crossover)
# ============================================================


class TrendFollowing:
    """Buy when MA20 crosses above MA50; sell when crosses below.

    Filter: ADX > 25 (trending market only).
    """

    name = "Trend Following"
    weight = 0.25

    def __init__(self, short: int = 20, long: int = 50, adx_threshold: int = 25):
        self.short = short
        self.long = long
        self.adx_threshold = adx_threshold

    def evaluate(
        self, ohlcv: pd.DataFrame, indicators: dict[str, pd.Series]
    ) -> StrategySignal:
        close = ohlcv["close"].iloc[-1]
        ma_short = indicators[f"ma_{self.short}"]
        ma_long = indicators[f"ma_{self.long}"]
        adx = indicators["adx_14"].iloc[-1]

        if pd.isna(ma_short.iloc[-1]) or pd.isna(ma_long.iloc[-1]) or pd.isna(adx):
            return StrategySignal("HOLD", 0.0, close)

        # Crossover detection: current vs previous bar
        prev_short = ma_short.iloc[-2]
        prev_long = ma_long.iloc[-2]
        curr_short = ma_short.iloc[-1]
        curr_long = ma_long.iloc[-1]

        if pd.isna(prev_short) or pd.isna(prev_long):
            return StrategySignal("HOLD", 0.0, close)

        atr_val = indicators["atr_14"].iloc[-1]

        if prev_short <= prev_long and curr_short > curr_long and adx > self.adx_threshold:
            conf = min(1.0, 0.6 + (adx - self.adx_threshold) / 50)
            return StrategySignal(
                "BUY", conf, close,
                stop_loss=close - atr_val * 1.5,
                take_profit=close + atr_val * 3.0,
                metadata={"ma_short": curr_short, "ma_long": curr_long, "adx": adx},
            )

        if prev_short >= prev_long and curr_short < curr_long and adx > self.adx_threshold:
            conf = min(1.0, 0.6 + (adx - self.adx_threshold) / 50)
            return StrategySignal(
                "SELL", conf, close,
                stop_loss=close + atr_val * 1.5,
                take_profit=close - atr_val * 3.0,
                metadata={"ma_short": curr_short, "ma_long": curr_long, "adx": adx},
            )

        return StrategySignal("HOLD", 0.0, close)


# ============================================================
# Strategy 3: Mean Reversion (RSI + Bollinger)
# ============================================================


class MeanReversion:
    """Buy when RSI oversold (<30) + price below lower Bollinger band.

    Sell when RSI overbought (>70) + price above upper Bollinger band.
    Filter: Volume confirmation > 1.3x SMA AND ADX < 20 (ranging market).
    """

    name = "Mean Reversion"
    weight = 0.20

    def __init__(self, rsi_length: int = 14, bb_length: int = 20):
        self.rsi_length = rsi_length
        self.bb_length = bb_length

    def evaluate(
        self, ohlcv: pd.DataFrame, indicators: dict[str, pd.Series]
    ) -> StrategySignal:
        close = ohlcv["close"].iloc[-1]
        rsi_val = indicators["rsi_14"].iloc[-1]
        bb_upper = indicators["bb_upper"].iloc[-1]
        bb_lower = indicators["bb_lower"].iloc[-1]
        vol_ratio = indicators["volume_ratio"].iloc[-1]
        adx = indicators["adx_14"].iloc[-1]
        atr_val = indicators["atr_14"].iloc[-1]

        if any(pd.isna(v) for v in [rsi_val, bb_upper, bb_lower, vol_ratio, adx]):
            return StrategySignal("HOLD", 0.0, close)

        # Only trigger in ranging markets
        if adx >= 20:
            return StrategySignal("HOLD", 0.0, close)

        if rsi_val < 30 and close < bb_lower and vol_ratio > 1.3:
            conf = min(1.0, (30 - rsi_val) / 30 + 0.3)
            return StrategySignal(
                "BUY", conf, close,
                stop_loss=close - atr_val * 1.5,
                take_profit=close + atr_val * 3.0,
                metadata={"rsi": rsi_val, "bb_lower": bb_lower, "vol_ratio": vol_ratio},
            )

        if rsi_val > 70 and close > bb_upper and vol_ratio > 1.3:
            conf = min(1.0, (rsi_val - 70) / 30 + 0.3)
            return StrategySignal(
                "SELL", conf, close,
                stop_loss=close + atr_val * 1.5,
                take_profit=close - atr_val * 3.0,
                metadata={"rsi": rsi_val, "bb_upper": bb_upper, "vol_ratio": vol_ratio},
            )

        return StrategySignal("HOLD", 0.0, close)


# ============================================================
# Strategy 4: Volatility Breakout (ATR Channel)
# ============================================================


class VolatilityBreakout:
    """Buy above SMA20 + ATR×k; sell below SMA20 − ATR×k."""

    name = "Volatility Breakout"
    weight = 0.15

    def __init__(self, atr_length: int = 14, k: float = 1.5):
        self.atr_length = atr_length
        self.k = k

    def evaluate(
        self, ohlcv: pd.DataFrame, indicators: dict[str, pd.Series]
    ) -> StrategySignal:
        close = ohlcv["close"].iloc[-1]
        sma_20 = indicators["ma_20"].iloc[-1]
        atr_val = indicators["atr_14"].iloc[-1]

        if pd.isna(sma_20) or pd.isna(atr_val):
            return StrategySignal("HOLD", 0.0, close)

        if close > sma_20 + atr_val * self.k:
            conf = min(1.0, 0.5 + (close - sma_20) / (atr_val * 3 + 1e-10))
            return StrategySignal(
                "BUY", conf, close,
                stop_loss=close - atr_val * 1.5,
                take_profit=close + atr_val * 3.0,
                metadata={"sma_20": sma_20, "atr": atr_val},
            )

        if close < sma_20 - atr_val * self.k:
            conf = min(1.0, 0.5 + (sma_20 - close) / (atr_val * 3 + 1e-10))
            return StrategySignal(
                "SELL", conf, close,
                stop_loss=close + atr_val * 1.5,
                take_profit=close - atr_val * 3.0,
                metadata={"sma_20": sma_20, "atr": atr_val},
            )

        return StrategySignal("HOLD", 0.0, close)


# ============================================================
# Strategy 5: Volume-Price Divergence
# ============================================================


class VolumeDivergence:
    """Bullish divergence: new low with declining volume → BUY.
    Bearish divergence: new high with declining volume → SELL.

    Compares last 2 swing points.
    """

    name = "Volume-Price Divergence"
    weight = 0.15

    def evaluate(
        self, ohlcv: pd.DataFrame, indicators: dict[str, pd.Series]
    ) -> StrategySignal:
        close = ohlcv["close"].iloc[-1]
        vol = ohlcv["volume"]
        if len(vol) < 20:
            return StrategySignal("HOLD", 0.0, close)

        atr_val = indicators["atr_14"].iloc[-1]
        if pd.isna(atr_val):
            return StrategySignal("HOLD", 0.0, close)

        # Find swing points: last 2 peaks for bearish, last 2 troughs for bullish
        n = len(ohlcv)
        window = min(20, n - 1)

        # Bullish divergence: price makes lower low, volume declines
        recent_lows = ohlcv["close"].iloc[-window:].rolling(5).min()
        troughs = []
        for i in range(window - 1):
            idx = -window + i
            if i > 0 and i < window - 1:
                if recent_lows.iloc[i] < recent_lows.iloc[i - 1] and recent_lows.iloc[i] < recent_lows.iloc[i + 1]:
                    troughs.append((close.index[idx], close.iloc[idx], vol.iloc[idx]))

        if len(troughs) >= 2:
            t1, t2 = troughs[-2], troughs[-1]
            # Price lower but volume lower → bullish divergence
            if t2[1] < t1[1] and t2[2] < t1[2]:
                conf = min(1.0, 0.5 + (t1[2] - t2[2]) / (t1[2] + 1e-10) * 0.5)
                return StrategySignal(
                    "BUY", conf, ohlcv["close"].iloc[-1],
                    stop_loss=close - atr_val * 1.5,
                    take_profit=close + atr_val * 3.0,
                    metadata={"divergence": "bullish"},
                )

        # Bearish divergence: price makes higher high, volume declines
        recent_highs = ohlcv["close"].iloc[-window:].rolling(5).max()
        peaks = []
        for i in range(window - 1):
            idx = -window + i
            if i > 0 and i < window - 1:
                if recent_highs.iloc[i] > recent_highs.iloc[i - 1] and recent_highs.iloc[i] > recent_highs.iloc[i + 1]:
                    peaks.append((ohlcv.index[idx], ohlcv["close"].iloc[idx], ohlcv["volume"].iloc[idx]))

        if len(peaks) >= 2:
            p1, p2 = peaks[-2], peaks[-1]
            if p2[1] > p1[1] and p2[2] < p1[2]:
                conf = min(1.0, 0.5 + (p1[2] - p2[2]) / (p1[2] + 1e-10) * 0.5)
                return StrategySignal(
                    "SELL", conf, ohlcv["close"].iloc[-1],
                    stop_loss=close + atr_val * 1.5,
                    take_profit=close - atr_val * 3.0,
                    metadata={"divergence": "bearish"},
                )

        return StrategySignal("HOLD", 0.0, close)


# ============================================================
# Strategy Registry
# ============================================================


def all_strategies() -> list:
    """Return all strategy instances with default parameters."""
    return [
        MomentumBreakout(),
        TrendFollowing(),
        MeanReversion(),
        VolatilityBreakout(),
        VolumeDivergence(),
    ]
