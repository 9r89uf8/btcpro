from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from contextlib import suppress
from typing import Any

import websockets

from app.books.binance_local_book import LocalBook, fetch_binance_futures_snapshot
from app.collectors.base import BaseCollector
from app.config import get_settings
from app.contract import BINANCE_FUTURES, Channels, Keys
from app.models import BBOEvent, BookDeltaEvent, LiquidationEvent, MarkIndexEvent, TradeEvent

logger = logging.getLogger(__name__)

PUBLIC_FEED_STALE_MS = 5000
MARKET_FEED_STALE_MS = 5000


class BinanceFuturesCollector(BaseCollector):
    """
    Uses Binance's separated routing:
    - /public for high-frequency book feeds
    - /market for aggTrade / markPrice / liquidation
    """

    def __init__(self, bus):
        super().__init__(bus)
        self.settings = get_settings()
        self.symbol = self.settings.symbol.lower()
        self.book = LocalBook()
        self._book_lock = asyncio.Lock()
        self._buffered_deltas: deque[BookDeltaEvent] = deque(maxlen=5000)
        self._snapshot_pending = False
        self._book_sync_status = "idle"
        self._book_sync_reason = "startup"
        self._book_last_sync_ms: int | None = None
        self._last_futures_bbo_key: tuple[float, float] | None = None
        self._trade_queue: asyncio.Queue | None = None
        self._last_book_event_ms: int | None = None
        self._last_public_event_ms: int | None = None
        self._last_trades_event_ms: int | None = None
        self._last_market_event_ms: int | None = None
        self._public_feed_stale = False
        self._trades_feed_stale = False
        self._market_feed_stale = False
        self._last_trade_state_write_ms: int = 0

    async def run(self) -> None:
        await asyncio.gather(
            self.run_public(),
            self.run_trades(),
            self.run_market(),
            self._watch_feeds(),
        )

    async def run_public(self) -> None:
        url = self._public_stream_url()
        attempt = 0
        while True:
            try:
                self._reset_book()
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=10000) as ws:
                    attempt = 0
                    # Start snapshot fetch as soon as we connect
                    asyncio.create_task(self._sync_local_book())
                    async for raw in ws:
                        msg = json.loads(raw)
                        stream_name = msg["stream"]
                        data = msg["data"]
                        self._last_public_event_ms = self.now_ms()
                        if stream_name.endswith("@bookTicker"):
                            # Skip — local book provides BBO once synced,
                            # mark price is the fallback before that.
                            # Emitting every bookTicker to remote Redis
                            # throttles the entire websocket loop.
                            pass
                        elif "@depth" in stream_name:
                            event = self._parse_book_delta(data)
                            # Don't publish raw depth deltas to remote Redis —
                            # they're too frequent and the round-trip latency
                            # throttles the websocket loop. Just buffer locally.
                            await self._handle_depth_delta(event)
            except Exception:
                attempt += 1
                logger.warning("Futures public ws disconnected, reconnect attempt %d", attempt, exc_info=True)
                await self.sleep_backoff(attempt)

    async def run_trades(self) -> None:
        """Dedicated loop for aggTrade — high frequency, publish-only."""
        url = self._trades_stream_url()
        attempt = 0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=10000) as ws:
                    attempt = 0
                    async for raw in ws:
                        msg = json.loads(raw)
                        data = msg["data"]
                        self._last_trades_event_ms = self.now_ms()
                        event = self._parse_trade(data)
                        # Push to in-process queue for feature engine (zero latency)
                        if self._trade_queue is not None:
                            self._trade_queue.put_nowait(event.model_dump())
                        # Throttled latest-state write (~1/sec) so API endpoint works
                        now = self.now_ms()
                        if now - self._last_trade_state_write_ms >= 1000:
                            self._last_trade_state_write_ms = now
                            await self.bus.set_json(
                                Keys.latest("trade", BINANCE_FUTURES),
                                event.model_dump(),
                            )
            except Exception:
                attempt += 1
                logger.warning("Futures trades ws disconnected, reconnect attempt %d", attempt, exc_info=True)
                await self.sleep_backoff(attempt)

    async def run_market(self) -> None:
        """Low-volume loop for markPrice + liquidations."""
        url = self._market_stream_url()
        attempt = 0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=10000) as ws:
                    attempt = 0
                    async for raw in ws:
                        msg = json.loads(raw)
                        stream_name = msg["stream"]
                        data = msg["data"]
                        self._last_market_event_ms = self.now_ms()
                        if "@markPrice" in stream_name:
                            event = self._parse_mark_index(data)
                            await self.emit(Channels.mark_index(BINANCE_FUTURES), event)
                        elif "forceOrder" in stream_name:
                            event = self._parse_liquidation(data)
                            if event is not None:
                                await self.emit(Channels.liquidation(BINANCE_FUTURES), event)
            except Exception:
                attempt += 1
                logger.warning("Futures market ws disconnected, reconnect attempt %d", attempt, exc_info=True)
                await self.sleep_backoff(attempt)

    async def _watch_feeds(self) -> None:
        while True:
            now_ms = self.now_ms()
            public_age = self._feed_age_ms(self._last_public_event_ms, now_ms)
            trades_age = self._feed_age_ms(self._last_trades_event_ms, now_ms)
            market_age = self._feed_age_ms(self._last_market_event_ms, now_ms)

            public_stale = public_age is not None and public_age > PUBLIC_FEED_STALE_MS
            trades_stale = trades_age is not None and trades_age > MARKET_FEED_STALE_MS
            market_stale = market_age is not None and market_age > MARKET_FEED_STALE_MS

            for name, is_stale, was_stale, age in [
                ("public", public_stale, self._public_feed_stale, public_age),
                ("trades", trades_stale, self._trades_feed_stale, trades_age),
                ("market", market_stale, self._market_feed_stale, market_age),
            ]:
                if is_stale and not was_stale:
                    logger.warning("Futures %s feed stale: no events for %dms", name, age)
                elif not is_stale and was_stale:
                    logger.info("Futures %s feed recovered", name)

            self._public_feed_stale = public_stale
            self._trades_feed_stale = trades_stale
            self._market_feed_stale = market_stale
            await self._publish_collector_state(now_ms)
            await asyncio.sleep(1.0)

    def _parse_trade(self, data: dict[str, Any]) -> TradeEvent:
        aggressive_side = "sell" if data["m"] else "buy"
        price = float(data["p"])
        size = float(data["q"])
        return TradeEvent(
            venue="binance_futures",
            symbol=data["s"],
            market_type="perp",
            aggressive_side=aggressive_side,
            price=price,
            size=size,
            notional=price * size,
            trade_id=str(data["a"]),
            ts_exchange=int(data["T"]),
            ts_local=self.now_ms(),
        )

    def _parse_mark_index(self, data: dict[str, Any]) -> MarkIndexEvent:
        mark = float(data["p"])
        index = float(data["i"])
        premium_bps = 10000.0 * (mark - index) / index if index else 0.0
        return MarkIndexEvent(
            venue="binance_futures",
            symbol=data["s"],
            mark_price=mark,
            index_price=index,
            funding_rate=float(data["r"]),
            premium_bps=premium_bps,
            ts_exchange=int(data["E"]),
            ts_local=self.now_ms(),
        )

    def _parse_bbo(self, data: dict[str, Any]) -> BBOEvent:
        bid_px = float(data["b"])
        ask_px = float(data["a"])
        bid_sz = float(data["B"])
        ask_sz = float(data["A"])
        mid = (bid_px + ask_px) / 2.0
        spread_bps = 10000.0 * (ask_px - bid_px) / mid if mid else 0.0
        return BBOEvent(
            venue="binance_futures",
            symbol=data["s"],
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
            mid_px=mid,
            spread_bps=spread_bps,
            ts_exchange=int(data["E"]),
            ts_local=self.now_ms(),
        )

    def _parse_book_delta(self, data: dict[str, Any]) -> BookDeltaEvent:
        return BookDeltaEvent(
            venue="binance_futures",
            symbol=data["s"],
            first_update_id=int(data["U"]),
            final_update_id=int(data["u"]),
            prev_final_update_id=int(data["pu"]),
            bids=[[float(px), float(sz)] for px, sz in data.get("b", [])],
            asks=[[float(px), float(sz)] for px, sz in data.get("a", [])],
            ts_exchange=int(data["E"]),
            ts_local=self.now_ms(),
        )

    def _parse_liquidation(self, data: dict[str, Any]) -> LiquidationEvent | None:
        order = data.get("o")
        if not order or order.get("s") != self.symbol.upper():
            return None
        price = float(order["ap"])
        size = float(order["z"])
        return LiquidationEvent(
            venue="binance_futures",
            symbol=order["s"],
            side=order["S"],
            price=price,
            size=size,
            notional=price * size,
            ts_exchange=int(order["T"]),
            ts_local=self.now_ms(),
        )

    # ── Local book sync ──────────────────────────────────────────────

    async def _handle_depth_delta(self, event: BookDeltaEvent) -> None:
        """Buffer every delta. If book is synced, also apply it live."""
        self._buffered_deltas.append(event)
        self._last_book_event_ms = self.now_ms()

        if not self.book.synced:
            # Not synced yet — just buffer. _sync_local_book will replay.
            return

        # Book is synced — apply the delta for live updates
        applied = self.book.apply_delta(
            first_update_id=event.first_update_id,
            final_update_id=event.final_update_id,
            prev_final_update_id=event.prev_final_update_id,
            bids=event.bids,
            asks=event.asks,
        )

        if applied:
            derived_bbo = self._build_bbo_from_book(event)
            if derived_bbo and self._futures_bbo_changed(derived_bbo):
                await self.emit(Channels.bbo(BINANCE_FUTURES), derived_bbo)
        else:
            # Lost continuity — resync
            logger.warning("Futures local book lost continuity at update %s, resyncing", event.final_update_id)
            self._set_book_sync_state("desynced", "continuity_lost")
            await self._publish_book_state()
            self.book.synced = False
            asyncio.create_task(self._sync_local_book())

    async def _sync_local_book(self) -> None:
        """Fetch REST snapshot, then replay buffered deltas to bridge."""
        if self._snapshot_pending:
            return
        self._snapshot_pending = True
        self._set_book_sync_state("syncing", "fetching_snapshot")
        await self._publish_book_state()

        try:
            snapshot = await fetch_binance_futures_snapshot(
                self.settings.binance_futures_rest,
                self.symbol,
            )
        except Exception:
            self._snapshot_pending = False
            self._set_book_sync_state("desynced", "snapshot_fetch_failed")
            logger.warning("Futures depth snapshot fetch failed", exc_info=True)
            await self._publish_book_state()
            return

        snapshot_id = int(snapshot["lastUpdateId"])
        self.book.apply_snapshot(snapshot["bids"], snapshot["asks"], snapshot_id)
        self._set_book_sync_state("syncing", f"awaiting_bridge (snap={snapshot_id})")
        await self._publish_book_state()
        logger.info("Snapshot applied, lastUpdateId=%s, buffer has %d deltas, waiting for bridge...",
                     snapshot_id, len(self._buffered_deltas))

        # Wait for bridging deltas to arrive (they come every 100ms)
        # Give it up to 10 seconds
        for attempt in range(100):
            await asyncio.sleep(0.1)
            buf_len = len(self._buffered_deltas)
            if buf_len > 0 and attempt % 10 == 0:
                first = self._buffered_deltas[0]
                last = self._buffered_deltas[-1]
                logger.info(
                    "Bridge check #%d: snap=%s, buffer=%d deltas, "
                    "first(U=%s u=%s pu=%s) last(U=%s u=%s pu=%s)",
                    attempt, snapshot_id, buf_len,
                    first.first_update_id, first.final_update_id, first.prev_final_update_id,
                    last.first_update_id, last.final_update_id, last.prev_final_update_id,
                )
            if self._try_replay_buffer(snapshot_id):
                self._snapshot_pending = False
                self._book_last_sync_ms = self.now_ms()
                self._set_book_sync_state("synced", "snapshot_bridged")
                logger.info("Futures local book synced at update %s", self.book.last_update_id)
                derived_bbo = self._build_bbo_from_book_direct()
                if derived_bbo:
                    await self.emit(Channels.bbo(BINANCE_FUTURES), derived_bbo)
                await self._publish_book_state()
                return

        # Failed to bridge
        self._snapshot_pending = False
        self._set_book_sync_state("desynced", "bridge_timeout")
        if self._buffered_deltas:
            first = self._buffered_deltas[0]
            last = self._buffered_deltas[-1]
            logger.warning(
                "Bridge timeout for snapshot %s. Buffer: %d deltas, "
                "first(U=%s u=%s pu=%s) last(U=%s u=%s pu=%s)",
                snapshot_id, len(self._buffered_deltas),
                first.first_update_id, first.final_update_id, first.prev_final_update_id,
                last.first_update_id, last.final_update_id, last.prev_final_update_id,
            )
        else:
            logger.warning("Bridge timeout for snapshot %s. Buffer is EMPTY — no deltas arriving!", snapshot_id)
        await self._publish_book_state()
        # Will retry on next continuity loss or can be triggered manually

    def _try_replay_buffer(self, snapshot_id: int) -> bool:
        """Try to find a bridging delta in the buffer and replay from there."""
        # Find bridge delta
        bridge_idx = None
        buffered = list(self._buffered_deltas)
        for i, ev in enumerate(buffered):
            if ev.final_update_id < snapshot_id:
                continue  # too old
            # Binance futures bridge: U <= lastUpdateId+1 <= u  OR  pu <= lastUpdateId
            if ev.first_update_id <= snapshot_id + 1 <= ev.final_update_id:
                bridge_idx = i
                break
            if ev.prev_final_update_id is not None and ev.prev_final_update_id == snapshot_id:
                bridge_idx = i
                break

        if bridge_idx is None:
            return False

        # Replay from bridge delta onward
        for ev in buffered[bridge_idx:]:
            applied = self.book.apply_delta(
                first_update_id=ev.first_update_id,
                final_update_id=ev.final_update_id,
                prev_final_update_id=ev.prev_final_update_id,
                bids=ev.bids,
                asks=ev.asks,
            )
            if not applied:
                # Gap in replay — snapshot is stale
                self.book.synced = False
                return False

        return self.book.synced

    def _reset_book(self) -> None:
        self.book.reset()
        self._buffered_deltas.clear()
        self._snapshot_pending = False
        self._set_book_sync_state("idle", "startup")

    def _build_bbo_from_book(self, event: BookDeltaEvent) -> BBOEvent | None:
        best_bid, best_ask = self.book.top()
        if not best_bid or not best_ask or best_bid[0] >= best_ask[0]:
            return None
        bid_px, bid_sz = best_bid
        ask_px, ask_sz = best_ask
        mid = (bid_px + ask_px) / 2.0
        spread_bps = 10000.0 * (ask_px - bid_px) / mid if mid else 0.0
        return BBOEvent(
            venue="binance_futures",
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
            venue="binance_futures",
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

    def _futures_bbo_changed(self, bbo: BBOEvent) -> bool:
        key = (bbo.bid_px, bbo.ask_px)
        if key == self._last_futures_bbo_key:
            return False
        self._last_futures_bbo_key = key
        return True

    def _public_stream_url(self) -> str:
        stream = f"{self.symbol}@bookTicker/{self.symbol}@depth@100ms"
        return f"{self.settings.binance_futures_public_ws}/stream?streams={stream}"

    def _trades_stream_url(self) -> str:
        stream = f"{self.symbol}@aggTrade"
        return f"{self.settings.binance_futures_market_ws}/stream?streams={stream}"

    def _market_stream_url(self) -> str:
        stream = f"{self.symbol}@markPrice@1s/!forceOrder@arr"
        return f"{self.settings.binance_futures_market_ws}/stream?streams={stream}"

    @staticmethod
    def _feed_age_ms(last_event_ms: int | None, now_ms: int) -> int | None:
        if last_event_ms is None:
            return None
        return max(0, now_ms - last_event_ms)

    def _collector_state_payload(self, now_ms: int | None = None) -> dict[str, Any]:
        if now_ms is None:
            now_ms = self.now_ms()
        public_age = self._feed_age_ms(self._last_public_event_ms, now_ms)
        trades_age = self._feed_age_ms(self._last_trades_event_ms, now_ms)
        market_age = self._feed_age_ms(self._last_market_event_ms, now_ms)
        return {
            "venue": BINANCE_FUTURES,
            "symbol": self.symbol.upper(),
            "public_feed_age_ms": public_age,
            "trades_feed_age_ms": trades_age,
            "market_feed_age_ms": market_age,
            "public_feed_stale": self._public_feed_stale,
            "trades_feed_stale": self._trades_feed_stale,
            "market_feed_stale": self._market_feed_stale,
        }

    def _set_book_sync_state(self, status: str, reason: str) -> None:
        self._book_sync_status = status
        self._book_sync_reason = reason

    def _book_state_payload(self) -> dict[str, Any]:
        best_bid, best_ask = self.book.top()
        now_ms = self.now_ms()
        book_age_ms = (now_ms - self._last_book_event_ms) if self._last_book_event_ms else None

        # Depth metrics (cheap local computation)
        bid_5, ask_5 = self.book.notional_within_bps(5) if self.book.synced else (0.0, 0.0)
        bid_10, ask_10 = self.book.notional_within_bps(10) if self.book.synced else (0.0, 0.0)
        denom_5 = bid_5 + ask_5
        denom_10 = bid_10 + ask_10
        imbalance_5bps = (bid_5 - ask_5) / denom_5 if denom_5 > 0 else 0.0
        imbalance_10bps = (bid_10 - ask_10) / denom_10 if denom_10 > 0 else 0.0

        return {
            "venue": BINANCE_FUTURES,
            "symbol": self.symbol.upper(),
            "synced": self.book.synced,
            "sync_status": self._book_sync_status,
            "sync_reason": self._book_sync_reason,
            "buffered_deltas": len(self._buffered_deltas),
            "last_sync_at_ms": self._book_last_sync_ms,
            "last_update_id": self.book.last_update_id,
            "book_age_ms": book_age_ms,
            "book_stale": book_age_ms is not None and book_age_ms > PUBLIC_FEED_STALE_MS,
            "best_bid_px": best_bid[0] if best_bid else None,
            "best_bid_sz": best_bid[1] if best_bid else None,
            "best_ask_px": best_ask[0] if best_ask else None,
            "best_ask_sz": best_ask[1] if best_ask else None,
            "mid_px": self.book.mid(),
            "depth_imbalance_5bps": imbalance_5bps,
            "depth_imbalance_10bps": imbalance_10bps,
            "near_touch_bid_usd": bid_5,
            "near_touch_ask_usd": ask_5,
        }

    async def _publish_book_state(self) -> None:
        await self.bus.set_json(Keys.book(BINANCE_FUTURES), self._book_state_payload())

    async def _publish_collector_state(self, now_ms: int | None = None) -> None:
        await self.bus.set_json(Keys.collector(BINANCE_FUTURES), self._collector_state_payload(now_ms))

async def main() -> None:
    from app.bus import RedisBus

    settings = get_settings()
    bus = RedisBus(settings.redis_url)
    collector = BinanceFuturesCollector(bus)
    await collector.run()


if __name__ == "__main__":
    asyncio.run(main())
