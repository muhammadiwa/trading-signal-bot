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

    Unclear profiles (no rule matched) → ensemble (all 5 strategies vote).

    Args:
        profile: PairProfile with 4-dimension scores.

    Returns:
        List of strategy instances, best-matched first.
    """
    matched = []

    # Rule 1: Strong trend + good volume → Trend Following
    if profile.trendiness > 60 and profile.volume_quality > 50:
        matched.append(TrendFollowing())

    # Rule 2: High mean-reversion + low trend → Mean Reversion
    if profile.mean_reversion > 60 and profile.trendiness < 40:
        matched.append(MeanReversion())

    # Rule 3: High volatility + high volume → Momentum Breakout
    if profile.volatility > 60 and profile.volume_quality > 50:
        matched.append(MomentumBreakout())

    # Rule 4: High volatility + low volume → Volatility Breakout
    if profile.volatility > 60 and profile.volume_quality < 40:
        matched.append(VolatilityBreakout())

    # Ensemble fallback: no rule matched → unclear profile, vote all 5
    if not matched:
        logger.info(
            "Unclear profile for %s (trend=%.0f vol=%.0f mr=%.0f vq=%.0f) — using ensemble",
            profile.symbol, profile.trendiness, profile.volatility,
            profile.mean_reversion, profile.volume_quality,
        )
        return [cls() for cls in _ALL_STRATEGIES]

    # Add remaining strategies as fallbacks after matched ones
    for cls in _ALL_STRATEGIES:
        inst = cls()
        if not any(type(s) is type(inst) for s in matched):
            matched.append(inst)

    # VolumeDivergence always last (weakest standalone)
    matched.sort(key=lambda s: 1 if isinstance(s, VolumeDivergence) else 0)

    return matched


def find_best_strategy(
    ohlcv: pd.DataFrame,
    indicators: dict[str, pd.Series],
    symbol: str = "unknown",
    min_win_rate: float = 0.40,
    min_sharpe: float = 0.5,
    walk_forward_enabled: bool = False,
) -> tuple[Optional[object], Optional[BacktestResult], list[BacktestResult]]:
    """Find the best strategy for a pair through profile matching + backtest.

    1. Compute 4D profile
    2. Match profile → candidate strategies
    3. Backtest each candidate in priority order
    4. Return first that passes gates (win_rate ≥ min, sharpe ≥ min)

    Walk-forward (80/20 split) is used when config flag is enabled AND
    data has > 12 months (~365 bars). Otherwise full backtest.

    Args:
        ohlcv: Full OHLCV DataFrame.
        indicators: Pre-computed indicators.
        symbol: Trading pair symbol.
        min_win_rate: Minimum win rate gate (default 0.40).
        min_sharpe: Minimum Sharpe ratio gate (default 0.5).
        walk_forward_enabled: Whether to use walk-forward validation.

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
        use_walk_forward = walk_forward_enabled and len(ohlcv) > 365
        train_ratio = 0.8 if use_walk_forward else 1.0
        result = run_backtest(strategy, ohlcv, indicators, train_ratio=train_ratio)
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
