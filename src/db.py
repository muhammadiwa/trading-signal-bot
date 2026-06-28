"""SQLite database initialization and helpers."""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


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
    timeframe TEXT NOT NULL DEFAULT '1d',
    sentiment_score REAL,
    onchain_signal TEXT,
    macro_flag INTEGER DEFAULT 0,
    research_metadata TEXT,
    timestamp_utc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'resolved', 'unresolvable'))
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
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'completed', 'failed', 'timeout', 'aborted')),
    stage_failed INTEGER,
    error_summary TEXT,
    win_rate_7d REAL
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
CREATE INDEX IF NOT EXISTS idx_outcomes_resolved_at ON outcomes(resolved_at);
CREATE INDEX IF NOT EXISTS idx_run_log_started ON run_log(started_at);

CREATE TABLE IF NOT EXISTS llm_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT,
    response TEXT,
    tokens_used INTEGER,
    cost REAL,
    called_at TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL CHECK(status IN ('success', 'timeout', 'error'))
);
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
    try:
        _configure_connection(conn)
        conn.executescript(SCHEMA)
    except Exception:
        conn.close()
        raise
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


def ensure_column(table: str, column: str, col_def: str,
                  db_path: str | None = None) -> bool:
    """Add a column to an existing table if it doesn't already exist.

    Idempotent — safe to call multiple times.

    Args:
        table: Table name.
        column: Column name.
        col_def: Column definition (e.g., 'REAL').
        db_path: Optional explicit DB path.

    Returns True if column was added.
    """
    conn = get_connection(db_path)
    try:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column in cols:
            return False
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        conn.commit()
        logger.info("Migration: added %s.%s (%s)", table, column, col_def)
        return True
    finally:
        conn.close()


def run_migrations(db_path: str | None = None) -> list[str]:
    """Run all pending schema migrations. Idempotent. Returns list of changes."""
    added = []
    if ensure_column("run_log", "win_rate_7d", "REAL", db_path):
        added.append("run_log.win_rate_7d")
    if ensure_column("signals", "timeframe", "TEXT NOT NULL DEFAULT '1d'", db_path):
        added.append("signals.timeframe")
    if added:
        logger.info("Migrations applied: %s", ", ".join(added))
    return added
