"""Exchange data router with CCXT fallback chain and Parquet caching."""

import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Ordered fallback chain: try each exchange in sequence
_EXCHANGE_IDS = ["binance", "okx"]
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 2  # Base delay; exponential backoff: delay * 2^attempt

# Per-symbol file locks for thread-safe cache writes
_cache_locks: dict[str, threading.Lock] = {}
_cache_locks_lock = threading.Lock()


def _normalize_symbol(symbol: str) -> str:
    """Normalize symbol to CCXT format (e.g., BTC-USDT → BTC/USDT).

    Handles edge cases: lowercase input, perpetual swaps, already-normalized.
    """
    s = symbol.upper()
    if "/" in s:
        return s  # Already normalized
    # For perpetuals: BTC-USDT-PERP → BTC/USDT:USDT
    parts = s.rsplit("-", 1)
    if len(parts) == 2 and parts[1] in ("PERP", "PERPETUAL", "SWAP"):
        base_quote = parts[0].replace("-", "/")
        return f"{base_quote}:{parts[1]}"
    return s.replace("-", "/")


def _ohlcv_cache_path(symbol: str) -> Path:
    """Get the Parquet cache path for a symbol."""
    project_root = Path(__file__).resolve().parent.parent
    cache_dir = project_root / "data" / "ohlcv"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{symbol}.parquet"


def _is_cache_fresh(cache_path: Path, max_age_hours: int = 4) -> bool:
    """Check if cached data is fresh enough.

    Returns True if cache exists and is newer than max_age_hours.
    """
    if not cache_path.exists():
        return False
    file_age = time.time() - cache_path.stat().st_mtime
    return file_age < max_age_hours * 3600


def _load_cache(symbol: str) -> Optional[pd.DataFrame]:
    """Load cached OHLCV data from Parquet, if available."""
    cache_path = _ohlcv_cache_path(symbol)
    if not cache_path.exists():
        return None
    try:
        df = pq.read_table(cache_path).to_pandas()
        if df.empty:
            return None
        # Ensure timestamp is datetime
        if "timestamp" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df
    except Exception as e:
        logger.warning("Failed to load cache for %s: %s", symbol, e)
        return None


def _save_cache(symbol: str, df: pd.DataFrame) -> None:
    """Save OHLCV data to Parquet cache atomically, thread-safe.

    Writes to a temp file first, then renames to avoid partial writes.
    Appends to existing cache if present (drops duplicate timestamps).
    Uses per-symbol file lock to prevent write-write races.
    """
    cache_path = _ohlcv_cache_path(symbol)

    # Per-symbol lock for thread safety
    with _cache_locks_lock:
        if symbol not in _cache_locks:
            _cache_locks[symbol] = threading.Lock()
        lock = _cache_locks[symbol]

    with lock:
        # Load existing cache and merge
        existing = _load_cache(symbol)
        if existing is not None and not existing.empty:
            df = pd.concat([existing, df], ignore_index=True)
            if "timestamp" in df.columns:
                before = len(df)
                df = df.drop_duplicates(subset=["timestamp"], keep="last")
                dropped = before - len(df)
                if dropped > 0:
                    logger.debug("Dropped %d duplicate timestamps for %s", dropped, symbol)
            df = df.sort_values("timestamp").reset_index(drop=True)

        # Atomic write: temp file → rename
        temp_path = cache_path.with_suffix(".parquet.tmp")
        try:
            table = pa.Table.from_pandas(df)
            pq.write_table(table, temp_path)
            os.replace(temp_path, cache_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise


def _fetch_from_exchange(exchange_id: str, symbol: str, since_ms: int,
                         timeframe: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV from a specific exchange via CCXT.

    Args:
        exchange_id: CCXT exchange id (e.g., 'binance', 'okx').
        symbol: Trading pair in our format (e.g., 'BTC-USDT').
        since_ms: Start timestamp in milliseconds.
        timeframe: OHLCV timeframe (default '1d').

    Returns:
        DataFrame with OHLCV data.

    Raises:
        Various CCXT exceptions on failure.
    """
    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True, "timeout": 30000})
    try:
        normalized = _normalize_symbol(symbol)
        ohlcv = exchange.fetch_ohlcv(normalized, timeframe, since=since_ms, limit=1000)
        if not ohlcv:
            raise ValueError(f"{exchange_id} returned empty OHLCV for {normalized}")

        df = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df
    finally:
        if hasattr(exchange, "close"):
            exchange.close()


def _fetch_from_coingecko(symbol: str, since_ms: int, timeframe: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV from CoinGecko public API as final fallback.

    CoinGecko uses crypto IDs (e.g., 'bitcoin'), not CCXT symbols.
    This is a best-effort fallback — may not support all pairs.
    """
    import requests

    # Map common symbols to CoinGecko IDs
    COINGECKO_IDS = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2",
        "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
    }

    base = symbol.split("-")[0].upper()
    coin_id = COINGECKO_IDS.get(base)
    if not coin_id:
        raise ValueError(f"CoinGecko: no ID mapping for {symbol}")

    days = max(1, int((time.time() * 1000 - since_ms) / 86400000))
    days = min(days, 365)  # CoinGecko free tier limit

    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {"vs_currency": "usd", "days": str(days)}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        raise ValueError(f"CoinGecko returned empty OHLCV for {coin_id}")

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
    df["volume"] = np.nan  # CoinGecko OHLC API does not include volume; NaN avoids corrupting volume-dependent computations
    logger.warning("CoinGecko fallback for %s — volume data unavailable (set to NaN)", symbol)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1d",
    since_ms: Optional[int] = None,
    force_refresh: bool = False,
    max_age_hours: int = 4,
) -> pd.DataFrame:
    """Fetch OHLCV data for a symbol with fallback and caching.

    Ordered fallback: Binance → OKX → cached data.
    Each exchange retries up to 3 times with exponential backoff.
    Results are cached to Parquet.

    Args:
        symbol: Trading pair (e.g., 'BTC-USDT').
        timeframe: OHLCV timeframe (default '1d').
        since_ms: Start timestamp in milliseconds. If None, defaults to
                  6 months ago from now.
        force_refresh: If True, bypass cache and fetch fresh data.
        max_age_hours: Maximum cache age before forcing refresh.

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume.

    Raises:
        RuntimeError: If all exchanges fail and no cache available.
    """
    if since_ms is None:
        # Default: 6 months ago
        six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
        since_ms = int(six_months_ago.timestamp() * 1000)

    # Check cache first (unless forced refresh)
    cache_path = _ohlcv_cache_path(symbol)
    if not force_refresh and _is_cache_fresh(cache_path, max_age_hours):
        cached = _load_cache(symbol)
        if cached is not None and not cached.empty:
            logger.debug("Using fresh cache for %s", symbol)
            return cached

    # Try each exchange in order
    last_error = None
    for exchange_id in _EXCHANGE_IDS:
        for attempt in range(_MAX_RETRIES):
            try:
                logger.info("Fetching %s from %s (attempt %d/%d)",
                            symbol, exchange_id, attempt + 1, _MAX_RETRIES)
                df = _fetch_from_exchange(exchange_id, symbol, since_ms, timeframe)
                _save_cache(symbol, df)

                # Minimum data gate: warn if insufficient
                if len(df) < 180:  # ~6 months daily
                    logger.warning(
                        "Insufficient history for %s: %d rows (minimum 6 months recommended)",
                        symbol, len(df),
                    )

                return df
            except Exception as e:
                last_error = e
                logger.warning("%s fetch for %s failed (attempt %d/%d): %s",
                               exchange_id, symbol, attempt + 1, _MAX_RETRIES, e)
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAY_SECONDS * (2 ** attempt)
                    time.sleep(delay)

        logger.warning("%s exhausted after %d attempts for %s", exchange_id, _MAX_RETRIES, symbol)

    # All exchanges failed — try CoinGecko as final fallback per AD-4
    try:
        logger.info("Falling back to CoinGecko for %s", symbol)
        df = _fetch_from_coingecko(symbol, since_ms, timeframe)
        _save_cache(symbol, df)
        if len(df) < 180:
            logger.warning(
                "Insufficient history for %s: %d rows (minimum 6 months recommended)",
                symbol, len(df),
            )
        return df
    except Exception as cg_error:
        logger.warning("CoinGecko fallback for %s failed: %s", symbol, cg_error)

    # All sources failed — try stale cache
    cached = _load_cache(symbol)
    if cached is not None and not cached.empty:
        cache_age_hours = "unknown"
        try:
            if cache_path.exists():
                age_seconds = time.time() - cache_path.stat().st_mtime
                cache_age_hours = f"{age_seconds / 3600:.1f}h"
        except OSError:
            pass
        logger.warning(
            "All sources failed for %s — using stale cache (age: %s). Last error: %s",
            symbol, cache_age_hours, last_error,
        )
        return cached

    raise RuntimeError(
        f"All sources failed for {symbol} and no cache available. "
        f"Last error: {last_error}"
    )


class ExchangeRouter:
    """Router for fetching OHLCV data with fallback and caching.

    Thin wrapper around the module-level fetch_ohlcv function.
    Keeps the same interface for consistency with the architecture (AD-4).
    """

    @staticmethod
    def fetch_ohlcv(
        symbol: str,
        timeframe: str = "1d",
        since_ms: Optional[int] = None,
        force_refresh: bool = False,
        max_age_hours: int = 4,
    ) -> pd.DataFrame:
        return fetch_ohlcv(symbol, timeframe, since_ms, force_refresh, max_age_hours)
