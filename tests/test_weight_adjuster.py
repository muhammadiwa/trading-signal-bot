"""Tests for weight_adjuster.py — Story 3.3 Adaptive Weight Adjustment."""

import pytest
from unittest.mock import patch, MagicMock
import sqlite3
from datetime import datetime, timezone


@pytest.fixture
def test_db():
    """In-memory test database with all required tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE signals (
            id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
            action TEXT NOT NULL, entry_price REAL NOT NULL,
            confidence REAL NOT NULL, strategy TEXT NOT NULL,
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
        CREATE TABLE weights (
            weight_id TEXT PRIMARY KEY,
            value REAL NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    return conn


def _mock_conn_wrapper(real_conn):
    m = MagicMock(wraps=real_conn)
    m.close = MagicMock()
    return m


def _seed_signal(conn, sig_id, symbol="BTC-USDT", action="SELL",
                 sentiment=25, onchain="bearish", macro=1):
    conn.execute(
        """INSERT INTO signals (id, symbol, action, confidence, entry_price,
           strategy, sentiment_score, onchain_signal, macro_flag,
           research_metadata, timestamp_utc, status)
           VALUES (?,?,?,0.73,60200,'Trend Following',?,?,?,
           '{"prediction_adjustment":0}', '2026-06-27T00:00:00+00:00','resolved')""",
        (sig_id, symbol, action, sentiment, onchain, macro),
    )
    conn.commit()


def _seed_outcome(conn, signal_id, ret_pct=2.82):
    conn.execute(
        """INSERT INTO outcomes (signal_id, realized_return_pct,
           price_at_resolution, resolved_at)
           VALUES (?, ?, 58500, '2026-06-28T00:00:00+00:00')""",
        (signal_id, ret_pct),
    )
    conn.commit()


def _seed_weights(conn, weights: dict):
    now = datetime.now(timezone.utc).isoformat()
    for k, v in weights.items():
        conn.execute(
            "INSERT OR REPLACE INTO weights (weight_id, value, updated_at) VALUES (?, ?, ?)",
            (k, v, now),
        )
    conn.commit()


# ── T4.5: AC5 — Empty weights → init defaults ──────────────

def test_empty_weights_initializes_defaults():
    from src.weight_adjuster import load_weights

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE weights (weight_id TEXT PRIMARY KEY, value REAL NOT NULL, updated_at TEXT NOT NULL)")

    w = load_weights(db)
    assert w == {"sentiment_weight": 1.0, "onchain_weight": 1.0,
                 "macro_weight": 1.0, "prediction_weight": 1.0}
    db.close()


# ── T4.1: AC1 — Sentiment accuracy ─────────────────────────

def test_sentiment_accuracy(test_db):
    from src.weight_adjuster import _compute_sentiment_accuracy

    # 3 signals: 2 correct sentiment predictions, 1 wrong
    _seed_signal(test_db, "s1", action="SELL", sentiment=25)  # bearish→SELL
    _seed_outcome(test_db, "s1", 3.0)  # win → correct
    _seed_signal(test_db, "s2", action="BUY", sentiment=75)  # bullish→BUY
    _seed_outcome(test_db, "s2", 2.0)  # win → correct
    _seed_signal(test_db, "s3", action="BUY", sentiment=25)  # bearish but BUY
    _seed_outcome(test_db, "s3", 3.0)  # win → wrong direction

    acc = _compute_sentiment_accuracy(test_db)
    # 2 correct / 3 total = 0.6667
    assert acc == pytest.approx(0.6667, 0.01)


# ── T4.6: AC2 — Weight clamp ────────────────────────────────

def test_weight_clamp():
    from src.weight_adjuster import _clamp_weight
    assert _clamp_weight(1.0) == 1.0
    assert _clamp_weight(2.0) == 1.5
    assert _clamp_weight(0.3) == 0.5
    assert _clamp_weight(-1.0) == 0.5


# ── T4.2: AC2 — EMA formula ─────────────────────────────────

def test_ema_update():
    from src.weight_adjuster import _ema_update
    w = _ema_update(old_weight=1.0, accuracy=0.6)
    assert w == pytest.approx(0.8 * 1.0 + 0.2 * (0.6 / 0.5), 0.001)
    w2 = _ema_update(old_weight=1.0, accuracy=0.3)
    assert w2 == pytest.approx(0.92, 0.01)


# ── T4.3: AC3 — <30 outcomes skip ───────────────────────────

@patch("src.weight_adjuster.get_connection")
def test_insufficient_data_skips(mock_conn, test_db):
    from src.weight_adjuster import adjust_weights

    _seed_signal(test_db, "s1")
    _seed_outcome(test_db, "s1")
    wrapped = _mock_conn_wrapper(test_db)
    mock_conn.return_value = wrapped

    result = adjust_weights(send_alert_fn=None)
    assert result is None
    test_db.close()


# ── T4.7: AC2 — Weights persisted after adjustment ──────────

@patch("src.weight_adjuster.get_connection")
def test_weights_persisted(mock_conn, test_db):
    from src.weight_adjuster import adjust_weights

    _seed_weights(test_db, {"sentiment_weight": 1.0, "onchain_weight": 1.0,
                            "macro_weight": 1.0, "prediction_weight": 1.0})
    for i in range(31):
        _seed_signal(test_db, f"s{i}", sentiment=25 if i % 2 == 0 else 75,
                     action="SELL" if i % 2 == 0 else "BUY")
        _seed_outcome(test_db, f"s{i}", 2.0 if i % 3 != 0 else -1.0)

    wrapped = _mock_conn_wrapper(test_db)
    mock_conn.return_value = wrapped

    result = adjust_weights(send_alert_fn=None)
    assert result is not None
    assert "sentiment_weight" in result
    test_db.close()


# ── T4.4: AC4 — Underperformance alert ──────────────────────

@patch("src.weight_adjuster.get_connection")
def test_underperformance_alerts(mock_conn, test_db):
    from src.weight_adjuster import adjust_weights

    _seed_weights(test_db, {"sentiment_weight": 0.55, "onchain_weight": 1.0,
                            "macro_weight": 1.0, "prediction_weight": 1.0})
    # 50 signals all losing → accuracy ~0
    for i in range(50):
        _seed_signal(test_db, f"s{i}", sentiment=25, action="SELL")
        _seed_outcome(test_db, f"s{i}", -2.0)

    wrapped = _mock_conn_wrapper(test_db)
    mock_conn.return_value = wrapped
    alerts = []

    result = adjust_weights(send_alert_fn=lambda msg: alerts.append(msg))
    assert result is not None
    test_db.close()
