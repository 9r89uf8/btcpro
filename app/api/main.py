from __future__ import annotations

from fastapi import FastAPI, Query

from app.bus import RedisBus
from app.config import get_settings

settings = get_settings()
bus = RedisBus(settings.redis_url)
app = FastAPI(title="BTC Microstructure Dashboard API", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    latest_score = await bus.get_json("state:latest:score:btcusdt")
    latest_features = await bus.get_json("state:latest:feature_bar:btcusdt")
    futures_book = await bus.get_json("state:book:binance_futures:btcusdt")
    return {
        "status": "ok",
        "score_available": latest_score is not None,
        "features_available": latest_features is not None,
        "book_sync_ok": bool(futures_book.get("synced")) if futures_book else False,
    }


@app.get("/latest/score")
async def latest_score() -> dict:
    return await bus.get_json("state:latest:score:btcusdt") or {}


@app.get("/latest/bbo/futures")
async def latest_bbo_futures() -> dict:
    return await bus.get_json("state:latest:bbo:binance_futures:btcusdt") or {}


@app.get("/latest/book/futures")
async def latest_book_futures() -> dict:
    return await bus.get_json("state:book:binance_futures:btcusdt") or {}


@app.get("/latest/book/spot")
async def latest_book_spot() -> dict:
    return await bus.get_json("state:book:binance_spot:btcusdt") or {}


@app.get("/latest/bbo/spot")
async def latest_bbo_spot() -> dict:
    return await bus.get_json("state:latest:bbo:binance_spot:btcusdt") or {}


@app.get("/latest/all")
async def latest_all() -> dict:
    """Single call for the dashboard — avoids multiple round-trips."""
    features = await bus.get_json("state:latest:feature_bar:btcusdt")
    score = await bus.get_json("state:latest:score:btcusdt")
    bbo_f = await bus.get_json("state:latest:bbo:binance_futures:btcusdt")
    book_f = await bus.get_json("state:book:binance_futures:btcusdt")
    bbo_s = await bus.get_json("state:latest:bbo:binance_spot:btcusdt")
    book_s = await bus.get_json("state:book:binance_spot:btcusdt")
    mark = await bus.get_json("state:latest:mark_index:binance_futures:btcusdt")
    trade_f = await bus.get_json("state:latest:trade:binance_futures:btcusdt")
    trade_s = await bus.get_json("state:latest:trade:binance_spot:btcusdt")
    return {
        "features": features or {},
        "score": score or {},
        "bbo_futures": bbo_f or {},
        "book_futures": book_f or {},
        "bbo_spot": bbo_s or {},
        "book_spot": book_s or {},
        "mark_index": mark or {},
        "trade_futures": trade_f or {},
        "trade_spot": trade_s or {},
    }


@app.get("/latest/features")
async def latest_features() -> dict:
    return await bus.get_json("state:latest:feature_bar:btcusdt") or {}


@app.get("/history/features")
async def history_features(minutes: int = Query(default=60, ge=1, le=1440)) -> dict:
    # Placeholder. Replace with ClickHouse/Parquet query in Phase 2.
    return {
        "message": "Not implemented yet",
        "minutes": minutes,
    }


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await bus.close()
