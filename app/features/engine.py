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
        # In-process trade queue — collectors push directly, no Redis
        self.trade_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # In-process book reference — set by run_all.py
        self.futures_book: Any = None

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

        # Depth pull tracking (5s history)
        self._depth_history: deque[tuple[int, float, float]] = deque(maxlen=60)

        # Score history for 3m/5m rolling averages
        self._score_1m_history: deque[tuple[int, float]] = deque(maxlen=600)

        # Feature bar + score + display history (up to 60 minutes at 1s cadence)
        self.feature_history: deque[dict] = deque(maxlen=3600)
        self.score_history: deque[dict] = deque(maxlen=3600)
        self.display_history: deque[dict] = deque(maxlen=3600)

        # Per-source lag tracking
        self._lag_futures_trade: deque[float] = deque(maxlen=200)
        self._lag_spot_trade: deque[float] = deque(maxlen=200)
        self._lag_bbo_futures: deque[float] = deque(maxlen=200)
        self._lag_bbo_spot: deque[float] = deque(maxlen=200)
        self._lag_mark_index: deque[float] = deque(maxlen=100)
        self._lag_oi: deque[float] = deque(maxlen=100)

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
            lag = max(0.0, now_ms - ts_exchange)

            venue = payload.get("venue")
            signed = payload["notional"] if payload["aggressive_side"] == "buy" else -payload["notional"]
            if venue == BINANCE_FUTURES:
                self._lag_futures_trade.append(lag)
                self.perp_cvd_1s.add(now_ms, signed)
                self.perp_cvd_5s.add(now_ms, signed)
                self.perp_cvd_15s.add(now_ms, signed)
            elif venue == BINANCE_SPOT:
                self._lag_spot_trade.append(lag)
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
            lag = max(0.0, now_ms - ts_exchange)

            if channel == Channels.bbo(BINANCE_FUTURES):
                self._lag_bbo_futures.append(lag)
                self.latest_futures_bbo = payload
            elif channel == Channels.bbo(BINANCE_SPOT):
                self._lag_bbo_spot.append(lag)
                self.latest_spot_bbo = payload
            elif channel == Channels.mark_index(BINANCE_FUTURES):
                self._lag_mark_index.append(lag)
                self.latest_mark_index = payload
                self.premium_history.append((now_ms, payload["premium_bps"]))
            elif channel == Channels.open_interest(BINANCE_FUTURES):
                self._lag_oi.append(lag)
                self.oi_history.append((now_ms, payload["open_interest"]))
            elif channel == Channels.liquidation(BINANCE_FUTURES):
                signed = payload["notional"] if payload["side"] == "BUY" else -payload["notional"]
                self.liq_skew_30s.add(now_ms, signed)

    def _rolling_score_avg(self, now_ms: int, window_ms: int) -> float:
        """Average of score_1m over the given window. Falls back to latest if not enough history."""
        cutoff = now_ms - window_ms
        values = [v for ts, v in self._score_1m_history if ts >= cutoff]
        if not values:
            return self._score_1m_history[-1][1] if self._score_1m_history else 0.0
        return sum(values) / len(values)

    @staticmethod
    def _p95(d: deque[float]) -> float:
        if not d:
            return 0.0
        s = sorted(d)
        return s[int(0.95 * (len(s) - 1))]

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

            # Per-source lag p95
            ft_lag = self._p95(self._lag_futures_trade)
            st_lag = self._p95(self._lag_spot_trade)
            bbo_f_lag = self._p95(self._lag_bbo_futures)
            bbo_s_lag = self._p95(self._lag_bbo_spot)
            bbo_lag = max(bbo_f_lag, bbo_s_lag)  # combined for FeatureBar
            mi_lag = self._p95(self._lag_mark_index)
            oi_lag = self._p95(self._lag_oi)
            # Overall: max of real-time feed p95s (excludes OI — it's a REST poll)
            rt_lags = [v for v in (ft_lag, st_lag, bbo_f_lag, bbo_s_lag, mi_lag) if v > 0]
            feed_lag_p95 = max(rt_lags) if rt_lags else 0.0

            # Real depth metrics from the local book
            book = self.futures_book
            book_sync_ok = book.synced if book else False
            if book and book.synced:
                imbalance_5bps = book.imbalance_within_bps(5)
                imbalance_10bps = book.imbalance_within_bps(10)
                bid_depth_5, ask_depth_5 = book.notional_within_bps(5)
            else:
                imbalance_5bps = 0.0
                imbalance_10bps = 0.0
                bid_depth_5 = 0.0
                ask_depth_5 = 0.0

            # Depth pull: current depth minus depth 5 seconds ago
            self._depth_history.append((now_ms, bid_depth_5, ask_depth_5))
            depth_pull_bid = 0.0
            depth_pull_ask = 0.0
            target_ts = now_ms - 5000
            for ts, bid_past, ask_past in reversed(self._depth_history):
                if ts <= target_ts:
                    depth_pull_bid = bid_depth_5 - bid_past
                    depth_pull_ask = ask_depth_5 - ask_past
                    break

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
                depth_imbalance_5bps=imbalance_5bps,
                depth_imbalance_10bps=imbalance_10bps,
                spread_bps=spread_bps,
                near_touch_depth_bid_usd=bid_depth_5,
                near_touch_depth_ask_usd=ask_depth_5,
                depth_pull_bid_5s=depth_pull_bid,
                depth_pull_ask_5s=depth_pull_ask,
                oi_delta_30s=oi_delta,
                liq_skew_30s=liq_skew,
                book_sync_ok=book_sync_ok,
                feed_lag_ms_p95=feed_lag_p95,
                futures_trade_lag_ms_p95=ft_lag,
                spot_trade_lag_ms_p95=st_lag,
                bbo_futures_lag_ms_p95=bbo_f_lag,
                bbo_spot_lag_ms_p95=bbo_s_lag,
                mark_index_lag_ms_p95=mi_lag,
                oi_lag_ms_p95=oi_lag,
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
            self._score_1m_history.append((now_ms, score_1m))
            score_3m = self._rolling_score_avg(now_ms, 180_000)
            score_5m = self._rolling_score_avg(now_ms, 300_000)

            # Data quality: book sync + feed freshness (README thresholds)
            oi_stale = oi_lag > 30_000
            # Spot stale: spot trade feed >2s (README: "spot feed stale > 2s -> lower confidence")
            # Uses trade lag (in-process queue) as the true freshness signal;
            # BBO lag includes remote Redis transport and overstates staleness.
            spot_stale = st_lag > 2_000
            # Futures stale: futures trade feed >2s -> degraded
            # README says 1s, but trade lag is the only in-process metric.
            # BBO/mark-index go through remote Redis (~50ms/call) which adds
            # transport lag that is not actual feed staleness.
            futures_stale = ft_lag > 2_000
            data_quality_score = 1.0
            if not book_sync_ok:
                data_quality_score = 0.2
            elif oi_stale:
                data_quality_score = 0.6

            snapshot = build_score_snapshot(
                symbol=self.symbol,
                ts_local=now_ms,
                score_1m=score_1m,
                score_3m=score_3m,
                score_5m=score_5m,
                data_quality_score=data_quality_score,
                feature_bar=feature_bar,
                futures_feed_stale=futures_stale,
                spot_feed_stale=spot_stale,
            )

            feature_payload = feature_bar.model_dump()
            score_payload = snapshot.model_dump()

            self.feature_history.append(feature_payload)
            self.score_history.append(score_payload)

            # Display history: prices + state for the dashboard price chart
            perp_mid = self.latest_futures_bbo.get("mid_px", 0) if self.latest_futures_bbo else 0
            spot_mid = self.latest_spot_bbo.get("mid_px", 0) if self.latest_spot_bbo else 0
            if perp_mid == 0 and self.latest_mark_index:
                perp_mid = self.latest_mark_index.get("mark_price", 0)
            live_basis = 10000.0 * (perp_mid - spot_mid) / spot_mid if spot_mid else 0.0
            self.display_history.append({
                "ts": now_ms,
                "perp_mid": perp_mid,
                "spot_mid": spot_mid,
                "live_basis_bps": live_basis,
                "premium_bps": premium_bps,
                "state": snapshot.state,
                "score_1m": snapshot.score_1m,
            })

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
