"""Telegram sender for daily signal delivery."""

import logging
import os
import time
from typing import Optional

from src.pipeline.stage_4_confidence import Signal

logger = logging.getLogger(__name__)


def _format_signal_block(signal: Signal, include_research: bool = False) -> str:
    """Format a single signal as a Telegram message block.

    Mixed language: signal fields in English, commentary in Indonesian.
    """
    emoji_map = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
    emoji = emoji_map.get(signal.action, "⚪")
    entry_str = f"${signal.entry_price:,.2f}" if signal.entry_price > 1 else f"${signal.entry_price:.6f}"

    lines = [
        f"{emoji} {signal.action} — {signal.symbol} {entry_str}",
        f"{signal.strategy} | Conf {signal.confidence*100:.0f}%",
        f"SL: {signal.stop_loss:,.2f} | TP: {signal.take_profit:,.2f}" if signal.take_profit
        else f"SL: {signal.stop_loss:,.2f} | TP: N/A",
    ]

    # Research context (Epic 2 — populated later)
    if include_research and signal.sentiment_score is not None:
        lines.append(f"📊 Sentiment: {signal.sentiment_score:.0f}/100")
    if include_research and signal.onchain_signal:
        lines.append(f"🔗 On-chain: {signal.onchain_signal}")
    if include_research and signal.macro_flag:
        lines.append("📅 Macro event nearby ⚠️")

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
    lines = [
        "📊 SINYAL HARIAN",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{len(signals)}/{pairs_analyzed} pair menghasilkan sinyal",
        f"Avg confidence: {avg_conf*100:.0f}%",
    ]

    if win_rate_7d is not None:
        lines.append(f"Win rate 7-hari: {win_rate_7d*100:.0f}%")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

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
        # Truncate with warning
        cutoff = message.rfind("\n", 0, 4000)
        message = message[:cutoff] + "\n\n... dan sinyal lainnya (pesan terlalu panjang)"
        logger.warning("Message truncated to %d chars (was %d)", len(message), len(message) + 100)

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
