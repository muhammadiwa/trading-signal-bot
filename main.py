"""Main pipeline orchestrator — nightly batch runner with scheduling.

Multi-timeframe support (1h, 4h, 1d) — each timeframe generates independent
signals so the user sees which interval the signal targets: scalp (1h),
swing (4h), or trend (1d).
"""

import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from src.config import load_config, Settings
from src.db import init_db, get_connection

logger = logging.getLogger(__name__)

# Timeframes to analyze (ordered: longest first for cache reuse)
TIME_FRAMES = ["1d", "4h", "1h"]

# Stage pipeline definition
STAGES = [
    ("outcome_resolution", "Stage 0: Outcome Resolution"),
    ("data_fetch", "Stage 1: Data Fetch"),
    ("profile_match", "Stage 2: Profile + Strategy Match"),
    ("research_context", "Stage 3: Research Context"),
    ("confidence_filter", "Stage 4: Confidence + Filter"),
    ("telegram_deliver", "Stage 5: Telegram Delivery"),
]

# Accumulated 7-day win rate — set by Stage 0, consumed by Stage 5
_win_rate_7d_cache: Optional[float] = None

CRITICAL_SOURCES = {"ccxt": "Exchange data (CCXT)", "telegram": "Telegram bot token"}
NON_CRITICAL_SOURCES = {"alternative_me": "Fear & Greed Index (Alternative.me)"}


def health_check(config: Settings) -> dict[str, bool]:
    """Verify data sources are reachable before pipeline start."""
    results = {}
    try:
        import ccxt
        exchange = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
        exchange.fetch_ticker("BTC/USDT")
        exchange.close()
        results["ccxt"] = True
    except Exception as e:
        logger.error("CCXT health check failed: %s", e)
        results["ccxt"] = False
    try:
        import requests
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        results["alternative_me"] = resp.status_code == 200
    except Exception as e:
        logger.error("Alternative.me health check failed: %s", e)
        results["alternative_me"] = False
    results["telegram"] = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    return results


def send_alert(message: str) -> bool:
    """Send critical alert to Telegram via sync HTTP (works from any thread)."""
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not bot_token or not chat_id:
            return False
        import requests
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id, "text": f"⚠️ Pipeline Alert\n{message}"}, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error("Failed to send alert: %s", e)
    return False


def fetch_top_symbols(config: Settings) -> list[str]:
    """Fetch top coin symbols from CoinGecko public API.

    Falls back to hardcoded default if API fails.
    """
    try:
        import requests
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": config.top_coins_limit,
            "page": 1,
            "sparkline": "false",
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        symbols = []
        for coin in data:
            symbol = coin.get("symbol", "").upper()
            if not symbol:
                continue
            # Exclude stablecoins if configured
            if config.top_coins_exclude_stablecoins and symbol in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP"):
                continue
            symbols.append(f"{symbol}-USDT")
        logger.info("Fetched %d top coins from CoinGecko", len(symbols))
        return symbols
    except Exception as e:
        logger.warning("CoinGecko trending API failed: %s — using default symbols", e)
        return ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
                "ADA-USDT", "DOGE-USDT", "DOT-USDT", "AVAX-USDT", "MATIC-USDT"]


def run_pipeline(config: Optional[Settings] = None) -> dict:
    """Execute the full multi-timeframe nightly pipeline."""
    global _win_rate_7d_cache
    if config is None:
        config = load_config()

    start_time = time.monotonic()
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
    pairs_analyzed = 0
    signals_generated = 0
    status = "running"
    stage_failed = None
    error_summary = None

    # Inter-stage state
    # Key: "sym|tf" → {best_strategy, best_result, ohlcv, indicators}
    pair_results: dict = {}
    research_results: dict = {}
    filtered_signals: list = []

    # DB init
    conn = init_db()
    try:
        conn.execute("INSERT INTO run_log (started_at, status) VALUES (?, ?)",
                     (datetime.now(timezone.utc).isoformat(), "running"))
        run_log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    try:
        # ── Health check ──────────────────────────────────────────
        skip_health = os.getenv("SKIP_HEALTH_CHECK", "").lower() in ("1", "true", "yes")
        if skip_health:
            logger.warning("SKIP_HEALTH_CHECK set — bypassing health checks")
            hc = {"ccxt": True, "alternative_me": True, "telegram": True}
        else:
            hc = health_check(config)
        critical_failed = [CRITICAL_SOURCES.get(k, k) for k, v in hc.items()
                          if k in CRITICAL_SOURCES and not v]
        non_critical_failed = [NON_CRITICAL_SOURCES.get(k, k) for k, v in hc.items()
                              if k in NON_CRITICAL_SOURCES and not v]
        if critical_failed:
            logger.error("Health check FAILED: %s", ", ".join(critical_failed))
            send_alert(f"Pipeline dibatalkan — health check gagal: {', '.join(critical_failed)}")
            return {"run_id": run_id, "status": "aborted",
                    "pairs_analyzed": 0, "signals_generated": 0, "duration_seconds": 0}
        if non_critical_failed:
            logger.warning("Non-critical down: %s — continuing", ", ".join(non_critical_failed))

        # ── Phase 0: Identity resolution ──────────────────────────
        symbols = fetch_top_symbols(config)
        pairs_analyzed = len(symbols)
        logger.info("Pipeline %s: %d pairs × %d timeframes", run_id, pairs_analyzed, len(TIME_FRAMES))

        pipeline_dir = Path("data") / "pipeline" / run_id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        # ── Stage execution ───────────────────────────────────────
        for idx, (stage_key, stage_name) in enumerate(STAGES, 1):
            elapsed = time.monotonic() - start_time
            warning_threshold = (config.runtime_budget_minutes - 5) * 60
            if elapsed > warning_threshold:
                logger.warning("Pipeline approaching timeout — %.0f min", elapsed / 60)
            if elapsed > config.runtime_budget_minutes * 60:
                logger.error("Pipeline timeout at stage: %s", stage_name)
                status = "timeout"
                if filtered_signals:
                    from src.telegram_sender import send_daily_signals
                    send_daily_signals(filtered_signals, pairs_analyzed, None, include_research=True)
                break

            stage_dir = pipeline_dir / f"stage_{idx}_{stage_key}"
            stage_dir.mkdir(parents=True, exist_ok=True)

            try:
                logger.info("Running %s", stage_name)

                # ── Stage 0: Outcome Resolution ────────────────────
                if stage_key == "outcome_resolution":
                    from src.outcome_tracker import resolve_pending_signals
                    resolved = resolve_pending_signals()
                    logger.info("Outcome resolution complete: %d signals resolved", len(resolved))
                    _win_rate_7d_cache = _compute_7day_win_rate()

                    # Generate LLM reflections for resolved outcomes (Story 3.2)
                    if resolved:
                        from src.reflection import generate_reflections
                        api_key = os.getenv("TOKENROUTER_API_KEY", "")
                        base_url = os.getenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1")
                        reflections = generate_reflections(
                            resolved, api_key, base_url,
                            model=config.llm_model,
                            timeout=config.llm_timeout_seconds,
                            max_tokens=config.llm_max_tokens,
                        )
                        llm_count = sum(1 for r in reflections if r.get("llm_used"))
                        logger.info("Reflections: %d LLM + %d fallback = %d total",
                                    llm_count, len(reflections) - llm_count, len(reflections))

                    # Adjust research weights based on outcomes (Story 3.3)
                    from src.weight_adjuster import adjust_weights
                    adjusted = adjust_weights(send_alert_fn=send_alert)
                    if adjusted:
                        logger.info("Weights adjusted: %s",
                                    ", ".join(f"{k}={v:.4f}" for k, v in adjusted.items()))

                # ── Stage 1: Data Fetch (multi-timeframe) ──────────
                elif stage_key == "data_fetch":
                    from src.exchange import fetch_ohlcv
                    from src.indicators import save_with_indicators
                    for sym in symbols:
                        for tf in TIME_FRAMES:
                            try:
                                df = fetch_ohlcv(sym, timeframe=tf, force_refresh=True,
                                                max_age_hours=config.freshness_max_hours)
                                save_with_indicators(df, f"data/ohlcv/{sym}-{tf}.parquet")
                                logger.debug("Fetched %s %s (%d rows)", sym, tf, len(df))
                            except Exception as e:
                                logger.error("Fetch failed %s %s: %s", sym, tf, e)

                # ── Stage 2: Profile + Strategy Match ──────────────
                elif stage_key == "profile_match":
                    from src.indicators import load_with_indicators
                    from src.pipeline.stage_2_profile import find_best_strategy
                    for sym in symbols:
                        for tf in TIME_FRAMES:
                            key = f"{sym}|{tf}"
                            try:
                                df = load_with_indicators(f"data/ohlcv/{sym}-{tf}.parquet")
                                indicator_keys = [k for k in df.columns
                                                if k not in ("timestamp", "open", "high", "low", "close", "volume")]
                                ind = {k: df[k] for k in indicator_keys}
                                best_s, best_r, all_r = find_best_strategy(
                                    df, ind, sym, config.min_win_rate, config.min_sharpe,
                                    walk_forward_enabled=config.walk_forward_enabled,
                                )
                                pair_results[key] = {
                                    "best_strategy": best_s, "best_result": best_r,
                                    "all_results": all_r, "ohlcv": df, "indicators": ind,
                                }
                            except Exception:
                                logger.warning("Profile match failed: %s %s", sym, tf)

                # ── Stage 3: Research Context ──────────────────────
                elif stage_key == "research_context":
                    from src.research import (
                        fetch_sentiment_composite, fetch_whale_transactions,
                        fetch_coingecko_active_addresses, compute_onchain,
                        macro_flag_for_date, fetch_polymarket,
                        polymarket_prediction_adjustment, polymarket_is_fresh,
                    )
                    from src.research_scoring import (
                        compute_research_multiplier, sentiment_mult, onchain_mult,
                    )
                    sentiment = fetch_sentiment_composite()
                    whale = fetch_whale_transactions()
                    polymarket = fetch_polymarket()
                    poly_fresh = polymarket_is_fresh()
                    # Determine if research is globally unavailable (for AC2.5 all-defaulted)
                    research_unavailable = (sentiment.get("active_sources", 0) == 0 and whale is None and polymarket is None)
                    if research_unavailable:
                        send_alert("⚠️ Research data unavailable — signals using technical confidence only")

                    # Call macro_flag_for_date ONCE (global, not per-symbol)
                    has_macro, macro_pen, _ = macro_flag_for_date()

                    for sym in symbols:
                        active_addr = fetch_coingecko_active_addresses(sym)
                        onchain_signal, _ = compute_onchain(whale, active_addr, sym)
                        pred_adj = polymarket_prediction_adjustment(sym, polymarket)
                        if not poly_fresh and polymarket is not None:
                            pred_adj = pred_adj * 0.5
                        multiplier = compute_research_multiplier(
                            sentiment_score=sentiment.get("composite"),
                            onchain_signal=onchain_signal,
                            macro_has_event=has_macro, macro_penalty=macro_pen,
                            prediction_adjustment=pred_adj,
                        )
                        research_results[sym] = {
                            "sentiment_score": sentiment.get("composite"),
                            "sentiment_mult": sentiment_mult(sentiment.get("composite")),
                            "onchain_signal": onchain_signal,
                            "onchain_mult": onchain_mult(onchain_signal),
                            "macro_flag": has_macro, "macro_penalty": macro_pen,
                            "final_multiplier": multiplier,
                            "prediction_adjustment": pred_adj,
                            "research_unavailable": research_unavailable,
                        }

                # ── Stage 4: Confidence + Filter ───────────────────
                elif stage_key == "confidence_filter":
                    from src.pipeline.stage_4_confidence import (
                        generate_signal, filter_signals, save_signals, compute_counter_metrics,
                    )
                    signals_list = []
                    for sym in symbols:
                        pr_result = None
                        # Multi-timeframe confirmation: prefer 1d → 4h → 1h
                        for tf in TIME_FRAMES:
                            key = f"{sym}|{tf}"
                            if key in pair_results and pair_results[key]["best_strategy"] is not None:
                                pr_result = pair_results[key]
                                pr_tf = tf
                                break
                        if pr_result is None:
                            continue

                        rr = research_results.get(sym, {})
                        strategy = pr_result["best_strategy"]
                        best_result = pr_result["best_result"]
                        ind = pr_result["indicators"]
                        ohlcv_df = pr_result["ohlcv"]
                        entry_price = float(ohlcv_df["close"].iloc[-1])
                        atr_14 = float(ind.get("atr_14", pd.Series([0])).iloc[-1])
                        atr_50_val = float(ind.get("atr_50", pd.Series([0])).iloc[-1]) if "atr_50" in ind else 0.0

                        try:
                            sig = strategy.evaluate(ohlcv_df, ind)
                        except Exception as e:
                            logger.warning("%s strat eval failed: %s", sym, e)
                            continue
                        if sig.action == "HOLD":
                            continue

                        signal = generate_signal(
                            symbol=sym, action=sig.action, entry_price=entry_price,
                            atr_14=atr_14 if not pd.isna(atr_14) else 0.0,
                            atr_50=atr_50_val if not pd.isna(atr_50_val) else 0.0,
                            strategy_signal=sig, backtest_result=best_result,
                            timeframe=pr_tf,
                            sl_mult=config.atr_sl_multiplier, tp_mult=config.atr_tp_multiplier,
                        )
                        signal.sentiment_score = rr.get("sentiment_score")
                        signal.onchain_signal = rr.get("onchain_signal")
                        signal.macro_flag = rr.get("macro_flag", False)
                        signal.research_metadata = json.dumps(rr) if rr else None

                        # Apply research multiplier to technical confidence (Story 2.5 AC3)
                        final_mult = rr.get("final_multiplier", 1.0)
                        signal.confidence = round(max(0.0, min(1.0, signal.confidence * final_mult)), 4)
                        if rr and not rr.get("research_unavailable", False):
                            logger.debug("%s: confidence %.0f%% × research %.2f → %.0f%%",
                                        sym, signal.confidence / final_mult * 100,
                                        final_mult, signal.confidence * 100)

                        signals_list.append(signal)

                    # Counter-metrics
                    metrics = compute_counter_metrics(signals_list)
                    for w in metrics.get("warnings", []):
                        logger.warning("Counter-metric: %s", w)

                    filtered_signals = filter_signals(
                        signals_list, config.min_confidence, config.max_signals_per_day,
                        cooldown_hours=config.cooldown_hours,
                        cooldown_override=config.cooldown_override_confidence,
                    )
                    signals_generated = save_signals(filtered_signals)
                    logger.info("Signals: %d generated, %d passed, %d saved | avg_conf=%.0f%%",
                                len(signals_list), len(filtered_signals), signals_generated,
                                metrics.get("avg_confidence", 0) * 100)

                    # Write stage artifact
                    (stage_dir / "metrics.json").write_text(json.dumps({
                        **metrics, "signals_generated": len(signals_list),
                        "signals_passed": len(filtered_signals),
                    }, indent=2))

                # ── Stage 5: Telegram Delivery ─────────────────────
                elif stage_key == "telegram_deliver":
                    from src.telegram_sender import send_daily_signals
                    success = send_daily_signals(filtered_signals, pairs_analyzed,
                                                _win_rate_7d_cache, include_research=True)
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
        # Use cached 7-day win rate from Stage 0, or compute fresh
        win_rate_7d = _win_rate_7d_cache if _win_rate_7d_cache is not None else _compute_7day_win_rate()
        try:
            conn = get_connection()
            conn.execute(
                """UPDATE run_log SET completed_at=?, pairs_analyzed=?,
                   signals_generated=?, duration_seconds=?, status=?,
                   stage_failed=?, error_summary=?, win_rate_7d=?
                   WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), pairs_analyzed,
                 signals_generated, round(duration, 1), status,
                 stage_failed, error_summary, win_rate_7d, run_log_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.error("Failed to update run_log")

        if status == "completed":
            send_alert(f"✅ Pipeline selesai — {pairs_analyzed} pair, {signals_generated} sinyal dalam {duration:.0f}s")
        elif status == "timeout":
            send_alert(f"⏰ Pipeline timeout — hasil parsial dikirim ({signals_generated} sinyal)")

        logger.info("Pipeline %s finished: status=%s duration=%.0fs", run_id, status, duration)

        # ── Saturday weekly digest ─────────────────────────────────
        today = datetime.now(timezone.utc)
        if today.weekday() == 5:  # Saturday
            try:
                _send_weekly_digest()
            except Exception as e:
                logger.error("Weekly digest failed: %s", e)

    return {"run_id": run_id, "status": status,
            "pairs_analyzed": pairs_analyzed,
            "signals_generated": signals_generated,
            "duration_seconds": round(duration, 1)}


def _compute_7day_win_rate() -> Optional[float]:
    """Compute rolling 7-day signal win rate from outcomes table."""
    try:
        conn = get_connection()
        row = conn.execute(
            """SELECT AVG(CASE WHEN realized_return_pct > 0 THEN 1 ELSE 0 END) AS wr
               FROM outcomes WHERE resolved_at > ?""",
            ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),),
        ).fetchone()
        conn.close()
        return round(row["wr"], 4) if row and row["wr"] is not None else None
    except Exception:
        return None


def _send_weekly_digest() -> None:
    """Send Saturday morning weekly performance digest."""
    try:
        conn = get_connection()
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()

        # Weekly stats
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM outcomes WHERE resolved_at > ?", (week_ago,)
        ).fetchone()
        wins = conn.execute(
            "SELECT COUNT(*) AS n FROM outcomes WHERE resolved_at > ? AND realized_return_pct > 0",
            (week_ago,),
        ).fetchone()

        total_n = total["n"] if total else 0
        win_n = wins["n"] if wins else 0
        win_rate = (win_n / total_n * 100) if total_n > 0 else 0.0

        # Best strategy
        best = conn.execute(
            """SELECT strategy, AVG(CASE WHEN realized_return_pct > 0 THEN 1 ELSE 0 END) AS wr,
                      COUNT(*) AS n
               FROM outcomes JOIN signals ON outcomes.signal_id = signals.id
               WHERE outcomes.resolved_at > ?
               GROUP BY strategy ORDER BY wr DESC LIMIT 1""",
            (week_ago,),
        ).fetchone()

        conn.close()

        lines = [
            "📈 WEEKLY DIGEST — Trading Signal",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Minggu ini: {total_n} sinyal, {win_n} win ({win_rate:.1f}%)",
        ]
        if best and best["n"] >= 3:
            lines.append(f"🏆 Best: {best['strategy']} ({best['wr']*100:.0f}% win, {best['n']}x)")

        send_alert("\n".join(lines))
    except Exception as e:
        logger.error("Weekly digest failed: %s", e)


def start_scheduler():
    """Start the APScheduler for nightly pipeline execution."""
    from apscheduler.schedulers.background import BackgroundScheduler
    config = load_config()
    hour, minute = map(int, config.cron_time_utc.split(":"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_pipeline, "cron", hour=hour, minute=minute,
                      id="nightly_pipeline", name="Trading Signal Pipeline")
    scheduler.start()
    logger.info("Scheduler started — daily at %02d:%02d UTC", hour, minute)

    hc = health_check(config)
    critical = [CRITICAL_SOURCES.get(k, k) for k, v in hc.items() if k in CRITICAL_SOURCES and not v]
    if critical:
        logger.error("Health check FAILED: %s", ", ".join(critical))
        send_alert(f"Health check gagal: {', '.join(critical)}")
    else:
        logger.info("Health check passed")
    return scheduler


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trading Signal Bot")
    parser.add_argument("--bot", action="store_true", help="Start interactive Telegram bot")
    parser.add_argument("--no-scheduler", action="store_true", help="Don't start cron scheduler")
    parser.add_argument("--pipeline", action="store_true", help="Run pipeline once and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("Trading Signal Pipeline starting...")
    init_db().close()
    from src.db import run_migrations
    run_migrations()

    # Default: run pipeline once
    if not args.bot and not args.pipeline:
        run_pipeline()
    elif args.pipeline:
        run_pipeline()
    elif args.bot:
        # Start scheduler (unless disabled) + bot in parallel
        import threading
        from src.telegram_bot import start_bot as start_tg_bot

        if not args.no_scheduler:
            sched = start_scheduler()
            logger.info("Scheduler + Bot running in parallel")
        else:
            logger.info("Bot only mode (no scheduler)")

        # Bot runs in main thread
        start_tg_bot()
