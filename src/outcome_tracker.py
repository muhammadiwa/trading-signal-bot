"""Outcome tracker — Story 3.1: resolves yesterday's pending signals.

Pure deterministic computation. No LLM — that's Story 3.2.
Runs as Stage 0 at pipeline start, BEFORE new signal generation.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def compute_return(action: str, entry_price: float, current_price: float) -> float:
    """Compute realized return percentage for a resolved signal.

    BUY:  (current_price − entry_price) / entry_price × 100
    SELL: (entry_price − current_price) / entry_price × 100

    Returns:
        Float percentage (e.g., +2.82 means +2.82%).
    """
    if entry_price <= 0:
        raise ValueError(f"Invalid entry_price {entry_price} for signal")
    if action == "BUY":
        return (current_price - entry_price) / entry_price * 100
    elif action == "SELL":
        return (entry_price - current_price) / entry_price * 100
    else:
        raise ValueError(f"Unknown action '{action}' — expected BUY or SELL")


def _fetch_current_price(symbol: str) -> float:
    """Fetch current price for a symbol via CCXT (reuse exchange module).

    Uses a fast ticker fetch — lighter than full OHLCV.
    Falls back to Binance only (no CoinGecko needed for ticker).

    Raises:
        Exception: If price cannot be fetched (delisted, network, etc.).
    """
    import ccxt
    from src.exchange import _normalize_symbol

    normalized = _normalize_symbol(symbol)
    for exchange_id in ("binance", "okx"):
        try:
            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True, "timeout": 15000})
            ticker = exchange.fetch_ticker(normalized)
            price = ticker.get("last") or ticker.get("close")
            exchange.close()
            if price and price > 0:
                return float(price)
        except Exception:
            # Suppress close-time errors; original fetch error is what matters
            try:
                if 'exchange' in locals() and hasattr(exchange, "close"):
                    exchange.close()
            except Exception:
                pass
            continue

    raise RuntimeError(f"Symbol {symbol} no longer available")


def resolve_pending_signals() -> list[dict]:
    """Resolve all pending signals from previous pipeline runs.

    1. Query all signals with status = "pending"
    2. Fetch current price per symbol (deduplicated)
    3. Compute realized_return_pct per signal
    4. Write outcomes, update signal status

    Returns:
        List of resolved outcome dicts: {signal_id, symbol, realized_return_pct,
        price_at_resolution, win, error}
    """
    from src.db import get_connection

    conn = get_connection()
    try:
        # Step 1: Get all pending signals
        rows = conn.execute(
            """SELECT id, symbol, action, entry_price, timestamp_utc
               FROM signals WHERE status = 'pending'
               ORDER BY timestamp_utc""",
        ).fetchall()

        if not rows:
            logger.info("No pending signals to resolve")
            return []

        logger.info("Resolving %d pending signals...", len(rows))

        # Step 2: Deduplicate symbols — fetch price once per unique symbol
        unique_symbols = list(dict.fromkeys(r["symbol"] for r in rows))
        prices: dict[str, float | None] = {}
        errors: dict[str, str] = {}

        for sym in unique_symbols:
            try:
                prices[sym] = _fetch_current_price(sym)
            except Exception as e:
                logger.warning("Symbol %s no longer available: %s", sym, e)
                prices[sym] = None
                errors[sym] = f"Symbol {sym} no longer available"

        # Step 3-4: Compute returns + write outcomes
        resolved_at = datetime.now(timezone.utc).isoformat()
        results = []

        for row in rows:
            sym = row["symbol"]
            current_price = prices.get(sym)
            signal_id = row["id"]

            if current_price is None:
                # AC5: Unresolvable — delisted or unreachable
                conn.execute(
                    """INSERT OR IGNORE INTO outcomes
                       (signal_id, realized_return_pct, price_at_resolution, resolved_at)
                       VALUES (?, NULL, NULL, ?)""",
                    (signal_id, resolved_at),
                )
                conn.execute(
                    "UPDATE signals SET status = 'unresolvable' WHERE id = ?",
                    (signal_id,),
                )
                result = {
                    "signal_id": signal_id, "symbol": sym,
                    "realized_return_pct": None,
                    "price_at_resolution": None,
                    "win": False, "error": errors.get(sym, "Unresolvable"),
                }
            else:
                # AC1+AC2: Resolve normally
                ret_pct = compute_return(row["action"], row["entry_price"], current_price)
                win = ret_pct > 0

                conn.execute(
                    """INSERT OR IGNORE INTO outcomes
                       (signal_id, realized_return_pct, price_at_resolution, resolved_at)
                       VALUES (?, ?, ?, ?)""",
                    (signal_id, round(ret_pct, 4), current_price, resolved_at),
                )
                conn.execute(
                    "UPDATE signals SET status = 'resolved' WHERE id = ?",
                    (signal_id,),
                )
                result = {
                    "signal_id": signal_id, "symbol": sym,
                    "realized_return_pct": round(ret_pct, 4),
                    "price_at_resolution": current_price,
                    "win": win, "error": None,
                }

            results.append(result)
            logger.debug(
                "%s %s: entry=%.2f current=%s return=%s%%",
                sym, row["action"], row["entry_price"],
                current_price, result["realized_return_pct"],
            )

        conn.commit()

        wins = sum(1 for r in results if r["win"])
        logger.info(
            "Outcomes resolved: %d total, %d wins, %d losses, %d unresolvable",
            len(results), wins,
            sum(1 for r in results if r["realized_return_pct"] is not None and r["realized_return_pct"] <= 0),
            sum(1 for r in results if r["realized_return_pct"] is None),
        )

        return results
    finally:
        conn.close()


def _compute_win_rate_7d() -> Optional[float]:
    """Compute rolling 7-day win rate from outcomes table (AC6).

    Reuses the same query pattern as main.py:_compute_7day_win_rate()
    but remains module-contained for testing.
    """
    from src.db import get_connection

    try:
        conn = get_connection()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        row = conn.execute(
            """SELECT AVG(CASE WHEN realized_return_pct > 0 THEN 1 ELSE 0 END) AS wr
               FROM outcomes WHERE resolved_at > ?""",
            (cutoff,),
        ).fetchone()
        conn.close()
        return round(row["wr"], 4) if row and row["wr"] is not None else None
    except Exception as e:
        logger.warning("Failed to compute 7-day win rate: %s", e)
        return None
