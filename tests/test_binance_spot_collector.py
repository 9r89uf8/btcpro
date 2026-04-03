from app.collectors.binance_spot import BinanceSpotCollector
from app.contract import BINANCE_SPOT


class DummyBus:
    async def publish_json(self, channel, payload):
        return None

    async def set_json(self, key, payload):
        return None

    async def publish_and_set_json(self, channel, key, payload):
        return None

    async def publish_only_json(self, channel, payload):
        return None


def make_collector() -> BinanceSpotCollector:
    return BinanceSpotCollector(DummyBus())


def test_parse_trade_maps_taker_sold_and_computes_notional():
    collector = make_collector()
    collector.now_ms = lambda: 1710000000999

    event = collector._parse_trade(
        {
            "s": "BTCUSDT",
            "m": True,
            "p": "68500.0",
            "q": "0.10",
            "a": 999999,
            "T": 1710000000100,
        }
    )

    assert event.venue == BINANCE_SPOT
    assert event.market_type == "spot"
    assert event.aggressive_side == "sell"
    assert event.price == 68500.0
    assert event.size == 0.10
    assert event.notional == 6850.0
    assert event.trade_id == "999999"
    assert event.ts_exchange == 1710000000100
    assert event.ts_local == 1710000000999


def test_parse_trade_maps_taker_bought():
    collector = make_collector()
    collector.now_ms = lambda: 1710000000999

    event = collector._parse_trade(
        {
            "s": "BTCUSDT",
            "m": False,
            "p": "68500.0",
            "q": "0.50",
            "a": 999998,
            "T": 1710000000100,
        }
    )

    assert event.aggressive_side == "buy"
    assert event.notional == 34250.0


def test_parse_book_delta_handles_optional_pu():
    collector = make_collector()
    collector.now_ms = lambda: 1710000001000

    # Spot depth without pu field
    event = collector._parse_book_delta(
        {
            "s": "BTCUSDT",
            "U": 100,
            "u": 105,
            "E": 1710000000500,
            "b": [["68000.0", "1.5"]],
            "a": [["68001.0", "2.0"]],
        }
    )

    assert event.venue == BINANCE_SPOT
    assert event.first_update_id == 100
    assert event.final_update_id == 105
    assert event.prev_final_update_id is None
    assert event.bids == [[68000.0, 1.5]]
    assert event.asks == [[68001.0, 2.0]]


def test_build_bbo_from_synced_book():
    collector = make_collector()
    collector.now_ms = lambda: 1710000002000

    # Bootstrap the book
    collector.book.apply_snapshot(
        bids=[["68000.0", "1.5"], ["67999.0", "3.0"]],
        asks=[["68001.0", "2.0"], ["68002.0", "1.0"]],
        last_update_id=100,
    )
    # Bridge it
    collector.book.apply_delta(
        first_update_id=101,
        final_update_id=101,
        prev_final_update_id=None,
        bids=[[68000.5, 0.5]],
        asks=[],
    )
    assert collector.book.synced is True

    from app.models import BookDeltaEvent
    dummy_event = BookDeltaEvent(
        venue="binance_spot",
        symbol="BTCUSDT",
        first_update_id=101,
        final_update_id=101,
        bids=[],
        asks=[],
        ts_exchange=1710000001500,
        ts_local=1710000002000,
    )
    bbo = collector._build_bbo_from_book(dummy_event)

    assert bbo is not None
    assert bbo.venue == BINANCE_SPOT
    assert bbo.bid_px == 68000.5
    assert bbo.ask_px == 68001.0
    assert bbo.mid_px == (68000.5 + 68001.0) / 2.0
    assert bbo.spread_bps > 0


def test_spot_bbo_dedup_skips_unchanged_prices():
    collector = make_collector()

    from app.models import BBOEvent
    bbo1 = BBOEvent(
        venue="binance_spot", symbol="BTCUSDT",
        bid_px=68000.0, bid_sz=1.0, ask_px=68001.0, ask_sz=1.0,
        mid_px=68000.5, spread_bps=0.015,
        ts_exchange=1000, ts_local=1000,
    )
    bbo2 = BBOEvent(
        venue="binance_spot", symbol="BTCUSDT",
        bid_px=68000.0, bid_sz=2.0, ask_px=68001.0, ask_sz=3.0,
        mid_px=68000.5, spread_bps=0.015,
        ts_exchange=2000, ts_local=2000,
    )
    bbo3 = BBOEvent(
        venue="binance_spot", symbol="BTCUSDT",
        bid_px=68000.5, bid_sz=1.0, ask_px=68001.0, ask_sz=1.0,
        mid_px=68000.75, spread_bps=0.007,
        ts_exchange=3000, ts_local=3000,
    )

    assert collector._spot_bbo_changed(bbo1) is True   # first one always passes
    assert collector._spot_bbo_changed(bbo2) is False  # same bid/ask prices
    assert collector._spot_bbo_changed(bbo3) is True   # bid changed
