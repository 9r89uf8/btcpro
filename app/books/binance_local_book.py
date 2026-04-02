from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LocalBook:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    last_update_id: int | None = None
    synced: bool = False

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = None
        self.synced = False

    def apply_snapshot(self, bids: list[list[str]], asks: list[list[str]], last_update_id: int) -> None:
        self.bids = {float(px): float(sz) for px, sz in bids if float(sz) > 0}
        self.asks = {float(px): float(sz) for px, sz in asks if float(sz) > 0}
        self.last_update_id = last_update_id
        # A REST snapshot is not live until a depth delta bridges it.
        self.synced = False

    def apply_delta(
        self,
        first_update_id: int,
        final_update_id: int,
        prev_final_update_id: int | None,
        bids: list[list[float]],
        asks: list[list[float]],
    ) -> bool:
        if self.last_update_id is None:
            self.synced = False
            return False

        if final_update_id < self.last_update_id:
            return False

        if not self.synced:
            # Binance futures bridge rule: U <= lastUpdateId+1 <= u
            target = self.last_update_id + 1
            bridges = first_update_id <= target <= final_update_id
            # Also accept pu == lastUpdateId (futures pu-based bridge)
            if prev_final_update_id is not None:
                bridges = bridges or (prev_final_update_id == self.last_update_id)
            if not bridges:
                return False
        else:
            if prev_final_update_id is not None:
                if prev_final_update_id != self.last_update_id:
                    self.synced = False
                    return False
            elif first_update_id > self.last_update_id + 1:
                self.synced = False
                return False

        for px, sz in bids:
            if sz == 0:
                self.bids.pop(px, None)
            else:
                self.bids[px] = sz

        for px, sz in asks:
            if sz == 0:
                self.asks.pop(px, None)
            else:
                self.asks[px] = sz

        self.last_update_id = final_update_id
        self.synced = True
        return True

    def top(self) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
        best_bid = max(self.bids.items(), key=lambda x: x[0]) if self.bids else None
        best_ask = min(self.asks.items(), key=lambda x: x[0]) if self.asks else None
        return best_bid, best_ask

    def mid(self) -> float | None:
        best_bid, best_ask = self.top()
        if not best_bid or not best_ask:
            return None
        return (best_bid[0] + best_ask[0]) / 2.0

    def notional_within_bps(self, bps: float) -> tuple[float, float]:
        mid = self.mid()
        if mid is None:
            return 0.0, 0.0
        bid_floor = mid * (1.0 - bps / 10000.0)
        ask_ceiling = mid * (1.0 + bps / 10000.0)
        bid_notional = sum(px * sz for px, sz in self.bids.items() if px >= bid_floor)
        ask_notional = sum(px * sz for px, sz in self.asks.items() if px <= ask_ceiling)
        return bid_notional, ask_notional

    def imbalance_within_bps(self, bps: float) -> float:
        bid_notional, ask_notional = self.notional_within_bps(bps)
        denom = bid_notional + ask_notional
        if denom <= 0:
            return 0.0
        return (bid_notional - ask_notional) / denom


async def fetch_binance_futures_snapshot(base_url: str, symbol: str, limit: int = 1000) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base_url}/fapi/v1/depth", params={"symbol": symbol.upper(), "limit": limit})
        response.raise_for_status()
        return response.json()


async def fetch_binance_spot_snapshot(base_url: str, symbol: str, limit: int = 5000) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base_url}/api/v3/depth", params={"symbol": symbol.upper(), "limit": limit})
        response.raise_for_status()
        return response.json()
