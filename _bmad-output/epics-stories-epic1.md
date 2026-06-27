### Story 1.1: Project Setup

As a developer,
I want a working project skeleton with all dependencies installed and configuration loaded,
So that all subsequent stories have a solid foundation to build upon.

**Acceptance Criteria:**

**Given** a fresh checkout of the repository
**When** I run `pip install -r requirements.txt`
**Then** all dependencies install without errors
**And** `python -c "import ccxt, pandas, pandas_ta, pyarrow, requests, telegram, apscheduler; print('OK')"` succeeds

**Given** the project directory exists
**When** I create `config/settings.yaml` with watchlist, thresholds, and API endpoints
**And** I create `.env` with TOKENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
**Then** `src/config.py` loads both files and exposes a `Settings` dataclass
**And** `.env` is listed in `.gitignore`

**Given** SQLite database does not exist
**When** the application initializes
**Then** `data/signals.db` is created with tables: `signals`, `outcomes`, `run_log`
**And** each table has correct schema per AD-8

**Given** the project structure follows ARCHITECTURE-SPINE.md seed
**When** I inspect `src/`, `data/`, `config/`
**Then** all directories exist with `__init__.py` files

---

### Story 1.2: OHLCV Data Fetcher

As the pipeline,
I want to fetch 6-12 months of OHLCV data for any crypto symbol via CCXT with automatic fallback,
So that every subsequent stage has reliable historical data to work with.

**Acceptance Criteria:**

**Given** a symbol "BTC-USDT" and timeframe "1d"
**When** `ExchangeRouter.fetch_ohlcv("BTC-USDT", "1d", since_ms)` is called
**Then** returns a pandas DataFrame with columns: timestamp, open, high, low, close, volume
**And** data spans at least 6 months from `since_ms` (minimum requirement per FR1.1)

**Given** Binance API returns error or timeout
**When** the router retries 3 times
**Then** it falls back to OKX automatically
**And** if OKX also fails, falls back to CoinGecko
**And** logs each attempt with exchange name and error

**Given** OHLCV data is successfully fetched
**When** the router saves to `data/ohlcv/{symbol}.parquet`
**Then** the file is written atomically (write to temp, then rename)
**And** saved to `data/ohlcv/{symbol}.parquet` (one file per symbol, contains OHLCV + indicators once Story 1.3 appends them)

**Given** the data freshness gate is active
**When** cached Parquet data is older than 4 hours
**Then** the router fetches fresh data from exchange
**And** if exchange fetch fails, uses cached data with a stale-data warning logged

**Given** cached Parquet data exists and exchange fetch succeeds
**When** the router saves
**Then** existing data is appended, not overwritten (preserves history)
**And** duplicate timestamps are dropped before save

**Given** a symbol has less than 6 months of available data
**When** the router attempts to fetch
**Then** it logs a warning: "Insufficient history for {symbol}"
**And** returns a DataFrame with whatever data is available (caller decides)

---

### Story 1.3: Indicator Engine

As the pipeline,
I want to pre-compute all technical indicators once per pair and store them alongside OHLCV data,
So that all 5 strategies can share the same indicator values without redundant computation.

**Acceptance Criteria:**

**Given** an OHLCV DataFrame loaded from Parquet
**When** `indicators.compute_all(ohlcv)` is called
**Then** returns a dict with keys: rsi_14, macd, macd_signal, macd_histogram, ma_20, ma_50, ma_200, atr_14, adx_14, bb_upper, bb_lower, bb_middle, volume_sma_20, volume_ratio
**And** each value is a pandas Series aligned with the OHLCV index

**Given** OHLCV data has fewer rows than the indicator lookback period
**When** `compute_all` is called
**Then** returns NaN for periods where calculation is impossible
**And** does not raise an exception

**Given** indicators are computed
**When** appended to the same Parquet file as OHLCV (`data/ohlcv/{symbol}.parquet`)
**Then** the file contains all indicator columns plus the original OHLCV columns in one file
**And** strategies load a single Parquet file to get both OHLCV and indicators

**Given** a strategy requests an indicator by key (e.g., "rsi_14")
**When** the indicator dict is accessed
**Then** the value is a pandas Series, not a scalar
**And** the strategy can slice by date index

---

### Story 1.4: Strategy Library

As the pipeline,
I want 5 trading strategies implemented as pure functions following a shared protocol,
So that the strategy-pair matcher can evaluate each pair against the best-fit strategy.

**Acceptance Criteria:**

**Given** any strategy module (e.g., `momentum.py`)
**When** imported
**Then** it exports a class implementing `StrategyProtocol` with: `name: str`, `weight: float`, `evaluate(ohlcv, indicators) -> StrategySignal`

**Given** Momentum Breakout strategy with N=20, k=0.005
**When** `evaluate(ohlcv, indicators)` is called on trending data
**Then** returns BUY when price > highest(N) × (1+k)
**And** returns SELL when price < lowest(N) × (1−k)
**And** confidence is proportional to breakout strength

**Given** Trend Following strategy with short=20, long=50, adx_threshold=25
**When** `evaluate` is called
**Then** returns BUY when MA20 crosses above MA50 AND ADX > 25
**And** returns SELL when MA20 crosses below MA50 AND ADX > 25
**And** returns HOLD when ADX < 25 (no trend)

**Given** Mean Reversion strategy with rsi_length=14, bb_length=20
**When** `evaluate` is called
**Then** returns BUY when RSI < 30 AND price < BB_lower AND volume > 1.3× SMA(volume,20)
**And** returns SELL when RSI > 70 AND price > BB_upper AND volume > 1.3× SMA(volume,20)
**And** only triggers when ADX < 20 (ranging market — avoiding strong trend whipsaws)

**Given** Volatility Breakout strategy with atr_length=14, k=1.5
**When** `evaluate` is called
**Then** returns BUY when price > SMA20 + ATR×k
**And** returns SELL when price < SMA20 − ATR×k

**Given** Volume-Price Divergence strategy
**When** `evaluate` is called on data with bullish divergence (new low + declining volume)
**Then** returns BUY
**And** bearish divergence (new high + declining volume) returns SELL

**Given** any strategy evaluates on data with no clear signal
**When** strategy conditions are not met
**Then** returns HOLD with confidence 0.0
**And** does not raise an exception

---

### Story 1.5: Profile + Strategy Matching + Backtest

As the pipeline,
I want to compute a 4-dimension profile for each pair and auto-match it to the best strategy validated by backtesting,
So that each pair uses the strategy most suited to its characteristics.

**Acceptance Criteria:**

**Given** OHLCV and indicators for a pair
**When** `profile.compute(ohlcv, indicators)` is called
**Then** returns a `PairProfile` with: trendiness (0-100), volatility (0-100), mean_reversion (0-100), volume_quality (0-100)
**And** profile uses rolling 90-day window for current values
**And** each dimension is explainable (e.g., "trendiness=72: ADX avg=28, ADX>25 for 65% of window")

**Given** a PairProfile with high trendiness (>60) and high volume_quality (>50)
**When** the matcher runs
**Then** selects Trend Following strategy
**And** the matching rule is deterministic (same profile → same strategy)

**Given** a PairProfile with high mean_reversion (>60) and low trendiness (<40)
**When** the matcher runs
**Then** selects Mean Reversion strategy

**Given** a PairProfile with no clear dominant characteristic
**When** the matcher runs
**Then** selects Ensemble mode (all 5 strategies vote)
**And** logs: "Unclear profile for {symbol} — using ensemble"

**Given** a matched strategy
**When** `backtest.run(strategy, ohlcv, indicators)` is called on 6-month historical data
**Then** returns `BacktestResult` with: win_rate, sharpe_ratio, max_drawdown, profit_factor, total_trades
**And** backtest uses walk-forward validation (train 80%, test 20%) if data > 12 months

**Given** backtest result with win_rate < 40% OR sharpe < 0.5
**When** the validator checks the gate
**Then** the pair is flagged as FAILED for this strategy
**And** the system tries the second-best matched strategy
**And** if all strategies fail → pair is dropped with log: "{symbol} dropped — no strategy passed backtest"

**Given** backtest result passes all gates
**When** the validator accepts
**Then** the pair proceeds to signal generation
**And** the matched strategy + backtest metrics are stored for this run

---

### Story 1.6: Signal Generation + Filter

As the pipeline,
I want to convert validated strategy matches into structured trading signals with confidence scoring, stop-loss/take-profit, and quality filters,
So that only high-confidence, actionable signals reach the user.

**Acceptance Criteria:**

**Given** a matched strategy and its backtest result
**When** signal generation runs
**Then** `Technical_Confidence = 0.7 × strategy_score + 0.3 × signal_strength`
**And** `strategy_score = win_rate × profit_factor / max_possible` (normalized 0-1)
**And** `signal_strength` = normalized distance of current price from trigger threshold (0-1)

**Given** a SELL signal on BTC-USDT with entry_price=60200
**When** SL/TP are computed
**Then** SL = entry + ATR×1.5 = 60200 + 867 = 61067
**And** TP = entry − ATR×3.0 = 60200 − 2600 = 57600
**And** both are rounded to 2 decimal places for USD pairs, 0 decimal for JPY/KRW pairs
**And** rounding is done via standard `round()` — no exchange-specific tick size needed in MVP

**Given** a Final Confidence of 73% for a signal
**When** the filter runs
**Then** the signal passes (≥ 60% threshold)
**And** is included in today's output

**Given** a Final Confidence of 45% for a signal
**When** the filter runs
**Then** the signal is dropped
**And** logged with reason: "Confidence 45% < 60% threshold"

**Given** 35 signals pass the confidence filter
**When** the cap is applied
**Then** only the top 30 by confidence are kept
**And** a warning is logged: "Signal cap reached — 5 signals dropped"

**Given** BTC-USDT generated a signal yesterday at 07:00
**When** the same pair generates a signal today with confidence 65%
**Then** the cooldown filter blocks it (24h rule)
**And** logs: "{symbol} in cooldown — last signal < 24h ago"

**Given** BTC-USDT generated a signal yesterday but today's confidence is 85%
**When** the cooldown filter runs
**Then** the cooldown is overridden (>80% exception)
**And** the signal passes

**Given** a passing signal
**When** the `Signal` dataclass is constructed
**Then** it contains all fields from AD-8: id (uuid), symbol, action, confidence, entry_price, sl, tp, strategy, timestamp_utc, status="pending"
**And** the signal is written to SQLite signals table

---

### Story 1.7: Telegram Delivery

As a user (Kumaha-sia),
I want to receive a single formatted Telegram message every morning with all daily signals and their details,
So that I can review trading opportunities from my phone without opening any app.

**Acceptance Criteria:**

**Given** a list of 20 validated Signal objects
**When** `telegram_sender.send_daily_signals(signals)` is called
**Then** exactly ONE Telegram message is sent to the configured chat_id
**And** the message starts with a summary line: "📊 {N}/{total} pair analyzed | Avg Conf: {avg}% | 7-day win: {win_rate}%"
**And** signal fields (symbol, action, price, confidence, SL, TP) are in English
**And** commentary text (summary, caveats) is in Indonesian (Bahasa Indonesia) per FR5.1

**Given** a SELL signal for BTC-USDT with confidence 73%, 7-day track record "4/5 win (+2.1%)"
**When** the message is formatted
**Then** the signal block contains:
```
🔴 SELL — BTC/USDT $60,200
Trend Following | Conf 73%
SL: 61,500 | TP: 56,000
📈 Track: 4/5 win (+2.1%)
```
**And** for BUY signals, uses 🟢 emoji
**And** the track record line is omitted if no historical data exists (new pair)

**Given** the Telegram API returns an error (network, rate limit)
**When** `send_daily_signals` catches the exception
**Then** retries up to 3 times with exponential backoff (1s, 2s, 4s)
**And** if all retries fail → logs error: "Telegram send failed after 3 retries"
**And** does NOT crash the pipeline

**Given** a signal with missing optional fields (e.g., no macro_flag)
**When** the message is formatted
**Then** those lines are simply omitted (no empty labels, no "—")
**And** the message remains valid and readable

**Given** the Telegram bot token is invalid or not configured
**When** `send_daily_signals` is called
**Then** logs an error and returns False
**And** the pipeline continues (signals are saved to DB, just not delivered)

---

### Story 1.8: Pipeline Orchestrator + Scheduling

As the system operator,
I want the pipeline to run automatically every night via cron, execute all stages in sequence, and handle failures gracefully,
So that signals are delivered reliably at 07:00 WIB without manual intervention.

**Acceptance Criteria:**

**Given** the system time reaches 23:00 UTC (06:00 WIB)
**When** the APScheduler cron trigger fires (configured via `apscheduler` library per FR7.1)
**Then** `main.py` executes `run_pipeline()`
**And** each stage function receives input_dir and returns output_dir
**And** artifacts from each stage are written to `data/pipeline/{date}/stage_{N}/`

**Given** Stage 1 (Data Fetch) completes successfully
**When** Stage 2 (Profile + Matching) starts
**Then** it reads artifacts from `stage_1/` output directory
**And** writes to `stage_2/` directory
**And** so on for all stages

**Given** a stage raises an unhandled exception
**When** the pipeline catches it
**Then** the error is logged with full traceback
**And** subsequent stages are skipped
**And** a Telegram alert is sent: "Pipeline failed at Stage {N}: {error}"
**And** the run_log table records: status="failed", stage_failed=N, error_summary

**Given** the pipeline has been running for 55 minutes
**When** the runtime budget check triggers (60 min max)
**Then** a warning is logged: "Pipeline approaching timeout — 5 min remaining"
**And** if 60 min is exceeded → pipeline terminates, delivers partial results with warning

**Given** it's 22:55 UTC (5 min before pipeline start)
**When** the health check runs
**Then** it verifies: CCXT connectivity, Alternative.me API reachable, Telegram token valid
**And** if any critical source fails → skips pipeline, sends Telegram alert
**And** if only non-critical source fails → logs warning, continues pipeline

**Given** the pipeline completes successfully
**When** `run_log` is updated
**Then** the record shows: status="completed", pairs_analyzed=N, signals_generated=M, duration_seconds=X
**And** a completion message is sent to Telegram: "✅ Pipeline complete — {M} signals from {N} pairs in {X}s"
