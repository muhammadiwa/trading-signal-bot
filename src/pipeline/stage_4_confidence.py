"""Signal generation, filtering, and persistence."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from src.strategies.base import StrategySignal

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """Final trading signal — ready for delivery."""

    id: str
    symbol: str
    action: str  # BUY | SELL | HOLD
    confidence: float  # 0.0 - 1.0
    entry_price: float
    stop_loss: float
    take_profit: Optional[float] = None
    strategy: str = ""
    timestamp_utc: str = ""
    status: str = "pending"

    # Research metadata (populated by Epic 2)
    sentiment_score: Optional[float] = None
    onchain_signal: Optional[str] = None
    macro_flag: bool = False
    research_metadata: Optional[str] = None  # JSON


def compute_confidence(
    strategy_signal: StrategySignal,
    backtest_result,  # BacktestResult
    atr_14: float,
    current_price: float,
    trigger_price: float,
) -> float:
    """Compute Technical Confidence from strategy signal and backtest.

    Formula: 0.7 × strategy_score + 0.3 × signal_strength
    - strategy_score = win_rate × profit_factor / max_possible (normalized)
    - signal_strength = 1 − |price − trigger| / (ATR × 2), clamped [0,1]
    """
    # Strategy score from backtest metrics
    max_possible = 1.0
    strategy_score = backtest_result.win_rate * backtest_result.profit_factor / max_possible
    strategy_score = min(1.0, strategy_score)

    # Signal strength: normalized distance from trigger threshold
    # Closer to trigger = stronger signal
    distance = abs(current_price - trigger_price)
    signal_strength = 1.0 - min(1.0, distance / (atr_14 * 2 + 1e-10))

    technical_confidence = 0.7 * strategy_score + 0.3 * signal_strength
    return round(min(1.0, max(0.0, technical_confidence)), 4)


def compute_sl_tp(
    action: str,
    symbol: str,
    entry_price: float,
    atr_14: float,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
) -> tuple[float, Optional[float]]:
    """Compute stop-loss and take-profit based on ATR.

    BUY:  SL = entry - ATR×sl_mult, TP = entry + ATR×tp_mult
    SELL: SL = entry + ATR×sl_mult, TP = entry - ATR×tp_mult

    Rounding: 2dp for USD pairs, 0dp for JPY/KRW, 4dp for small caps.
    """
    atr_val = max(atr_14, 1e-8)

    if action == "BUY":
        sl = entry_price - atr_val * sl_mult
        tp = entry_price + atr_val * tp_mult
    elif action == "SELL":
        sl = entry_price + atr_val * sl_mult
        tp = entry_price - atr_val * tp_mult
    else:  # HOLD
        return entry_price, None

    # Rounding by quote currency (detected from symbol suffix)
    upper = symbol.upper()
    if "JPY" in upper or "KRW" in upper:
        decimals = 0
    elif entry_price > 1000:
        decimals = 2
    elif entry_price > 1:
        decimals = 4
    else:
        decimals = 6

    return round(sl, decimals), round(tp, decimals)


def generate_signal(
    symbol: str,
    action: str,
    entry_price: float,
    atr_14: float,
    strategy_signal: StrategySignal,
    backtest_result,  # BacktestResult
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
) -> Signal:
    """Generate a structured Signal from strategy output and backtest."""
    confidence = compute_confidence(
        strategy_signal, backtest_result, atr_14,
        current_price=entry_price, trigger_price=entry_price,
    )
    sl, tp = compute_sl_tp(action, symbol, entry_price, atr_14, sl_mult, tp_mult)

    return Signal(
        id=str(uuid.uuid4()),
        symbol=symbol,
        action=action,
        confidence=confidence,
        entry_price=entry_price,
        stop_loss=sl,
        take_profit=tp,
        strategy=strategy_signal.metadata.get("strategy_name", backtest_result.strategy_name),
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        status="pending",
    )


def filter_signals(
    signals: list[Signal],
    min_confidence: float = 0.60,
    max_signals: int = 30,
    cooldown_hours: int = 24,
    cooldown_override: float = 0.80,
) -> list[Signal]:
    """Apply quality filters to a list of signals.

    1. Confidence threshold: keep only conf ≥ min_confidence
    2. Cap: keep top N by confidence
    3. Cooldown: skip if same symbol had signal within cooldown_hours
       (unless confidence ≥ cooldown_override)

    Args:
        signals: Raw signal list.
        min_confidence: Minimum confidence to pass.
        max_signals: Maximum signals per day.
        cooldown_hours: Minimum hours between same-symbol signals.
        cooldown_override: Confidence above which cooldown is ignored.

    Returns:
        Filtered and sorted signal list.
    """
    now = datetime.now(timezone.utc)

    # Step 1: Confidence filter
    passed = [s for s in signals if s.confidence >= min_confidence]
    dropped_conf = len(signals) - len(passed)
    if dropped_conf > 0:
        logger.info("Confidence filter: dropped %d signals (< %.0f%%)", dropped_conf, min_confidence * 100)

    # Step 2: Sort by confidence descending
    passed.sort(key=lambda s: s.confidence, reverse=True)

    # Step 3: Cap
    if len(passed) > max_signals:
        logger.warning("Signal cap reached — %d signals dropped (max %d)", len(passed) - max_signals, max_signals)
        passed = passed[:max_signals]

    # Step 4: Cooldown filter (check DB)
    try:
        from src.db import get_connection
        conn = get_connection()
        try:
            cooldown_passed = []
            for s in passed:
                cutoff = (now - pd.Timedelta(hours=cooldown_hours)).isoformat()
                row = conn.execute(
                    "SELECT id FROM signals WHERE symbol=? AND timestamp_utc > ? LIMIT 1",
                    (s.symbol, cutoff),
                ).fetchone()
                if row and s.confidence < cooldown_override:
                    logger.info("%s in cooldown — last signal < %dh ago (conf=%.0f%% < %.0f%%)",
                                s.symbol, cooldown_hours, s.confidence * 100, cooldown_override * 100)
                    continue
                cooldown_passed.append(s)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Cooldown check failed (DB unavailable?): %s — skipping cooldown filter", e)
        cooldown_passed = passed

    return cooldown_passed


def save_signals(signals: list[Signal]) -> int:
    """Persist signals to SQLite database.

    Args:
        signals: List of Signal objects to save.

    Returns:
        Number of signals saved.
    """
    if not signals:
        return 0

    from src.db import get_connection
    conn = get_connection()
    try:
        for s in signals:
            conn.execute(
                """INSERT OR REPLACE INTO signals
                   (id, symbol, action, confidence, entry_price, stop_loss,
                    take_profit, strategy, sentiment_score, onchain_signal,
                    macro_flag, research_metadata, timestamp_utc, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.id, s.symbol, s.action, s.confidence, s.entry_price,
                 s.stop_loss, s.take_profit, s.strategy, s.sentiment_score,
                 s.onchain_signal, s.macro_flag, s.research_metadata,
                 s.timestamp_utc, s.status),
            )
        conn.commit()
        logger.info("Saved %d signals to DB", len(signals))
    finally:
        conn.close()

    return len(signals)
