from app.features.scoring import (
    ScoreInputs,
    build_score_snapshot,
    classify,
    confidence,
    score_linear,
)
from app.models import FeatureBar


def _bar(**overrides) -> FeatureBar:
    defaults = dict(symbol="BTCUSDT", bar_ts=0, book_sync_ok=True)
    defaults.update(overrides)
    return FeatureBar(**defaults)


def test_bullish_score_positive():
    score = score_linear(
        ScoreInputs(
            z_perp_cvd_5s=2.0,
            z_spot_cvd_5s=1.0,
            z_depth_imbalance_10bps=1.0,
            z_delta_premium_bps_5s=1.0,
            z_oi_delta_30s=1.0,
            z_liq_skew_30s=1.0,
            z_spread_bps=0.0,
            z_feed_lag_ms_p95=0.0,
        )
    )
    assert score > 0
    assert classify(score) in {"mild_bullish", "bullish_pressure"}


def test_bearish_score_negative():
    score = score_linear(
        ScoreInputs(
            z_perp_cvd_5s=-2.0,
            z_spot_cvd_5s=-1.0,
            z_depth_imbalance_10bps=-1.0,
            z_delta_premium_bps_5s=-1.0,
            z_oi_delta_30s=-1.0,
            z_liq_skew_30s=-1.0,
            z_spread_bps=0.0,
            z_feed_lag_ms_p95=0.0,
        )
    )
    assert score < 0
    assert classify(score) in {"mild_bearish", "bearish_pressure"}


def test_all_zero_neutral():
    score = score_linear(ScoreInputs())
    assert score == 0.0
    assert classify(score) == "neutral"


def test_confidence_cap_when_book_unsynced():
    bar = _bar(book_sync_ok=False)
    snap = build_score_snapshot(
        symbol="BTCUSDT",
        ts_local=0,
        score_1m=0.8,
        score_3m=0.7,
        score_5m=0.6,
        data_quality_score=0.2,
        feature_bar=bar,
    )
    assert snap.confidence <= 0.25


def test_spot_staleness_lowers_confidence():
    bar = _bar()
    snap_fresh = build_score_snapshot(
        symbol="BTCUSDT", ts_local=0,
        score_1m=0.5, score_3m=0.4, score_5m=0.3,
        data_quality_score=1.0, feature_bar=bar,
        spot_feed_stale=False,
    )
    snap_stale = build_score_snapshot(
        symbol="BTCUSDT", ts_local=0,
        score_1m=0.5, score_3m=0.4, score_5m=0.3,
        data_quality_score=1.0, feature_bar=bar,
        spot_feed_stale=True,
    )
    assert snap_stale.confidence < snap_fresh.confidence


def test_extreme_premium_without_spot_caps_bullish_confidence():
    bar = _bar(premium_bps=15.0, spot_cvd_5s=0.0)
    snap = build_score_snapshot(
        symbol="BTCUSDT", ts_local=0,
        score_1m=0.8, score_3m=0.7, score_5m=0.6,
        data_quality_score=1.0, feature_bar=bar,
    )
    assert snap.confidence <= 0.50


def test_extreme_negative_premium_does_not_cap_bearish_confidence():
    bar = _bar(premium_bps=-15.0, spot_cvd_5s=0.0)
    snap_bearish = build_score_snapshot(
        symbol="BTCUSDT", ts_local=0,
        score_1m=-0.8, score_3m=-0.7, score_5m=-0.6,
        data_quality_score=1.0, feature_bar=bar,
    )
    # Bearish score with negative premium should NOT be capped
    bar_normal = _bar(premium_bps=0.0, spot_cvd_5s=0.0)
    snap_normal = build_score_snapshot(
        symbol="BTCUSDT", ts_local=0,
        score_1m=-0.8, score_3m=-0.7, score_5m=-0.6,
        data_quality_score=1.0, feature_bar=bar_normal,
    )
    assert snap_bearish.confidence == snap_normal.confidence


def test_futures_stale_forces_degraded_state():
    bar = _bar()
    snap = build_score_snapshot(
        symbol="BTCUSDT", ts_local=0,
        score_1m=0.8, score_3m=0.7, score_5m=0.6,
        data_quality_score=1.0, feature_bar=bar,
        futures_feed_stale=True,
    )
    assert snap.state == "degraded"


def test_reasons_include_bearish_signals():
    bar = _bar(
        perp_cvd_5s=-5000.0,
        spot_cvd_5s=-3000.0,
        delta_premium_bps_5s=-1.0,
        depth_imbalance_10bps=-0.3,
        oi_delta_30s=-100.0,
        liq_skew_30s=-50000.0,
    )
    snap = build_score_snapshot(
        symbol="BTCUSDT", ts_local=0,
        score_1m=-0.5, score_3m=-0.4, score_5m=-0.3,
        data_quality_score=1.0, feature_bar=bar,
    )
    assert "perp CVD negative (taker selling)" in snap.reasons
    assert "spot selling" in snap.reasons
    assert "premium falling" in snap.reasons
    assert "ask depth dominates" in snap.reasons
    assert "OI declining" in snap.reasons
    assert "long liquidation fuel" in snap.reasons
