from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import Any

from app.bus import RedisBus


class BaseCollector(ABC):
    def __init__(self, bus: RedisBus) -> None:
        self.bus = bus

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def dumps(model_or_dict: Any) -> dict[str, Any]:
        if hasattr(model_or_dict, "model_dump"):
            return model_or_dict.model_dump()
        if isinstance(model_or_dict, dict):
            return model_or_dict
        raise TypeError(f"Unsupported payload type: {type(model_or_dict)!r}")

    @staticmethod
    async def sleep_backoff(attempt: int) -> None:
        delay = min(30, 2 ** min(attempt, 4))
        await asyncio.sleep(delay)

    @abstractmethod
    async def run(self) -> None:
        raise NotImplementedError
