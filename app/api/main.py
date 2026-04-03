from __future__ import annotations

import time

from fastapi import FastAPI, Query

from app.bus import RedisBus
from app.config import get_settings
from app.contract import BINANCE_FUTURES, BINANCE_SPOT, Channels, Keys

settings = get_settings()
bus = RedisBus(settings.redis_url)
app = FastAPI(title="BTC Microstructure Dashboard API", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    now_ms = int(time.time() * 1000)
    latest_score = await bus.get_json(Keys.score())
    latest_features = await bus.get_json(Keys.feature_bar())
    futures_book = await bus.get_json(Keys.book(BINANCE_FUTURES))
    spot_book = await bus.get_json(Keys.book(BINANCE_SPOT))
    futures_collector = await bus.get_json(Keys.collector(BINANCE_FUTURES))
    features = latest_features or {}

    # Last event age: how old is the most recent feature bar
    bar_ts = features.get("bar_ts")
    last_event_age_ms = (now_ms - bar_ts) if bar_ts else None

    # Per-venue feed ages from collector state
    fc = futures_collector or {}
    futures_feed_age_ms = max(
        fc.get("public_feed_age_ms") or 0,
        fc.get("trades_feed_age_ms") or 0,
        fc.get("market_feed_age_ms") or 0,
    ) if futures_collector else None

    # Spot feed age: worst of spot trade lag and spot BBO lag
    spot_trade_lag = features.get("spot_trade_lag_ms_p95") or 0
    spot_bbo_lag = features.get("bbo_spot_lag_ms_p95") or 0
    spot_feed_age_ms = max(spot_trade_lag, spot_bbo_lag) if (spot_trade_lag or spot_bbo_lag) else None

    return {
        "status": "ok",
        "score_available": latest_score is not None,
        "features_available": latest_features is not None,
        "book_sync_ok": bool(futures_book.get("synced")) if futures_book else False,
        "spot_book_sync_ok": bool(spot_book.get("synced")) if spot_book else False,
        "last_event_age_ms": last_event_age_ms,
        "futures_feed_age_ms": futures_feed_age_ms,
        "spot_feed_age_ms": spot_feed_age_ms,
        "futures_trade_lag_ms": features.get("futures_trade_lag_ms_p95"),
        "spot_trade_lag_ms": features.get("spot_trade_lag_ms_p95"),
        "bbo_futures_lag_ms": features.get("bbo_futures_lag_ms_p95"),
        "bbo_spot_lag_ms": features.get("bbo_spot_lag_ms_p95"),
        "mark_index_lag_ms": features.get("mark_index_lag_ms_p95"),
        "oi_lag_ms": features.get("oi_lag_ms_p95"),
        "oi_stale": (features.get("oi_lag_ms_p95") or 0) > 30_000,
        "futures_feeds_stale": bool(fc.get("public_feed_stale") or
                                    fc.get("trades_feed_stale") or
                                    fc.get("market_feed_stale")) if futures_collector else None,
        "spot_feeds_stale": (spot_feed_age_ms is not None and spot_feed_age_ms > 2_000),
    }


@app.get("/latest/score")
async def latest_score() -> dict:
    return await bus.get_json(Keys.score()) or {}


@app.get("/latest/bbo/futures")
async def latest_bbo_futures() -> dict:
    return await bus.get_json(Keys.latest("bbo", BINANCE_FUTURES)) or {}


@app.get("/latest/trade/futures")
async def latest_trade_futures() -> dict:
    return await bus.get_json(Keys.latest("trade", BINANCE_FUTURES)) or {}


@app.get("/latest/open-interest/futures")
async def latest_open_interest_futures() -> dict:
    return await bus.get_json(Keys.latest("open_interest", BINANCE_FUTURES)) or {}


@app.get("/latest/mark-index/futures")
async def latest_mark_index_futures() -> dict:
    return await bus.get_json(Keys.latest("mark_index", BINANCE_FUTURES)) or {}


@app.get("/latest/liquidation/futures")
async def latest_liquidation_futures() -> dict:
    return await bus.get_json(Keys.latest("liquidation", BINANCE_FUTURES)) or {}


@app.get("/latest/collector/futures")
async def latest_collector_futures() -> dict:
    return await bus.get_json(Keys.collector(BINANCE_FUTURES)) or {}


@app.get("/latest/book/futures")
async def latest_book_futures() -> dict:
    return await bus.get_json(Keys.book(BINANCE_FUTURES)) or {}


@app.get("/latest/book/spot")
async def latest_book_spot() -> dict:
    return await bus.get_json(Keys.book(BINANCE_SPOT)) or {}


@app.get("/latest/trade/spot")
async def latest_trade_spot() -> dict:
    return await bus.get_json(Keys.latest("trade", BINANCE_SPOT)) or {}


@app.get("/latest/bbo/spot")
async def latest_bbo_spot() -> dict:
    return await bus.get_json(Keys.latest("bbo", BINANCE_SPOT)) or {}


@app.get("/latest/all")
async def latest_all() -> dict:
    """Single call for the dashboard — avoids multiple round-trips."""
    features = await bus.get_json(Keys.feature_bar())
    score = await bus.get_json(Keys.score())
    bbo_f = await bus.get_json(Keys.latest("bbo", BINANCE_FUTURES))
    collector_f = await bus.get_json(Keys.collector(BINANCE_FUTURES))
    book_f = await bus.get_json(Keys.book(BINANCE_FUTURES))
    bbo_s = await bus.get_json(Keys.latest("bbo", BINANCE_SPOT))
    book_s = await bus.get_json(Keys.book(BINANCE_SPOT))
    mark = await bus.get_json(Keys.latest("mark_index", BINANCE_FUTURES))
    trade_f = await bus.get_json(Keys.latest("trade", BINANCE_FUTURES))
    trade_s = await bus.get_json(Keys.latest("trade", BINANCE_SPOT))
    return {
        "features": features or {},
        "score": score or {},
        "bbo_futures": bbo_f or {},
        "collector_futures": collector_f or {},
        "book_futures": book_f or {},
        "bbo_spot": bbo_s or {},
        "book_spot": book_s or {},
        "mark_index": mark or {},
        "trade_futures": trade_f or {},
        "trade_spot": trade_s or {},
    }


@app.get("/latest/features")
async def latest_features() -> dict:
    return await bus.get_json(Keys.feature_bar()) or {}


@app.get("/history/features")
async def history_features(minutes: int = Query(default=5, ge=1, le=60)) -> dict:
    engine = app.state.feature_engine if hasattr(app.state, "feature_engine") else None
    if engine is None:
        return {"bars": [], "message": "Feature engine not connected"}
    cutoff_ms = int(time.time() * 1000) - minutes * 60_000
    bars = [b for b in engine.feature_history if b.get("bar_ts", 0) >= cutoff_ms]
    return {"bars": bars, "count": len(bars), "minutes": minutes}


@app.get("/history/score")
async def history_score(minutes: int = Query(default=5, ge=1, le=60)) -> dict:
    engine = app.state.feature_engine if hasattr(app.state, "feature_engine") else None
    if engine is None:
        return {"scores": [], "message": "Feature engine not connected"}
    cutoff_ms = int(time.time() * 1000) - minutes * 60_000
    scores = [s for s in engine.score_history if s.get("ts_local", 0) >= cutoff_ms]
    return {"scores": scores, "count": len(scores), "minutes": minutes}


from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app):
    yield
    await bus.close()

app.router.lifespan_context = _lifespan
