from __future__ import annotations

import asyncio
import logging

import httpx

from app.collectors.base import BaseCollector
from app.config import get_settings
from app.contract import BINANCE_FUTURES, Channels
from app.models import OpenInterestEvent

logger = logging.getLogger(__name__)


class BinanceOpenInterestPoller(BaseCollector):
    def __init__(self, bus):
        super().__init__(bus)
        self.settings = get_settings()
        self.symbol = self.settings.symbol.upper()

    async def run(self) -> None:
        url = f"{self.settings.binance_futures_rest}/fapi/v1/openInterest"
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                try:
                    response = await client.get(url, params={"symbol": self.symbol})
                    response.raise_for_status()
                    data = response.json()
                    event = OpenInterestEvent(
                        venue="binance_futures",
                        symbol=data["symbol"],
                        open_interest=float(data["openInterest"]),
                        ts_exchange=int(data["time"]),
                        ts_local=self.now_ms(),
                    )
                    await self.emit(Channels.open_interest(BINANCE_FUTURES), event)
                except Exception:
                    logger.warning("OI poll failed", exc_info=True)
                await asyncio.sleep(1.0)


async def main() -> None:
    from app.bus import RedisBus

    settings = get_settings()
    bus = RedisBus(settings.redis_url)
    collector = BinanceOpenInterestPoller(bus)
    await collector.run()


if __name__ == "__main__":
    asyncio.run(main())
