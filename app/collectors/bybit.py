from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

from app.collectors.base import BaseCollector
from app.config import get_settings
from app.models import LiquidationEvent, TradeEvent


class BybitCollector(BaseCollector):
    """
    Optional confirmation collector.
    This starter keeps only trade + liquidation normalization.
    Extend with ticker/orderbook after Binance is stable.
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
                                await self._emit("raw:trade:bybit:btcusdt", event)
                        elif topic.startswith("allLiquidation."):
                            for liq in msg.get("data", []):
                                event = self._parse_liquidation(liq)
                                await self._emit("raw:liquidation:bybit:btcusdt", event)
                        else:
                            # Keep raw ticker/orderbook messages for later feature additions.
                            await self.bus.publish_json(f"raw:passthrough:bybit:btcusdt", msg)
            except Exception:
                attempt += 1
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

    async def _emit(self, channel: str, model) -> None:
        payload = model.model_dump()
        await self.bus.publish_json(channel, payload)
        key = f"state:latest:{channel.removeprefix('raw:')}"
        await self.bus.set_json(key, payload)


async def main() -> None:
    from app.bus import RedisBus

    settings = get_settings()
    bus = RedisBus(settings.redis_url)
    collector = BybitCollector(bus)
    await collector.run()


if __name__ == "__main__":
    asyncio.run(main())
