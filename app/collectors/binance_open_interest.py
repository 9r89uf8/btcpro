from __future__ import annotations

import asyncio
import logging

import httpx

from app.collectors.base import BaseCollector
from app.config import get_settings
from app.contract import BINANCE_FUTURES, Channels, Keys
from app.models import OpenInterestEvent

logger = logging.getLogger(__name__)

OI_STALE_MS = 30_000


class BinanceOpenInterestPoller(BaseCollector):
    def __init__(self, bus):
        super().__init__(bus)
        self.settings = get_settings()
        self.symbol = self.settings.symbol.upper()
        self._last_success_ms: int | None = None
        self._last_error_ms: int | None = None
        self._last_error_msg: str | None = None
        self._consecutive_failures: int = 0
        self._total_polls: int = 0
        self._total_failures: int = 0

    async def run(self) -> None:
        url = f"{self.settings.binance_futures_rest}/fapi/v1/openInterest"
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                self._total_polls += 1
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
                    self._last_success_ms = self.now_ms()
                    self._consecutive_failures = 0
                except Exception as e:
                    self._consecutive_failures += 1
                    self._total_failures += 1
                    self._last_error_ms = self.now_ms()
                    self._last_error_msg = f"{type(e).__name__}: {e}"
                    logger.warning("OI poll failed (attempt %d)", self._consecutive_failures, exc_info=True)
                await asyncio.sleep(1.0)

    def health_payload(self) -> dict:
        now = self.now_ms()
        age_ms = (now - self._last_success_ms) if self._last_success_ms else None
        return {
            "last_success_ms": self._last_success_ms,
            "last_success_age_ms": age_ms,
            "stale": age_ms is not None and age_ms > OI_STALE_MS,
            "consecutive_failures": self._consecutive_failures,
            "total_polls": self._total_polls,
            "total_failures": self._total_failures,
            "last_error_ms": self._last_error_ms,
            "last_error": self._last_error_msg,
        }


async def main() -> None:
    from app.bus import RedisBus

    settings = get_settings()
    bus = RedisBus(settings.redis_url)
    collector = BinanceOpenInterestPoller(bus)
    await collector.run()


if __name__ == "__main__":
    asyncio.run(main())
