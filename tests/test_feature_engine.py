from collections import deque

from app.features.rolling import RollingSignedWindow, RollingSeries
from app.features.engine import FeatureEngine


# ── RollingSignedWindow tests ────────────────────────────────────────

def test_rolling_window_sum_within_window():
    w = RollingSignedWindow(5000)
    w.add(1000, 100.0)
    w.add(2000, -50.0)
    w.add(3000, 200.0)
    assert w.sum(3000) == 250.0


def test_rolling_window_expires_old_items():
    w = RollingSignedWindow(5000)
    w.add(1000, 100.0)
    w.add(2000, 200.0)
    w.add(7000, 50.0)  # this should expire the 1000 entry
    assert w.sum(7000) == 250.0  # 200 + 50 (100 expired)


def test_rolling_window_all_expired():
    w = RollingSignedWindow(1000)
    w.add(1000, 100.0)
    w.add(1500, 200.0)
    assert w.sum(5000) == 0.0  # both expired


def test_rolling_window_empty():
    w = RollingSignedWindow(5000)
    assert w.sum(1000) == 0.0


def test_rolling_window_signed_values():
    w = RollingSignedWindow(10000)
    w.add(1000, 500.0)   # buy
    w.add(2000, -300.0)  # sell
    w.add(3000, -400.0)  # sell
    assert w.sum(3000) == -200.0  # net bearish


# ── RollingSeries z-score tests ──────────────────────────────────────

def test_rolling_series_zscore_needs_min_samples():
    s = RollingSeries()
    for i in range(10):
        s.add(float(i))
    # Less than 20 samples -> returns 0
    assert s.zscore(5.0) == 0.0


def test_rolling_series_zscore_with_enough_samples():
    s = RollingSeries()
    for i in range(30):
        s.add(10.0)  # all same value -> sigma=0 -> zscore=0
    assert s.zscore(10.0) == 0.0


def test_rolling_series_zscore_directional():
    s = RollingSeries()
    # Build a series centered around 0
    for i in range(50):
        s.add(float(i % 10 - 5))
    # A high value should give positive z-score
    z_high = s.zscore(10.0)
    z_low = s.zscore(-10.0)
    assert z_high > 0
    assert z_low < 0


# ── FeatureEngine._p95 helper ────────────────────────────────────────

def test_p95_empty():
    assert FeatureEngine._p95(deque()) == 0.0


def test_p95_single_value():
    assert FeatureEngine._p95(deque([42.0])) == 42.0


def test_p95_returns_near_max():
    d = deque(range(100))
    p95 = FeatureEngine._p95(d)
    assert p95 >= 94  # 95th percentile of 0..99


# ── Premium delta logic ──────────────────────────────────────────────

def test_premium_delta_5s():
    from app.bus import RedisBus

    class FakeBus:
        async def publish_json(self, *a): pass
        async def set_json(self, *a): pass
        async def publish_and_set_json(self, *a): pass
        async def publish_only_json(self, *a): pass
        @property
        def client(self): return None

    engine = FeatureEngine(FakeBus())
    engine.premium_history.append((1000, 5.0))
    engine.premium_history.append((3000, 6.0))
    engine.premium_history.append((6000, 8.0))

    # At t=6000, 5s ago = t=1000, value=5.0, current=8.0
    delta = engine._premium_delta_5s(6000)
    assert delta == 3.0  # 8.0 - 5.0


def test_oi_delta_30s():
    class FakeBus:
        async def publish_json(self, *a): pass
        async def set_json(self, *a): pass
        async def publish_and_set_json(self, *a): pass
        async def publish_only_json(self, *a): pass
        @property
        def client(self): return None

    engine = FeatureEngine(FakeBus())
    engine.oi_history.append((1000, 25000.0))
    engine.oi_history.append((15000, 25100.0))
    engine.oi_history.append((31000, 25200.0))

    # At t=31000, 30s ago = t=1000, value=25000.0, current=25200.0
    delta = engine._oi_delta_30s(31000)
    assert delta == 200.0


# ── Rolling score average ────────────────────────────────────────────

def test_rolling_score_avg_with_history():
    class FakeBus:
        async def publish_json(self, *a): pass
        async def set_json(self, *a): pass
        async def publish_and_set_json(self, *a): pass
        async def publish_only_json(self, *a): pass
        @property
        def client(self): return None

    engine = FeatureEngine(FakeBus())
    # Simulate 5 seconds of score_1m history
    for i in range(5):
        engine._score_1m_history.append((i * 1000, 0.1 * (i + 1)))
    # History: (0, 0.1), (1000, 0.2), (2000, 0.3), (3000, 0.4), (4000, 0.5)

    # 3s window from t=4000: includes t=1000..4000 -> [0.2, 0.3, 0.4, 0.5]
    avg_3s = engine._rolling_score_avg(4000, 3000)
    assert abs(avg_3s - 0.35) < 0.01  # (0.2+0.3+0.4+0.5)/4

    # Full 5s window: all values -> [0.1, 0.2, 0.3, 0.4, 0.5]
    avg_5s = engine._rolling_score_avg(4000, 5000)
    assert abs(avg_5s - 0.3) < 0.01  # (0.1+0.2+0.3+0.4+0.5)/5


def test_rolling_score_avg_empty_falls_back():
    class FakeBus:
        async def publish_json(self, *a): pass
        async def set_json(self, *a): pass
        async def publish_and_set_json(self, *a): pass
        async def publish_only_json(self, *a): pass
        @property
        def client(self): return None

    engine = FeatureEngine(FakeBus())
    # No history at all
    assert engine._rolling_score_avg(1000, 180_000) == 0.0

    # One sample outside the window
    engine._score_1m_history.append((500, 0.42))
    assert engine._rolling_score_avg(200_000, 1000) == 0.42  # falls back to latest
