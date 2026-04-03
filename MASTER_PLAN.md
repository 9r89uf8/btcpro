# BTC Microstructure Dashboard Master Plan


## How To Use This Plan

- Treat each section as a hard checkpoint.
- Do not move to the next section until the current section passes its validation items.
- Prefer making the existing scaffold correct over rewriting it.
- Keep raw events, timestamps, book health, and data-quality handling first-class throughout.

## Current Starting Point

The current repo has:

- [README.md](/mnt/c/Users/alexa/PycharmProjects/Polypro3/README.md) with the target spec
- `btc_microstructure_dashboard_handoff.zip` with a starter scaffold

Key known gaps from the handoff scaffold:

- The zip scaffold is not yet extracted into the working tree.
- Redis keys and channel naming are not fully aligned with the spec.
- Local book sync exists as a helper but is not wired into the live feature path.
- The feature engine still contains placeholder values.
- The API and dashboard are starter-level only.
- The unified runtime is missing.
- Required tests for trade normalization and book continuity are missing.

## Runtime Note: Redis Environment

We have a user-provided remote Redis instance available for later integration.

Rules for using it:

- Use environment-based configuration only, preferably `REDIS_URL`
- Do not hardcode credentials in source files
- Do not commit secrets into `.env.example`, README files, or tracked config
- Treat the user-provided Redis instance as the primary runtime path

## Section 0: Bootstrap The Working Tree

Goal:

- Extract the handoff scaffold into the repo and make the base project runnable.

Build tasks:

- [x] Extract `btc_microstructure_dashboard_handoff.zip`
- [x] Decide whether extracted files live at repo root or under a subdirectory
- [x] Copy `.env.example` to `.env`
- [x] Point `REDIS_URL` at the user-provided Redis instance
- [x] Install dependencies with `pip install -e .` or equivalent
- [x] Verify the configured Redis instance is reachable
- [x] Verify package imports work from the chosen project root

Validation:

- [x] `pytest` runs
- [x] `from app.config import get_settings; get_settings()` works
- [x] `app.bus.RedisBus` can connect and set/get a key
- [x] FastAPI and Dash modules import without crashing

Definition of done:

- The scaffold is present in the working tree and can be run locally.

## Section 1: Normalize Core Contracts And Runtime Conventions

Goal:

- Align config, models, Redis channels, and latest-state keys with the README contract before deeper integration.

Build tasks:

- [x] Standardize symbol casing rules across collectors, models, API, and dashboard
- [x] Standardize event schemas around `venue`, `market_type`, `symbol`, `ts_exchange`, and `ts_local`
- [x] Replace inconsistent latest-state keys with a documented contract
- [x] Add shared helpers for raw publish + latest-state updates
- [x] Decide how raw event archival metadata will be stored for later replay (deferred to Phase 2 / not blocking Phase 1)

Validation:

- [x] Every emitted event matches one documented schema
- [x] Latest-state keys are predictable and no longer ad hoc
- [x] The API can read the expected keys without collector-specific exceptions

Definition of done:

- The data contract is stable enough that later sections can build on it without renaming churn.

## Section 2: Binance Futures Collector

Goal:

- Get live Binance futures trades, mark/index prices, bookTicker, depth deltas, and liquidations flowing correctly into Redis.

Build tasks:

- [x] Validate the separated futures websocket routing required by the README
- [x] Keep `/public` for book feeds and `/market` for aggTrade, markPrice, and liquidations unless actual connectivity forces a fallback
- [x] Run `binance_futures.py` standalone
- [x] Publish raw events and latest-state updates through one consistent contract
- [x] Add reconnect logging, backoff behavior, and stale-feed tracking

Data correctness checks:

- [x] `m=true` maps to taker sold, so `aggressive_side="sell"`
- [x] `notional = price * size`
- [x] `ts_exchange` is in milliseconds and close to `ts_local`
- [x] `mark_price` and `index_price` are realistic relative to Binance UI
- [x] `premium_bps = 10000 * (mark - index) / index`
- [x] `funding_rate` looks reasonable relative to the venue
- [x] `side=BUY` liquidation is treated as short liquidation and bullish fuel
- [x] `side=SELL` liquidation is treated as long liquidation and bearish fuel
- [x] Liquidation payload parsing matches the actual `forceOrder` shape

Definition of done:

- Raw futures trade, BBO, book delta, mark/index, and liquidation events are continuously available and recover cleanly from disconnects.

## Section 3: Binance Futures Local Order Book

Goal:

- Turn the starter `LocalBook` into a production-usable synchronized futures book with book-health tracking.

Build tasks:

- [x] Buffer depth deltas on connect
- [x] Fetch the REST snapshot with `fetch_binance_futures_snapshot()`
- [x] Apply only deltas that correctly bridge the snapshot
- [x] Validate `prev_final_update_id` continuity on every delta
- [x] Resnapshot automatically on desync
- [x] Expose `book_sync_ok`, best bid/ask, depth metrics, and freshness

Data correctness checks:

- [x] `book.top()` matches Binance UI within one tick
- [x] `book.mid()` is close to live trade price
- [x] `best_bid < best_ask` always holds
- [x] `notional_within_bps(10)` returns realistic USD depth
- [x] `imbalance_within_bps(10)` stays within `[-1, 1]`

Tests to add:

- [x] Snapshot bootstrap behavior
- [x] Delta continuity acceptance
- [x] Desync detection and resync behavior

Definition of done:

- Futures local book stays synchronized under normal flow and visibly degrades when continuity breaks.

## Section 4: Binance Spot Collector

Goal:

- Bring spot trades, BBO, and depth into the same normalized pipeline for confirmation.

Build tasks:

- [x] Run `binance_spot.py` standalone
- [x] Validate spot trade normalization
- [x] Validate spot BBO parsing (now derived from local book, not bookTicker)
- [x] Decide whether spot needs a fully synchronized local book or only BBO plus limited depth features (decided: full local book)
- [x] Publish spot events into the unified Redis contract

Data correctness checks:

- [x] Spot price is close to futures price
- [x] Spot uses the same taker-side logic: `m=true` means taker sold
- [x] Spot local-book BBO matches Binance spot UI
- [x] Spot depth delta handling is correct with optional `pu`
- [x] `ts_exchange` is close to `ts_local` (sub-100ms via in-process queue)

Definition of done:

- Spot confirmation data is live, normalized, and usable by the feature engine.

## Section 5: Open Interest Poller

Goal:

- Poll Binance open interest reliably and expose it through the same event/state contract.

Build tasks:

- [x] Run `binance_open_interest.py` standalone
- [x] Normalize the latest-state key naming to match the rest of the system
- [x] Confirm polling rate is acceptable for Binance limits
- [x] Surface failures with counters, timestamps, and health payload

Data correctness checks:

- [x] `open_interest` is in BTC, not USD
- [x] `ts_exchange` comes from the REST payload time field
- [x] Values are in a plausible range for BTCUSDT futures

Definition of done:

- OI updates arrive roughly once per second and are consumable by the feature engine without special-case key logic.

## Section 6: Feature Engine

Goal:

- Replace placeholders in `app/features/engine.py` with real 1-second feature bars built from raw events and synchronized book state.

Build tasks:

- [x] Keep rolling perp CVD windows
- [x] Keep rolling spot CVD windows
- [x] Track premium and 5-second premium delta
- [x] Track OI history and 30-second OI delta
- [x] Track liquidation skew over 30 seconds
- [x] Wire real depth imbalance and near-touch depth from the local book
- [x] Compute depth pull over 5 seconds
- [x] Compute feed lag metrics from exchange vs local timestamps (per-source breakdown)
- [x] Store latest feature bars and enough short-term history for API/dashboard use (60min ring buffer)

Features that must be real before this section is complete:

- [x] `perp_cvd_1s`, `perp_cvd_5s`, `perp_cvd_15s`
- [x] `spot_cvd_1s`, `spot_cvd_5s`, `spot_cvd_15s`
- [x] `premium_bps`, `delta_premium_bps_5s`
- [x] `depth_imbalance_5bps`, `depth_imbalance_10bps`
- [x] `near_touch_depth_bid_usd`, `near_touch_depth_ask_usd`
- [x] `depth_pull_bid_5s`, `depth_pull_ask_5s`
- [x] `oi_delta_30s`
- [x] `liq_skew_30s`
- [x] `book_sync_ok`
- [x] `feed_lag_ms_p95` (plus per-source: futures_trade, spot_trade, bbo, mark_index, oi)

Validation:

- [x] `perp_cvd_5s` fluctuates under live trade flow and is not stuck at zero
- [x] `spot_cvd_5s` fluctuates when spot trades are present
- [x] `delta_premium_bps_5s` stays in a realistic range
- [x] `spread_bps` looks realistic for BTCUSDT
- [x] `oi_delta_30s` is small relative to total OI
- [x] `liq_skew_30s` is near zero most of the time but spikes during liquidation bursts
- [x] `feed_lag_ms_p95` remains reasonable in normal conditions
- [x] Rolling window trimming works and does not leak memory

Tests to add:

- [x] Rolling signed window expiry behavior
- [x] Premium delta logic
- [x] OI delta logic
- [x] Feature-bar assembly from controlled events (p95 helper, z-score directionality)

Definition of done:

- A real `FeatureBar` is emitted every second and is good enough to drive scoring and the dashboard.

## Section 7: Score Engine

Goal:

- Make the rule-based score match the README logic and handle degraded data properly.

Build tasks:

- [x] Keep the current weighted linear score as the MVP core
- [x] Improve the 3-minute and 5-minute score path so they are not just scaled placeholders forever (rolling average over 180s/300s)
- [x] Add confidence gating for stale feeds and unsynced books
- [x] Add degraded state behavior for stale futures data
- [x] Improve reason strings so the dashboard can explain state changes clearly (bearish + degraded coverage)

Validation:

- [x] Bullish z-score inputs produce positive scores
- [x] Bearish z-score inputs produce negative scores
- [x] All-zero inputs produce a neutral score
- [x] `book_sync_ok=False` caps confidence appropriately
- [x] Spot staleness lowers confidence
- [x] Extreme premium without spot confirmation reduces confidence as intended

Tests to add:

- [x] Existing bullish case
- [x] Existing bearish case
- [x] All-zero neutral case
- [x] Confidence cap when book is unsynced
- [x] Degraded state or stale-feed behavior
- [x] Spot staleness lowers confidence
- [x] Extreme premium without spot confirmation
- [x] Bearish reason strings

Definition of done:

- Score, confidence, and reasons are directionally correct and data-quality aware.

## Section 8: FastAPI Server

Goal:

- Serve health, latest state, and recent history as the stable application boundary.

Build tasks:

- [x] Run `uvicorn app.api.main:app --reload` (standalone for latest-state; history requires run_all.py)
- [x] Upgrade `/health` to report real feed ages and book health (last_event_age_ms, futures_feed_age_ms, spot_feed_age_ms, per-venue lags)
- [x] Keep `/latest/score` and `/latest/features` aligned with the normalized state keys
- [x] Implement `/history/features?minutes=60`
- [x] Implement `/history/score?minutes=60`
- [x] Decide the MVP history store: 60-minute in-memory ring buffers via feature engine

Validation:

- [x] Endpoint values match Redis state
- [x] `/health` reports `book_sync_ok`, `last_event_age_ms`, `futures_feed_age_ms`, and `spot_feed_age_ms`
- [x] Response times are acceptable for live UI polling

Tests to add:

- [x] Health contract test (3 tests: empty state, book sync, feed ages)
- [x] Latest score/features endpoint tests (7 tests: empty, with data, all, trades, books)
- [x] History endpoint tests (3 tests: without engine, with engine for features and scores)

Definition of done:

- The dashboard can rely on the API without directly knowing Redis internals.

## Section 9: Dashboard UI

Goal:

- Replace the placeholder Dash app with the dashboard described in the README.

Build tasks:

- [x] Build the ribbon with perp mid, spot mid, premium, scores, confidence, lag, and book sync
- [x] Build the price/state chart (with state shading and zero-price filtering)
- [x] Build the premium chart (exchange premium + delta premium)
- [x] Build the perp and spot CVD chart (1s/5s/15s for both venues)
- [x] Build the depth/spread chart (imbalance 5bps/10bps + near-touch depth + spread)
- [x] Build the OI and liquidation chart (OI delta line + liq skew bars)
- [x] Build the right column for reasons, warnings, state changes, and feed health
- [x] Decide whether to stay with polling or move to a lower-latency transport later (polling at 5s/10s; stores bypassed due to Dash callback issues)

Validation:

- [x] Visual values match API responses
- [x] The UI remains readable during degraded feed conditions
- [x] History panels load enough recent data to be useful (10 minutes from in-memory ring buffers)

Definition of done:

- The dashboard presents the full live state in a usable way, not just raw JSON dumps.

## Section 10: Unified Runtime

Goal:

- Provide one documented way to bring up the stack locally and one clear structure for how the services fit together.

Build tasks:

- [ ] Create a top-level launcher such as `run_all.py` or equivalent
- [ ] Start collectors, OI poller, feature engine, API, and dashboard with a clear startup order
- [ ] Decide whether the dashboard runs inside the launcher or as a separate optional process
- [ ] Add graceful shutdown handling
- [ ] Surface stale-feed alerts visibly
- [ ] Document the single “start the system” command path

Important note:

- Do not make “all components share one RedisBus instance” a hard requirement if the chosen runtime model uses multiple processes.

Validation:

- [ ] Trades flow through Redis to features, scores, API, and dashboard
- [ ] End-to-end latency is acceptable for a 1-second feature cadence
- [ ] Feed failures become visible degraded states, not silent bad outputs

Tests to add:

- [ ] End-to-end smoke test for the core pipeline

Definition of done:

- A new developer can start Redis, run one documented workflow, and get a live dashboard.

## Section 11: Bybit Confirmation

Goal:

- Add Bybit as an optional secondary confirmation layer after the Binance path is stable.

Build tasks:

- [ ] Verify Bybit websocket connectivity and parsing
- [ ] Normalize trades and liquidations correctly
- [ ] Extend ticker and orderbook handling beyond passthrough if needed
- [ ] Expose any useful confirmation signals to the engine and UI without making Binance depend on Bybit

Data correctness checks:

- [ ] Bybit BTCUSDT price is close to Binance futures price
- [ ] Bybit side mapping is normalized correctly
- [ ] Liquidation semantics match Bybit docs and observed payloads

Definition of done:

- Bybit adds optional confirmation without increasing fragility in the core system.

## Critical Data Correctness Checklist

Run these throughout the build:

- [ ] BTC price matches the venue UI within a reasonable live-market tolerance
- [ ] `m=true` on Binance means taker sold
- [ ] `premium_bps` uses mark and index, not last trade
- [ ] Spread stays in a plausible range for BTCUSDT
- [ ] OI values are plausible for BTCUSDT futures
- [ ] `BUY` liquidation means short liquidation and bullish fuel
- [ ] `SELL` liquidation means long liquidation and bearish fuel
- [ ] `ts_exchange` is in milliseconds and close to `ts_local`
- [ ] Best bid always stays below best ask
- [ ] Depth metrics remain numerically sane

## Test Plan By Section

- Section 2: trade-side normalization for futures and spot
- Section 3: snapshot/delta continuity and resync tests
- Section 6: feature calculations from controlled event streams
- Section 7: score sign, gating, neutral, and degraded-behavior tests
- Section 8: API contract tests
- Section 10: end-to-end smoke test

## Recommended Execution Order

1. Section 0: bootstrap the working tree
2. Section 1: normalize contracts and runtime conventions
3. Section 2: make Binance futures ingestion correct
4. Section 3: finish futures local-book sync and book health
5. Section 4: add Binance spot confirmation
6. Section 5: make open interest polling correct
7. Section 6: complete the feature engine
8. Section 7: tighten the score engine
9. Section 8: build the API around real state and history
10. Section 9: replace the placeholder dashboard
11. Section 10: unify everything into one runnable system
12. Section 11: add Bybit confirmation

## Suggested First Working Slice

The best first slice is:

1. Extract the scaffold.
2. Normalize the Redis and event contract.
3. Make the Binance futures collector correct.
4. Finish the futures local book and book health path.
5. Add the missing tests for trade normalization and book continuity.

That creates a stable spine before we add spot confirmation, richer features, scoring refinements, API history, and the full UI.
