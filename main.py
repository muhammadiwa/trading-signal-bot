"""Main pipeline orchestrator — nightly batch runner with scheduling."""

import logging
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
    conn = get_connection()
    conn.execute(
        "INSERT INTO run_log (started_at, status) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), "running"),
    )
    run_log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    try:
        # Phase 0: Identity resolution — fetch top coins
        logger.info("Pipeline %s starting — fetching top %d coins", run_id, config.top_coins_limit)
        # (Implementation deferred to Epic 2 — stub for now)
        symbols = ["BTC-USDT", "ETH-USDT"]  # Placeholder
        pairs_analyzed = len(symbols)

        # Stage execution
        pipeline_dir = Path("data") / "pipeline" / run_id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        for stage_key, stage_name in STAGES:
            elapsed = time.monotonic() - start_time
            if elapsed > config.runtime_budget_minutes * 55:  # 5 min warning
                logger.warning("Pipeline approaching timeout — %.0f min elapsed", elapsed / 60)

            if elapsed > config.runtime_budget_minutes * 60:
                logger.error("Pipeline timeout — terminating at stage: %s", stage_name)
                status = "timeout"
                break

            try:
                logger.info("Running %s", stage_name)
                # (Stage implementations connected in Epic 1 completion)
                time.sleep(0.1)  # Stub — replace with actual stage function calls
            except Exception as e:
                logger.error("%s failed: %s", stage_name, e)
                stage_failed = STAGES.index((stage_key, stage_name)) + 1
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
    run_pipeline()
