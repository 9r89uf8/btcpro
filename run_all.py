"""Unified launcher — starts all collectors, feature engine, API, and dashboard."""

import asyncio
import logging
import signal
import sys
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-35s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("launcher")


async def run_services() -> None:
    from app.bus import RedisBus
    from app.config import get_settings

    # Clear cached settings to pick up fresh .env
    get_settings.cache_clear()
    settings = get_settings()
    bus = RedisBus(settings.redis_url)

    # Verify Redis before starting anything
    try:
        await bus.set_json("state:launcher:heartbeat", {"ts": int(time.time() * 1000)})
        logger.info("Redis connected: %s", settings.redis_url.split("@")[-1])
    except Exception as e:
        logger.error("Cannot reach Redis: %s", e)
        return

    from app.collectors.binance_futures import BinanceFuturesCollector
    from app.collectors.binance_spot import BinanceSpotCollector
    from app.collectors.binance_open_interest import BinanceOpenInterestPoller
    from app.features.engine import FeatureEngine

    futures_collector = BinanceFuturesCollector(bus)
    spot_collector = BinanceSpotCollector(bus)
    oi_poller = BinanceOpenInterestPoller(bus)
    feature_engine = FeatureEngine(bus)

    # Wire in-process connections — bypasses Redis for high-frequency data
    futures_collector._trade_queue = feature_engine.trade_queue
    spot_collector._trade_queue = feature_engine.trade_queue
    feature_engine.futures_book = futures_collector.book

    # Share engine reference with API for history endpoints
    from app.api.main import app as api_app
    api_app.state.feature_engine = feature_engine

    logger.info("Starting collectors + feature engine...")

    tasks = [
        asyncio.create_task(futures_collector.run(), name="binance_futures"),
        asyncio.create_task(spot_collector.run(), name="binance_spot"),
        asyncio.create_task(oi_poller.run(), name="oi_poller"),
        asyncio.create_task(feature_engine.run(), name="feature_engine"),
    ]

    # Graceful shutdown on Ctrl+C
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    except NotImplementedError:
        # Windows doesn't support add_signal_handler
        pass

    logger.info("All services running. Press Ctrl+C to stop.")
    try:
        await stop.wait() if sys.platform != "win32" else await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bus.close()
        logger.info("Shutdown complete.")


def start_api_server() -> None:
    """Run the FastAPI server in a background thread."""
    import uvicorn
    from app.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )


def start_dashboard() -> None:
    """Run the Dash dashboard in a background thread."""
    from dashboard.app import app as dash_app

    dash_app.run(debug=False, port=8050, use_reloader=False)


def main() -> None:
    # Start API server in a thread
    api_thread = threading.Thread(target=start_api_server, daemon=True, name="api")
    api_thread.start()
    logger.info("API server starting on http://localhost:8000")

    # Start dashboard in a thread
    dash_thread = threading.Thread(target=start_dashboard, daemon=True, name="dashboard")
    dash_thread.start()
    logger.info("Dashboard starting on http://localhost:8050")

    # Run async services in the main thread
    try:
        asyncio.run(run_services())
    except KeyboardInterrupt:
        logger.info("Interrupted.")


if __name__ == "__main__":
    main()
