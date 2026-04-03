# BTC Microstructure Dashboard — Build Status

## What Has Been Built

### Infrastructure
- Extracted scaffold from `btc_microstructure_dashboard_handoff.zip` into repo root
- Fixed `pyproject.toml` package discovery for multi-package layout (`app/`, `dashboard/`)
- All dependencies installed and importable
- `.env` configured with remote Redis Cloud instance (redis.io)
- Redis connectivity verified

### Collectors (app/collectors/)
- **Binance Futures** — collector split across three websocket loops: `/public` for depth (local book sync), `/market` aggTrade on its own loop, and `/market` markPrice/liquidations on a separate low-volume loop. Futures trades feed the feature engine through an in-process queue (zero latency) with throttled (~1/sec) latest-state writes for API inspection. Futures BBO is derived from the synchronized local book with price-change deduplication.
- **Binance Spot** — collector split into separate trade and depth websocket loops. Spot trades feed the feature engine through the same in-process queue with throttled latest-state writes. Spot depth stays local, synchronizes a local spot book via REST snapshot + live deltas, and spot BBO is derived from the synchronized local book with bid/ask-price dedupe.
- **Binance OI Poller** — REST poller hitting `/fapi/v1/openInterest` every 1 second. Tracks last-success timing, stale detection, consecutive failures, total polls/failures, and last error details.
- **Bybit** — optional confirmation collector for trades and liquidations (starter-level). Ticker/orderbook topics subscribed but not processed (Section 11).
- All collectors have reconnect + backoff + logging on disconnect.

### Data Layer
- **RedisBus** (`app/bus.py`) — async Redis wrapper with pub/sub, latest-state key/value, pipelined publish+set, and publish-only methods.
- **Contract Layer** (`app/contract.py`) — centralized venue constants plus shared Redis channel/key builders. Source of truth for symbol casing and Redis naming conventions.
- **Models** (`app/models.py`) — Pydantic schemas for all event types and FeatureBar/ScoreSnapshot.
- **LocalBook** (`app/books/`) — shared order book for futures and spot with snapshot bootstrap, delta bridging (futures `pu` path and spot `U/u` path), desync detection, and depth metrics.

### Feature Engine (app/features/)
- Receives trades via in-process queue (zero Redis latency), other events via Redis pub/sub.
- Rolling CVD windows (1s/5s/15s) for perp and spot, premium/OI/liquidation tracking.
- Real depth metrics from synchronized in-process futures local book.
- Per-source lag tracking (futures_trade, spot_trade, bbo_futures, bbo_spot, mark_index, oi) with OI excluded from aggregate real-time metric.
- 60-minute in-memory ring buffers for feature and score history.

### Score Engine (app/features/scoring.py)
- Weighted linear score matching the README spec weights.
- Real 3m/5m scores via rolling average of score_1m over 180s/300s.
- State classification with `degraded` override when futures feed is stale (>1s).
- Confidence gating: book unsync (cap 0.25), spot stale >2s (0.7x), extreme bullish premium without spot confirmation (cap 0.50).
- Bidirectional reason strings covering both bullish and bearish signals, plus stale/degraded warnings.

### API (app/api/main.py)
- FastAPI on port 8000.
- `/health` — reports book sync (futures + spot), last_event_age_ms, per-venue feed ages, per-source lags, OI stale, futures/spot feeds stale.
- `/latest/*` — score, features, bbo, trade, book, mark-index, liquidation, open-interest, collector state, and `/latest/all` aggregate.
- `/history/features?minutes=N` and `/history/score?minutes=N` — real in-memory history (requires `run_all.py`; returns empty with message when standalone).
- 13 API contract tests covering health, latest, and history endpoints.

### Dashboard (dashboard/app.py)
- Dash app on port 8050 with dark theme, 5-second polling.
- Live charts: price (perp vs spot from local books), premium bps, perp CVD 5s.
- Status bar: price, state, score, confidence, lag, spread, book sync, BBO source.

### Unified Launcher (run_all.py)
- Single `python run_all.py` starts everything: futures collector, spot collector, OI poller, feature engine, API server, dashboard.
- Wires in-process trade queue and book reference between collectors and feature engine.
- Shares feature engine with API for history endpoints.

### Tests (59 passing)
- **Futures collector** (6) — routing, trade normalization, mark/index, liquidation parsing, feed-age state.
- **Local book** (6) — snapshot bootstrap, bridge acceptance (futures pu + spot U/u), continuity mismatch.
- **Spot collector** (5) — trade normalization, book delta parsing, BBO from book, BBO dedup.
- **OI poller** (5) — health state lifecycle, stale detection, failure tracking, event parsing.
- **Feature engine** (15) — rolling windows, z-scores, p95, premium delta, OI delta, rolling score avg.
- **Scoring** (9) — bullish, bearish, neutral, book unsync cap, spot stale, premium cap (bullish-only), degraded state, bearish reasons.
- **API** (13) — health contract, latest endpoints, history with/without engine.

---

## Current Phase

**Sections 0–8 are complete.**

Sections 9 (Dashboard UI), 10 (Unified Runtime), and 11 (Bybit Confirmation) remain.

---

## Phases Remaining

| Section | Name | Status | Summary |
|---------|------|--------|---------|
| 0 | Bootstrap | **Done** | Scaffold extracted, deps installed, Redis connected, launcher working |
| 1 | Normalize Contracts | **Done** | Central contract layer, symbol casing rules, shared channel/key builders |
| 2 | Binance Futures Collector | **Done** | Separated routing, trade/mark/liquidation normalization, feed-age diagnostics |
| 3 | Futures Local Order Book | **Done** | Live book sync, sync telemetry, real depth/freshness metrics |
| 4 | Binance Spot Collector | **Done** | Spot trade normalization, local-book BBO, latest trade state, collector tests |
| 5 | Open Interest Poller | **Done** | OI contract validated, health tracking, API endpoint, lag/stale handling |
| 6 | Feature Engine | **Done** | Real rolling features, per-source lag, depth from local book, 60min history |
| 7 | Score Engine | **Done** | Real 3m/5m rolling scores, confidence gating (book/spot/premium), degraded state, bidirectional reasons |
| 8 | FastAPI Server | **Done** | Full health reporting, per-venue feed ages, history endpoints, 13 API tests |
| 9 | Dashboard UI | Not started | Full ribbon, charts, right column |
| 10 | Unified Runtime | Not started | Startup ordering, documentation |
| 11 | Bybit Confirmation | Not started | Validate Bybit parsing, expose confirmation signals |

---

## Known Issues & Things To Watch

### Dashboard Performance
- **The dashboard cannot handle polling faster than 5 seconds.** Dash rebuilds the full Plotly figure on every callback. For faster updates, switch to WebSocket push or Dash clientside callbacks.

### Remote Redis Latency
- **Do not publish high-frequency data to the remote Redis Cloud instance.** Each Redis call is ~50ms round-trip. Only publish derived/aggregated state. Raw depth deltas and individual trades are consumed locally via in-process queue.

### No Raw Event Archival
- Raw events are not persisted for replay/backtesting. Phase 2 work (ClickHouse, Parquet, or Redis Streams).
