from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

from app.bus import RedisBus
from app.contract import latest_key


class BaseCollector(ABC):
    def __init__(self, bus: RedisBus) -> None:
        self.bus = bus

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    async def sleep_backoff(attempt: int) -> None:
        delay = min(30, 2 ** min(attempt, 4))
        await asyncio.sleep(delay)

    async def emit(self, channel: str, model: Any) -> None:
        """Publish event to channel and update latest-state key (pipelined)."""
        prefix, event_type, venue, symbol = channel.split(":", 3)
        if prefix != "raw":
            raise ValueError(f"BaseCollector.emit expected raw channel, got {channel!r}")
        payload = model.model_dump()
        key = latest_key(event_type, venue, symbol)
        await self.bus.publish_and_set_json(channel, key, payload)

    async def emit_publish_only(self, channel: str, model: Any) -> None:
        """Publish event without updating latest-state key.

        Use for very high-frequency events (e.g. aggTrade) where
        the latest-state write would be immediately overwritten
        and the remote Redis round-trip cost outweighs the value.
        """
        payload = model.model_dump()
        await self.bus.publish_only_json(channel, payload)

    @abstractmethod
    async def run(self) -> None:
        raise NotImplementedError
