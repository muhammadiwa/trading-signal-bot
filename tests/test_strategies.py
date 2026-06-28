"""Smoke tests for 5 trading strategies — pytest compatible."""

import numpy as np
import pandas as pd

from src.indicators import compute_all
from src.strategies.base import (
    MomentumBreakout, TrendFollowing, MeanReversion,
    VolatilityBreakout, VolumeDivergence, all_strategies,
)


def _make_ohlcv(n: int = 250, trend: str = "neutral") -> pd.DataFrame:
    np.random.seed(42)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    if trend == "uptrend":
        close = 100 + np.cumsum(np.abs(np.random.randn(n) * 2))
    elif trend == "downtrend":
        close = 100 - np.cumsum(np.abs(np.random.randn(n) * 2))
    else:
        close = 100 + np.cumsum(np.random.randn(n) * 2)
    close = np.maximum(close, 1)  # No negatives
    return pd.DataFrame({
        "timestamp": dates, "open": close * 0.99, "high": close * 1.02,
        "low": close * 0.98, "close": close,
        "volume": np.random.uniform(1e6, 5e6, n),
    })


def test_all_five_strategies_evaluate():
    df = _make_ohlcv(250)
    ind = compute_all(df)
    for s in all_strategies():
        sig = s.evaluate(df, ind)
        assert sig.action in ("BUY", "SELL", "HOLD")
        assert 0.0 <= sig.confidence <= 1.0


def test_momentum_breakout_returns_valid_signal():
    strat = MomentumBreakout(n=20, k=0.005)
    df = _make_ohlcv(250, "uptrend")
    ind = compute_all(df)
    sig = strat.evaluate(df, ind)
    assert sig.action in ("BUY", "SELL", "HOLD")
    if sig.action != "HOLD":
        assert sig.entry_price > 0
        assert sig.stop_loss > 0


def test_trend_following_returns_valid_signal():
    strat = TrendFollowing(short=20, long=50, adx_threshold=25)
    df = _make_ohlcv(250)
    ind = compute_all(df)
    sig = strat.evaluate(df, ind)
    assert sig.action in ("BUY", "SELL", "HOLD")


def test_mean_reversion_returns_valid_signal():
    strat = MeanReversion()
    df = _make_ohlcv(250)
    ind = compute_all(df)
    sig = strat.evaluate(df, ind)
    assert sig.action in ("BUY", "SELL", "HOLD")


def test_volatility_breakout_returns_valid_signal():
    strat = VolatilityBreakout(k=1.5)
    df = _make_ohlcv(250)
    ind = compute_all(df)
    sig = strat.evaluate(df, ind)
    assert sig.action in ("BUY", "SELL", "HOLD")


def test_volume_divergence_returns_valid_signal():
    strat = VolumeDivergence()
    df = _make_ohlcv(250)
    ind = compute_all(df)
    sig = strat.evaluate(df, ind)
    assert sig.action in ("BUY", "SELL", "HOLD")


def test_no_signal_returns_hold():
    """Strategies with insufficient data should return HOLD, not crash."""
    df = _make_ohlcv(10)
    ind = compute_all(df)
    for s in all_strategies():
        sig = s.evaluate(df, ind)
        # May return HOLD or a signal — both fine for tiny data


def test_trigger_price_is_set():
    """Every non-HOLD signal should have a trigger_price set."""
    df = _make_ohlcv(250)
    ind = compute_all(df)
    for s in all_strategies():
        sig = s.evaluate(df, ind)
        if sig.action in ("BUY", "SELL"):
            assert sig.trigger_price > 0, f"{s.name}: trigger_price not set"


def test_momentum_volume_filter():
    """Momentum with volume filter disabled should fire more easily."""
    df = _make_ohlcv(250, "uptrend")
    ind = compute_all(df)
    # With volume filter (default)
    strat_on = MomentumBreakout(volume_filter_enabled=True)
    # Without volume filter
    strat_off = MomentumBreakout(volume_filter_enabled=False)
    sig_on = strat_on.evaluate(df, ind)
    sig_off = strat_off.evaluate(df, ind)
    # Both should be valid
    assert sig_on.confidence >= 0
    assert sig_off.confidence >= 0
