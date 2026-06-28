"""Telegram sender for daily signal delivery."""

import logging
import os
import time
from typing import Optional

from src.pipeline.stage_4_confidence import Signal

logger = logging.getLogger(__name__)


def _price_decimals(price: float, symbol: str) -> int:
    """Determine display decimals matching compute_sl_tp rounding."""
    upper = symbol.upper()
    if "JPY" in upper or "KRW" in upper:
        return 0
    elif price > 1000:
        return 2
    elif price > 1:
        return 4
    else:
        return 6


def _price_str(price: float, decimals: int) -> str:
    """Format a price with appropriate decimals and commas."""
    if decimals == 0:
        return f"{price:,.0f}"
    else:
        return f"{price:,.{decimals}f}"


def _format_signal_block(signal: Signal, include_research: bool = False,
                          track_record: Optional[str] = None) -> str:
    """Format a single signal as a Telegram message block.

    Mixed language: signal fields in English, commentary in Indonesian.
    """
    emoji_map = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
    emoji = emoji_map.get(signal.action, "⚪")
    entry_dec = _price_decimals(signal.entry_price, signal.symbol)
    entry_str = f"${_price_str(signal.entry_price, entry_dec)}"
    sl_dec = _price_decimals(signal.stop_loss, signal.symbol)
    tp_dec = _price_decimals(signal.take_profit, signal.symbol) if signal.take_profit else entry_dec

    lines = [
        f"{emoji} {signal.action} — {signal.symbol} {entry_str}",
        f"{signal.strategy} | Conf {signal.confidence*100:.0f}%",
        f"SL: ${_price_str(signal.stop_loss, sl_dec)} | TP: ${_price_str(signal.take_profit, tp_dec)}" if signal.take_profit
        else f"SL: ${_price_str(signal.stop_loss, sl_dec)} | TP: N/A",
    ]

    # Track record (when available)
    if track_record:
        lines.append(f"📈 Track: {track_record}")

    # Research context (Epic 2)
    if include_research and signal.research_metadata:
        import json
        try:
            meta = json.loads(signal.research_metadata)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    else:
        meta = {}

    # Check if all research sources defaulted (AC4)
    all_unavailable = (
        signal.sentiment_score is None and
        not signal.onchain_signal and
        not signal.macro_flag and
        not meta.get("prediction_adjustment", 0)
    )

    if include_research and all_unavailable:
        lines.append("(Technical confidence only — research data unavailable)")
    elif include_research:
        fg_val = signal.sentiment_score
        classification = "Fear" if fg_val < 40 else ("Greed" if fg_val > 60 else "Neutral")
        lines.append(f"📊 Sentiment: {classification} {fg_val:.0f}/100")
    if include_research and signal.onchain_signal:
        detail = signal.onchain_signal.capitalize()
        mult = meta.get("onchain_mult")
        if mult and mult != 1.0:
            detail += f" (×{mult:.2f})"
        lines.append(f"🔗 On-chain: {detail}")
    if include_research and signal.macro_flag:
        detail = "⚠️"
        if meta.get("macro_penalty", 0) > 0:
            detail = f"−{meta['macro_penalty']*100:.0f}% confidence"
        lines.append(f"📅 Macro event nearby {detail}")
    if include_research and signal.research_metadata and meta.get("prediction_adjustment", 0) != 0:
        adj = meta["prediction_adjustment"]
        direction = "bullish" if adj > 0 else "bearish"
        lines.append(f"🗳️ Polymarket: {direction} (+{abs(adj):.2f})" if adj > 0 else f"🗳️ Polymarket: {direction} ({adj:+.2f})")
    if include_research and meta.get("final_multiplier"):
        mult = meta["final_multiplier"]
        if mult != 1.0:
            direction = "boost" if mult > 1.0 else "reduce"
            lines.append(f"🔬 Research: {direction} {abs(mult-1.0)*100:.0f}%")

    return "\n".join(lines)


def format_daily_message(
    signals: list[Signal],
    pairs_analyzed: int = 100,
    win_rate_7d: Optional[float] = None,
    include_research: bool = False,
) -> str:
    """Format the daily signal report as a single Telegram message.

    Args:
        signals: List of signals to include.
        pairs_analyzed: Total pairs analyzed today.
        win_rate_7d: Rolling 7-day win rate (optional).
        include_research: Whether to include research context lines.

    Returns:
        Formatted message string.
    """
    if not signals:
        return (
            "📊 SINYAL HARIAN\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{pairs_analyzed} pair dianalisa — tidak ada sinyal valid hari ini.\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

    avg_conf = sum(s.confidence for s in signals) / len(signals)
    summary = f"📊 {len(signals)}/{pairs_analyzed} pair analyzed | Avg Conf: {avg_conf*100:.0f}%"
    if win_rate_7d is not None:
        summary += f" | 7-day win: {win_rate_7d*100:.0f}%"

    lines = [
        "📊 SINYAL HARIAN",
        "━━━━━━━━━━━━━━━━━━━━",
        summary,
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for s in signals:
        lines.append(_format_signal_block(s, include_research))
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 Confidence >70% = sinyal kuat")

    return "\n".join(lines)


def send_daily_signals(
    signals: list[Signal],
    pairs_analyzed: int = 100,
    win_rate_7d: Optional[float] = None,
    include_research: bool = False,
) -> bool:
    """Send daily signal report to configured Telegram chat.

    Retries up to 3 times with exponential backoff.
    On failure, logs error and returns False — does NOT crash the pipeline.

    Returns:
        True if message was sent successfully.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured")
        return False

    message = format_daily_message(signals, pairs_analyzed, win_rate_7d, include_research)

    if len(message) > 4000:
        # Truncate with warning — find last complete signal block
        original_len = len(message)
        cutoff = message.rfind("\n", 0, 4000)
        if cutoff <= 0:
            cutoff = 3900  # Fallback: hard cut (no newline found)
        message = message[:cutoff] + "\n\n... dan sinyal lainnya (pesan terlalu panjang)"
        logger.warning("Message truncated to %d chars (was %d)", len(message), original_len)

    # Late import — python-telegram-bot is a required dependency
    from telegram import Bot
    from telegram.error import TelegramError

    bot = Bot(token=bot_token)
    max_retries = 3

    for attempt in range(max_retries):
        try:
            bot.send_message(chat_id=chat_id, text=message, parse_mode=None)
            logger.info("Daily signals sent to Telegram (%d signals)", len(signals))
            return True
        except TelegramError as e:
            logger.warning("Telegram send failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                delay = 2 ** attempt  # 1s, 2s, 4s
                time.sleep(delay)
        except Exception as e:
            logger.error("Unexpected Telegram error: %s", e)
            return False

    logger.error("Telegram send failed after %d retries — signals saved to DB only", max_retries)
    return False
