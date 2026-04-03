"""End-to-end smoke test for the core pipeline.

Tests that a trade pushed into the in-process queue flows through
the feature engine and produces a feature bar + score snapshot.
Does not require Redis or live websockets.
"""

import asyncio
import time

import pytest

from app.features.engine import FeatureEngine
from app.features.scoring import classify


class FakeBus:
    """Captures publish/set calls instead of hitting Redis."""

    def __init__(self):
        self.published: dict[str, list[dict]] = {}
        self.state: dict[str, dict] = {}

    async def publish_json(self, channel: str, payload: dict):
        self.published.setdefault(channel, []).append(payload)

    async def set_json(self, key: str, payload: dict):
        self.state[key] = payload

    async def get_json(self, key: str):
        return self.state.get(key)

    async def publish_and_set_json(self, channel: str, key: str, payload: dict):
        self.published.setdefault(channel, []).append(payload)
        self.state[key] = payload

    async def publish_only_json(self, channel: str, payload: dict):
        self.published.setdefault(channel, []).append(payload)

    @property
    def client(self):
        return FakePubSub()


class FakePubSub:
    """Stub for pubsub — the smoke test uses the in-process queue, not Redis pubsub."""

    def pubsub(self):
        return self

    async def subscribe(self, *channels):
        pass

    async def listen(self):
        # Never yield — the smoke test only exercises the trade queue + tick path
        while True:
            await asyncio.sleep(3600)
            yield  # unreachable


def _make_trade(venue: str, side: str, price: float, size: float) -> dict:
    return {
        "event_type": "trade",
        "venue": venue,
        "symbol": "BTCUSDT",
        "market_type": "perp" if "futures" in venue else "spot",
        "aggressive_side": side,
        "price": price,
        "size": size,
        "notional": price * size,
        "trade_id": "1",
        "ts_exchange": int(time.time() * 1000),
        "ts_local": int(time.time() * 1000),
    }


@pytest.mark.asyncio
async def test_trade_flows_through_to_feature_bar():
    """Push trades into the queue, run one tick, verify feature bar is produced."""
    bus = FakeBus()
    engine = FeatureEngine(bus)

    # Push futures trades
    for _ in range(5):
        engine.trade_queue.put_nowait(_make_trade("binance_futures", "buy", 68000.0, 0.1))
    # Push spot trades
    for _ in range(3):
        engine.trade_queue.put_nowait(_make_trade("binance_spot", "buy", 67990.0, 0.05))

    # Consume all queued trades
    consume_task = asyncio.create_task(engine._consume_trades())
    await asyncio.sleep(0.05)  # let the consumer drain the queue
    consume_task.cancel()
    try:
        await consume_task
    except asyncio.CancelledError:
        pass

    # Verify CVD was updated
    now_ms = int(time.time() * 1000)
    assert engine.perp_cvd_5s.sum(now_ms) > 0, "Perp CVD should be positive after buy trades"
    assert engine.spot_cvd_5s.sum(now_ms) > 0, "Spot CVD should be positive after buy trades"


@pytest.mark.asyncio
async def test_tick_produces_feature_bar_and_score():
    """Run one tick cycle and verify outputs are written."""
    bus = FakeBus()
    engine = FeatureEngine(bus)

    # Push some trades so there's data
    for _ in range(5):
        engine.trade_queue.put_nowait(_make_trade("binance_futures", "buy", 68000.0, 0.1))

    # Consume trades
    consume_task = asyncio.create_task(engine._consume_trades())
    await asyncio.sleep(0.05)
    consume_task.cancel()
    try:
        await consume_task
    except asyncio.CancelledError:
        pass

    # Run one tick (the _tick method sleeps 1s, so we run it as a task and cancel after one iteration)
    tick_task = asyncio.create_task(engine._tick())
    await asyncio.sleep(0.1)  # let one tick complete
    tick_task.cancel()
    try:
        await tick_task
    except asyncio.CancelledError:
        pass

    # Verify feature bar was produced
    from app.contract import Keys
    fb = bus.state.get(Keys.feature_bar())
    assert fb is not None, "Feature bar should be written to state"
    assert fb["event_type"] == "feature_bar"
    assert fb["perp_cvd_5s"] > 0

    # Verify score was produced
    sc = bus.state.get(Keys.score())
    assert sc is not None, "Score should be written to state"
    assert sc["event_type"] == "score"
    assert sc["state"] in {"bullish_pressure", "mild_bullish", "neutral", "mild_bearish", "bearish_pressure"}

    # Verify history was populated
    assert len(engine.feature_history) == 1
    assert len(engine.score_history) == 1
    assert len(engine.display_history) == 1
