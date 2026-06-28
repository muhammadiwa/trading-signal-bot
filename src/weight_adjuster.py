"""Adaptive weight adjustment — Story 3.3 (FR6.3).

Self-improving accuracy: adjusts research multiplier weights based on
historical per-source accuracy using Exponential Moving Average.
Weights persist in SQLite across pipeline runs.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from src.db import get_connection

logger = logging.getLogger(__name__)

# Default weight identifiers in weights table
WEIGHT_IDS = ["sentiment_weight", "onchain_weight", "macro_weight", "prediction_weight"]
DEFAULT_WEIGHT = 1.0
WEIGHT_FLOOR = 0.5
WEIGHT_CEIL = 1.5
MIN_OUTCOMES = 30
EMA_ALPHA = 0.2  # Weight for new accuracy in EMA
BASELINE = 0.5  # Random chance baseline
UNDERPERFORM_THRESHOLD = 0.40
UNDERPERFORM_MIN_OUTCOMES = 50


def _clamp_weight(w: float) -> float:
    """Clamp weight to [0.5, 1.5]."""
    return max(WEIGHT_FLOOR, min(WEIGHT_CEIL, w))


def _ema_update(old_weight: float, accuracy: float) -> float:
    """EMA: new_weight = 0.8 × old + 0.2 × (accuracy / 0.5)."""
    return 0.8 * old_weight + 0.2 * (accuracy / BASELINE)


def load_weights(conn) -> dict[str, float]:
    """Load weights from SQLite, initializing defaults if empty."""
    rows = conn.execute(
        "SELECT weight_id, value FROM weights"
    ).fetchall()

    weights = {}
    for row in rows:
        weights[row["weight_id"]] = row["value"]

    if not weights:
        now = datetime.now(timezone.utc).isoformat()
        for wid in WEIGHT_IDS:
            conn.execute(
                "INSERT OR IGNORE INTO weights (weight_id, value, updated_at) VALUES (?, ?, ?)",
                (wid, DEFAULT_WEIGHT, now),
            )
            weights[wid] = DEFAULT_WEIGHT
        conn.commit()
        logger.info("Weights initialized to defaults in SQLite")

    return weights


def _save_weights(conn, weights: dict[str, float]) -> None:
    """Persist weights to SQLite."""
    now = datetime.now(timezone.utc).isoformat()
    for wid, val in weights.items():
        conn.execute(
            "INSERT OR REPLACE INTO weights (weight_id, value, updated_at) VALUES (?, ?, ?)",
            (wid, val, now),
        )
    conn.commit()


def _compute_sentiment_accuracy(conn) -> float:
    """Compute how often sentiment direction matches outcome direction.

    Sentiment <40 → bearish (SELL expected), >60 → bullish (BUY expected).
    Accuracy: fraction where sentiment-predicted direction matches realized outcome win.
    """
    rows = conn.execute(
        """SELECT s.action, s.sentiment_score, o.realized_return_pct
           FROM signals s JOIN outcomes o ON s.id = o.signal_id
           WHERE o.realized_return_pct IS NOT NULL
           ORDER BY o.resolved_at DESC LIMIT ?""",
        (MIN_OUTCOMES,),
    ).fetchall()

    correct = 0
    total = 0
    for row in rows:
        score = row["sentiment_score"]
        if score is None:
            continue
        if 40 <= score <= 60:
            continue  # Neutral — skip

        predicted_bullish = score > 60
        is_win = row["realized_return_pct"] > 0
        action = row["action"]

        if predicted_bullish and action == "BUY":
            correct += 1 if is_win else 0
        elif not predicted_bullish and action == "SELL":
            correct += 1 if is_win else 0
        else:
            # Opposite direction: correct if loss
            correct += 1 if not is_win else 0

        total += 1

    return correct / total if total > 0 else 0.5


def _compute_onchain_accuracy(conn) -> float:
    """Accuracy of on-chain signal direction vs outcome."""
    rows = conn.execute(
        """SELECT s.onchain_signal, o.realized_return_pct
           FROM signals s JOIN outcomes o ON s.id = o.signal_id
           WHERE o.realized_return_pct IS NOT NULL
             AND s.onchain_signal IS NOT NULL AND s.onchain_signal != ''
           ORDER BY o.resolved_at DESC LIMIT ?""",
        (MIN_OUTCOMES,),
    ).fetchall()

    correct = 0
    for row in rows:
        signal = row["onchain_signal"].lower()
        is_win = row["realized_return_pct"] > 0

        if signal == "bullish" and is_win:
            correct += 1
        elif signal == "bearish" and not is_win:
            correct += 1
        elif signal == "neutral":
            pass

    return correct / len(rows) if len(rows) > 0 else 0.5


def _compute_macro_accuracy(conn) -> float:
    """Accuracy of macro flag: macro=true predicted loss (confidence reduction)."""
    rows = conn.execute(
        """SELECT s.macro_flag, o.realized_return_pct
           FROM signals s JOIN outcomes o ON s.id = o.signal_id
           WHERE o.realized_return_pct IS NOT NULL AND s.macro_flag = 1
           ORDER BY o.resolved_at DESC LIMIT ?""",
        (MIN_OUTCOMES,),
    ).fetchall()

    if len(rows) < 10:
        return 0.5  # Not enough macro-tagged outcomes

    correct = sum(1 for r in rows if r["realized_return_pct"] <= 0)
    return correct / len(rows)


def _compute_prediction_accuracy(conn) -> float:
    """Accuracy of prediction market adjustment vs outcome direction."""
    rows = conn.execute(
        """SELECT s.research_metadata, o.realized_return_pct
           FROM signals s JOIN outcomes o ON s.id = o.signal_id
           WHERE o.realized_return_pct IS NOT NULL
             AND s.research_metadata IS NOT NULL AND s.research_metadata != ''
           ORDER BY o.resolved_at DESC LIMIT ?""",
        (MIN_OUTCOMES,),
    ).fetchall()

    correct = 0
    total = 0
    for row in rows:
        try:
            meta = json.loads(row["research_metadata"])
        except (json.JSONDecodeError, TypeError):
            continue
        pred = meta.get("prediction_adjustment", 0)
        if pred == 0:
            continue
        is_win = row["realized_return_pct"] > 0
        # pred > 0 = bullish adjustment → predict win
        # pred < 0 = bearish adjustment → predict loss
        if (pred > 0 and is_win) or (pred < 0 and not is_win):
            correct += 1
        total += 1

    return correct / total if total > 0 else 0.5


def adjust_weights(send_alert_fn=None) -> Optional[dict[str, float]]:
    """Compute per-source accuracy and adjust weights via EMA (AC1-AC5).

    Reads last 30 outcomes, computes accuracy per research source,
    applies EMA update, clamps weights, persists to SQLite.
    Sends Telegram alert if source underperforms (AC4).

    Args:
        send_alert_fn: Optional alert callback (for AC4 underperformance).

    Returns:
        Updated weights dict, or None if insufficient data.
    """
    conn = get_connection()
    try:
        # Check total outcomes for minimum data gate (AC3)
        total = conn.execute("SELECT COUNT(*) AS n FROM outcomes WHERE realized_return_pct IS NOT NULL").fetchone()
        if total["n"] < MIN_OUTCOMES:
            logger.info("Insufficient data for weight adjustment (%d/%d outcomes)", total["n"], MIN_OUTCOMES)
            return None

        # Load current weights (AC5: init defaults if empty)
        weights = load_weights(conn)

        # Compute per-source accuracy (AC1)
        accuracies = {
            "sentiment_weight": _compute_sentiment_accuracy(conn),
            "onchain_weight": _compute_onchain_accuracy(conn),
            "macro_weight": _compute_macro_accuracy(conn),
            "prediction_weight": _compute_prediction_accuracy(conn),
        }

        # EMA update + clamp (AC2)
        updated = {}
        for wid in WEIGHT_IDS:
            old_w = weights.get(wid, DEFAULT_WEIGHT)
            acc = accuracies.get(wid, 0.5)
            new_raw = _ema_update(old_w, acc)
            new_w = _clamp_weight(new_raw)
            updated[wid] = round(new_w, 4)

        _save_weights(conn, updated)

        # Underperformance detection (AC4)
        if total["n"] >= UNDERPERFORM_MIN_OUTCOMES:
            for wid in WEIGHT_IDS:
                acc = accuracies.get(wid, 0.5)
                new_w = updated.get(wid, DEFAULT_WEIGHT)
                if acc < UNDERPERFORM_THRESHOLD and new_w <= WEIGHT_FLOOR:
                    source_name = wid.replace("_weight", "")
                    msg = f"⚠️ {source_name} accuracy {acc*100:.0f}% — weight reduced to {WEIGHT_FLOOR}. Review recommended."
                    logger.warning(msg)
                    if send_alert_fn:
                        send_alert_fn(msg)

        logger.info(
            "Weights adjusted: sent=%.4f onchain=%.4f macro=%.4f pred=%.4f",
            updated.get("sentiment_weight", 1.0),
            updated.get("onchain_weight", 1.0),
            updated.get("macro_weight", 1.0),
            updated.get("prediction_weight", 1.0),
        )

        return updated
    finally:
        conn.close()
