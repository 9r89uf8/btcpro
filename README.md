# BTC 1–5 Minute Microstructure Dashboard — Handoff Spec

## Objective

Build a live dashboard that estimates **directional pressure** for BTC over the next **1, 3, and 5 minutes** using:
- aggressive perpetual-futures buying/selling,
- perp premium versus index/spot,
- order-book imbalance and liquidity withdrawal,
- open-interest change,
- liquidation skew,
- spot confirmation.

This matches the report focus on **order flow**, **liquidity**, **leverage/liquidations**, and **funding/open-interest as context rather than the main trigger**.

## Scope

### Phase 1 (required)
Primary venue:
- Binance USDⓈ-M BTCUSDT perpetual
- Binance Spot BTCUSDT

Confirmation venue:
- Bybit BTCUSDT perpetual (optional but recommended after Binance is stable)

### Phase 2
Optional spot anchor:
- Coinbase Advanced Trade BTC-USD ticker + level2

## Exchange feeds to ingest

### Binance futures
Use the new separated websocket routing:
- public: `wss://fstream.binance.com/public`
- market: `wss://fstream.binance.com/market`

Subscribe to:
- `btcusdt@aggTrade`
- `btcusdt@markPrice@1s`
- `btcusdt@bookTicker`
- `btcusdt@depth@100ms`
- `!forceOrder@arr`

Poll REST:
- `GET /fapi/v1/openInterest`
- `GET /fapi/v1/premiumIndex` (only if you want a REST fallback for mark/index)

### Binance spot
- websocket: `wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker/btcusdt@depth@100ms`

### Bybit (optional confirmation)
- websocket: `wss://stream.bybit.com/v5/public/linear`
- topics:
  - `publicTrade.BTCUSDT`
  - `tickers.BTCUSDT`
  - `orderbook.200.BTCUSDT`
  - `allLiquidation.BTCUSDT`

## Non-goals

This is **not** a universal BTC predictor.
It is a **short-horizon market-state estimator**.
The first release should answer:
- who is pressing the perp book,
- whether spot confirms,
- whether leverage/liquidations amplify,
- whether liquidity is thin/pulled.

## Recommended stack

- Python 3.11+
- `asyncio`
- `websockets`
- `httpx`
- `redis` (pub/sub + state cache)
- `fastapi`
- `uvicorn`
- `plotly` + `dash`
- `pydantic`
- `sortedcontainers`
- `numpy`, `pandas`

Storage:
- MVP: Redis for live state + rolling in-memory windows
- Later: ClickHouse or Parquet for raw event archive and backtests

## Architecture

```text
exchange collectors
  -> normalized events
  -> Redis pub/sub + Redis latest-state keys
  -> feature engine
  -> score engine
  -> FastAPI
  -> dashboard UI
  -> raw event archive
```

## Folder layout

```text
btc_microstructure_dashboard_handoff/
  README.md
  pyproject.toml
  .env.example
  docker-compose.yml
  app/
    config.py
    models.py
    bus.py
    collectors/
      base.py
      binance_futures.py
      binance_spot.py
      bybit.py
      binance_open_interest.py
    books/
      binance_local_book.py
    features/
      rolling.py
      engine.py
      scoring.py
    api/
      main.py
  dashboard/
    app.py
  tests/
    test_scoring.py
```

## Normalized event schemas

### TradeEvent
```python
{
  "event_type": "trade",
  "venue": "binance_futures",
  "symbol": "BTCUSDT",
  "market_type": "perp",
  "aggressive_side": "buy",   # buy means taker bought / lifted offers
  "price": 69000.0,
  "size": 0.25,
  "notional": 17250.0,
  "trade_id": "123456",
  "ts_exchange": 1710000000000,
  "ts_local": 1710000000123
}
```

### MarkIndexEvent
```python
{
  "event_type": "mark_index",
  "venue": "binance_futures",
  "symbol": "BTCUSDT",
  "mark_price": 69010.5,
  "index_price": 68995.2,
  "funding_rate": 0.0001,
  "premium_bps": 2.22,
  "ts_exchange": 1710000001000,
  "ts_local": 1710000001012
}
```

### BBOEvent
```python
{
  "event_type": "bbo",
  "venue": "binance_futures",
  "symbol": "BTCUSDT",
  "bid_px": 69000.0,
  "bid_sz": 3.12,
  "ask_px": 69000.1,
  "ask_sz": 2.91,
  "mid_px": 69000.05,
  "spread_bps": 0.0145,
  "ts_exchange": 1710000001000,
  "ts_local": 1710000001012
}
```

### BookDeltaEvent
```python
{
  "event_type": "book_delta",
  "venue": "binance_futures",
  "symbol": "BTCUSDT",
  "first_update_id": 100,
  "final_update_id": 105,
  "prev_final_update_id": 99,
  "bids": [[68999.9, 1.1], [68999.8, 0.0]],
  "asks": [[69000.2, 3.4]],
  "ts_exchange": 1710000001000,
  "ts_local": 1710000001012
}
```

### LiquidationEvent
```python
{
  "event_type": "liquidation",
  "venue": "binance_futures",
  "symbol": "BTCUSDT",
  "side": "SELL",     # liquidation order side from venue payload
  "price": 68900.0,
  "size": 1.25,
  "notional": 86125.0,
  "ts_exchange": 1710000001000,
  "ts_local": 1710000001012
}
```

### OpenInterestEvent
```python
{
  "event_type": "open_interest",
  "venue": "binance_futures",
  "symbol": "BTCUSDT",
  "open_interest": 25231.44,
  "ts_exchange": 1710000001000,
  "ts_local": 1710000001012
}
```

### FeatureBar
Emit every 1 second:
```python
{
  "event_type": "feature_bar",
  "symbol": "BTCUSDT",
  "bar_ts": 1710000001000,
  "perp_cvd_1s": 120000.0,
  "perp_cvd_5s": 355000.0,
  "spot_cvd_1s": 25000.0,
  "spot_cvd_5s": 72000.0,
  "premium_bps": 2.22,
  "delta_premium_bps_5s": 1.10,
  "depth_imbalance_10bps": 0.14,
  "spread_bps": 0.015,
  "near_touch_depth_bid_usd": 2500000.0,
  "near_touch_depth_ask_usd": 1800000.0,
  "depth_pull_bid_5s": -350000.0,
  "depth_pull_ask_5s": 150000.0,
  "oi_delta_30s": 420.0,
  "liq_skew_30s": 1300000.0,
  "book_sync_ok": True,
  "feed_lag_ms_p95": 65.0
}
```

### ScoreSnapshot
```python
{
  "event_type": "score",
  "symbol": "BTCUSDT",
  "score_1m": 0.68,
  "score_3m": 0.54,
  "score_5m": 0.49,
  "confidence": 0.73,
  "state": "bullish_pressure",
  "reasons": [
    "perp_cvd_5s high",
    "premium rising",
    "bid depth dominates",
    "spot confirming"
  ],
  "ts_local": 1710000001100
}
```

## Redis contract

### Channels
- `raw:trade:binance_futures:btcusdt`
- `raw:bbo:binance_futures:btcusdt`
- `raw:book_delta:binance_futures:btcusdt`
- `raw:mark_index:binance_futures:btcusdt`
- `raw:liquidation:binance_futures:btcusdt`
- `raw:open_interest:binance_futures:btcusdt`
- `raw:trade:binance_spot:btcusdt`
- `raw:bbo:binance_spot:btcusdt`
- `raw:book_delta:binance_spot:btcusdt`
- `raw:trade:bybit:btcusdt`
- `raw:ticker:bybit:btcusdt`
- `raw:orderbook:bybit:btcusdt`
- `raw:liquidation:bybit:btcusdt`
- `derived:feature_bar:btcusdt`
- `derived:score:btcusdt`

### Keys
- `state:latest:feature_bar:btcusdt`
- `state:latest:score:btcusdt`
- `state:latest:bbo:binance_futures:btcusdt`
- `state:latest:bbo:binance_spot:btcusdt`
- `state:book:binance_futures:btcusdt`
- `state:book:binance_spot:btcusdt`

## Feature definitions

All rolling features should be computed on **1-second bars**, but sourced from raw events.

### Signed trade flow
Use **quote notional** and sign by taker direction.

```python
signed_notional = notional if aggressive_side == "buy" else -notional
```

Then:
- `perp_cvd_1s` = sum of perp signed notional in last 1s
- `perp_cvd_5s` = sum of perp signed notional in last 5s
- `perp_cvd_15s` = sum of perp signed notional in last 15s
- same for spot

### Premium
```python
premium_bps = 10000 * (mark_price - index_price) / index_price
delta_premium_bps_5s = premium_bps_now - premium_bps_5s_ago
```

### Spread
```python
spread_bps = 10000 * (ask_px - bid_px) / ((ask_px + bid_px) / 2)
```

### Depth imbalance
Compute notional within X bps of mid.

```python
depth_imbalance_xbps = (
    bid_notional_within_xbps - ask_notional_within_xbps
) / (
    bid_notional_within_xbps + ask_notional_within_xbps + 1e-9
)
```

Recommended:
- `depth_imbalance_5bps`
- `depth_imbalance_10bps`

### Liquidity withdrawal
```python
depth_pull_bid_5s = bid_depth_now - bid_depth_5s_ago
depth_pull_ask_5s = ask_depth_now - ask_depth_5s_ago
```

### Open interest
```python
oi_delta_30s = oi_now - oi_30s_ago
oi_z_5m = zscore(oi_now, trailing_5m_series)
```

### Liquidation skew
Convert each liquidation event to signed notional:
- short liquidation pressure -> bullish
- long liquidation pressure -> bearish

For the first implementation:
- if liquidation order side is `BUY`, treat as **short liquidation / bullish fuel**
- if liquidation order side is `SELL`, treat as **long liquidation / bearish fuel**

Then:
```python
liq_skew_30s = bull_liq_notional_30s - bear_liq_notional_30s
```

## Score engine

Start with a rule-based linear score and later replace it with a calibrated model.

```python
score_1m_raw = (
    0.30 * z(perp_cvd_5s)
  + 0.18 * z(spot_cvd_5s)
  + 0.18 * z(depth_imbalance_10bps)
  + 0.14 * z(delta_premium_bps_5s)
  + 0.10 * z(oi_delta_30s)
  + 0.07 * z(liq_skew_30s)
  - 0.17 * z(spread_bps)
  - 0.10 * z(feed_lag_ms_p95)
)
```

Then gate:
- if `book_sync_ok == False` -> force `confidence <= 0.25`
- if spot feed stale > 2s -> lower confidence
- if futures feed stale > 1s -> set state to `degraded`
- if premium extreme but spot not confirming -> cap bullish confidence

Suggested state mapping:
- `score >= 0.60` -> `bullish_pressure`
- `0.20 <= score < 0.60` -> `mild_bullish`
- `-0.20 < score < 0.20` -> `neutral`
- `-0.60 < score <= -0.20` -> `mild_bearish`
- `score <= -0.60` -> `bearish_pressure`

Confidence:
```python
confidence = min(
    1.0,
    0.35
    + 0.25 * abs(score_1m_raw)
    + 0.20 * agreement(score_1m, score_3m, score_5m)
    + 0.20 * data_quality_score
)
```

## Dashboard panels

### Ribbon
- perp mid
- spot mid
- premium bps
- score_1m / score_3m / score_5m
- confidence
- feed lag
- book sync status

### Chart 1
- Binance futures mid
- Binance spot mid
- shaded predicted state

### Chart 2
- premium bps
- delta premium

### Chart 3
- perp CVD 1s / 5s / 15s
- spot CVD 1s / 5s / 15s

### Chart 4
- depth imbalance 5bps / 10bps
- spread bps
- near-touch depth

### Chart 5
- OI delta
- liquidation skew

### Right column
- current reasons list
- anomaly warnings
- last 20 state changes
- feed health

## API endpoints

### `GET /health`
Return:
```json
{
  "status": "ok",
  "book_sync_ok": true,
  "last_event_age_ms": 120,
  "futures_feed_age_ms": 120,
  "spot_feed_age_ms": 240
}
```

### `GET /latest/score`
Return latest `ScoreSnapshot`.

### `GET /latest/features`
Return latest `FeatureBar`.

### `GET /history/features?minutes=60`
Return recent feature bars.

### `GET /history/score?minutes=60`
Return recent scores.

### `GET /replay?start=...&end=...`
Later: replay historical feature bars for visual debugging.

## Implementation order

1. Binance futures market collector
2. Binance futures local book sync
3. Binance spot trade + BBO collector
4. Feature engine
5. Rule-based score engine
6. FastAPI
7. Dashboard
8. Bybit confirmation collector
9. Raw archive + offline backtest
10. Trained model

## Hard requirements for the coding agent

1. Use **exchange event timestamps** and **local arrival timestamps** on every normalized event.
2. Keep **raw events** for replay/debugging.
3. Do **not** use 5m taker-buy/sell REST data as the main trigger layer.
4. Treat **book health** as first-class state.
5. Use **same-venue spot** first for confirmation.
6. Use **mark/index** for premium, not last trade.
7. Separate Binance futures connections by traffic type:
   - `/public` for book feeds
   - `/market` for aggTrade/markPrice/liquidation
8. Implement reconnect + backoff + stale-feed alerts.
9. Use z-scores against trailing windows, not hard-coded thresholds only.
10. Add unit tests for:
    - trade side normalization,
    - book sync continuity,
    - score sign on obvious bullish/bearish cases.

## Model-training handoff for Phase 2

Feature bar cadence:
- 250ms or 1s

Labels:
- `future_mid_ret_60s`
- `future_mid_ret_180s`
- `future_mid_ret_300s`

Starter target:
```python
y_60s = sign(mid_px_t_plus_60s - mid_px_t)
```

Models:
- logistic regression
- XGBoost / LightGBM
- calibration layer for confidence

Evaluate by:
- precision on high-confidence alerts
- recall on large moves
- post-fee paper PnL
- latency-adjusted fill simulation

## Notes on interpretation

The dashboard should not interpret every premium spike as bullish.
A strong bullish setup usually looks like:
- perp CVD positive,
- spot CVD positive or catching up,
- premium rising but not absurdly stretched,
- bid depth > ask depth,
- OI rising,
- short-liquidation support appearing.

A likely trap looks like:
- futures price jumps,
- premium spikes,
- spot does not confirm,
- OI stalls or falls,
- spread widens,
- near-touch bid depth disappears.

That interpretation is exactly why the report prioritized perp aggression, premium, liquidity, leverage, and liquidations.

## Quick Start

### Prerequisites
- Python 3.11+
- A Redis instance (local or remote). Set `REDIS_URL` in `.env`.

### Setup
```bash
cp .env.example .env
# Edit .env — set REDIS_URL to your Redis instance
pip install -e .
```

### Run
```bash
python run_all.py
```

This single command starts:
- Binance futures collector (3 websocket loops: depth, trades, market)
- Binance spot collector (2 websocket loops: trades, depth)
- Open interest poller (REST, 1s cadence)
- Feature engine (1s feature bars)
- FastAPI server on http://localhost:8000
- Dashboard on http://localhost:8050

### Verify
- Dashboard: http://localhost:8050
- API health: http://localhost:8000/health
- Latest features: http://localhost:8000/latest/features
- Latest score: http://localhost:8000/latest/score
