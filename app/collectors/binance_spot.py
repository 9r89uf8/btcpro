from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any

import websockets

from app.books.binance_local_book import LocalBook, fetch_binance_spot_snapshot
from app.collectors.base import BaseCollector
from app.config import get_settings
from app.contract import BINANCE_SPOT, Channels, Keys
from app.models import BBOEvent, BookDeltaEvent, TradeEvent

logger = logging.getLogger(__name__)


class BinanceSpotCollector(BaseCollector):
    def __init__(self, bus):
        super().__init__(bus)
        self.settings = get_settings()
        self.symbol = self.settings.symbol.lower()
        self.book = LocalBook()
        self._buffered_deltas: deque[BookDeltaEvent] = deque(maxlen=5000)
        self._snapshot_pending = False
        self._book_sync_status = "idle"
        self._book_sync_reason = "startup"
        self._book_last_sync_ms: int | None = None
        self._last_spot_bbo_key: tuple[float, float] | None = None
        self._trade_queue: asyncio.Queue | None = None
        self._last_trade_state_write_ms: int = 0

    async def run(self) -> None:
        await asyncio.gather(
            self.run_trades(),
            self.run_depth(),
        )

    async def run_trades(self) -> None:
        stream = f"{self.symbol}@aggTrade"
        url = f"{self.settings.binance_spot_ws}/stream?streams={stream}"
        attempt = 0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=10000) as ws:
                    attempt = 0
                    async for raw in ws:
                        msg = json.loads(raw)
                        data = msg["data"]
                        event = self._parse_trade(data)
                        # Push to in-process queue for feature engine (zero latency)
                        if self._trade_queue is not None:
                            self._trade_queue.put_nowait(event.model_dump())
                        # Throttled latest-state write (~1/sec) so API endpoint works
                        now = self.now_ms()
                        if now - self._last_trade_state_write_ms >= 1000:
                            self._last_trade_state_write_ms = now
                            await self.bus.set_json(
                                Keys.latest("trade", BINANCE_SPOT),
                                event.model_dump(),
                            )
            except Exception:
                attempt += 1
                logger.warning("Spot trades ws disconnected, reconnect attempt %d", attempt, exc_info=True)
                await self.sleep_backoff(attempt)

    async def run_depth(self) -> None:
        stream = f"{self.symbol}@depth@100ms"
        url = f"{self.settings.binance_spot_ws}/stream?streams={stream}"
        attempt = 0
        while True:
            try:
                self._reset_book()
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=10000) as ws:
                    attempt = 0
                    asyncio.create_task(self._sync_local_book())
                    async for raw in ws:
                        msg = json.loads(raw)
                        data = msg["data"]
                        event = self._parse_book_delta(data)
                        await self._handle_depth_delta(event)
            except Exception:
                attempt += 1
                logger.warning("Spot depth ws disconnected, reconnect attempt %d", attempt, exc_info=True)
                await self.sleep_backoff(attempt)

    def _parse_trade(self, data: dict[str, Any]) -> TradeEvent:
        aggressive_side = "sell" if data["m"] else "buy"
        price = float(data["p"])
        size = float(data["q"])
        return TradeEvent(
            venue="binance_spot",
            symbol=data["s"],
            market_type="spot",
            aggressive_side=aggressive_side,
            price=price,
            size=size,
            notional=price * size,
            trade_id=str(data["a"]),
            ts_exchange=int(data["T"]),
            ts_local=self.now_ms(),
        )

    def _parse_book_delta(self, data: dict[str, Any]) -> BookDeltaEvent:
        return BookDeltaEvent(
            venue="binance_spot",
            symbol=data["s"],
            first_update_id=int(data["U"]),
            final_update_id=int(data["u"]),
            prev_final_update_id=int(data.get("pu", 0)) or None,
            bids=[[float(px), float(sz)] for px, sz in data.get("b", [])],
            asks=[[float(px), float(sz)] for px, sz in data.get("a", [])],
            ts_exchange=int(data["E"]),
            ts_local=self.now_ms(),
        )

    async def _handle_depth_delta(self, event: BookDeltaEvent) -> None:
        self._buffered_deltas.append(event)

        if not self.book.synced:
            return

        applied = self.book.apply_delta(
            first_update_id=event.first_update_id,
            final_update_id=event.final_update_id,
            prev_final_update_id=event.prev_final_update_id,
            bids=event.bids,
            asks=event.asks,
        )

        if applied:
            derived_bbo = self._build_bbo_from_book(event)
            if derived_bbo and self._spot_bbo_changed(derived_bbo):
                await self.emit(Channels.bbo(BINANCE_SPOT), derived_bbo)
            return

        logger.warning("Spot local book lost continuity at update %s, resyncing", event.final_update_id)
        self._set_book_sync_state("desynced", "continuity_lost")
        await self._publish_book_state()
        self.book.synced = False
        asyncio.create_task(self._sync_local_book())

    async def _sync_local_book(self) -> None:
        if self._snapshot_pending:
            return
        self._snapshot_pending = True
        self._set_book_sync_state("syncing", "fetching_snapshot")
        await self._publish_book_state()

        try:
            snapshot = await fetch_binance_spot_snapshot(
                self.settings.binance_spot_rest,
                self.symbol,
            )
        except Exception:
            self._snapshot_pending = False
            self._set_book_sync_state("desynced", "snapshot_fetch_failed")
            logger.warning("Spot depth snapshot fetch failed", exc_info=True)
            await self._publish_book_state()
            return

        snapshot_id = int(snapshot["lastUpdateId"])
        self.book.apply_snapshot(snapshot["bids"], snapshot["asks"], snapshot_id)
        self._set_book_sync_state("syncing", f"awaiting_bridge (snap={snapshot_id})")
        await self._publish_book_state()
        logger.info(
            "Spot snapshot applied, lastUpdateId=%s, buffer has %d deltas, waiting for bridge...",
            snapshot_id,
            len(self._buffered_deltas),
        )

        for _ in range(100):
            await asyncio.sleep(0.1)
            if self._try_replay_buffer(snapshot_id):
                self._snapshot_pending = False
                self._book_last_sync_ms = self.now_ms()
                self._set_book_sync_state("synced", "snapshot_bridged")
                logger.info("Spot local book synced at update %s", self.book.last_update_id)
                derived_bbo = self._build_bbo_from_book_direct()
                if derived_bbo and self._spot_bbo_changed(derived_bbo):
                    await self.emit(Channels.bbo(BINANCE_SPOT), derived_bbo)
                await self._publish_book_state()
                return

        self._snapshot_pending = False
        self._set_book_sync_state("desynced", "bridge_timeout")
        logger.warning("Spot local book bridge timeout for snapshot %s", snapshot_id)
        await self._publish_book_state()

    def _try_replay_buffer(self, snapshot_id: int) -> bool:
        bridge_idx = None
        buffered = list(self._buffered_deltas)
        for index, event in enumerate(buffered):
            if event.final_update_id < snapshot_id:
                continue
            if event.first_update_id <= snapshot_id + 1 <= event.final_update_id:
                bridge_idx = index
                break
            if event.prev_final_update_id is not None and event.prev_final_update_id == snapshot_id:
                bridge_idx = index
                break

        if bridge_idx is None:
            return False

        for event in buffered[bridge_idx:]:
            applied = self.book.apply_delta(
                first_update_id=event.first_update_id,
                final_update_id=event.final_update_id,
                prev_final_update_id=event.prev_final_update_id,
                bids=event.bids,
                asks=event.asks,
            )
            if not applied:
                self.book.synced = False
                return False

        return self.book.synced

    def _reset_book(self) -> None:
        self.book.reset()
        self._buffered_deltas.clear()
        self._snapshot_pending = False
        self._book_last_sync_ms = None
        self._last_spot_bbo_key = None
        self._set_book_sync_state("idle", "startup")

    def _spot_bbo_changed(self, event: BBOEvent) -> bool:
        key = (event.bid_px, event.ask_px)
        if key == self._last_spot_bbo_key:
            return False
        self._last_spot_bbo_key = key
        return True

    def _build_bbo_from_book(self, event: BookDeltaEvent) -> BBOEvent | None:
        best_bid, best_ask = self.book.top()
        if not best_bid or not best_ask or best_bid[0] >= best_ask[0]:
            return None
        bid_px, bid_sz = best_bid
        ask_px, ask_sz = best_ask
        mid = (bid_px + ask_px) / 2.0
        spread_bps = 10000.0 * (ask_px - bid_px) / mid if mid else 0.0
        return BBOEvent(
            venue="binance_spot",
            symbol=event.symbol,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
            mid_px=mid,
            spread_bps=spread_bps,
            ts_exchange=event.ts_exchange,
            ts_local=self.now_ms(),
        )

    def _build_bbo_from_book_direct(self) -> BBOEvent | None:
        best_bid, best_ask = self.book.top()
        if not best_bid or not best_ask or best_bid[0] >= best_ask[0]:
            return None
        bid_px, bid_sz = best_bid
        ask_px, ask_sz = best_ask
        mid = (bid_px + ask_px) / 2.0
        spread_bps = 10000.0 * (ask_px - bid_px) / mid if mid else 0.0
        return BBOEvent(
            venue="binance_spot",
            symbol=self.symbol.upper(),
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
            mid_px=mid,
            spread_bps=spread_bps,
            ts_exchange=self.now_ms(),
            ts_local=self.now_ms(),
        )

    def _set_book_sync_state(self, status: str, reason: str) -> None:
        self._book_sync_status = status
        self._book_sync_reason = reason

    async def _publish_book_state(self) -> None:
        best_bid, best_ask = self.book.top()
        payload = {
            "venue": "binance_spot",
            "symbol": self.symbol.upper(),
            "synced": self.book.synced,
            "sync_status": self._book_sync_status,
            "sync_reason": self._book_sync_reason,
            "buffered_deltas": len(self._buffered_deltas),
            "last_sync_at_ms": self._book_last_sync_ms,
            "last_update_id": self.book.last_update_id,
            "best_bid_px": best_bid[0] if best_bid else None,
            "best_bid_sz": best_bid[1] if best_bid else None,
            "best_ask_px": best_ask[0] if best_ask else None,
            "best_ask_sz": best_ask[1] if best_ask else None,
            "mid_px": self.book.mid(),
        }
        await self.bus.set_json(Keys.book(BINANCE_SPOT), payload)

async def main() -> None:
    from app.bus import RedisBus

    settings = get_settings()
    bus = RedisBus(settings.redis_url)
    collector = BinanceSpotCollector(bus)
    await collector.run()


if __name__ == "__main__":
    asyncio.run(main())
