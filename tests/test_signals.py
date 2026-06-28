"""Tests for signal generation, filtering, and metrics — pytest compatible."""

import numpy as np
import pandas as pd

from src.indicators import compute_all
from src.strategies.base import MomentumBreakout
from src.backtest import BacktestResult
from src.pipeline.stage_4_confidence import (
    Signal, generate_signal, compute_sl_tp, compute_confidence,
    filter_signals, compute_counter_metrics, _are_correlated,
)


def _make_ohlcv(n: int = 250) -> pd.DataFrame:
    np.random.seed(42)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 2)
    return pd.DataFrame({
        "timestamp": dates, "open": close * 0.99, "high": close * 1.02,
        "low": close * 0.98, "close": close,
        "volume": np.random.uniform(1e6, 5e6, n),
    })


def _make_backtest() -> BacktestResult:
    return BacktestResult("Test", 0.55, 0.8, -0.15, 1.5, 20, 0.12, True)


def _make_sig(action="BUY", conf=0.75, sym="BTC-USDT", tf="1d",
              entry=60000.0, sl=59000.0, tp=61800.0) -> Signal:
    return Signal("id", sym, action, conf, entry, sl, tp, "Test", tf)


# ── SL/TP ────────────────────────────────────────────────

def test_sl_tp_buy():
    sl, tp = compute_sl_tp("BUY", "BTC-USDT", 60200, atr_14=578)
    assert sl < 60200
    assert tp > 60200


def test_sl_tp_sell():
    sl, tp = compute_sl_tp("SELL", "BTC-USDT", 60200, atr_14=578)
    assert sl > 60200
    assert tp < 60200


def test_sl_tp_negative_guard():
    sl, tp = compute_sl_tp("BUY", "BTC-USDT", 50, atr_14=40)
    assert sl > 0
    assert tp > 0


def test_adaptive_sl_widening():
    """Low-vol regime should widen SL multiplier (2x when atr14 < 0.5×atr50)."""
    sl_widened, _ = compute_sl_tp("BUY", "BTC-USDT", 60000, atr_14=300, atr_50=1000, sl_mult=1.5)
    # Without adaptation (same atr, no atr50 context): sl = 60000 - 300*1.5 = 59550
    # With adaptation: sl = 60000 - 300*1.5*2 = 59100  (wider!)
    expected_normal = 60000 - 300 * 1.5   # 59550
    assert sl_widened < expected_normal, f"Adaptive SL {sl_widened} should be lower than normal {expected_normal}"


# ── Signal Generation ─────────────────────────────────────

def test_generate_signal_timeframe():
    df = _make_ohlcv(250)
    ind = compute_all(df)
    sig = MomentumBreakout().evaluate(df, ind)
    br = _make_backtest()
    signal = generate_signal("BTC-USDT", "BUY", 60000, 600, sig, br, timeframe="4h")
    assert signal.timeframe == "4h"
    assert signal.id  # UUID


# ── Counter-Metrics ───────────────────────────────────────

def test_clustering_warning():
    """>50% BUY should trigger clustering warning."""
    signals = [
        _make_sig("BUY"), _make_sig("BUY", sym="ETH-USDT"),
        _make_sig("BUY", sym="SOL-USDT"), _make_sig("SELL", sym="AVAX-USDT"),
    ]
    m = compute_counter_metrics(signals)
    assert any("clustering" in w.lower() for w in m["warnings"])


def test_counter_metrics_no_signals():
    m = compute_counter_metrics([])
    assert m["signal_count"] == 0


# ── Correlation ───────────────────────────────────────────

def test_btc_wbtc_correlated():
    assert _are_correlated("BTC-USDT", "WBTC-USDT")


def test_eth_steth_correlated():
    assert _are_correlated("ETH-USDT", "stETH-USDT")


def test_btc_eth_not_correlated():
    assert not _are_correlated("BTC-USDT", "ETH-USDT")
