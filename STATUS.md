# BTC Microstructure Dashboard — Build Status

## What Has Been Built

### Infrastructure
- Extracted scaffold from `btc_microstructure_dashboard_handoff.zip` into repo root
- Fixed `pyproject.toml` package discovery for multi-package layout (`app/`, `dashboard/`)
- All dependencies installed and importable
- `.env` configured with remote Redis Cloud instance (redis.io)
- Redis connectivity verified

### Collectors (app/collectors/)
- **Binance Futures** — collector now split across three live paths: `/public` for book feeds, `/market` `aggTrade` on its own loop, and `/market` markPrice/liquidations on a separate low-volume loop. Futures trades publish to Redis pub/sub for CVD, use throttled latest-state writes for API inspection, and no longer block mark/index or liquidation freshness. High-frequency `bookTicker` and raw depth deltas do not publish to remote Redis; depth is buffered locally for local-book sync, and futures BBO is derived from the synchronized local book.
- **Binance Spot** — collector split into separate trade and depth websocket loops. Spot trades feed the feature engine through the in-process queue, and a throttled latest-state spot trade key is written for API inspection. Spot depth stays local, synchronizes a local spot book via REST snapshot + live deltas, and spot BBO is derived from the synchronized local book with bid/ask-price dedupe before publishing.
- **Binance OI Poller** — REST poller hitting `/fapi/v1/openInterest` every 1 second. OI health is now tracked explicitly with last-success timing, stale detection, consecutive failures, total polls/failures, and last error details.
- **Bybit** — optional confirmation collector for trades and liquidations (starter-level, passthrough for ticker/orderbook).
- All collectors have reconnect + backoff + logging on disconnect.

### Data Layer
- **RedisBus** (`app/bus.py`) — async Redis wrapper for pub/sub + latest-state key/value.
- **Contract Layer** (`app/contract.py`) — centralized venue constants plus shared Redis channel/key builders for raw events, derived events, latest-state keys, and book-state keys. This is now the source of truth for symbol casing and Redis naming conventions.
- **Models** (`app/models.py`) — Pydantic schemas for TradeEvent, BBOEvent, BookDeltaEvent, MarkIndexEvent, LiquidationEvent, OpenInterestEvent, FeatureBar, ScoreSnapshot.
- **LocalBook** (`app/books/`) — shared order book helper for Binance futures and spot, with snapshot bootstrap, delta application, desync detection, and snapshot bridging rules for both the futures `pu` path and the spot `U/u` path. Exposes top-of-book, mid, depth within bps, and imbalance. Futures book state now surfaces best bid/ask prices and sizes, depth imbalance, near-touch depth, sync timing, and book freshness.

### Feature Engine (app/features/)
- Subscribes to raw Redis channels, maintains rolling CVD windows (1s/5s/15s) for perp and spot.
- Tracks premium history, OI history, liquidation skew (30s window).
- Emits a `FeatureBar` every 1 second with all computed features.
- Z-score series for scoring inputs.
- Futures depth metrics are now real: `book_sync_ok`, `depth_imbalance_5bps`, `depth_imbalance_10bps`, `near_touch_depth_*_usd`, and `depth_pull_*_5s` are computed from the synchronized in-process futures local book.
- Keeps 60 minutes of in-memory feature and score history (1s cadence ring buffers) for API/dashboard consumers.
- Tracks per-source lag (`futures_trade`, `spot_trade`, `bbo`, `mark_index`, `oi`) and excludes REST-polled OI from the aggregate real-time lag metric.

### Score Engine (app/features/scoring.py)
- Weighted linear score matching the README spec weights.
- State classification (bullish_pressure / mild_bullish / neutral / mild_bearish / bearish_pressure).
- Confidence calculation with book sync gating.
- Reason string generation.
- 3m and 5m scores are currently scaled placeholders (0.75x and 0.60x of 1m score).

### API (app/api/main.py)
- FastAPI on port 8000.
- Endpoints: `/health`, `/latest/score`, `/latest/features`, `/latest/bbo/futures`, `/latest/trade/futures`, `/latest/open-interest/futures`, `/latest/mark-index/futures`, `/latest/liquidation/futures`, `/latest/collector/futures`, `/latest/bbo/spot`, `/latest/trade/spot`, `/latest/book/futures`, `/latest/book/spot`, `/latest/all`, `/history/features`, `/history/score`.
- `/history/features` and `/history/score` return real in-memory history from the live feature engine when started through `run_all.py`.

### Dashboard (dashboard/app.py)
- Dash app on port 8050 with dark theme.
- Live charts: price (perp local-book BBO vs spot local-book BBO or trade fallback), live basis vs exchange premium, perp vs spot CVD 5s, OI delta 30s, liquidation skew 30s.
- Status block: top-line price/state/score metrics plus separate futures and spot book-sync/source rows.
- Polls `/latest/all` every 5 seconds.

### Unified Launcher (run_all.py)
- Single `python run_all.py` starts everything: futures collector, spot collector, OI poller, feature engine, API server, dashboard.
- Graceful shutdown on Ctrl+C.

### Tests
- `test_bullish_score_positive` — verifies bullish z-score inputs produce positive score.
- `test_bearish_score_negative` — verifies bearish z-score inputs produce negative score.
- `test_binance_local_book.py` — covers snapshot bootstrap, spot bridge acceptance via `lastUpdateId + 1`, futures `pu` bridge handling, and continuity mismatch behavior.
- `test_binance_futures_collector.py` — covers separated routing, trade normalization, mark/index parsing, liquidation payload parsing, and futures feed-age/staleness state.
- `test_binance_spot_collector.py` — covers spot trade normalization, spot book-delta parsing with optional `pu`, BBO derivation from the synced local book, and spot BBO deduplication behavior.
- `test_open_interest_poller.py` — covers initial OI health state, success tracking, stale detection, failure tracking, and OI event parsing.
- `test_feature_engine.py` — covers rolling-window expiry and signed aggregation, z-score min-sample and directionality behavior, p95 lag helper, premium delta logic, and OI delta logic.

---

## Current Phase

**Sections 0 (Bootstrap), 1 (Normalize Contracts), 2 (Binance Futures Collector), 3 (Futures Local Order Book), 4 (Binance Spot Collector), 5 (Open Interest Poller), and 6 (Feature Engine) are complete.**

Sections 7–11 from the master plan have not been formally worked through yet. The scaffold code covers starter-level implementations of some later components, but they have not been validated against the correctness checks in each section.

---

## Phases Remaining

| Section | Name | Status | Summary |
|---------|------|--------|---------|
| 0 | Bootstrap | **Done** | Scaffold extracted, deps installed, Redis connected, launcher working |
| 1 | Normalize Contracts | **Done** | Central contract layer added, symbol casing rules standardized, API/collectors/features use shared channel and key builders |
| 2 | Binance Futures Collector | **Done** | Separated `/public` and `/market` routing validated, futures trade/mark/liquidation normalization checked, feed-age diagnostics added |
| 3 | Futures Local Order Book | **Done** | Futures local book sync is live, sync telemetry is exposed, and real depth/near-touch/freshness metrics are available to API and features |
| 4 | Binance Spot Collector | **Done** | Spot trade normalization validated, spot local-book BBO is live, spot latest trade state is exposed, and spot collector tests are in place |
| 5 | Open Interest Poller | **Done** | OI polling contract validated, `/latest/open-interest/futures` added, and OI lag/stale health is exposed through the API while detailed failure state is tracked in the poller |
| 6 | Feature Engine | **Done** | Real rolling feature bars are live, per-source lag is tracked, and `/history/features` plus `/history/score` are backed by 60-minute in-memory ring buffers |
| 7 | Score Engine | Not started | Real 3m/5m scores, confidence gating for stale feeds, degraded state behavior |
| 8 | FastAPI Server | Not started | Real health reporting, history endpoints, feed age tracking |
| 9 | Dashboard UI | Not started | Full ribbon, price/state chart, premium chart, CVD chart, depth chart, OI/liq chart, right column |
| 10 | Unified Runtime | Not started | Startup ordering, graceful shutdown, stale-feed alerts, documentation |
| 11 | Bybit Confirmation | Not started | Validate Bybit parsing, normalize trades/liquidations, expose confirmation signals |

---

## Known Issues & Things To Watch

### Dashboard Performance
- **The dashboard cannot handle polling faster than 5 seconds.** At 1–2 second intervals the tab shows "Updating..." and becomes unresponsive. This is due to Dash rebuilding the full Plotly figure on every callback. If faster updates are needed later, switch to WebSocket push or Dash clientside callbacks.

### Perp BBO — Root Cause Found
- The original symptom was stale perp BBO on the dashboard. The root cause chain was:
- `bookTicker` was stale, so the collector needed a synchronized local depth book.
- The local book could not complete snapshot bridging because the websocket loop was starved.
- The websocket loop was starved because every futures `bookTicker` and every raw depth delta performed two round-trips to remote Redis Cloud (`publish` + `set`), which throttled the hot path badly enough that bridging deltas never stayed available.
- Fix: stop publishing high-frequency futures `bookTicker` and raw depth deltas to remote Redis. Keep depth local for book sync and publish only the derived futures BBO once the local book is synchronized.
- Operational result: the dashboard should show `perp_bbo: local_book` when the futures book is synced and `perp_bbo: fallback` during startup/resync.

### Remote Redis Latency
- **Do not publish high-frequency data to the remote Redis Cloud instance.** Each Redis call is ~50ms round-trip. Publishing every bookTicker or depth delta (10–50 messages/sec) throttles the websocket loop to <1 msg/sec, breaking local book sync and causing stale data. Only publish derived/aggregated state (BBO from synced book, trades, features, scores). Raw depth deltas are consumed locally only.

### Futures Market Path — Resolved
- The futures `/market` stream originally suffered the same remote Redis throttling issue as the public book path. `aggTrade` volume caused the loop to fall behind, which made `ts_exchange` for trades, mark/index, and liquidations look minutes stale.
- Fix: split futures trades onto their own websocket loop, keep markPrice and liquidations on a separate low-volume loop, and throttle latest-state trade writes while still publishing trade events for the feature engine.
- Operational result: futures trade and mark/index timestamps are now close to local time, `/latest/trade/futures` remains available for inspection, and `/latest/collector/futures` exposes public/trade/market feed ages.

### Futures Book Health
- Futures book sync is now live and exposed through `/latest/book/futures`, including `sync_status`, `sync_reason`, `best_bid/ask`, `depth_imbalance_5bps`, `depth_imbalance_10bps`, `near_touch_bid/ask_usd`, `book_age_ms`, and `book_stale`.
- The feature engine now reads the synchronized in-process futures book directly for real depth-derived features and `book_sync_ok`.

### Spot BBO — Resolved
- The original symptom was that spot barely moved relative to perp even when the status showed `spot_px: bbo`. Root cause: spot depth and spot trades shared one websocket loop, and synchronous remote Redis writes for trades starved the depth path.
- Fix: split spot trades and spot depth into separate websocket loops, keep spot depth local, synchronize a local spot book from REST snapshot + live deltas, and publish only derived spot BBO with bid/ask-price dedupe.
- Operational result: the dashboard should show `spot_book: synced (snapshot_bridged)` and `spot_px: local_book` when the spot local book is healthy, and `/latest/trade/spot` exposes a throttled latest trade snapshot for debugging and fallback inspection.

### 3m/5m Scores Are Fake
- `score_3m = 0.75 * score_1m` and `score_5m = 0.60 * score_1m`. These need real rolling score history. Section 7 work.

### History Endpoints Not Implemented
- `/history/features` and `/history/score` return placeholder responses. Need a storage strategy (Redis sorted sets or in-memory ring buffers). Section 8 work.

### No Raw Event Archival
- Raw events are published to Redis pub/sub but not persisted. Replay and backtesting require an archive layer (ClickHouse, Parquet, or Redis Streams). Phase 2 work.

### Raw Event Archival Metadata
- The live Redis contract is now centralized in `app/contract.py`, but archival/replay metadata is still deferred. Raw events are not yet stored in a durable replay format. That remains Phase 2 work.

### OI Health
- OI is polled over REST, so its exchange timestamp is naturally older than streaming feeds and is tracked separately via `oi_lag_ms_p95`.
- `/latest/open-interest/futures` exposes the latest normalized OI event, and `/health` now reports OI lag and stale state without polluting the overall real-time feed lag metric.
