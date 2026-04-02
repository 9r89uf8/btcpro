from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

from app.collectors.base import BaseCollector
from app.config import get_settings
from app.contract import BYBIT, Channels
from app.models import LiquidationEvent, TradeEvent

logger = logging.getLogger(__name__)


class BybitCollector(BaseCollector):
    """
    Optional confirmation collector.
    Normalizes trades and liquidations only.
    Ticker/orderbook topics are subscribed but not yet processed —
    extend after Binance path is stable (Section 11).
    """

    def __init__(self, bus):
        super().__init__(bus)
        self.settings = get_settings()
        self.symbol = self.settings.symbol.upper()

    async def run(self) -> None:
        url = self.settings.bybit_linear_ws
        payload = {
            "op": "subscribe",
            "args": [
                f"publicTrade.{self.symbol}",
                f"allLiquidation.{self.symbol}",
                f"tickers.{self.symbol}",
                f"orderbook.200.{self.symbol}",
            ],
        }
        attempt = 0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=10000) as ws:
                    await ws.send(json.dumps(payload))
                    attempt = 0
                    async for raw in ws:
                        msg = json.loads(raw)
                        topic = msg.get("topic", "")
                        if topic.startswith("publicTrade."):
                            for trade in msg.get("data", []):
                                event = self._parse_trade(trade)
                                await self.emit(Channels.trade(BYBIT), event)
                        elif topic.startswith("allLiquidation."):
                            for liq in msg.get("data", []):
                                event = self._parse_liquidation(liq)
                                await self.emit(Channels.liquidation(BYBIT), event)
                        # tickers and orderbook intentionally ignored until Section 11
            except Exception:
                attempt += 1
                logger.warning("Bybit ws disconnected, reconnect attempt %d", attempt, exc_info=True)
                await self.sleep_backoff(attempt)

    def _parse_trade(self, trade: dict[str, Any]) -> TradeEvent:
        price = float(trade["p"])
        size = float(trade["v"])
        return TradeEvent(
            venue="bybit",
            symbol=trade["s"],
            market_type="perp",
            aggressive_side=trade["S"].lower(),
            price=price,
            size=size,
            notional=price * size,
            trade_id=str(trade.get("i", "")),
            ts_exchange=int(trade["T"]),
            ts_local=self.now_ms(),
        )

    def _parse_liquidation(self, liq: dict[str, Any]) -> LiquidationEvent:
        price = float(liq["p"])
        size = float(liq["v"])
        return LiquidationEvent(
            venue="bybit",
            symbol=liq["s"],
            side=liq["S"],
            price=price,
            size=size,
            notional=price * size,
            ts_exchange=int(liq["T"]),
            ts_local=self.now_ms(),
        )


async def main() -> None:
    from app.bus import RedisBus

    settings = get_settings()
    bus = RedisBus(settings.redis_url)
    collector = BybitCollector(bus)
    await collector.run()


if __name__ == "__main__":
    asyncio.run(main())
