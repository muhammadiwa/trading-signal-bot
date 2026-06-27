"""Strategy-pair matching — profile → best strategy → backtest validation."""

import logging
from typing import Optional

import pandas as pd

from src.profile import PairProfile, compute as compute_profile
from src.backtest import run as run_backtest, BacktestResult
from src.strategies.base import (
    MomentumBreakout, TrendFollowing, MeanReversion,
    VolatilityBreakout, VolumeDivergence, StrategySignal,
)

logger = logging.getLogger(__name__)

# All strategy constructors (without params — use defaults)
_ALL_STRATEGIES = [
    TrendFollowing,
    MomentumBreakout,
    MeanReversion,
    VolatilityBreakout,
    VolumeDivergence,
]


def match_strategy(profile: PairProfile) -> list:
    """Return ordered list of strategy instances best-matched to profile.

    Returns strategies in priority order. First match might fail backtest,
    so caller tries them in sequence.

    Args:
        profile: PairProfile with 4-dimension scores.

    Returns:
        List of strategy instances, best-matched first.
    """
    ranked = []

    # Rule 1: Strong trend + good volume → Trend Following
    if profile.trendiness > 60 and profile.volume_quality > 50:
        ranked.append(TrendFollowing())

    # Rule 2: High mean-reversion + low trend → Mean Reversion
    if profile.mean_reversion > 60 and profile.trendiness < 40:
        ranked.append(MeanReversion())

    # Rule 3: High volatility + high volume → Momentum Breakout
    if profile.volatility > 60 and profile.volume_quality > 50:
        ranked.append(MomentumBreakout())

    # Rule 4: High volatility + low volume → Volatility Breakout
    if profile.volatility > 60 and profile.volume_quality < 40:
        ranked.append(VolatilityBreakout())

    # Add remaining strategies (not yet matched) in default order
    for cls in _ALL_STRATEGIES:
        inst = cls()
        if not any(type(s) is type(inst) for s in ranked):
            ranked.append(inst)

    # If nothing matched (unlikely), use all
    if not ranked:
        ranked = [cls() for cls in _ALL_STRATEGIES]

    # VolumeDivergence always last (weakest standalone)
    ranked.sort(key=lambda s: 1 if isinstance(s, VolumeDivergence) else 0)

    return ranked


def find_best_strategy(
    ohlcv: pd.DataFrame,
    indicators: dict[str, pd.Series],
    symbol: str = "unknown",
    min_win_rate: float = 0.40,
    min_sharpe: float = 0.5,
) -> tuple[Optional[object], Optional[BacktestResult], list[BacktestResult]]:
    """Find the best strategy for a pair through profile matching + backtest.

    1. Compute 4D profile
    2. Match profile → candidate strategies
    3. Backtest each candidate in priority order
    4. Return first that passes gates (win_rate ≥ min, sharpe ≥ min)

    Args:
        ohlcv: Full OHLCV DataFrame.
        indicators: Pre-computed indicators.
        symbol: Trading pair symbol.
        min_win_rate: Minimum win rate gate (default 0.40).
        min_sharpe: Minimum Sharpe ratio gate (default 0.5).

    Returns:
        Tuple of (best_strategy | None, best_result | None, all_results).
        If no strategy passes, best_strategy is None.
    """
    profile = compute_profile(ohlcv, indicators, symbol)
    logger.info(
        "%s profile: trend=%.0f vol=%.0f mr=%.0f vq=%.0f",
        symbol, profile.trendiness, profile.volatility,
        profile.mean_reversion, profile.volume_quality,
    )

    candidates = match_strategy(profile)
    all_results = []
    best_strategy = None
    best_result = None

    for strategy in candidates:
        result = run_backtest(strategy, ohlcv, indicators)
        all_results.append(result)

        logger.info(
            "%s with %s: win=%.1f%% sharpe=%.2f trades=%d %s",
            symbol, result.strategy_name,
            result.win_rate * 100, result.sharpe_ratio,
            result.total_trades,
            "✅ PASS" if result.passed else "❌ FAIL",
        )

        if result.passed and best_strategy is None:
            best_strategy = strategy
            best_result = result

    if best_strategy is None:
        logger.warning(
            "%s: no strategy passed backtest (min win=%.0f%%, sharpe=%.1f) — dropping pair",
            symbol, min_win_rate * 100, min_sharpe,
        )

    return best_strategy, best_result, all_results
