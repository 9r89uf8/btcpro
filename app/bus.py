from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis


class RedisBus:
    def __init__(self, url: str) -> None:
        self._redis = redis.from_url(url, decode_responses=True)

    @property
    def client(self) -> redis.Redis:
        return self._redis

    async def publish_json(self, channel: str, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, separators=(",", ":"))
        await self._redis.publish(channel, message)

    async def set_json(self, key: str, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, separators=(",", ":"))
        await self._redis.set(key, message)

    async def publish_and_set_json(self, channel: str, key: str, payload: dict[str, Any]) -> None:
        """Publish + set in a single pipeline round-trip."""
        message = json.dumps(payload, separators=(",", ":"))
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.publish(channel, message)
            pipe.set(key, message)
            await pipe.execute()

    async def publish_only_json(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish without updating latest-state. Use for high-frequency events."""
        message = json.dumps(payload, separators=(",", ":"))
        await self._redis.publish(channel, message)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        message = await self._redis.get(key)
        if message is None:
            return None
        return json.loads(message)

    async def close(self) -> None:
        await self._redis.aclose()
