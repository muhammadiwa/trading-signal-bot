"""SQLite database initialization and helpers."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


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
    research_metadata TEXT,
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

PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
"""


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply standard pragmas and row factory to a connection."""
    conn.executescript(PRAGMAS)
    conn.row_factory = sqlite3.Row


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    """Initialize the SQLite database with schema.

    Creates the database file and all tables if they don't exist.
    Safe to call multiple times — uses IF NOT EXISTS.

    Caller is responsible for closing the returned connection.

    Args:
        db_path: Path to SQLite file. Defaults to data/signals.db
                 relative to project root.

    Returns:
        sqlite3.Connection ready for use.

    Raises:
        PermissionError: If the database directory cannot be created.
    """
    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent
        db_path = str(project_root / "data" / "signals.db")

    db_dir = Path(db_path).parent
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise PermissionError(f"Cannot create database directory: {db_dir}")

    conn = sqlite3.connect(db_path)
    _configure_connection(conn)
    conn.executescript(SCHEMA)
    return conn


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Get a configured connection to the database.

    Applies WAL journal mode, foreign keys, busy timeout, and row factory.
    Does NOT create tables — use init_db() for schema initialization.

    Caller is responsible for closing the returned connection.
    """
    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent
        db_path = str(project_root / "data" / "signals.db")

    conn = sqlite3.connect(db_path)
    _configure_connection(conn)
    return conn


@contextmanager
def managed_connection(db_path: str | None = None):
    """Context manager that yields a connection and auto-closes it."""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()
