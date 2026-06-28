"""Research multiplier engine — Story 2.5: Research Multiplier Engine (FR3.1-3.4).

Computes the final research confidence multiplier from all research dimensions.
"""

import logging

logger = logging.getLogger(__name__)


def sentiment_mult(fear_greed_val: float | None) -> float:
    """Convert Fear & Greed score to confidence multiplier (FR3.1).

    - composite > 60 → 1.2 (greed/bullish — boost confidence)
    - composite 40-60 → 1.0 (neutral — no change)
    - composite < 40 → 0.8 (fear/bearish — reduce confidence)
    - None → 1.0 (no data)
    """
    if fear_greed_val is None:
        return 1.0
    if fear_greed_val > 60:
        return 1.2
    if fear_greed_val < 40:
        return 0.8
    return 1.0


def onchain_mult(onchain_signal: str | None) -> float:
    """Convert on-chain signal to confidence multiplier (FR3.2).

    - bullish → 1.15
    - neutral → 1.0
    - bearish → 0.85
    - None → 1.0 (no data)
    """
    if not onchain_signal:
        return 1.0
    mapping = {"bullish": 1.15, "neutral": 1.0, "bearish": 0.85}
    return mapping.get(onchain_signal.lower(), 1.0)


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
    sent = sentiment_mult(sentiment_score)
    onch = onchain_mult(onchain_signal)
    macro = macro_penalty if macro_has_event else 0.0

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
