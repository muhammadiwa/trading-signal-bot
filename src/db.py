"""SQLite database initialization and helpers."""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'HOLD')),
    confidence REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL,
    strategy TEXT NOT NULL,
    sentiment_score REAL,
    onchain_signal TEXT,
    macro_flag INTEGER DEFAULT 0,
    research_metadata TEXT,       -- JSON blob
    timestamp_utc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'resolved'))
);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL UNIQUE REFERENCES signals(id),
    realized_return_pct REAL,
    price_at_resolution REAL,
    resolved_at TEXT NOT NULL,
    reflection_text TEXT,
    llm_used INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    pairs_analyzed INTEGER DEFAULT 0,
    signals_generated INTEGER DEFAULT 0,
    duration_seconds REAL,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'completed', 'failed')),
    stage_failed INTEGER,
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS weights (
    weight_id TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_outcomes_signal ON outcomes(signal_id);
CREATE INDEX IF NOT EXISTS idx_run_log_started ON run_log(started_at);
"""


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    """Initialize the SQLite database with schema.

    Creates the database file and all tables if they don't exist.
    Safe to call multiple times — uses IF NOT EXISTS.

    Args:
        db_path: Path to SQLite file. Defaults to data/signals.db
                 relative to project root.

    Returns:
        sqlite3.Connection ready for use.
    """
    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent
        db_path = str(project_root / "data" / "signals.db")

    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")     # Better concurrent access
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()

    return conn


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Get a connection to the database. Initializes if needed."""
    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent
        db_path = str(project_root / "data" / "signals.db")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn
