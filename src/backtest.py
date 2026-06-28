"""Backtest engine — validates strategy performance on historical data."""

import logging
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Performance metrics from a backtest run."""

    strategy_name: str
    win_rate: float  # 0.0 - 1.0
    sharpe_ratio: float
    max_drawdown: float  # 0.0 - 1.0 (as negative decimal)
    profit_factor: float
    total_trades: int
    total_return: float  # 0.0 - 1.0
    passed: bool = False  # Did it meet minimum gates?
    trade_log: list = None  # Individual trade records for display

    def __post_init__(self):
        if self.trade_log is None:
            self.trade_log = []


class StrategyLike(Protocol):
    """Minimal protocol for any strategy that can be backtested."""
    name: str

    def evaluate(
        self, ohlcv: pd.DataFrame, indicators: dict[str, pd.Series]
    ) -> "StrategySignal": ...  # noqa: F821


def _compute_returns(signals: list[dict]) -> list[float]:
    """Compute per-trade returns from signal list.

    Each signal dict: {"action": "BUY"|"SELL", "entry": price, "exit": price}
    Returns list of decimal returns (e.g., 0.02 = +2%).
    """
    returns = []
    for sig in signals:
        if sig["action"] == "BUY":
            ret = (sig["exit"] - sig["entry"]) / sig["entry"]
        else:  # SELL
            ret = (sig["entry"] - sig["exit"]) / sig["entry"]
        returns.append(ret)
    return returns


def run(
    strategy,
    ohlcv: pd.DataFrame,
    indicators: dict[str, pd.Series],
    train_ratio: float = 1.0,
) -> BacktestResult:
    """Run backtest for a strategy on historical data.

    Walks forward through the data, generating signals at each bar
    and tracking hypothetical outcomes.

    Args:
        strategy: Strategy instance with evaluate() method.
        ohlcv: Full OHLCV DataFrame.
        indicators: Pre-computed indicators dict.
        train_ratio: Fraction of data for training (1.0 = full backtest).
                     If < 1.0, walk-forward: train on first portion,
                     test on remainder.

    Returns:
        BacktestResult with metrics.
    """
    if len(ohlcv) < 50:
        return BacktestResult(
            strategy_name=strategy.name,
            win_rate=0.0, sharpe_ratio=0.0, max_drawdown=0.0,
            profit_factor=0.0, total_trades=0, total_return=0.0, passed=False,
        )

    # For full backtest or insufficient data, use entire dataset
    if train_ratio >= 1.0 or len(ohlcv) <= 50:
        test_ohlcv = ohlcv.copy()
        use_walk_forward = False
    else:
        split_idx = int(len(ohlcv) * train_ratio)
        test_ohlcv = ohlcv.iloc[split_idx:].copy()
        use_walk_forward = True

    # Walk-forward simulation — recompute indicators per window (no look-ahead)
    from src.indicators import compute_all as compute_indicators

    trades = []
    lookback = min(50, max(20, len(test_ohlcv) // 4))
    entry_price = None
    entry_action = None
    entry_sl = None
    entry_tp = None
    holding = False

    for i in range(lookback, len(test_ohlcv)):
        window_ohlcv = test_ohlcv.iloc[:i + 1]
        # Recompute indicators on window ONLY (prevents look-ahead bias)
        try:
            window_indicators = compute_indicators(window_ohlcv)
        except Exception:
            continue

        try:
            signal = strategy.evaluate(window_ohlcv, window_indicators)
        except Exception as e:
            logger.debug("Strategy %s failed at bar %d: %s", strategy.name, i, e)
            continue

        current_price = test_ohlcv["close"].iloc[i]

        if not holding and signal.action != "HOLD" and signal.confidence > 0:
            entry_price = current_price
            entry_action = signal.action
            entry_sl = signal.stop_loss  # Store ENTRY signal's SL/TP
            entry_tp = signal.take_profit
            holding = True

        elif holding and i == len(test_ohlcv) - 1:
            trades.append({
                "action": entry_action, "entry": entry_price, "exit": current_price,
            })
            holding = False

        elif holding:
            exit_signal = False
            exit_price_val = current_price

            # Use ENTRY SL/TP, not current bar's signal
            if entry_action == "BUY":
                if signal.action == "SELL":
                    exit_signal = True
                elif entry_sl and current_price <= entry_sl:
                    exit_signal = True
                elif entry_tp and current_price >= entry_tp:
                    exit_signal = True
            else:  # SELL
                if signal.action == "BUY":
                    exit_signal = True
                elif entry_sl and current_price >= entry_sl:
                    exit_signal = True
                elif entry_tp and current_price <= entry_tp:
                    exit_signal = True

            if exit_signal:
                trades.append({
                    "action": entry_action, "entry": entry_price, "exit": exit_price_val,
                })
                holding = False

    # Compute metrics
    if not trades:
        return BacktestResult(
            strategy_name=strategy.name,
            win_rate=0.0, sharpe_ratio=0.0, max_drawdown=0.0,
            profit_factor=0.0, total_trades=0, total_return=0.0, passed=False,
        )

    returns = _compute_returns(trades)
    wins = sum(1 for r in returns if r > 0)
    total = len(returns)
    win_rate = wins / total if total > 0 else 0.0

    # Sharpe ratio (annualized, assuming daily data)
    if len(returns) > 1:
        mean_ret = np.mean(returns)
        std_ret = np.std(returns, ddof=1)
        sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown
    cumulative = np.cumprod([1 + r for r in returns])
    peak = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - peak) / peak
    max_dd = float(drawdown.min()) if len(drawdown) > 0 else 0.0

    # Profit factor
    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (1.0 if gross_profit > 0 else 0.0)

    total_return = float(cumulative[-1] - 1) if len(cumulative) > 0 else 0.0

    # Gate check: win_rate >= 40% AND sharpe >= 0.5
    passed = win_rate >= 0.40 and sharpe >= 0.5

    # Build trade log with return per trade
    trade_log = []
    for t in trades:
        r = (t["exit"] - t["entry"]) / t["entry"] if t["action"] == "BUY" else (t["entry"] - t["exit"]) / t["entry"]
        trade_log.append({
            "action": t["action"], "entry": round(t["entry"], 2),
            "exit": round(t["exit"], 2), "return_pct": round(r * 100, 2),
            "win": r > 0,
        })

    return BacktestResult(
        strategy_name=strategy.name,
        win_rate=round(win_rate, 4),
        sharpe_ratio=round(sharpe, 4),
        max_drawdown=round(max_dd, 4),
        profit_factor=round(profit_factor, 4),
        total_trades=total,
        total_return=round(total_return, 4),
        passed=passed,
        trade_log=trade_log,
    )
