from __future__ import annotations

from fastapi import FastAPI, Query

from app.bus import RedisBus
from app.config import get_settings
from app.contract import BINANCE_FUTURES, BINANCE_SPOT, Channels, Keys

settings = get_settings()
bus = RedisBus(settings.redis_url)
app = FastAPI(title="BTC Microstructure Dashboard API", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    latest_score = await bus.get_json(Keys.score())
    latest_features = await bus.get_json(Keys.feature_bar())
    futures_book = await bus.get_json(Keys.book(BINANCE_FUTURES))
    return {
        "status": "ok",
        "score_available": latest_score is not None,
        "features_available": latest_features is not None,
        "book_sync_ok": bool(futures_book.get("synced")) if futures_book else False,
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
async def history_features(minutes: int = Query(default=60, ge=1, le=1440)) -> dict:
    return {
        "message": "Not implemented yet",
        "minutes": minutes,
    }


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await bus.close()
