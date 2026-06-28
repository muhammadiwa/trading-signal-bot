"""Main pipeline orchestrator — nightly batch runner with scheduling."""

import logging
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import load_config, Settings
from src.db import init_db, get_connection

logger = logging.getLogger(__name__)

# Stage pipeline definition (ordered)
STAGES = [
    ("data_fetch", "Stage 1: Data Fetch"),
    ("profile_match", "Stage 2: Profile + Strategy Match"),
    ("research_context", "Stage 3: Research Context"),
    ("confidence_filter", "Stage 4: Confidence + Filter"),
    ("telegram_deliver", "Stage 5: Telegram Delivery"),
]

# Critical data sources for health check
CRITICAL_SOURCES = {
    "ccxt": "Exchange data (CCXT)",
    "alternative_me": "Fear & Greed Index (Alternative.me)",
    "telegram": "Telegram bot token",
}


def health_check(config: Settings) -> dict[str, bool]:
    """Verify critical data sources are reachable before pipeline start.

    Returns:
        Dict mapping source name to True (reachable) or False (failed).
    """
    results = {}

    # CCXT check
    try:
        import ccxt
        exchange = ccxt.binance({"enableRateLimit": True})
        exchange.fetch_ticker("BTC/USDT")
        exchange.close()
        results["ccxt"] = True
    except Exception as e:
        logger.error("CCXT health check failed: %s", e)
        results["ccxt"] = False

    # Alternative.me check
    try:
        import requests
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        results["alternative_me"] = resp.status_code == 200
    except Exception as e:
        logger.error("Alternative.me health check failed: %s", e)
        results["alternative_me"] = False

    # Telegram check
    results["telegram"] = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())

    return results


def send_alert(message: str) -> bool:
    """Send a critical alert to Telegram. Non-blocking, best-effort."""
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if bot_token and chat_id:
            import asyncio
            from telegram import Bot
            async def _send():
                await Bot(token=bot_token).send_message(chat_id=chat_id, text=f"⚠️ Pipeline Alert\n{message}")
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_send())
                else:
                    loop.run_until_complete(_send())
            except RuntimeError:
                asyncio.run(_send())
            return True
    except Exception as e:
        logger.error("Failed to send alert: %s", e)
    return False


def run_pipeline(config: Optional[Settings] = None) -> dict:
    """Execute the full nightly pipeline.

    Args:
        config: Settings object. Loads from default if None.

    Returns:
        Dict with run summary: status, pairs_analyzed, signals_generated, duration.
    """
    if config is None:
        config = load_config()

    start_time = time.monotonic()
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
    pairs_analyzed = 0
    signals_generated = 0
    status = "running"
    stage_failed = None
    error_summary = None

    # Initialize DB
    conn = init_db()  # Creates tables if not exist, returns ready connection
    conn.execute(
        "INSERT INTO run_log (started_at, status) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), "running"),
    )
    run_log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    try:
        # Health check before starting
        hc_results = health_check(config)
        critical_failed = [CRITICAL_SOURCES.get(k, k) for k, v in hc_results.items() if not v]
        if critical_failed:
            logger.error("Health check FAILED — skipping pipeline: %s", ", ".join(critical_failed))
            send_alert(f"Pipeline dibatalkan — health check gagal: {', '.join(critical_failed)}")
            status = "aborted"
            return {
                "run_id": run_id, "status": status,
                "pairs_analyzed": 0, "signals_generated": 0, "duration_seconds": 0,
            }

        # Phase 0: Identity resolution — fetch top coins
        logger.info("Pipeline %s starting — fetching top %d coins", run_id, config.top_coins_limit)
        symbols = ["BTC-USDT", "ETH-USDT"]  # Placeholder (Epic 2+ will fetch real top coins)
        pairs_analyzed = len(symbols)

        # Stage execution with artifact directories
        pipeline_dir = Path("data") / "pipeline" / run_id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        for idx, (stage_key, stage_name) in enumerate(STAGES, 1):
            elapsed = time.monotonic() - start_time
            warning_threshold = (config.runtime_budget_minutes - 5) * 60
            if elapsed > warning_threshold:
                logger.warning("Pipeline approaching timeout — %.0f min elapsed", elapsed / 60)

            if elapsed > config.runtime_budget_minutes * 60:
                logger.error("Pipeline timeout — terminating at stage: %s", stage_name)
                status = "timeout"
                break

            stage_dir = pipeline_dir / f"stage_{idx}_{stage_key}"
            stage_dir.mkdir(parents=True, exist_ok=True)

            try:
                logger.info("Running %s (artifacts → %s)", stage_name, stage_dir)
                # Stage implementations
                if stage_key == "data_fetch":
                    from src.exchange import fetch_ohlcv
                    for sym in symbols:
                        fetch_ohlcv(sym, force_refresh=True, max_age_hours=config.freshness_max_hours)
                elif stage_key == "profile_match":
                    from src.indicators import load_with_indicators
                    from src.pipeline.stage_2_profile import find_best_strategy
                    for sym in symbols:
                        try:
                            df = load_with_indicators(f"data/ohlcv/{sym}.parquet")
                            ind = {k: df[k] for k in df.columns if k not in ("timestamp","open","high","low","close","volume")}
                            find_best_strategy(df, ind, sym, config.min_win_rate, config.min_sharpe)
                        except Exception:
                            logger.warning("Profile match failed for %s", sym)
                elif stage_key == "research_context":
                    from src.research import (
                        fetch_sentiment_composite, fetch_whale_transactions,
                        fetch_coingecko_active_addresses, compute_onchain,
                        macro_flag_for_date, fetch_polymarket,
                        polymarket_prediction_adjustment, polymarket_is_fresh,
                    )
                    from src.research_scoring import (
                        compute_research_multiplier, apply_research_to_confidence,
                        sentiment_mult, onchain_mult,
                    )
                    sentiment = fetch_sentiment_composite()
                    whale = fetch_whale_transactions()
                    polymarket = fetch_polymarket()
                    poly_fresh = polymarket_is_fresh()

                    # Check if all research sources failed
                    all_down = (
                        sentiment.get("active_sources", 0) == 0 and
                        whale is None and
                        polymarket is None
                    )
                    if all_down:
                        logger.warning("Research data unavailable — using technical confidence only")
                        send_alert("⚠️ Research data unavailable — signals using technical confidence only")

                    for sym in symbols:
                        active_addr = fetch_coingecko_active_addresses(sym)
                        onchain_signal, _ = compute_onchain(whale, active_addr, sym)
                        has_macro, macro_pen, macro_warning = macro_flag_for_date()
                        # Prediction adjustment (reduced if stale per AC4)
                        pred_adj = polymarket_prediction_adjustment(sym, polymarket)
                        if not poly_fresh and polymarket is not None:
                            pred_adj = pred_adj * 0.5  # Reduce weight when stale
                            logger.info("Polymarket stale — prediction adj reduced to %+.2f", pred_adj)
                        multiplier = compute_research_multiplier(
                            sentiment_score=sentiment.get("composite"),
                            onchain_signal=onchain_signal,
                            macro_has_event=has_macro,
                            macro_penalty=macro_pen,
                            prediction_adjustment=pred_adj,
                        )
                        # Store for downstream stages
                        research_results[sym] = {
                            "sentiment_score": sentiment.get("composite"),
                            "sentiment_mult": sentiment_mult(sentiment.get("composite")),
                            "onchain_signal": onchain_signal,
                            "onchain_mult": onchain_mult(onchain_signal),
                            "macro_flag": has_macro,
                            "macro_penalty": macro_pen,
                            "final_multiplier": multiplier,
                            "prediction_adjustment": pred_adj,
                        }
                        logger.info(
                            "Research %s: sentiment=%.0f onchain=%s macro=%s → multiplier=%.2f",
                            sym, sentiment.get("composite", 50), onchain_signal,
                            "yes" if has_macro else "no", multiplier,
                        )
                elif stage_key == "confidence_filter":
                    import json
                    from src.pipeline.stage_4_confidence import (
                        generate_signal, filter_signals, save_signals, Signal,
                    )
                    signals_list = []
                    for sym in symbols:
                        rr = research_results.get(sym, {})
                        s = Signal(
                            id=f"sig-{sym}-{datetime.now(timezone.utc).strftime('%H%M%S')}",
                            symbol=sym, action="HOLD", confidence=0.55,
                            entry_price=50000, stop_loss=49000, take_profit=52000,
                            strategy="pipeline",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            sentiment_score=rr.get("sentiment_score"),
                            onchain_signal=rr.get("onchain_signal"),
                            macro_flag=rr.get("macro_flag", False),
                            research_metadata=json.dumps(rr) if rr else None,
                        )
                        signals_list.append(s)
                    filtered = filter_signals(signals_list, config.min_confidence, config.max_signals_per_day)
                    signals_generated = save_signals(filtered)
                    logger.info("Confidence filter: %d signals, %d passed", len(signals_list), signals_generated)
                elif stage_key == "telegram_deliver":
                    from src.telegram_sender import send_daily_signals, format_daily_message
                    signals_list = []
                    for sym in symbols:
                        from src.pipeline.stage_4_confidence import Signal
                        rr = research_results.get(sym, {})
                        s = Signal(
                            id=f"sig-{sym}", symbol=sym,
                            action="BUY" if rr.get("final_multiplier", 1.0) > 1.0 else "SELL",
                            confidence=0.65, entry_price=50000, stop_loss=49000,
                            take_profit=52000, strategy="pipeline",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            sentiment_score=rr.get("sentiment_score"),
                            onchain_signal=rr.get("onchain_signal"),
                            macro_flag=rr.get("macro_flag", False),
                            research_metadata=json.dumps(rr) if rr else None,
                        )
                        signals_list.append(s)
                    send_daily_signals(signals_list, pairs_analyzed, None, include_research=True)
            except Exception as e:
                logger.error("%s failed: %s", stage_name, e, exc_info=True)
                stage_failed = idx
                error_summary = str(e)[:200]
                status = "failed"
                send_alert(f"Pipeline gagal di {stage_name}: {str(e)[:100]}")
                break

        status = status if status != "running" else "completed"

    except Exception as e:
        logger.error("Pipeline crashed: %s\n%s", e, traceback.format_exc())
        status = "failed"
        error_summary = str(e)[:200]
        send_alert(f"Pipeline crash: {str(e)[:100]}")

    finally:
        duration = time.monotonic() - start_time
        # Update run_log
        try:
            conn = get_connection()
            conn.execute(
                """UPDATE run_log SET completed_at=?, pairs_analyzed=?,
                   signals_generated=?, duration_seconds=?, status=?,
                   stage_failed=?, error_summary=?
                   WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), pairs_analyzed,
                 signals_generated, round(duration, 1), status,
                 stage_failed, error_summary, run_log_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.error("Failed to update run_log")

        # Completion message
        if status == "completed":
            send_alert(f"✅ Pipeline selesai — {pairs_analyzed} pair dianalisa, {signals_generated} sinyal dalam {duration:.0f}s")
        elif status == "timeout":
            send_alert(f"⏰ Pipeline timeout — hasil parsial tersedia ({pairs_analyzed} pair)")

        logger.info("Pipeline %s finished: status=%s duration=%.0fs", run_id, status, duration)

    return {
        "run_id": run_id,
        "status": status,
        "pairs_analyzed": pairs_analyzed,
        "signals_generated": signals_generated,
        "duration_seconds": round(duration, 1),
    }


def start_scheduler():
    """Start the APScheduler for nightly pipeline execution."""
    from apscheduler.schedulers.background import BackgroundScheduler

    config = load_config()
    hour, minute = map(int, config.cron_time_utc.split(":"))

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_pipeline,
        "cron",
        hour=hour,
        minute=minute,
        id="nightly_pipeline",
        name="Trading Signal Pipeline",
    )

    scheduler.start()
    logger.info("Scheduler started — pipeline will run daily at %02d:%02d UTC", hour, minute)

    # Run health check on startup
    results = health_check(config)
    failed = [CRITICAL_SOURCES.get(k, k) for k, v in results.items() if not v]
    if failed:
        logger.error("Health check FAILED: %s", ", ".join(failed))
        send_alert(f"Health check gagal: {', '.join(failed)}")
    else:
        logger.info("Health check passed — all critical sources reachable")

    return scheduler


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("Trading Signal Pipeline starting...")
    init_db()  # Ensure tables exist before pipeline runs
    run_pipeline()
