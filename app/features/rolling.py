from __future__ import annotations

from collections import deque
from statistics import mean, pstdev


class RollingSignedWindow:
    def __init__(self, max_ms: int) -> None:
        self.max_ms = max_ms
        self.items: deque[tuple[int, float]] = deque()
        self.total = 0.0

    def add(self, ts_ms: int, value: float) -> None:
        self.items.append((ts_ms, value))
        self.total += value
        self.trim(ts_ms)

    def trim(self, now_ms: int) -> None:
        cutoff = now_ms - self.max_ms
        while self.items and self.items[0][0] < cutoff:
            _, value = self.items.popleft()
            self.total -= value

    def sum(self, now_ms: int) -> float:
        self.trim(now_ms)
        return self.total


class RollingSeries:
    def __init__(self, max_points: int = 600) -> None:
        self.values: deque[float] = deque(maxlen=max_points)

    def add(self, value: float) -> None:
        self.values.append(value)

    def zscore(self, value: float) -> float:
        if len(self.values) < 20:
            return 0.0
        mu = mean(self.values)
        sigma = pstdev(self.values)
        if sigma == 0:
            return 0.0
        return (value - mu) / sigma
