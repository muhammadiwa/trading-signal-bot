"""Interactive Telegram Bot — v2 with commands, inline keyboards, polling.

Runs alongside the scheduler in a separate thread. All pipeline control
and backtesting accessible from Telegram chat.
"""

import asyncio
import logging
import os
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import TelegramError

from src.config import load_config
from src.db import get_connection

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────

async def _send_long_message(update: Update, text: str) -> None:
    """Send a message, splitting if over Telegram's 4096 char limit."""
    if len(text) <= 4000:
        await update.message.reply_text(text)
        return
    chunks = [text[i:i+3900] for i in range(0, len(text), 3900)]
    for i, chunk in enumerate(chunks):
        await update.message.reply_text(chunk if i == 0 else f"(lanjutan...)\n{chunk}")


def _check_auth(update: Update) -> bool:
    """Restrict bot to configured chat_id only."""
    allowed = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not allowed:
        return True  # No restriction configured
    return str(update.effective_chat.id) == allowed


# ── Command: /start + /help ─────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    await update.message.reply_text(
        "🤖 **Trading Signal Bot**\n\n"
        "Gunakan command berikut:\n\n"
        "/run — Jalankan pipeline manual\n"
        "/backtest — Backtest interaktif (pair × timeframe × strategi)\n"
        "/signals — Lihat sinyal hari ini\n"
        "/status — Status pipeline terakhir\n"
        "/pairs — Daftar pair yang di-track\n"
        "/profile <pair> — 4D profile detail (contoh: /profile BTC-USDT)\n"
        "/outcomes — Statistik performa historis\n"
        "/help — Tampilkan menu ini",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


# ── Command: /run ───────────────────────────────────────────

async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return

    keyboard = [
        [InlineKeyboardButton("✅ Ya, jalankan sekarang", callback_data="run_confirm")],
        [InlineKeyboardButton("❌ Batal", callback_data="run_cancel")],
    ]
    await update.message.reply_text(
        "⚡ **Run Pipeline Manual**\n\n"
        "Ini akan menjalankan full pipeline (Stage 0-5).\n"
        "Proses bisa memakan waktu 1-5 menit.\n\n"
        "Lanjutkan?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _do_run_pipeline(update: Update) -> None:
    """Execute full pipeline and report results."""
    msg = await update.callback_query.message.reply_text("⏳ Pipeline berjalan... (mungkin 1-5 menit)")

    try:
        from main import run_pipeline
        config = load_config()
        start = time.monotonic()
        result = run_pipeline(config)
        elapsed = time.monotonic() - start

        lines = [
            "✅ **Pipeline Selesai**",
            f"⏱️ Durasi: {elapsed:.0f} detik",
            f"📊 Status: {result['status']}",
            f"🔍 Pair dianalisa: {result['pairs_analyzed']}",
            f"📡 Sinyal dihasilkan: {result['signals_generated']}",
            f"🆔 Run ID: {result['run_id']}",
            "",
            "Gunakan /signals untuk lihat sinyal.",
        ]
        await msg.edit_text("\n".join(lines))
    except Exception as e:
        await msg.edit_text(f"❌ Pipeline gagal: {str(e)[:200]}")


# ── Command: /status ────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return

    conn = get_connection()
    try:
        last_run = conn.execute(
            "SELECT * FROM run_log ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM signals WHERE status='pending'"
        ).fetchone()["n"]

        resolved = conn.execute(
            "SELECT COUNT(*) AS n FROM outcomes"
        ).fetchone()["n"]

        wins = conn.execute(
            "SELECT COUNT(*) AS n FROM outcomes WHERE realized_return_pct > 0"
        ).fetchone()["n"]

        if last_run:
            wr = conn.execute(
                """SELECT AVG(CASE WHEN realized_return_pct > 0 THEN 1 ELSE 0 END) AS wr
                   FROM outcomes WHERE resolved_at > ?""",
                ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),),
            ).fetchone()

            win_rate_7d = round(wr["wr"] * 100, 1) if wr and wr["wr"] is not None else "N/A"

            lines = [
                "📊 **Status Pipeline**",
                f"🕐 Terakhir: {last_run['started_at'][:19]}",
                f"📌 Status: {last_run['status']}",
                f"🔍 Pair: {last_run['pairs_analyzed']}",
                f"📡 Sinyal: {last_run['signals_generated']}",
                f"⏱️ Durasi: {last_run['duration_seconds']}s" if last_run["duration_seconds"] else "",
                "",
                f"⏳ Pending: {pending}",
                f"✅ Resolved: {resolved}",
                f"🏆 Wins: {wins}",
                f"📈 Win Rate (7D): {win_rate_7d}%",
            ]
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text("📊 Belum ada data pipeline.")
    finally:
        conn.close()


# ── Command: /signals ───────────────────────────────────────

async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return

    keyboard = [
        [
            InlineKeyboardButton("📅 Hari ini", callback_data="signals_today"),
            InlineKeyboardButton("⏳ Pending", callback_data="signals_pending"),
        ],
        [InlineKeyboardButton("📋 Semua", callback_data="signals_all")],
    ]
    await update.message.reply_text(
        "🔍 **Lihat Sinyal**\nPilih filter:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _show_signals(update: Update, filter_mode: str) -> None:
    conn = get_connection()
    try:
        if filter_mode == "today":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT * FROM signals WHERE timestamp_utc LIKE ? ORDER BY confidence DESC LIMIT 20",
                (f"{today}%",),
            ).fetchall()
        elif filter_mode == "pending":
            rows = conn.execute(
                "SELECT * FROM signals WHERE status='pending' ORDER BY timestamp_utc DESC LIMIT 20"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY timestamp_utc DESC LIMIT 20"
            ).fetchall()

        if not rows:
            await update.callback_query.message.reply_text("📭 Tidak ada sinyal.")
            return

        lines = [f"📡 **Sinyal ({filter_mode})**", ""]
        for s in rows:
            emoji = {"BUY": "🟢", "SELL": "🔴"}.get(s["action"], "⚪")
            lines.append(
                f"{emoji} {s['action']} — {s['symbol']} [{s.get('timeframe', '1d')}]\n"
                f"   {s['strategy']} | Conf {s['confidence']*100:.0f}%\n"
                f"   Entry: ${s['entry_price']:,.2f} | SL: ${s['stop_loss']:,.2f} | "
                f"TP: ${s['take_profit']:,.2f}" if s.get("take_profit") else ""
            )

        await _send_long_message(update.callback_query, "\n".join(lines))
    finally:
        conn.close()


# ── Command: /outcomes ──────────────────────────────────────

async def cmd_outcomes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return

    keyboard = [
        [
            InlineKeyboardButton("📅 7 Hari", callback_data="outcomes_7d"),
            InlineKeyboardButton("📅 30 Hari", callback_data="outcomes_30d"),
        ],
        [InlineKeyboardButton("📋 Semua", callback_data="outcomes_all")],
    ]
    await update.message.reply_text(
        "📊 **Statistik Performa**\nPilih periode:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _show_outcomes(update: Update, days: Optional[int]) -> None:
    conn = get_connection()
    try:
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            rows = conn.execute(
                "SELECT * FROM outcomes WHERE resolved_at > ?", (cutoff,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM outcomes").fetchall()

        if not rows:
            await update.callback_query.message.reply_text("📭 Belum ada data outcome.")
            return

        total = len(rows)
        wins = sum(1 for r in rows if r["realized_return_pct"] and r["realized_return_pct"] > 0)
        losses = total - wins
        wr = wins / total * 100 if total > 0 else 0
        avg_ret = sum(r["realized_return_pct"] for r in rows if r["realized_return_pct"]) / max(wins + losses, 1)

        # Best strategy
        strat = conn.execute(
            """SELECT s.strategy, COUNT(*) AS n,
               AVG(CASE WHEN o.realized_return_pct > 0 THEN 1 ELSE 0 END) AS wr
               FROM outcomes o JOIN signals s ON o.signal_id = s.id
               GROUP BY s.strategy ORDER BY wr DESC LIMIT 3"""
        ).fetchall()

        label = f"{days} hari" if days else "semua"
        lines = [
            f"📊 **Performa ({label})**",
            f"Total: {total} | ✅ Win: {wins} | ❌ Loss: {losses}",
            f"Win Rate: {wr:.1f}% | Avg Return: {avg_ret:+.1f}%",
            "",
            "🏆 **Best Strategies:**",
        ]
        for s in strat:
            lines.append(f"  {s['strategy']}: {s['wr']*100:.0f}% ({s['n']}x)")

        await update.callback_query.message.reply_text("\n".join(lines))
    finally:
        conn.close()


# ── Command: /pairs ─────────────────────────────────────────

async def cmd_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return

    from main import fetch_top_symbols
    config = load_config()
    symbols = fetch_top_symbols(config)

    lines = [f"📋 **Top {len(symbols)} Pairs**", ""]
    for i, sym in enumerate(symbols[:15], 1):
        lines.append(f"{i}. {sym}")
    if len(symbols) > 15:
        lines.append(f"... dan {len(symbols) - 15} lainnya")
    lines.append("")
    lines.append("💡 Gunakan /profile <pair> untuk detail.")

    await update.message.reply_text("\n".join(lines))


# ── Command: /profile ───────────────────────────────────────

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return

    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Gunakan: /profile <pair>\nContoh: /profile BTC-USDT")
        return

    symbol = args[0].upper()
    try:
        from src.indicators import load_with_indicators
        from src.profile import compute as compute_profile

        # Try 1d first, fallback to available timeframes
        for tf in ("1d", "4h", "1h"):
            try:
                df = load_with_indicators(f"data/ohlcv/{symbol}-{tf}.parquet")
                indicator_keys = [k for k in df.columns
                                  if k not in ("timestamp", "open", "high", "low", "close", "volume")]
                ind = {k: df[k] for k in indicator_keys}
                profile = compute_profile(df, ind, symbol)
                break
            except Exception:
                continue
        else:
            await update.message.reply_text(f"❌ Data tidak tersedia untuk {symbol}")
            return

        lines = [
            f"📊 **{symbol} Profile**",
            f"Trendiness: {profile.trendiness:.0f}/100",
            f"Volatility: {profile.volatility:.0f}/100",
            f"Mean Reversion: {profile.mean_reversion:.0f}/100",
            f"Volume Quality: {profile.volume_quality:.0f}/100",
            "",
            "**Detail:**",
            f"ADX Avg: {profile.detail.get('adx_avg', 'N/A')}",
            f"ADX Trending: {profile.detail.get('adx_trending_pct', 'N/A')}%",
            f"ATR %: {profile.detail.get('atr_pct', 'N/A')}",
            f"RSI Bounces: {profile.detail.get('rsi_bounces', 'N/A')}",
            f"Vol CV: {profile.detail.get('vol_cv', 'N/A')}",
            f"P/V Corr: {profile.detail.get('pv_corr', 'N/A')}",
        ]
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal load profile: {str(e)[:100]}")


# ── Command: /backtest ──────────────────────────────────────

async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return

    from main import fetch_top_symbols
    config = load_config()
    symbols = fetch_top_symbols(config)[:12]

    # Step 1: Pick pair
    keyboard = []
    row = []
    for i, sym in enumerate(symbols):
        row.append(InlineKeyboardButton(sym, callback_data=f"bt_pair_{sym}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    context.user_data["bt_symbols"] = symbols
    await update.message.reply_text(
        "📊 **Backtest — Step 1/4**\nPilih pair:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── Callback Handler ────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    # /run confirmations
    if data == "run_confirm":
        await _do_run_pipeline(query)
    elif data == "run_cancel":
        await query.message.edit_text("❌ Dibatalkan.")

    # /signals filter
    elif data.startswith("signals_"):
        mode = data.replace("signals_", "")
        await _show_signals(query, mode)

    # /outcomes filter
    elif data.startswith("outcomes_"):
        period = data.replace("outcomes_", "")
        days_map = {"7d": 7, "30d": 30, "all": None}
        await _show_outcomes(query, days_map.get(period))

    # /backtest wizard
    elif data.startswith("bt_pair_"):
        sym = data.replace("bt_pair_", "")
        context.user_data["bt_pair"] = sym
        keyboard = [
            [InlineKeyboardButton("1h", callback_data="bt_tf_1h"),
             InlineKeyboardButton("4h", callback_data="bt_tf_4h"),
             InlineKeyboardButton("1d", callback_data="bt_tf_1d")],
            [InlineKeyboardButton("All", callback_data="bt_tf_all")],
        ]
        await query.message.edit_text(
            f"📊 Backtest {sym} — **Step 2/4**\nPilih timeframe:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("bt_tf_"):
        tf = data.replace("bt_tf_", "")
        context.user_data["bt_tf"] = tf
        keyboard = [
            [InlineKeyboardButton("Auto (best match)", callback_data="bt_strat_auto")],
            [InlineKeyboardButton("Momentum Breakout", callback_data="bt_strat_momentum")],
            [InlineKeyboardButton("Trend Following", callback_data="bt_strat_trend")],
            [InlineKeyboardButton("Mean Reversion", callback_data="bt_strat_mean")],
            [InlineKeyboardButton("Volatility Breakout", callback_data="bt_strat_volatility")],
            [InlineKeyboardButton("Volume Divergence", callback_data="bt_strat_volume")],
            [InlineKeyboardButton("All 5", callback_data="bt_strat_all")],
        ]
        await query.message.edit_text(
            f"📊 Backtest {context.user_data['bt_pair']} [{tf}] — **Step 3/4**\nPilih strategi:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("bt_strat_"):
        strat = data.replace("bt_strat_", "")
        context.user_data["bt_strat"] = strat
        keyboard = [
            [InlineKeyboardButton("1 bulan", callback_data="bt_range_30")],
            [InlineKeyboardButton("3 bulan", callback_data="bt_range_90")],
            [InlineKeyboardButton("6 bulan", callback_data="bt_range_180")],
            [InlineKeyboardButton("12 bulan", callback_data="bt_range_365")],
        ]
        await query.message.edit_text(
            f"📊 Backtest — **Step 4/4**\nPilih data range:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("bt_range_"):
        days = int(data.replace("bt_range_", ""))
        await _run_backtest(query, context, days)

    else:
        await query.message.edit_text(f"❓ Unknown action: {data}")


async def _run_backtest(query, context, days: int) -> None:
    """Execute backtest and display detailed results."""
    sym = context.user_data.get("bt_pair", "BTC-USDT")
    tf = context.user_data.get("bt_tf", "1d")
    strat_key = context.user_data.get("bt_strat", "auto")

    await query.message.edit_text(f"⏳ Running backtest {sym} [{tf}]...")

    try:
        from src.indicators import load_with_indicators
        from src.backtest import run as backtest_run
        from src.strategies.base import (
            MomentumBreakout, TrendFollowing, MeanReversion,
            VolatilityBreakout, VolumeDivergence, all_strategies,
        )

        # Load data
        try:
            df = load_with_indicators(f"data/ohlcv/{sym}-{tf}.parquet")
        except Exception:
            await query.message.edit_text(f"❌ Data {tf} tidak tersedia untuk {sym}. Jalankan pipeline dulu.")
            return

        # Trim to requested range
        if len(df) > days:
            df = df.iloc[-days:]

        indicator_keys = [k for k in df.columns
                          if k not in ("timestamp", "open", "high", "low", "close", "volume")]
        ind = {k: df[k] for k in indicator_keys}

        # Map strategy key to class
        strat_map = {
            "momentum": MomentumBreakout,
            "trend": TrendFollowing,
            "mean": MeanReversion,
            "volatility": VolatilityBreakout,
            "volume": VolumeDivergence,
        }

        if strat_key == "auto":
            from src.pipeline.stage_2_profile import find_best_strategy
            best_s, best_r, all_r = find_best_strategy(df, ind, sym)
            if best_s is None:
                candidates = [cls() for cls in [MomentumBreakout, TrendFollowing, MeanReversion,
                                                VolatilityBreakout, VolumeDivergence]]
                results = [backtest_run(s, df, ind) for s in candidates]
            else:
                results = all_r
        elif strat_key == "all":
            candidates = [cls() for cls in [MomentumBreakout, TrendFollowing, MeanReversion,
                                            VolatilityBreakout, VolumeDivergence]]
            results = [backtest_run(s, df, ind) for s in candidates]
        else:
            cls = strat_map.get(strat_key)
            if cls is None:
                await query.message.edit_text(f"❌ Strategi tidak dikenal: {strat_key}")
                return
            results = [backtest_run(cls(), df, ind)]

        # Format results
        lines = [f"📊 **Backtest {sym} [{tf}]** — {days} hari", ""]

        for r in results:
            emoji = "✅" if r.passed else "❌"
            lines.extend([
                f"{emoji} **{r.strategy_name}**",
                f"Win Rate: {r.win_rate*100:.1f}% | Sharpe: {r.sharpe_ratio:.2f}",
                f"Max DD: {r.max_drawdown*100:.1f}% | Profit Factor: {r.profit_factor:.2f}",
                f"Trades: {r.total_trades} | Return: {r.total_return*100:+.1f}%",
                f"Status: {'✅ PASS' if r.passed else '❌ FAIL'} (win≥40%, sharpe≥0.5)",
                "",
            ])

            # Show last 5 trades
            if r.trade_log:
                lines.append("📈 **Last trades:**")
                for t in r.trade_log[-5:]:
                    emoji_t = "🟢" if t["win"] else "🔴"
                    lines.append(
                        f"  {emoji_t} {t['action']} entry ${t['entry']:,.2f} → "
                        f"exit ${t['exit']:,.2f} ({t['return_pct']:+.1f}%)"
                    )
                lines.append("")

        lines.append("💡 /backtest untuk coba pair lain")
        await query.message.edit_text("\n".join(lines))

    except Exception as e:
        await query.message.edit_text(f"❌ Backtest gagal: {str(e)[:200]}")


# ── Bot Runner ──────────────────────────────────────────────

def start_bot(token: str | None = None, chat_id: str | None = None) -> Application:
    """Start the Telegram bot polling loop.

    Returns the Application instance so the caller can manage lifecycle.
    Runs in the current thread (caller should put this in a thread).
    """
    if token is None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set — bot cannot start")
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(token).build()

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("outcomes", cmd_outcomes))
    app.add_handler(CommandHandler("pairs", cmd_pairs))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Telegram bot started — polling for commands")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return app
