"""API contract tests.

These test the API endpoints with a mock Redis bus.
History endpoints require run_all.py (feature_engine wiring) and return
an empty result when run standalone — that is the expected contract.
"""

import time
from collections import deque
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app, bus


@pytest.fixture(autouse=True)
def _mock_bus(monkeypatch):
    """Replace the real Redis bus with a dict-backed mock."""
    store: dict[str, dict] = {}

    async def mock_get_json(key: str):
        return store.get(key)

    async def mock_set_json(key: str, payload: dict):
        store[key] = payload

    monkeypatch.setattr(bus, "get_json", mock_get_json)
    monkeypatch.setattr(bus, "set_json", mock_set_json)
    return store


@pytest.fixture
def client():
    return TestClient(app)


# ── /health ──────────────────────────────────────────────────────────

def test_health_returns_ok_with_empty_state(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["score_available"] is False
    assert data["features_available"] is False
    assert data["book_sync_ok"] is False


def test_health_reports_book_sync(client, _mock_bus):
    _mock_bus["state:book:binance_futures:btcusdt"] = {"synced": True}
    r = client.get("/health")
    assert r.json()["book_sync_ok"] is True


def test_health_reports_feed_ages(client, _mock_bus):
    now_ms = int(time.time() * 1000)
    _mock_bus["state:latest:feature_bar:btcusdt"] = {
        "bar_ts": now_ms - 500,
        "futures_trade_lag_ms_p95": 200,
        "spot_trade_lag_ms_p95": 150,
        "bbo_futures_lag_ms_p95": 300,
        "bbo_spot_lag_ms_p95": 250,
        "mark_index_lag_ms_p95": 100,
        "oi_lag_ms_p95": 5000,
    }
    _mock_bus["state:collector:binance_futures:btcusdt"] = {
        "public_feed_age_ms": 50,
        "trades_feed_age_ms": 100,
        "market_feed_age_ms": 200,
        "public_feed_stale": False,
        "trades_feed_stale": False,
        "market_feed_stale": False,
    }
    r = client.get("/health")
    data = r.json()
    assert data["last_event_age_ms"] is not None
    assert data["futures_feed_age_ms"] == 200
    assert data["futures_trade_lag_ms"] == 200
    assert data["oi_stale"] is False


# ── /latest/* ────────────────────────────────────────────────────────

def test_latest_score_empty(client):
    r = client.get("/latest/score")
    assert r.status_code == 200
    assert r.json() == {}


def test_latest_score_returns_data(client, _mock_bus):
    _mock_bus["state:latest:score:btcusdt"] = {"score_1m": 0.5, "state": "mild_bullish"}
    r = client.get("/latest/score")
    assert r.json()["score_1m"] == 0.5


def test_latest_features_empty(client):
    r = client.get("/latest/features")
    assert r.status_code == 200
    assert r.json() == {}


def test_latest_features_returns_data(client, _mock_bus):
    _mock_bus["state:latest:feature_bar:btcusdt"] = {"bar_ts": 123, "perp_cvd_5s": 1000}
    r = client.get("/latest/features")
    assert r.json()["perp_cvd_5s"] == 1000


def test_latest_all_returns_aggregated(client, _mock_bus):
    _mock_bus["state:latest:score:btcusdt"] = {"score_1m": 0.3}
    _mock_bus["state:latest:feature_bar:btcusdt"] = {"bar_ts": 1}
    r = client.get("/latest/all")
    data = r.json()
    assert data["score"]["score_1m"] == 0.3
    assert data["features"]["bar_ts"] == 1
    # Empty venues return {}
    assert data["bbo_futures"] == {}


def test_latest_trade_endpoints(client, _mock_bus):
    _mock_bus["state:latest:trade:binance_futures:btcusdt"] = {"price": 68000}
    _mock_bus["state:latest:trade:binance_spot:btcusdt"] = {"price": 67999}
    assert client.get("/latest/trade/futures").json()["price"] == 68000
    assert client.get("/latest/trade/spot").json()["price"] == 67999


def test_latest_book_endpoints(client, _mock_bus):
    _mock_bus["state:book:binance_futures:btcusdt"] = {"synced": True}
    _mock_bus["state:book:binance_spot:btcusdt"] = {"synced": False}
    assert client.get("/latest/book/futures").json()["synced"] is True
    assert client.get("/latest/book/spot").json()["synced"] is False


# ── /history/* ───────────────────────────────────────────────────────

def test_history_features_without_engine(client):
    """Without run_all.py wiring, history returns empty — expected contract."""
    r = client.get("/history/features?minutes=5")
    assert r.status_code == 200
    data = r.json()
    assert data["bars"] == []
    assert "not connected" in data.get("message", "").lower()


def test_history_features_with_engine(client):
    """With engine wired, history returns bars from the ring buffer."""
    now_ms = int(time.time() * 1000)

    class FakeEngine:
        feature_history = deque()
        score_history = deque()

    engine = FakeEngine()
    engine.feature_history.append({"bar_ts": now_ms - 1000, "perp_cvd_5s": 100})
    engine.feature_history.append({"bar_ts": now_ms - 500, "perp_cvd_5s": 200})
    engine.feature_history.append({"bar_ts": now_ms - 400_000, "perp_cvd_5s": -50})  # >5min ago

    app.state.feature_engine = engine
    try:
        r = client.get("/history/features?minutes=5")
        data = r.json()
        assert data["count"] == 2  # old bar excluded
        assert data["bars"][0]["perp_cvd_5s"] == 100
    finally:
        del app.state.feature_engine


def test_history_score_with_engine(client):
    now_ms = int(time.time() * 1000)

    class FakeEngine:
        feature_history = deque()
        score_history = deque()

    engine = FakeEngine()
    engine.score_history.append({"ts_local": now_ms - 1000, "score_1m": 0.5})

    app.state.feature_engine = engine
    try:
        r = client.get("/history/score?minutes=5")
        data = r.json()
        assert data["count"] == 1
        assert data["scores"][0]["score_1m"] == 0.5
    finally:
        del app.state.feature_engine
