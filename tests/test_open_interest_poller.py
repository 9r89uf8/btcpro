from app.collectors.binance_open_interest import BinanceOpenInterestPoller, OI_STALE_MS
from app.contract import BINANCE_FUTURES


class DummyBus:
    async def publish_json(self, channel, payload):
        return None

    async def set_json(self, key, payload):
        return None

    async def publish_and_set_json(self, channel, key, payload):
        return None


def make_poller() -> BinanceOpenInterestPoller:
    return BinanceOpenInterestPoller(DummyBus())


def test_health_payload_initial_state():
    poller = make_poller()
    h = poller.health_payload()
    assert h["last_success_ms"] is None
    assert h["stale"] is False  # no data yet, not "stale" — just empty
    assert h["consecutive_failures"] == 0
    assert h["total_polls"] == 0
    assert h["last_error"] is None


def test_health_payload_after_success():
    poller = make_poller()
    poller._last_success_ms = 1000
    poller._total_polls = 5
    poller.now_ms = lambda: 2000

    h = poller.health_payload()
    assert h["last_success_age_ms"] == 1000
    assert h["stale"] is False
    assert h["consecutive_failures"] == 0


def test_health_payload_stale_after_threshold():
    poller = make_poller()
    poller._last_success_ms = 1000
    poller.now_ms = lambda: 1000 + OI_STALE_MS + 1

    h = poller.health_payload()
    assert h["stale"] is True


def test_health_payload_tracks_failures():
    poller = make_poller()
    poller._consecutive_failures = 5
    poller._total_failures = 12
    poller._total_polls = 100
    poller._last_error_ms = 9000
    poller._last_error_msg = "TimeoutError: timed out"

    h = poller.health_payload()
    assert h["consecutive_failures"] == 5
    assert h["total_failures"] == 12
    assert h["total_polls"] == 100
    assert h["last_error"] == "TimeoutError: timed out"


def test_parse_oi_event():
    """Verify OI event construction matches expected contract."""
    from app.models import OpenInterestEvent

    poller = make_poller()
    poller.now_ms = lambda: 1710000001500

    # Simulate what the poller builds from REST response
    data = {
        "symbol": "BTCUSDT",
        "openInterest": "25231.44",
        "time": 1710000001000,
    }
    event = OpenInterestEvent(
        venue="binance_futures",
        symbol=data["symbol"],
        open_interest=float(data["openInterest"]),
        ts_exchange=int(data["time"]),
        ts_local=poller.now_ms(),
    )

    assert event.venue == BINANCE_FUTURES
    assert event.symbol == "BTCUSDT"
    assert event.open_interest == 25231.44  # BTC, not USD
    assert event.ts_exchange == 1710000001000
    assert event.ts_local == 1710000001500
