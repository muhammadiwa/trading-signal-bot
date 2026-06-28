"""Research multiplier engine — Story 2.5: Research Multiplier Engine (FR3.1-3.4).

Computes the final research confidence multiplier from all research dimensions.
"""

import logging

logger = logging.getLogger(__name__)


def _get_dynamic_weights() -> dict[str, float]:
    """Read weights from SQLite, returning defaults if unavailable."""
    defaults = {"sentiment_weight": 1.0, "onchain_weight": 1.0,
                "macro_weight": 1.0, "prediction_weight": 1.0}
    try:
        from src.db import get_connection
        conn = get_connection()
        rows = conn.execute("SELECT weight_id, value FROM weights").fetchall()
        conn.close()
        if rows:
            return {r["weight_id"]: r["value"] for r in rows}
    except Exception:
        pass
    return defaults


def sentiment_mult(fear_greed_val: float | None, weight: float | None = None) -> float:
    """Convert Fear & Greed score to confidence multiplier (FR3.1).

    Static (weight=None): >60→1.2, 40-60→1.0, <40→0.8, None→1.0
    Dynamic: 1.0 + (score − 50) / 50 × weight (Story 3.3 AC6)
    """
    import math
    if fear_greed_val is None:
        return 1.0
    if math.isnan(fear_greed_val):
        logger.warning("sentiment_mult: NaN input — returning neutral 1.0")
        return 1.0

    if weight is not None:
        return round(max(0.5, min(1.5, 1.0 + (fear_greed_val - 50) / 50 * weight)), 4)

    # Static defaults
    if fear_greed_val > 60:
        return 1.2
    if fear_greed_val < 40:
        return 0.8
    return 1.0


def onchain_mult(onchain_signal: str | None, weight: float | None = None) -> float:
    """Convert on-chain signal to confidence multiplier (FR3.2 + Story 3.3).

    Static: bullish→1.15, neutral→1.0, bearish→0.85
    Dynamic: 1.0 + sign × weight × 0.15
    """
    if not onchain_signal:
        return 1.0

    signal_lower = onchain_signal.lower()
    if weight is not None:
        sign_map = {"bullish": 1, "neutral": 0, "bearish": -1}
        sign = sign_map.get(signal_lower, 0)
        return round(max(0.5, min(1.5, 1.0 + sign * weight * 0.15)), 4)

    mapping = {"bullish": 1.15, "neutral": 1.0, "bearish": 0.85}
    return mapping.get(signal_lower, 1.0)


def compute_research_multiplier(
    sentiment_score: float | None = None,
    onchain_signal: str | None = None,
    macro_has_event: bool = False,
    macro_penalty: float = 0.0,
    prediction_adjustment: float = 0.0,
) -> float:
    """Compute final research confidence multiplier (FR3.4).

    Formula: sentiment_mult × onchain_mult × (1 − macro_penalty) + prediction_adjustment
    Clamped to [0.5, 1.5].

    Args:
        sentiment_score: Fear & Greed composite (0-100).
        onchain_signal: "bullish", "neutral", or "bearish".
        macro_has_event: Whether there's a macro event nearby.
        macro_penalty: Penalty value (0.0-0.20).
        prediction_adjustment: ±0.05 from Polymarket (Story 2.4).

    Returns:
        Multiplier: 0.5-1.5. 1.0 = no adjustment, >1 = boost, <1 = reduce.
    """
    # Read dynamic weights from DB (Story 3.3)
    weights = _get_dynamic_weights()

    sent = sentiment_mult(sentiment_score, weight=weights.get("sentiment_weight"))
    onch = onchain_mult(onchain_signal, weight=weights.get("onchain_weight"))
    macro = (macro_penalty * weights.get("macro_weight", 1.0)) if macro_has_event else 0.0

    # Count active sources (4 dimensions: sentiment, on-chain, macro, prediction)
    active_count = int(sentiment_score is not None) + int(onchain_signal is not None)
    if macro_has_event:
        active_count += 1
    if prediction_adjustment != 0.0:
        active_count += 1
    total_sources = 4
    if active_count == 0:
        logger.warning("Research multiplier: 0 of %d sources active — using technical confidence only", total_sources)
    elif active_count < total_sources:
        logger.info("Research multiplier: %d of %d sources active", active_count, total_sources)

    multiplier = sent * onch * (1.0 - macro) + prediction_adjustment
    clamped = max(0.5, min(1.5, multiplier))

    logger.debug(
        "Research multiplier: sent=%.2f onch=%.2f macro=%.2f pred=%+.2f → %.2f (clamped %.2f)",
        sent, onch, macro, prediction_adjustment, multiplier, clamped,
    )

    return round(clamped, 4)


def apply_research_to_confidence(technical_confidence: float,
                                 research_multiplier: float) -> float:
    """Apply research multiplier to technical confidence (FR4.1).

    Final Confidence = Technical_Confidence × Research_Multiplier
    Clamped to [0.0, 1.0], rounded to 2 decimal places per FR2.5 AC.
    """
    final = technical_confidence * research_multiplier
    clamped = max(0.0, min(1.0, final))
    return round(clamped, 2)
