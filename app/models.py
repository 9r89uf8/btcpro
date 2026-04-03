from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


Venue = Literal["binance_futures", "binance_spot", "bybit"]
MarketType = Literal["perp", "spot"]
AggressiveSide = Literal["buy", "sell"]


class BaseEvent(BaseModel):
    event_type: str
    venue: Venue
    symbol: str
    ts_exchange: int
    ts_local: int


class TradeEvent(BaseEvent):
    event_type: Literal["trade"] = "trade"
    market_type: MarketType
    aggressive_side: AggressiveSide
    price: float
    size: float
    notional: float
    trade_id: Optional[str] = None


class BBOEvent(BaseEvent):
    event_type: Literal["bbo"] = "bbo"
    bid_px: float
    bid_sz: float
    ask_px: float
    ask_sz: float
    mid_px: float
    spread_bps: float


class BookDeltaEvent(BaseEvent):
    event_type: Literal["book_delta"] = "book_delta"
    first_update_id: int
    final_update_id: int
    prev_final_update_id: int | None = None
    bids: list[list[float]] = Field(default_factory=list)
    asks: list[list[float]] = Field(default_factory=list)


class MarkIndexEvent(BaseEvent):
    event_type: Literal["mark_index"] = "mark_index"
    mark_price: float
    index_price: float
    funding_rate: float
    premium_bps: float


class OpenInterestEvent(BaseEvent):
    event_type: Literal["open_interest"] = "open_interest"
    open_interest: float


class LiquidationEvent(BaseEvent):
    event_type: Literal["liquidation"] = "liquidation"
    side: Literal["BUY", "SELL"]
    price: float
    size: float
    notional: float


class FeatureBar(BaseModel):
    event_type: Literal["feature_bar"] = "feature_bar"
    symbol: str
    bar_ts: int
    perp_cvd_1s: float = 0.0
    perp_cvd_5s: float = 0.0
    perp_cvd_15s: float = 0.0
    spot_cvd_1s: float = 0.0
    spot_cvd_5s: float = 0.0
    spot_cvd_15s: float = 0.0
    premium_bps: float = 0.0
    delta_premium_bps_5s: float = 0.0
    depth_imbalance_5bps: float = 0.0
    depth_imbalance_10bps: float = 0.0
    spread_bps: float = 0.0
    near_touch_depth_bid_usd: float = 0.0
    near_touch_depth_ask_usd: float = 0.0
    depth_pull_bid_5s: float = 0.0
    depth_pull_ask_5s: float = 0.0
    oi_delta_30s: float = 0.0
    liq_skew_30s: float = 0.0
    book_sync_ok: bool = False
    feed_lag_ms_p95: float = 0.0
    futures_trade_lag_ms_p95: float = 0.0
    spot_trade_lag_ms_p95: float = 0.0
    bbo_futures_lag_ms_p95: float = 0.0
    bbo_spot_lag_ms_p95: float = 0.0
    mark_index_lag_ms_p95: float = 0.0
    oi_lag_ms_p95: float = 0.0


class ScoreSnapshot(BaseModel):
    event_type: Literal["score"] = "score"
    symbol: str
    score_1m: float
    score_3m: float
    score_5m: float
    confidence: float
    state: str
    reasons: list[str]
    ts_local: int
