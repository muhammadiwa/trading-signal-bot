"""Technical indicators — pure pandas/numpy implementation.

No pandas-ta dependency needed (Python 3.14 compatibility, pandas-ta unavailable).
"""

import pandas as pd
import numpy as np


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing).

    Uses alpha=1/length for standard Wilder's RSI compatible with TradingView.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
    # Guard against zero and near-zero (floating-point underflow)
    avg_loss = avg_loss.where(avg_loss > 1e-10)
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=length, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average True Range."""
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=length, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average Directional Index."""
    atr_val = atr(high, low, close, length)
    # Guard against zero ATR (flat market) — replace with NaN to avoid inf
    atr_safe = atr_val.where(atr_val > 1e-10)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    plus_di = 100.0 * plus_dm.ewm(span=length, adjust=False).mean() / atr_safe
    minus_di = 100.0 * minus_dm.ewm(span=length, adjust=False).mean() / atr_safe
    di_sum = plus_di + minus_di
    di_sum = di_sum.where(di_sum > 1e-10)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(span=length, adjust=False).mean()


def bollinger_bands(
    close: pd.Series, length: int = 20, std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands: upper, middle, lower."""
    middle = sma(close, length)
    std = close.rolling(window=length).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


_REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}


def compute_all(ohlcv: pd.DataFrame) -> dict[str, pd.Series]:
    """Compute all indicators for an OHLCV DataFrame.

    Args:
        ohlcv: DataFrame with columns: open, high, low, close, volume.

    Returns:
        Dict[str, pd.Series] with 14 indicator values aligned to OHLCV index.

    Raises:
        ValueError: If required columns are missing or DataFrame is empty.
    """
    if len(ohlcv) == 0:
        raise ValueError("OHLCV DataFrame is empty")

    missing = _REQUIRED_COLUMNS - set(ohlcv.columns)
    if missing:
        raise ValueError(f"OHLCV DataFrame missing columns: {sorted(missing)}")

    close = ohlcv["close"]
    high = ohlcv["high"]
    low = ohlcv["low"]
    volume = ohlcv["volume"]

    macd_line, macd_signal, macd_hist = macd(close)
    # Compute SMA(20) once, reuse for BB and MA
    ma_20_val = sma(close, 20)
    bb_upper, bb_middle, bb_lower = bollinger_bands(close)

    result = {
        "rsi_14": rsi(close, 14),
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_hist,
        "ma_20": ma_20_val,
        "ma_50": sma(close, 50),
        "ma_200": sma(close, 200),
        "atr_14": atr(high, low, close, 14),
        "adx_14": adx(high, low, close, 14),
        "bb_upper": bb_upper,
        "bb_middle": bb_middle,
        "bb_lower": bb_lower,
        "volume_sma_20": sma(volume, 20),
        "volume_ratio": volume / sma(volume, 20).replace(0, np.nan),
    }

    # Warn if data insufficient for long-lookback indicators
    if len(ohlcv) < 200:
        import logging
        logging.getLogger(__name__).warning(
            "Only %d rows available; indicators requiring 50+ lookback (ma_50, ma_200) "
            "will have NaN values at the start of the series.",
            len(ohlcv),
        )

    return result
