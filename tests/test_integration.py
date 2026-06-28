"""Integration tests — full pipeline smoke test (Retro AI #1, #5)."""

import os
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def mock_env():
    """Set minimal env vars for pipeline."""
    old = dict(os.environ)
    os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
    os.environ["TELEGRAM_CHAT_ID"] = "test-chat"
    os.environ["TOKENROUTER_API_KEY"] = "test-key"
    os.environ["TOKENROUTER_BASE_URL"] = "https://test.example.com/v1"
    yield
    os.environ.clear()
    os.environ.update(old)


def _make_ohlcv():
    np.random.seed(99)
    dates = pd.date_range("2025-07-01", periods=365, freq="D")
    close = 100 + np.cumsum(np.random.randn(365) * 1.5)
    close = np.maximum(close, 1)
    return pd.DataFrame({
        "timestamp": dates, "open": close * 0.99, "high": close * 1.02,
        "low": close * 0.98, "close": close,
        "volume": np.random.uniform(1e6, 5e6, 365),
    })


# ── AI #2 + #4: DB migration tests ────────────────────────

def test_ensure_column_idempotent():
    from src.db import ensure_column, init_db

    db_path = tempfile.mktemp(suffix=".db")
    init_db(db_path).close()

    assert ensure_column("run_log", "test_col", "REAL", db_path) is True
    assert ensure_column("run_log", "test_col", "REAL", db_path) is False

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(run_log)").fetchall()]
    assert "test_col" in cols
    conn.close()


def test_run_migrations_safe():
    from src.db import run_migrations, init_db

    db_path = tempfile.mktemp(suffix=".db")
    init_db(db_path).close()

    added = run_migrations(db_path)
    assert isinstance(added, list)
    # Fresh DB from updated schema should already have columns
    assert all(c not in added for c in ["run_log.win_rate_7d", "signals.timeframe"])


def test_old_db_schema_migration():
    """Pipeline can start against an older DB schema (missing columns)."""
    from src.db import ensure_column

    old_db = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(old_db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE run_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL, completed_at TEXT,
            pairs_analyzed INTEGER DEFAULT 0,
            signals_generated INTEGER DEFAULT 0, duration_seconds REAL,
            status TEXT NOT NULL DEFAULT 'running',
            stage_failed INTEGER, error_summary TEXT
        );
        CREATE TABLE signals (
            id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
            action TEXT NOT NULL, confidence REAL NOT NULL,
            entry_price REAL NOT NULL, stop_loss REAL NOT NULL,
            strategy TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
    """)
    conn.close()

    assert ensure_column("run_log", "win_rate_7d", "REAL", old_db) is True
    assert ensure_column("signals", "timeframe", "TEXT NOT NULL DEFAULT '1d'", old_db) is True

    conn = sqlite3.connect(old_db)
    cols_run = [r[1] for r in conn.execute("PRAGMA table_info(run_log)").fetchall()]
    cols_sig = [r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()]
    assert "win_rate_7d" in cols_run, f"{cols_run}"
    assert "timeframe" in cols_sig, f"{cols_sig}"
    conn.close()


# ── Config + DB smoke tests ─────────────────────────────────

def test_config_loads():
    from src.config import load_config
    cfg = load_config()
    assert cfg.min_confidence > 0
    assert cfg.runtime_budget_minutes >= 5


def test_db_init_creates_tables():
    from src.db import init_db
    db_path = tempfile.mktemp(suffix=".db")
    conn = init_db(db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    assert "signals" in tables
    assert "outcomes" in tables
    assert "run_log" in tables
    assert "weights" in tables
    conn.close()


def test_all_modules_importable():
    modules = [
        "src.config", "src.db", "src.exchange", "src.indicators",
        "src.profile", "src.backtest", "src.strategies.base",
        "src.pipeline.stage_2_profile", "src.pipeline.stage_4_confidence",
        "src.telegram_sender", "src.research", "src.research_scoring",
        "src.outcome_tracker", "src.reflection", "src.weight_adjuster",
        "main",
    ]
    for mod in modules:
        __import__(mod)
