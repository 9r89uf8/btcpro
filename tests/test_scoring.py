from app.features.scoring import ScoreInputs, classify, score_linear


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
