"""Smoke tests for technical indicators — pytest compatible."""

import numpy as np
import pandas as pd

from src.indicators import rsi, macd, sma, ema, atr, adx, bollinger_bands, compute_all


def _make_ohlcv(n: int = 250) -> pd.DataFrame:
    np.random.seed(42)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 2)
    return pd.DataFrame({
        "timestamp": dates,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "volume": np.random.uniform(1e6, 5e6, n),
    })


def test_compute_all_returns_14_indicators():
    df = _make_ohlcv(250)
    result = compute_all(df)
    assert len(result) == 14, f"Expected 14, got {len(result)}"
    expected_keys = {
        "rsi_14", "macd", "macd_signal", "macd_histogram",
        "ma_20", "ma_50", "ma_200", "atr_14", "adx_14",
        "bb_upper", "bb_middle", "bb_lower",
        "volume_sma_20", "volume_ratio",
    }
    assert set(result.keys()) == expected_keys


def test_rsi_range():
    df = _make_ohlcv(250)
    rsi_vals = rsi(df["close"], 14)
    valid = rsi_vals.dropna()
    assert valid.min() >= 0
    assert valid.max() <= 100


def test_rsi_all_gain_returns_100():
    """RSI for strictly increasing prices should be 100 (all gains, no losses)."""
    close = pd.Series(np.linspace(20, 50, 30))
    result = rsi(close, 14)
    assert result.iloc[-1] > 99.0, f"All-gain RSI should be ~100, got {result.iloc[-1]}"


def test_macd_shape():
    df = _make_ohlcv(250)
    macd_line, signal, hist = macd(df["close"])
    assert len(macd_line) == len(df)
    assert len(signal) == len(df)
    assert len(hist) == len(df)


def test_bollinger_bands():
    df = _make_ohlcv(250)
    upper, middle, lower = bollinger_bands(df["close"])
    assert len(upper) == len(df)
    # Upper should be above lower where both exist
    valid = upper.notna() & lower.notna()
    assert (upper[valid] >= lower[valid]).all()


def test_atr_positive():
    df = _make_ohlcv(250)
    atr_vals = atr(df["high"], df["low"], df["close"], 14)
    valid = atr_vals.dropna()
    assert (valid > 0).all()


def test_adx_range():
    df = _make_ohlcv(250)
    adx_vals = adx(df["high"], df["low"], df["close"], 14)
    valid = adx_vals.dropna()
    assert valid.min() >= 0
    assert valid.max() <= 100


def test_short_data_does_not_raise():
    """Indicators should return NaN for insufficient lookback, not crash."""
    df = _make_ohlcv(15)
    result = compute_all(df)
    assert "rsi_14" in result  # Should not raise


def test_volume_ratio_not_infinite():
    df = _make_ohlcv(250)
    ind = compute_all(df)
    vr = ind["volume_ratio"].dropna()
    assert vr.max() < 1000, f"Volume ratio too extreme: {vr.max()}"
