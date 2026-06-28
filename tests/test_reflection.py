"""Tests for reflection.py — Story 3.2 LLM Reflection Generator."""

import pytest
from unittest.mock import patch, MagicMock
import sqlite3
from datetime import datetime, timezone


@pytest.fixture
def test_db():
    """In-memory test database with signals + outcomes tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE signals (
            id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'HOLD')),
            confidence REAL NOT NULL, entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL, strategy TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '1d',
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


def _mock_conn_wrapper(real_conn):
    m = MagicMock(wraps=real_conn)
    m.close = MagicMock()
    return m


def _seed_signal(conn, sig_id, symbol="BTC-USDT", action="SELL",
                 entry=60200, strategy="Trend Following", confidence=0.73):
    conn.execute(
        """INSERT INTO signals (id, symbol, action, confidence, entry_price,
           stop_loss, strategy, timeframe, timestamp_utc, research_metadata,
           status) VALUES (?,?,?,?,?,0,'Trend Following','1d',
           '2026-06-27T00:00:00+00:00',
           '{"sentiment_score":25,"onchain_signal":"bearish","macro_flag":true}',
           'resolved')""",
        (sig_id, symbol, action, confidence, entry),
    )
    conn.commit()


def _seed_outcome(conn, signal_id, ret_pct=2.82, price=58500):
    conn.execute(
        """INSERT INTO outcomes (signal_id, realized_return_pct,
           price_at_resolution, resolved_at)
           VALUES (?, ?, ?, '2026-06-28T00:00:00+00:00')""",
        (signal_id, ret_pct, price),
    )
    conn.commit()


# ── T4.1: AC1 — Prompt contains all required fields ────────

def test_build_prompt_contains_all_fields():
    from src.reflection import build_reflection_prompt

    signal = {"symbol": "BTC-USDT", "action": "SELL", "entry_price": 60200,
              "confidence": 0.73, "strategy": "Trend Following",
              "sentiment_score": 25, "onchain_signal": "bearish",
              "macro_flag": True}
    outcome = {"realized_return_pct": 2.82, "price_at_resolution": 58500,
               "signal_id": "sig-001", "win": True}

    prompt = build_reflection_prompt(outcome, signal)
    assert "BTC-USDT" in prompt
    assert "SELL" in prompt
    assert "60200" in prompt
    assert "58500" in prompt
    assert "+2.82" in prompt
    assert "Trend Following" in prompt
    assert "win" in prompt.lower()
    assert "Return ONLY 1-2 sentences" in prompt


# ── T4.2: AC4 — Deterministic fallback format ──────────────

def test_deterministic_fallback_win():
    from src.reflection import _deterministic_fallback

    signal = {"symbol": "BTC-USDT", "action": "SELL"}
    outcome = {"realized_return_pct": 2.82}

    fb = _deterministic_fallback(outcome, signal)
    assert "BTC-USDT" in fb
    assert "SELL" in fb
    assert "+2.8%" in fb


def test_deterministic_fallback_loss():
    from src.reflection import _deterministic_fallback

    signal = {"symbol": "ETH-USDT", "action": "BUY"}
    outcome = {"realized_return_pct": -1.5}

    fb = _deterministic_fallback(outcome, signal)
    assert "ETH-USDT" in fb
    assert "BUY" in fb
    assert "-1.5%" in fb  # correct format with sign
    assert "did not work out" in fb  # loss message


# ── T4.3: AC5 — Truncation at 300 chars ───────────────────

def test_truncate_long_response():
    from src.reflection import _truncate_response

    short = "short response"
    assert _truncate_response(short) == short

    long = "x" * 350
    result = _truncate_response(long)
    assert len(result) == 303  # 300 + "..."
    assert result.endswith("...")


# ── T4.4: AC4+AC6 — LLM failure → fallback ─────────────────

@patch("src.reflection.requests.post")
def test_llm_failure_falls_back(mock_post):
    from src.reflection import generate_reflection

    mock_post.side_effect = Exception("Connection timeout")

    signal = {"symbol": "BTC-USDT", "action": "SELL", "strategy": "Test"}
    outcome = {"realized_return_pct": 2.82, "signal_id": "sig-001", "win": True}

    text, llm_used = generate_reflection(outcome, signal,
                                          api_key="test", base_url="http://test",
                                          model="test", timeout=3, max_tokens=150)
    assert "BTC-USDT" in text
    assert llm_used == False


@patch("src.reflection.requests.post")
def test_llm_no_api_key_falls_back(mock_post):
    from src.reflection import generate_reflection
    signal = {"symbol": "ETH-USDT", "action": "BUY", "strategy": "Test"}
    outcome = {"realized_return_pct": -1.5, "signal_id": "sig-002", "win": False}

    text, llm_used = generate_reflection(outcome, signal,
                                          api_key="", base_url="http://test",
                                          model="test", timeout=3, max_tokens=150)
    assert "ETH-USDT" in text
    assert llm_used == False
    mock_post.assert_not_called()


@patch("src.reflection.requests.post")
def test_llm_success(mock_post):
    from src.reflection import generate_reflection

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Good signal. On-chain confirmed."}}],
        "usage": {"total_tokens": 45},
    }
    mock_post.return_value = mock_resp

    signal = {"symbol": "BTC-USDT", "action": "SELL", "strategy": "Test"}
    outcome = {"realized_return_pct": 2.82, "signal_id": "sig-001", "win": True}

    text, llm_used = generate_reflection(outcome, signal,
                                          api_key="test", base_url="http://test",
                                          model="test", timeout=3, max_tokens=150)
    assert text == "Good signal. On-chain confirmed."
    assert llm_used == True


# ── T4.5: Integration test — reflection stored in DB ───────

@patch("src.reflection.requests.post")
@patch("src.db.get_connection")
def test_reflection_stored_in_db(mock_conn_fn, mock_post, test_db):
    from src.reflection import generate_reflections

    _seed_signal(test_db, "sig-001")
    _seed_outcome(test_db, "sig-001")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Reflection text here."}}],
        "usage": {"total_tokens": 30},
    }
    mock_post.return_value = mock_resp

    wrapped = _mock_conn_wrapper(test_db)
    mock_conn_fn.return_value = wrapped

    outcome_rows = [{"signal_id": "sig-001", "realized_return_pct": 2.82,
                     "price_at_resolution": 58500, "win": True,
                     "symbol": "BTC-USDT"}]

    results = generate_reflections(outcome_rows, "test-key", "http://test",
                                   "test-model", 3, 150)
    assert len(results) == 1
    assert results[0]["reflection_text"] == "Reflection text here."
    assert results[0]["llm_used"] == True

    row = test_db.execute("SELECT reflection_text, llm_used FROM outcomes WHERE signal_id='sig-001'").fetchone()
    assert row["reflection_text"] == "Reflection text here."
    assert row["llm_used"] == 1
    test_db.close()
