"""Main pipeline orchestrator — nightly batch runner with scheduling."""

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

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

# Health check tiers: critical failures → abort; non-critical → warn + continue
CRITICAL_SOURCES = {
    "ccxt": "Exchange data (CCXT)",
    "telegram": "Telegram bot token",
}
NON_CRITICAL_SOURCES = {
    "alternative_me": "Fear & Greed Index (Alternative.me)",
}


def health_check(config: Settings) -> dict[str, bool]:
    """Verify data sources are reachable before pipeline start.

    Returns:
        Dict mapping source name to True (reachable) or False (failed).
    """
    results = {}

    # CCXT check
    try:
        import ccxt
        exchange = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
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
    """Send a critical alert to Telegram. Synchronous, best-effort.

    Uses raw HTTP POST (no asyncio) so it works from any thread,
    including the APScheduler worker.
    """
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not bot_token or not chat_id:
            return False
        import requests
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": f"⚠️ Pipeline Alert\n{message}"},
            timeout=10,
        )
        return resp.status_code == 200
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

    # Inter-stage shared state
    pair_results: dict = {}      # symbol → {best_strategy, best_result, profile}
    research_results: dict = {}  # symbol → {sentiment, onchain, macro, multiplier}
    filtered_signals: list = []  # Signals after filter (populated in stage 4)

    # Initialize DB
    conn = init_db()
    try:
        conn.execute(
            "INSERT INTO run_log (started_at, status) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), "running"),
        )
        run_log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    try:
        # ── Health check ──────────────────────────────────────────
        hc_results = health_check(config)
        critical_failed = [CRITICAL_SOURCES.get(k, k) for k, v in hc_results.items()
                          if k in CRITICAL_SOURCES and not v]
        non_critical_failed = [NON_CRITICAL_SOURCES.get(k, k) for k, v in hc_results.items()
                              if k in NON_CRITICAL_SOURCES and not v]

        if critical_failed:
            logger.error("Health check FAILED — skipping pipeline: %s", ", ".join(critical_failed))
            send_alert(f"Pipeline dibatalkan — health check gagal: {', '.join(critical_failed)}")
            status = "aborted"
            return {
                "run_id": run_id, "status": status,
                "pairs_analyzed": 0, "signals_generated": 0, "duration_seconds": 0,
            }
        if non_critical_failed:
            logger.warning(
                "Non-critical sources unavailable: %s — continuing pipeline",
                ", ".join(non_critical_failed),
            )

        # ── Phase 0: Identity resolution ──────────────────────────
        logger.info("Pipeline %s starting — fetching top %d coins", run_id, config.top_coins_limit)
        symbols = ["BTC-USDT", "ETH-USDT"]  # Placeholder (Epic 2+ will fetch real top coins)
        pairs_analyzed = len(symbols)

        # Pipeline artifact root
        pipeline_dir = Path("data") / "pipeline" / run_id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        # ── Stage execution ───────────────────────────────────────
        for idx, (stage_key, stage_name) in enumerate(STAGES, 1):
            elapsed = time.monotonic() - start_time
            warning_threshold = (config.runtime_budget_minutes - 5) * 60
            if elapsed > warning_threshold:
                logger.warning("Pipeline approaching timeout — %.0f min elapsed", elapsed / 60)

            if elapsed > config.runtime_budget_minutes * 60:
                logger.error("Pipeline timeout — terminating at stage: %s", stage_name)
                status = "timeout"
                # Deliver whatever signals we have so far
                if filtered_signals:
                    from src.telegram_sender import send_daily_signals
                    send_daily_signals(filtered_signals, pairs_analyzed, None, include_research=True)
                    send_alert(f"⏰ Pipeline timeout — {len(filtered_signals)} sinyal dikirim (hasil parsial)")
                else:
                    send_alert(f"⏰ Pipeline timeout — tidak ada sinyal tersedia untuk {pairs_analyzed} pair")
                break

            stage_dir = pipeline_dir / f"stage_{idx}_{stage_key}"
            stage_dir.mkdir(parents=True, exist_ok=True)

            try:
                logger.info("Running %s (artifacts → %s)", stage_name, stage_dir)

                # ── Stage 1: Data Fetch ───────────────────────────
                if stage_key == "data_fetch":
                    from src.exchange import fetch_ohlcv
                    from src.indicators import save_with_indicators
                    for sym in symbols:
                        try:
                            df = fetch_ohlcv(sym, force_refresh=True,
                                            max_age_hours=config.freshness_max_hours)
                            # Compute indicators and save OHLCV + indicators in one file
                            save_with_indicators(df, f"data/ohlcv/{sym}.parquet")
                            logger.info("Fetched + indicators saved: %s (%d rows)", sym, len(df))
                        except Exception as e:
                            logger.error("Data fetch failed for %s: %s", sym, e)

                # ── Stage 2: Profile + Strategy Match ──────────────
                elif stage_key == "profile_match":
                    from src.indicators import load_with_indicators
                    from src.pipeline.stage_2_profile import find_best_strategy
                    for sym in symbols:
                        try:
                            df = load_with_indicators(f"data/ohlcv/{sym}.parquet")
                            indicator_keys = [k for k in df.columns
                                            if k not in ("timestamp", "open", "high", "low", "close", "volume")]
                            ind = {k: df[k] for k in indicator_keys}
                            best_strategy, best_result, all_results = find_best_strategy(
                                df, ind, sym, config.min_win_rate, config.min_sharpe,
                                walk_forward_enabled=config.walk_forward_enabled,
                            )
                            pair_results[sym] = {
                                "best_strategy": best_strategy,
                                "best_result": best_result,
                                "all_results": all_results,
                                "ohlcv": df,
                                "indicators": ind,
                            }
                        except Exception:
                            logger.warning("Profile match failed for %s", sym, exc_info=True)

                    # Write stage artifact
                    stage_summary = {
                        sym: {
                            "strategy": pr["best_strategy"].name if pr["best_strategy"] else None,
                            "win_rate": pr["best_result"].win_rate if pr["best_result"] else None,
                            "passed": pr["best_result"].passed if pr["best_result"] else False,
                        }
                        for sym, pr in pair_results.items()
                    }
                    (stage_dir / "profile_match.json").write_text(json.dumps(stage_summary, indent=2))

                # ── Stage 3: Research Context ──────────────────────
                elif stage_key == "research_context":
                    from src.research import (
                        fetch_sentiment_composite, fetch_whale_transactions,
                        fetch_coingecko_active_addresses, compute_onchain,
                        macro_flag_for_date, fetch_polymarket,
                        polymarket_prediction_adjustment, polymarket_is_fresh,
                    )
                    from src.research_scoring import (
                        compute_research_multiplier,
                        sentiment_mult, onchain_mult,
                    )

                    sentiment = fetch_sentiment_composite()
                    whale = fetch_whale_transactions()
                    polymarket = fetch_polymarket()
                    poly_fresh = polymarket_is_fresh()

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
                        pred_adj = polymarket_prediction_adjustment(sym, polymarket)
                        if not poly_fresh and polymarket is not None:
                            pred_adj = pred_adj * 0.5
                            logger.info("Polymarket stale — prediction adj reduced to %+.2f", pred_adj)
                        multiplier = compute_research_multiplier(
                            sentiment_score=sentiment.get("composite"),
                            onchain_signal=onchain_signal,
                            macro_has_event=has_macro,
                            macro_penalty=macro_pen,
                            prediction_adjustment=pred_adj,
                        )
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

                    # Write stage artifact
                    (stage_dir / "research_context.json").write_text(
                        json.dumps({sym: {k: str(v) if isinstance(v, float) else v
                                          for k, v in rr.items()}
                                    for sym, rr in research_results.items()}, indent=2))

                # ── Stage 4: Confidence + Filter ───────────────────
                elif stage_key == "confidence_filter":
                    from src.pipeline.stage_4_confidence import (
                        generate_signal, filter_signals, save_signals,
                    )
                    signals_list = []

                    for sym in symbols:
                        pr = pair_results.get(sym)
                        rr = research_results.get(sym, {})
                        if pr is None or pr["best_strategy"] is None:
                            logger.info("%s: no strategy passed — skipping signal generation", sym)
                            continue

                        strategy = pr["best_strategy"]
                        best_result = pr["best_result"]
                        indicators = pr["indicators"]
                        ohlcv_df = pr["ohlcv"]
                        entry_price = float(ohlcv_df["close"].iloc[-1])
                        atr_val = float(indicators.get("atr_14", pd.Series([0])).iloc[-1])

                        try:
                            strategy_signal = strategy.evaluate(ohlcv_df, indicators)
                        except Exception as e:
                            logger.warning("%s strategy eval failed: %s", sym, e)
                            continue

                        if strategy_signal.action == "HOLD":
                            continue

                        signal = generate_signal(
                            symbol=sym,
                            action=strategy_signal.action,
                            entry_price=entry_price,
                            atr_14=atr_val if not pd.isna(atr_val) else 0.0,
                            strategy_signal=strategy_signal,
                            backtest_result=best_result,
                            sl_mult=config.atr_sl_multiplier,
                            tp_mult=config.atr_tp_multiplier,
                        )

                        # Attach research metadata
                        signal.sentiment_score = rr.get("sentiment_score")
                        signal.onchain_signal = rr.get("onchain_signal")
                        signal.macro_flag = rr.get("macro_flag", False)
                        signal.research_metadata = json.dumps(rr) if rr else None

                        signals_list.append(signal)

                    # Filter + save
                    filtered_signals = filter_signals(
                        signals_list,
                        config.min_confidence,
                        config.max_signals_per_day,
                        cooldown_hours=config.cooldown_hours,
                        cooldown_override=config.cooldown_override_confidence,
                    )
                    signals_generated = save_signals(filtered_signals)
                    logger.info("Confidence filter: %d generated, %d passed, %d saved",
                                len(signals_list), len(filtered_signals), signals_generated)

                    # Write stage artifact
                    (stage_dir / "signals.json").write_text(json.dumps(
                        [{"id": s.id, "symbol": s.symbol, "action": s.action,
                          "confidence": s.confidence, "entry_price": s.entry_price,
                          "strategy": s.strategy}
                         for s in filtered_signals], indent=2))

                # ── Stage 5: Telegram Delivery ─────────────────────
                elif stage_key == "telegram_deliver":
                    from src.telegram_sender import send_daily_signals

                    if not filtered_signals:
                        logger.warning("No filtered signals available — sending empty report")
                    success = send_daily_signals(
                        filtered_signals, pairs_analyzed, None,
                        include_research=True,
                    )
                    if not success:
                        logger.error("Telegram delivery failed — signals saved to DB only")

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
            send_alert(f"⏰ Pipeline timeout — hasil parsial dikirim ({pairs_analyzed} pair, {signals_generated} sinyal)")

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
    critical_failed = [CRITICAL_SOURCES.get(k, k) for k, v in results.items()
                      if k in CRITICAL_SOURCES and not v]
    non_critical_failed = [NON_CRITICAL_SOURCES.get(k, k) for k, v in results.items()
                          if k in NON_CRITICAL_SOURCES and not v]
    if critical_failed:
        logger.error("Health check FAILED: %s", ", ".join(critical_failed))
        send_alert(f"Health check gagal: {', '.join(critical_failed)}")
    else:
        status_msg = "all sources reachable"
        if non_critical_failed:
            status_msg += f" (non-critical down: {', '.join(non_critical_failed)})"
        logger.info("Health check passed — %s", status_msg)

    return scheduler


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("Trading Signal Pipeline starting...")
    # init_db only once to ensure tables exist; run_pipeline calls its own init
    init_db().close()
    run_pipeline()
