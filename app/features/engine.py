from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any

from app.bus import RedisBus
from app.config import get_settings
from app.contract import BINANCE_FUTURES, BINANCE_SPOT, Channels, Keys
from app.features.rolling import RollingSeries, RollingSignedWindow
from app.features.scoring import ScoreInputs, build_score_snapshot, score_linear
from app.models import FeatureBar


class FeatureEngine:
    """
    Receives trades via in-process queue (no Redis round-trip),
    other events via Redis pub/sub.
    """

    def __init__(self, bus: RedisBus) -> None:
        self.bus = bus
        self.settings = get_settings()
        self.symbol = self.settings.symbol.upper()
        # In-process trade queues — collectors push directly, no Redis
        self.trade_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        self.perp_cvd_1s = RollingSignedWindow(1000)
        self.perp_cvd_5s = RollingSignedWindow(5000)
        self.perp_cvd_15s = RollingSignedWindow(15000)

        self.spot_cvd_1s = RollingSignedWindow(1000)
        self.spot_cvd_5s = RollingSignedWindow(5000)
        self.spot_cvd_15s = RollingSignedWindow(15000)

        self.liq_skew_30s = RollingSignedWindow(30000)
        self.oi_history: deque[tuple[int, float]] = deque(maxlen=300)
        self.premium_history: deque[tuple[int, float]] = deque(maxlen=300)

        self.z_perp_cvd = RollingSeries()
        self.z_spot_cvd = RollingSeries()
        self.z_depth = RollingSeries()
        self.z_delta_premium = RollingSeries()
        self.z_oi_delta = RollingSeries()
        self.z_liq_skew = RollingSeries()
        self.z_spread = RollingSeries()
        self.z_feed_lag = RollingSeries()

        self.latest_mark_index: dict | None = None
        self.latest_futures_bbo: dict | None = None
        self.latest_spot_bbo: dict | None = None

        self.latest_depth_imbalance_10bps: float = 0.0
        self.latest_depth_imbalance_5bps: float = 0.0
        self.latest_bid_depth_usd: float = 0.0
        self.latest_ask_depth_usd: float = 0.0
        self.latest_bid_depth_usd_5s_ago: float = 0.0
        self.latest_ask_depth_usd_5s_ago: float = 0.0

        self.book_sync_ok: bool = True
        self.feed_lags: deque[float] = deque(maxlen=200)

    async def run(self) -> None:
        pubsub = self.bus.client.pubsub()
        await pubsub.subscribe(
            Channels.bbo(BINANCE_FUTURES),
            Channels.bbo(BINANCE_SPOT),
            Channels.mark_index(BINANCE_FUTURES),
            Channels.open_interest(BINANCE_FUTURES),
            Channels.liquidation(BINANCE_FUTURES),
        )
        await asyncio.gather(
            self._consume_trades(),
            self._consume_redis(pubsub),
            self._tick(),
        )

    async def _consume_trades(self) -> None:
        """Read trades from in-process queue (zero latency, no Redis)."""
        while True:
            payload = await self.trade_queue.get()
            now_ms = int(time.time() * 1000)
            ts_exchange = int(payload.get("ts_exchange", now_ms))
            self.feed_lags.append(max(0.0, now_ms - ts_exchange))

            venue = payload.get("venue")
            signed = payload["notional"] if payload["aggressive_side"] == "buy" else -payload["notional"]
            if venue == BINANCE_FUTURES:
                self.perp_cvd_1s.add(now_ms, signed)
                self.perp_cvd_5s.add(now_ms, signed)
                self.perp_cvd_15s.add(now_ms, signed)
            elif venue == BINANCE_SPOT:
                self.spot_cvd_1s.add(now_ms, signed)
                self.spot_cvd_5s.add(now_ms, signed)
                self.spot_cvd_15s.add(now_ms, signed)

    async def _consume_redis(self, pubsub) -> None:
        """Read low-frequency events from Redis pub/sub."""
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message["channel"]
            payload = json.loads(message["data"])
            now_ms = int(time.time() * 1000)
            ts_exchange = int(payload.get("ts_exchange", now_ms))
            self.feed_lags.append(max(0.0, now_ms - ts_exchange))

            if channel == Channels.bbo(BINANCE_FUTURES):
                self.latest_futures_bbo = payload
            elif channel == Channels.bbo(BINANCE_SPOT):
                self.latest_spot_bbo = payload
            elif channel == Channels.mark_index(BINANCE_FUTURES):
                self.latest_mark_index = payload
                self.premium_history.append((now_ms, payload["premium_bps"]))
            elif channel == Channels.open_interest(BINANCE_FUTURES):
                self.oi_history.append((now_ms, payload["open_interest"]))
            elif channel == Channels.liquidation(BINANCE_FUTURES):
                signed = payload["notional"] if payload["side"] == "BUY" else -payload["notional"]
                self.liq_skew_30s.add(now_ms, signed)

    def _premium_delta_5s(self, now_ms: int) -> float:
        if not self.premium_history:
            return 0.0
        current = self.premium_history[-1][1]
        target_ts = now_ms - 5000
        past = None
        for ts, value in reversed(self.premium_history):
            if ts <= target_ts:
                past = value
                break
        if past is None:
            past = self.premium_history[0][1]
        return current - past

    def _oi_delta_30s(self, now_ms: int) -> float:
        if not self.oi_history:
            return 0.0
        current = self.oi_history[-1][1]
        target_ts = now_ms - 30000
        past = None
        for ts, value in reversed(self.oi_history):
            if ts <= target_ts:
                past = value
                break
        if past is None:
            past = self.oi_history[0][1]
        return current - past

    async def _tick(self) -> None:
        while True:
            now_ms = int(time.time() * 1000)

            perp_1 = self.perp_cvd_1s.sum(now_ms)
            perp_5 = self.perp_cvd_5s.sum(now_ms)
            perp_15 = self.perp_cvd_15s.sum(now_ms)

            spot_1 = self.spot_cvd_1s.sum(now_ms)
            spot_5 = self.spot_cvd_5s.sum(now_ms)
            spot_15 = self.spot_cvd_15s.sum(now_ms)

            premium_bps = float(self.latest_mark_index["premium_bps"]) if self.latest_mark_index else 0.0
            delta_premium = self._premium_delta_5s(now_ms)
            oi_delta = self._oi_delta_30s(now_ms)
            liq_skew = self.liq_skew_30s.sum(now_ms)
            spread_bps = float(self.latest_futures_bbo["spread_bps"]) if self.latest_futures_bbo else 0.0
            feed_lag_p95 = sorted(self.feed_lags)[int(0.95 * (len(self.feed_lags) - 1))] if self.feed_lags else 0.0

            # TODO: wire real local-book depth stats here.
            feature_bar = FeatureBar(
                symbol=self.symbol,
                bar_ts=now_ms,
                perp_cvd_1s=perp_1,
                perp_cvd_5s=perp_5,
                perp_cvd_15s=perp_15,
                spot_cvd_1s=spot_1,
                spot_cvd_5s=spot_5,
                spot_cvd_15s=spot_15,
                premium_bps=premium_bps,
                delta_premium_bps_5s=delta_premium,
                depth_imbalance_5bps=self.latest_depth_imbalance_5bps,
                depth_imbalance_10bps=self.latest_depth_imbalance_10bps,
                spread_bps=spread_bps,
                near_touch_depth_bid_usd=self.latest_bid_depth_usd,
                near_touch_depth_ask_usd=self.latest_ask_depth_usd,
                depth_pull_bid_5s=self.latest_bid_depth_usd - self.latest_bid_depth_usd_5s_ago,
                depth_pull_ask_5s=self.latest_ask_depth_usd - self.latest_ask_depth_usd_5s_ago,
                oi_delta_30s=oi_delta,
                liq_skew_30s=liq_skew,
                book_sync_ok=self.book_sync_ok,
                feed_lag_ms_p95=feed_lag_p95,
            )

            self.z_perp_cvd.add(perp_5)
            self.z_spot_cvd.add(spot_5)
            self.z_depth.add(feature_bar.depth_imbalance_10bps)
            self.z_delta_premium.add(delta_premium)
            self.z_oi_delta.add(oi_delta)
            self.z_liq_skew.add(liq_skew)
            self.z_spread.add(spread_bps)
            self.z_feed_lag.add(feed_lag_p95)

            inputs = ScoreInputs(
                z_perp_cvd_5s=self.z_perp_cvd.zscore(perp_5),
                z_spot_cvd_5s=self.z_spot_cvd.zscore(spot_5),
                z_depth_imbalance_10bps=self.z_depth.zscore(feature_bar.depth_imbalance_10bps),
                z_delta_premium_bps_5s=self.z_delta_premium.zscore(delta_premium),
                z_oi_delta_30s=self.z_oi_delta.zscore(oi_delta),
                z_liq_skew_30s=self.z_liq_skew.zscore(liq_skew),
                z_spread_bps=self.z_spread.zscore(spread_bps),
                z_feed_lag_ms_p95=self.z_feed_lag.zscore(feed_lag_p95),
            )

            score_1m = score_linear(inputs)
            score_3m = 0.75 * score_1m
            score_5m = 0.60 * score_1m
            data_quality_score = 1.0 if self.book_sync_ok else 0.2

            snapshot = build_score_snapshot(
                symbol=self.symbol,
                ts_local=now_ms,
                score_1m=score_1m,
                score_3m=score_3m,
                score_5m=score_5m,
                data_quality_score=data_quality_score,
                feature_bar=feature_bar,
            )

            feature_payload = feature_bar.model_dump()
            score_payload = snapshot.model_dump()

            await self.bus.set_json(Keys.feature_bar(), feature_payload)
            await self.bus.set_json(Keys.score(), score_payload)
            await self.bus.publish_json(Channels.feature_bar(), feature_payload)
            await self.bus.publish_json(Channels.score(), score_payload)

            await asyncio.sleep(1.0)


async def main() -> None:
    from app.bus import RedisBus as _RedisBus
    settings = get_settings()
    bus = _RedisBus(settings.redis_url)
    engine = FeatureEngine(bus)
    await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
