from __future__ import annotations

from dataclasses import dataclass

from app.models import FeatureBar, ScoreSnapshot


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def agreement(a: float, b: float, c: float) -> float:
    same_sign = int((a >= 0 and b >= 0 and c >= 0) or (a <= 0 and b <= 0 and c <= 0))
    return 1.0 if same_sign else 0.0


@dataclass
class ScoreInputs:
    z_perp_cvd_5s: float = 0.0
    z_spot_cvd_5s: float = 0.0
    z_depth_imbalance_10bps: float = 0.0
    z_delta_premium_bps_5s: float = 0.0
    z_oi_delta_30s: float = 0.0
    z_liq_skew_30s: float = 0.0
    z_spread_bps: float = 0.0
    z_feed_lag_ms_p95: float = 0.0


def score_linear(inputs: ScoreInputs) -> float:
    return (
        0.30 * inputs.z_perp_cvd_5s
        + 0.18 * inputs.z_spot_cvd_5s
        + 0.18 * inputs.z_depth_imbalance_10bps
        + 0.14 * inputs.z_delta_premium_bps_5s
        + 0.10 * inputs.z_oi_delta_30s
        + 0.07 * inputs.z_liq_skew_30s
        - 0.17 * inputs.z_spread_bps
        - 0.10 * inputs.z_feed_lag_ms_p95
    )


def classify(score: float) -> str:
    if score >= 0.60:
        return "bullish_pressure"
    if score >= 0.20:
        return "mild_bullish"
    if score > -0.20:
        return "neutral"
    if score > -0.60:
        return "mild_bearish"
    return "bearish_pressure"


def confidence(score_1m: float, score_3m: float, score_5m: float, data_quality_score: float) -> float:
    raw = 0.35 + 0.25 * abs(score_1m) + 0.20 * agreement(score_1m, score_3m, score_5m) + 0.20 * data_quality_score
    return clamp(raw, 0.0, 1.0)


def reason_strings(feature_bar: FeatureBar) -> list[str]:
    reasons: list[str] = []

    # Directional signals — bullish
    if feature_bar.perp_cvd_5s > 0:
        reasons.append("perp CVD positive (taker buying)")
    elif feature_bar.perp_cvd_5s < 0:
        reasons.append("perp CVD negative (taker selling)")

    if feature_bar.spot_cvd_5s > 0:
        reasons.append("spot confirming (taker buying)")
    elif feature_bar.spot_cvd_5s < 0:
        reasons.append("spot selling")

    if feature_bar.delta_premium_bps_5s > 0.5:
        reasons.append("premium rising")
    elif feature_bar.delta_premium_bps_5s < -0.5:
        reasons.append("premium falling")

    if feature_bar.depth_imbalance_10bps > 0.1:
        reasons.append("bid depth dominates")
    elif feature_bar.depth_imbalance_10bps < -0.1:
        reasons.append("ask depth dominates")

    if feature_bar.oi_delta_30s > 0:
        reasons.append("OI rising")
    elif feature_bar.oi_delta_30s < 0:
        reasons.append("OI declining")

    if feature_bar.liq_skew_30s > 0:
        reasons.append("short liquidation fuel")
    elif feature_bar.liq_skew_30s < 0:
        reasons.append("long liquidation fuel")

    # Warnings
    if feature_bar.spread_bps > 0.5:
        reasons.append("spread elevated")
    if not feature_bar.book_sync_ok:
        reasons.append("book not synchronized")
    if feature_bar.oi_lag_ms_p95 > 30_000:
        reasons.append("OI feed stale")
    if max(feature_bar.spot_trade_lag_ms_p95, feature_bar.bbo_spot_lag_ms_p95) > 2_000:
        reasons.append("spot feed stale")
    if max(feature_bar.futures_trade_lag_ms_p95, feature_bar.bbo_futures_lag_ms_p95,
           feature_bar.mark_index_lag_ms_p95) > 1_000:
        reasons.append("futures feed stale")

    return reasons


def build_score_snapshot(
    symbol: str,
    ts_local: int,
    score_1m: float,
    score_3m: float,
    score_5m: float,
    data_quality_score: float,
    feature_bar: FeatureBar,
    futures_feed_stale: bool = False,
    spot_feed_stale: bool = False,
) -> ScoreSnapshot:
    conf = confidence(score_1m, score_3m, score_5m, data_quality_score)

    # Confidence gating
    if not feature_bar.book_sync_ok:
        conf = min(conf, 0.25)
    if spot_feed_stale:
        conf *= 0.7
    # Extreme premium without spot confirmation -> cap bullish confidence only
    if feature_bar.premium_bps > 10 and score_1m > 0 and abs(feature_bar.spot_cvd_5s) < 1000:
        conf = min(conf, 0.50)

    # State: override to degraded if futures feed is stale
    if futures_feed_stale:
        state = "degraded"
    else:
        state = classify(score_1m)

    return ScoreSnapshot(
        symbol=symbol,
        score_1m=score_1m,
        score_3m=score_3m,
        score_5m=score_5m,
        confidence=conf,
        state=state,
        reasons=reason_strings(feature_bar),
        ts_local=ts_local,
    )
