from app.collectors.binance_futures import (
    BINANCE_FUTURES,
    MARKET_FEED_STALE_MS,
    PUBLIC_FEED_STALE_MS,
    BinanceFuturesCollector,
)


class DummyBus:
    async def publish_json(self, channel, payload):
        return None

    async def set_json(self, key, payload):
        return None


def make_collector() -> BinanceFuturesCollector:
    return BinanceFuturesCollector(DummyBus())


def test_futures_stream_urls_use_separated_routing():
    collector = make_collector()

    assert collector._public_stream_url() == (
        f"{collector.settings.binance_futures_public_ws}/stream"
        f"?streams={collector.symbol}@bookTicker/{collector.symbol}@depth@100ms"
    )
    # aggTrade is on its own dedicated websocket
    assert collector._trades_stream_url() == (
        f"{collector.settings.binance_futures_market_ws}/stream"
        f"?streams={collector.symbol}@aggTrade"
    )
    # markPrice + liquidations on a separate low-volume websocket
    assert collector._market_stream_url() == (
        f"{collector.settings.binance_futures_market_ws}/stream"
        f"?streams={collector.symbol}@markPrice@1s/!forceOrder@arr"
    )


def test_parse_trade_maps_taker_sold_and_computes_notional():
    collector = make_collector()
    collector.now_ms = lambda: 1710000000999

    event = collector._parse_trade(
        {
            "s": "BTCUSDT",
            "m": True,
            "p": "69000.5",
            "q": "0.25",
            "a": 123456,
            "T": 1710000000123,
        }
    )

    assert event.venue == BINANCE_FUTURES
    assert event.market_type == "perp"
    assert event.symbol == "BTCUSDT"
    assert event.aggressive_side == "sell"
    assert event.price == 69000.5
    assert event.size == 0.25
    assert event.notional == 17250.125
    assert event.trade_id == "123456"
    assert event.ts_exchange == 1710000000123
    assert event.ts_local == 1710000000999


def test_parse_mark_index_computes_premium_bps_and_funding():
    collector = make_collector()
    collector.now_ms = lambda: 1710000001999

    event = collector._parse_mark_index(
        {
            "s": "BTCUSDT",
            "p": "69010.5",
            "i": "68995.2",
            "r": "0.0001",
            "E": 1710000001000,
        }
    )

    expected = 10000.0 * (69010.5 - 68995.2) / 68995.2

    assert event.venue == BINANCE_FUTURES
    assert event.symbol == "BTCUSDT"
    assert event.mark_price == 69010.5
    assert event.index_price == 68995.2
    assert event.funding_rate == 0.0001
    assert event.premium_bps == expected
    assert event.ts_exchange == 1710000001000
    assert event.ts_local == 1710000001999


def test_parse_liquidation_handles_force_order_shape_for_target_symbol():
    collector = make_collector()
    collector.now_ms = lambda: 1710000001555

    event = collector._parse_liquidation(
        {
            "o": {
                "s": "BTCUSDT",
                "S": "SELL",
                "ap": "68900.0",
                "z": "1.25",
                "T": 1710000001000,
            }
        }
    )

    assert event is not None
    assert event.venue == BINANCE_FUTURES
    assert event.symbol == "BTCUSDT"
    assert event.side == "SELL"
    assert event.price == 68900.0
    assert event.size == 1.25
    assert event.notional == 86125.0
    assert event.ts_exchange == 1710000001000
    assert event.ts_local == 1710000001555


def test_parse_liquidation_ignores_other_symbols():
    collector = make_collector()

    event = collector._parse_liquidation(
        {
            "o": {
                "s": "ETHUSDT",
                "S": "BUY",
                "ap": "3500.0",
                "z": "2.0",
                "T": 1710000001000,
            }
        }
    )

    assert event is None


def test_collector_state_payload_tracks_feed_ages_and_staleness():
    collector = make_collector()
    collector._last_public_event_ms = 1000
    collector._last_trades_event_ms = 2000
    collector._last_market_event_ms = 3000

    # Simulate the watch loop setting stale flags
    collector._public_feed_stale = True
    collector._trades_feed_stale = True
    collector._market_feed_stale = True

    payload = collector._collector_state_payload(now_ms=9000)

    assert payload["venue"] == BINANCE_FUTURES
    assert payload["symbol"] == "BTCUSDT"
    assert payload["public_feed_age_ms"] == 8000
    assert payload["trades_feed_age_ms"] == 7000
    assert payload["market_feed_age_ms"] == 6000
    assert payload["public_feed_stale"] is True
    assert payload["trades_feed_stale"] is True
    assert payload["market_feed_stale"] is True

    # Reset flags as watch loop would
    collector._market_feed_stale = False
    fresh_payload = collector._collector_state_payload(now_ms=3000 + MARKET_FEED_STALE_MS - 1)
    assert fresh_payload["market_feed_stale"] is False
