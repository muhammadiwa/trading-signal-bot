"""LLM Reflection Generator — Story 3.2 (FR6.2).

Generates 1-2 sentence reflections for resolved signal outcomes using
TokenRouter API (OpenAI-compatible). Falls back to deterministic text
on any failure. Never crashes the pipeline.

AD-1: This is the ONLY module where LLM is used.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from src.config import Settings

logger = logging.getLogger(__name__)


def build_reflection_prompt(outcome: dict, signal: dict) -> str:
    """Build the LLM reflection prompt from outcome + signal context (AC1).

    Includes: symbol, action, entry/exit prices, return_pct, strategy,
    confidence, research context, win/loss status.
    """
    symbol = signal.get("symbol", "?")
    action = signal.get("action", "?")
    entry = signal.get("entry_price", 0)
    exit_price = outcome.get("price_at_resolution", "N/A")
    ret_pct = outcome.get("realized_return_pct", 0)
    ret_str = f"{ret_pct:+.2f}%" if ret_pct is not None else "N/A"
    strategy = signal.get("strategy", "Unknown")
    confidence = signal.get("confidence", 0)
    win = "win" if outcome.get("win") else "loss"

    # Research context
    sentiment = signal.get("sentiment_score", "N/A")
    onchain = signal.get("onchain_signal", "none")
    macro = "yes" if signal.get("macro_flag") else "no"

    # Parse metadata for more detail
    meta = {}
    raw_meta = signal.get("research_metadata")
    if raw_meta:
        try:
            meta = json.loads(raw_meta)
        except (json.JSONDecodeError, TypeError):
            pass

    prompt = (
        f"Reflect on this trading signal outcome in 1-2 sentences.\n\n"
        f"Symbol: {symbol}\n"
        f"Action: {action}\n"
        f"Entry: ${entry:.2f}\n"
        f"Exit: ${exit_price}\n"
        f"Return: {ret_str}\n"
        f"Result: {win}\n"
        f"Strategy: {strategy}\n"
        f"Confidence: {confidence*100:.0f}%\n"
        f"Sentiment: {sentiment}/100\n"
        f"On-chain: {onchain}\n"
        f"Macro event: {macro}\n"
        f"Research multiplier: {meta.get('final_multiplier', 'N/A')}\n\n"
        f"Return ONLY 1-2 sentences. No markdown, no analysis, no recommendations."
    )
    return prompt


def _deterministic_fallback(outcome: dict, signal: dict) -> str:
    """Generate deterministic fallback reflection (AC4)."""
    symbol = signal.get("symbol", "?")
    action = signal.get("action", "?")
    ret_pct = outcome.get("realized_return_pct", 0)
    if ret_pct is not None:
        ret_str = f"{ret_pct:+.1f}%"
    else:
        ret_str = "unresolvable"

    if outcome.get("win"):
        return f"{symbol} {action}: {ret_str} — signal aligned with price movement."
    else:
        return f"{symbol} {action}: {ret_str} — signal did not work out this time."


def _truncate_response(text: str, max_len: int = 300) -> str:
    """Truncate response to max_len characters with '...' (AC5)."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    truncated = text[:max_len] + "..."
    logger.info("Reflection truncated: %d → %d chars", len(text), len(truncated))
    return truncated


def generate_reflection(
    outcome: dict,
    signal: dict,
    api_key: str = "",
    base_url: str = "",
    model: str = "deepseek/deepseek-v4-pro",
    timeout: int = 3,
    max_tokens: int = 150,
) -> tuple[str, bool]:
    """Generate LLM reflection for a resolved outcome.

    Returns (reflection_text, llm_used):
      - llm_used=True  → LLM response was used
      - llm_used=False → deterministic fallback was used

    Never raises — always returns a reflection string.
    """
    # AC6: No API key → immediate fallback
    if not api_key:
        logger.info("LLM unavailable — no API key configured, using deterministic reflection")
        return _deterministic_fallback(outcome, signal), False

    # Build prompt
    prompt = build_reflection_prompt(outcome, signal)

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
            timeout=timeout,
        )

        if resp.status_code != 200:
            logger.warning("LLM API returned %d — using deterministic reflection", resp.status_code)
            return _deterministic_fallback(outcome, signal), False

        body = resp.json()
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = body.get("usage", {})

        if not content:
            logger.warning("LLM returned empty response — using deterministic reflection")
            return _deterministic_fallback(outcome, signal), False

        tokens = usage.get("total_tokens", 0)
        reflection = _truncate_response(content, 300)
        logger.info("LLM reflection: %s (%d tokens)", signal.get("symbol"), tokens)
        return reflection, True

    except requests.Timeout:
        logger.warning("LLM call timed out after %ds — using deterministic reflection", timeout)
    except Exception as e:
        logger.warning("LLM reflection failed: %s — using deterministic reflection", e)

    return _deterministic_fallback(outcome, signal), False


def generate_reflections(
    resolved_outcomes: list[dict],
    api_key: str = "",
    base_url: str = "",
    model: str = "deepseek/deepseek-v4-pro",
    timeout: int = 3,
    max_tokens: int = 150,
) -> list[dict]:
    """Generate LLM reflections for all resolved outcomes.

    For each outcome: fetch the original signal, generate reflection,
    write result back to outcomes table.

    Returns list of result dicts with reflection_text and llm_used.
    """
    if not resolved_outcomes:
        logger.info("No outcomes to reflect on")
        return []

    from src.db import get_connection

    conn = get_connection()
    try:
        results = []
        for oc in resolved_outcomes:
            signal_id = oc.get("signal_id")
            if not signal_id:
                continue

            # Fetch original signal for prompt context
            row = conn.execute(
                """SELECT symbol, action, entry_price, confidence, strategy,
                   sentiment_score, onchain_signal, macro_flag, research_metadata
                   FROM signals WHERE id = ?""",
                (signal_id,),
            ).fetchone()

            if not row:
                logger.debug("Signal %s not found — skipping reflection", signal_id)
                continue

            signal = dict(row)

            text, llm_used = generate_reflection(
                oc, signal, api_key, base_url, model, timeout, max_tokens,
            )

            conn.execute(
                """UPDATE outcomes SET reflection_text = ?, llm_used = ?
                   WHERE signal_id = ?""",
                (text, 1 if llm_used else 0, signal_id),
            )

            results.append({
                "signal_id": signal_id,
                "reflection_text": text,
                "llm_used": llm_used,
            })

            # Rate-limit: space LLM calls by 100ms to avoid rate limits
            if llm_used:
                time.sleep(0.1)

        conn.commit()

        llm_count = sum(1 for r in results if r["llm_used"])
        fallback_count = len(results) - llm_count
        logger.info("Reflections: %d LLM + %d fallback = %d total",
                    llm_count, fallback_count, len(results))

        return results
    finally:
        conn.close()
