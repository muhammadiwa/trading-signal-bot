"""Tests for outcome_tracker.py — Story 3.1 Outcome Tracker."""

import pytest
from unittest.mock import patch, MagicMock
import sqlite3


@pytest.fixture
def test_db():
    """In-memory database for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE signals (
            id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'HOLD')),
            confidence REAL NOT NULL, entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL, take_profit REAL,
            strategy TEXT NOT NULL, timeframe TEXT NOT NULL DEFAULT '1d',
            sentiment_score REAL, onchain_signal TEXT,
            macro_flag INTEGER DEFAULT 0, research_metadata TEXT,
            timestamp_utc TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'resolved', 'unresolvable'))
        );
        CREATE TABLE outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id TEXT NOT NULL UNIQUE REFERENCES signals(id),
            realized_return_pct REAL,
            price_at_resolution REAL,
            resolved_at TEXT NOT NULL,
            reflection_text TEXT,
            llm_used INTEGER DEFAULT 0
        );
    """)
    return conn


def _seed(conn, sig_id, symbol, action, entry_price):
    conn.execute(
        """INSERT INTO signals (id, symbol, action, confidence, entry_price,
           stop_loss, strategy, timeframe, timestamp_utc, status)
           VALUES (?,?,?,0.65,?,0.0,'Test','1d',
           '2026-06-27T00:00:00+00:00','pending')""",
        (sig_id, symbol, action, entry_price),
    )
    conn.commit()


def _mock_conn_wrapper(real_conn):
    """Wrap a real connection so close() is a no-op (test can still query)."""
    m = MagicMock(wraps=real_conn)
    m.close = MagicMock()  # no-op
    return m


# ── T4.1+4.2+AC3: Return calculations ───────────────────────

def test_buy_return_calculation():
    from src.outcome_tracker import compute_return
    assert round(compute_return("BUY", 60200.0, 61500.0), 2) == 2.16


def test_sell_return_calculation():
    from src.outcome_tracker import compute_return
    assert round(compute_return("SELL", 60200.0, 58500.0), 2) == 2.82


def test_buy_loss_is_negative():
    from src.outcome_tracker import compute_return
    assert compute_return("BUY", 60200.0, 58000.0) < 0


# ── T4.3: No pending signals ────────────────────────────────

@patch("src.db.get_connection")
def test_no_pending_signals(mock_conn, test_db):
    from src.outcome_tracker import resolve_pending_signals
    mock_conn.return_value = _mock_conn_wrapper(test_db)
    assert resolve_pending_signals() == []
    test_db.close()


# ── T4.5: Integration — resolve writes outcome ──────────────

@patch("src.db.get_connection")
@patch("src.outcome_tracker._fetch_current_price")
def test_resolve_writes_outcome(mock_price, mock_conn, test_db):
    from src.outcome_tracker import resolve_pending_signals

    _seed(test_db, "sig-001", "BTC-USDT", "SELL", 60200.0)
    mock_conn.return_value = _mock_conn_wrapper(test_db)
    mock_price.return_value = 58500.0

    r = resolve_pending_signals()
    assert len(r) == 1
    assert r[0]["realized_return_pct"] == pytest.approx(2.82, 0.1)
    assert r[0]["win"] is True

    # Signal status updated on real connection
    row = test_db.execute("SELECT status FROM signals WHERE id='sig-001'").fetchone()
    assert row["status"] == "resolved"
    test_db.close()


# ── T4.4: Delisted symbol ───────────────────────────────────

@patch("src.db.get_connection")
@patch("src.outcome_tracker._fetch_current_price")
def test_delisted_symbol(mock_price, mock_conn, test_db):
    from src.outcome_tracker import resolve_pending_signals

    _seed(test_db, "sig-del", "DELCOIN-USDT", "BUY", 50.0)
    mock_conn.return_value = _mock_conn_wrapper(test_db)
    mock_price.side_effect = Exception("Symbol not found")

    r = resolve_pending_signals()
    assert r[0]["realized_return_pct"] is None
    assert r[0]["error"] is not None

    row = test_db.execute("SELECT status FROM signals WHERE id='sig-del'").fetchone()
    assert row["status"] == "unresolvable"
    test_db.close()


# ── AC6: 7-day win rate ────────────────────────────────────

@patch("src.db.get_connection")
def test_seven_day_win_rate(mock_conn, test_db):
    from src.outcome_tracker import resolve_pending_signals
    import main as main_module

    _seed(test_db, "sig-w1", "BTC-USDT", "SELL", 60000.0)
    _seed(test_db, "sig-w2", "ETH-USDT", "BUY", 3000.0)
    _seed(test_db, "sig-l1", "SOL-USDT", "BUY", 150.0)

    wrapped = _mock_conn_wrapper(test_db)
    mock_conn.return_value = wrapped

    with patch("src.outcome_tracker._fetch_current_price") as mp:
        mp.side_effect = [58500.0, 3300.0, 130.0]
        resolve_pending_signals()

    wr = main_module._compute_7day_win_rate()
    assert wr == pytest.approx(0.6667, 0.01)
    test_db.close()


# ── Dedup: Same symbol ─────────────────────────────────────

@patch("src.db.get_connection")
@patch("src.outcome_tracker._fetch_current_price")
def test_multiple_signals_same_symbol(mock_price, mock_conn, test_db):
    from src.outcome_tracker import resolve_pending_signals

    _seed(test_db, "sig-a", "BTC-USDT", "BUY", 60000.0)
    _seed(test_db, "sig-b", "BTC-USDT", "SELL", 61000.0)
    mock_conn.return_value = _mock_conn_wrapper(test_db)
    mock_price.return_value = 60500.0

    r = resolve_pending_signals()
    assert len(r) == 2
    assert mock_price.call_count == 1  # Deduplication
    test_db.close()
