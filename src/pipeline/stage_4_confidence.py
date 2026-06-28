"""Signal generation, filtering, and persistence."""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from src.strategies.base import StrategySignal

logger = logging.getLogger(__name__)

# Correlation pairs — high-correlation assets where clustered signals are noise
_CORRELATED_PAIRS: list[tuple[str, str]] = [
    ("BTC", "WBTC"),  # Wrapped BTC = same asset
    ("ETH", "stETH"),  # Lido staked ETH = same asset
]


def _are_correlated(sym_a: str, sym_b: str) -> bool:
    """Check if two symbols are correlated (same underlying asset)."""
    base_a = sym_a.split("-")[0].upper()
    base_b = sym_b.split("-")[0].upper()
    for a, b in _CORRELATED_PAIRS:
        a_u, b_u = a.upper(), b.upper()
        if base_a == a_u and base_b == b_u:
            return True
        if base_a == b_u and base_b == a_u:
            return True
    return False


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
    timeframe: str = "1d"  # "1h" | "4h" | "1d"
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
    max_possible = 1.0
    strategy_score = backtest_result.win_rate * backtest_result.profit_factor / max_possible
    strategy_score = min(1.0, strategy_score)

    distance = abs(current_price - trigger_price)
    signal_strength = 1.0 - min(1.0, distance / (atr_14 * 2 + 1e-10))

    technical_confidence = 0.7 * strategy_score + 0.3 * signal_strength
    return round(min(1.0, max(0.0, technical_confidence)), 4)


def compute_sl_tp(
    action: str,
    symbol: str,
    entry_price: float,
    atr_14: float,
    atr_50: float = 0.0,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
) -> tuple[float, Optional[float]]:
    """Compute stop-loss and take-profit based on ATR.

    BUY:  SL = entry - ATR×sl_mult, TP = entry + ATR×tp_mult
    SELL: SL = entry + ATR×sl_mult, TP = entry - ATR×tp_mult

    Adaptive SL: if ATR(14) < 0.5 × ATR(50), widen SL to 2× ATR(14)×sl_mult
    (prevents tight stops during low-volatility regime — from brainstorm Sabotase #4).

    SL/TP clamped to minimum 0.01. Rounding per currency.
    """
    atr_val = max(atr_14, 1e-8)

    # Adaptive SL: widen when current-atr < 50% of longer-term atr
    effective_sl_mult = sl_mult
    if atr_50 > 1e-8 and atr_14 < 0.5 * atr_50:
        effective_sl_mult = sl_mult * 2.0  # Double SL width in low-vol regime
        logger.debug("Adaptive SL: atr14=%.2f < 0.5×atr50=%.2f → SL mult %.1f→%.1f",
                     atr_14, atr_50, sl_mult, effective_sl_mult)

    if action == "BUY":
        sl = entry_price - atr_val * effective_sl_mult
        tp = entry_price + atr_val * tp_mult
    elif action == "SELL":
        sl = entry_price + atr_val * effective_sl_mult
        tp = entry_price - atr_val * tp_mult
    else:
        return entry_price, None

    sl = max(sl, 0.01)
    if tp is not None:
        tp = max(tp, 0.01)

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
    timeframe: str = "1d",
    atr_50: float = 0.0,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
) -> Signal:
    """Generate a structured Signal from strategy output and backtest."""
    trigger = strategy_signal.trigger_price if strategy_signal.trigger_price else entry_price
    confidence = compute_confidence(
        strategy_signal, backtest_result, atr_14,
        current_price=entry_price, trigger_price=trigger,
    )
    sl, tp = compute_sl_tp(action, symbol, entry_price, atr_14, atr_50, sl_mult, tp_mult)

    return Signal(
        id=str(uuid.uuid4()),
        symbol=symbol,
        action=action,
        confidence=confidence,
        entry_price=entry_price,
        stop_loss=sl,
        take_profit=tp,
        strategy=strategy_signal.metadata.get("strategy_name", backtest_result.strategy_name),
        timeframe=timeframe,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        status="pending",
    )


def filter_signals(
    signals: list[Signal],
    min_confidence: float = 0.60,
    max_signals: int = 30,
    cooldown_hours: int = 24,
    cooldown_override: float = 0.80,
    break_glass_pct: float = 0.05,
) -> list[Signal]:
    """Apply quality filters to signals.

    1. Confidence threshold: conf ≥ min_confidence
    2. Cap: top N by confidence
    3. Cooldown: skip if same symbol within cooldown_hours (override at ≥cooldown_override)
    4. Break-glass: override cooldown if price change > break_glass_pct (crash signals)
    5. Correlation: drop lower-confidence signal from correlated pairs

    Returns filtered + sorted signal list.
    """
    now = datetime.now(timezone.utc)

    # Step 1: Confidence filter
    passed = [s for s in signals if s.confidence >= min_confidence]
    dropped_conf = len(signals) - len(passed)
    if dropped_conf > 0:
        logger.info("Confidence filter: dropped %d signals (< %.0f%%)", dropped_conf, min_confidence * 100)

    # Step 2: Sort by confidence descending
    passed.sort(key=lambda s: s.confidence, reverse=True)

    # Step 5: Correlation filter (before cap, so correlated low-conf pairs don't steal slots)
    if len(passed) > 1:
        correlated_passed = []
        for s in passed:
            # Check if this signal is correlated with an already-kept signal
            conflict = next((c for c in correlated_passed
                           if _are_correlated(s.symbol, c.symbol)), None)
            if conflict:
                # Keep higher confidence one
                logger.info("Correlation filter: dropping %s (conf=%.0f%%) — correlated with %s (conf=%.0f%%)",
                            s.symbol, s.confidence * 100, conflict.symbol, conflict.confidence * 100)
                continue
            correlated_passed.append(s)
        passed = correlated_passed

    # Step 3: Cap
    if len(passed) > max_signals:
        logger.warning("Signal cap reached — %d signals dropped (max %d)", len(passed) - max_signals, max_signals)
        passed = passed[:max_signals]

    # Step 4: Cooldown filter + break-glass override
    conn = None
    try:
        from src.db import get_connection
        conn = get_connection()
        try:
            cooldown_passed = []
            for s in passed:
                cutoff = (now - pd.Timedelta(hours=cooldown_hours)).isoformat()
                row = conn.execute(
                    "SELECT id, entry_price FROM signals WHERE symbol=? AND timestamp_utc > ? LIMIT 1",
                    (s.symbol, cutoff),
                ).fetchone()

                if row and s.confidence < cooldown_override:
                    # Break-glass: override cooldown if price shifted > break_glass_pct since last signal
                    last_entry = row["entry_price"]
                    price_change = abs(s.entry_price - last_entry) / max(last_entry, 1e-10)
                    if price_change > break_glass_pct:
                        logger.info(
                            "%s: break-glass — cooldown overridden (price change %.1f%% > %.1f%%)",
                            s.symbol, price_change * 100, break_glass_pct * 100,
                        )
                        cooldown_passed.append(s)
                        continue

                    logger.info("%s in cooldown — last signal < %dh ago (conf=%.0f%% < %.0f%%)",
                                s.symbol, cooldown_hours, s.confidence * 100, cooldown_override * 100)
                    continue
                cooldown_passed.append(s)
        finally:
            if conn is not None:
                conn.close()
    except Exception as e:
        logger.warning("Cooldown check failed (DB unavailable?): %s — skipping cooldown filter", e)
        cooldown_passed = passed

    return cooldown_passed


def save_signals(signals: list[Signal]) -> int:
    """Persist signals to SQLite database."""
    if not signals:
        return 0

    from src.db import get_connection
    conn = None
    try:
        conn = get_connection()
        for s in signals:
            conn.execute(
                """INSERT OR REPLACE INTO signals
                   (id, symbol, action, confidence, entry_price, stop_loss,
                    take_profit, strategy, timeframe, sentiment_score, onchain_signal,
                    macro_flag, research_metadata, timestamp_utc, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.id, s.symbol, s.action, s.confidence, s.entry_price,
                 s.stop_loss, s.take_profit, s.strategy, s.timeframe,
                 s.sentiment_score, s.onchain_signal, s.macro_flag,
                 s.research_metadata, s.timestamp_utc, s.status),
            )
        conn.commit()
        logger.info("Saved %d signals to DB", len(signals))
    finally:
        if conn is not None:
            conn.close()

    return len(signals)


def compute_counter_metrics(signals: list[Signal]) -> dict:
    """Compute success + counter-metrics from today's signal batch.

    Checks specified in PRD Success Metrics + Counter-Metrics sections:
    - Signal clustering: >50% same action → warn
    - False confidence: >70% conf but too many on same symbol
    """
    result: dict = {"signal_count": len(signals), "warnings": []}

    if not signals:
        return result

    # Signal clustering check
    buy_count = sum(1 for s in signals if s.action == "BUY")
    sell_count = sum(1 for s in signals if s.action == "SELL")
    total = buy_count + sell_count
    if total > 0:
        buy_pct = buy_count / total * 100
        sell_pct = sell_count / total * 100
        if buy_pct > 50:
            result["warnings"].append(f"Signal clustering: {buy_pct:.0f}% BUY (threshold 50%)")
        if sell_pct > 50:
            result["warnings"].append(f"Signal clustering: {sell_pct:.0f}% SELL (threshold 50%)")

    # High-confidence count
    high_conf = [s for s in signals if s.confidence >= 0.70]
    result["high_confidence_count"] = len(high_conf)

    # Confidence distribution
    if signals:
        result["avg_confidence"] = round(sum(s.confidence for s in signals) / len(signals), 4)
        result["max_confidence"] = max(s.confidence for s in signals)
        result["min_confidence"] = min(s.confidence for s in signals)

    return result
