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
    if feature_bar.perp_cvd_5s > 0:
        reasons.append("perp_cvd_5s positive")
    if feature_bar.spot_cvd_5s > 0:
        reasons.append("spot_cvd_5s positive")
    if feature_bar.delta_premium_bps_5s > 0:
        reasons.append("premium rising")
    if feature_bar.depth_imbalance_10bps > 0:
        reasons.append("bid depth dominates")
    if feature_bar.oi_delta_30s > 0:
        reasons.append("open interest rising")
    if feature_bar.liq_skew_30s > 0:
        reasons.append("bullish liquidation skew")
    if feature_bar.spread_bps > 0.5:
        reasons.append("spread elevated")
    if not feature_bar.book_sync_ok:
        reasons.append("book not synchronized")
    if feature_bar.oi_lag_ms_p95 > 30_000:
        reasons.append("OI feed stale")
    return reasons


def build_score_snapshot(
    symbol: str,
    ts_local: int,
    score_1m: float,
    score_3m: float,
    score_5m: float,
    data_quality_score: float,
    feature_bar: FeatureBar,
) -> ScoreSnapshot:
    conf = confidence(score_1m, score_3m, score_5m, data_quality_score)
    if not feature_bar.book_sync_ok:
        conf = min(conf, 0.25)
    return ScoreSnapshot(
        symbol=symbol,
        score_1m=score_1m,
        score_3m=score_3m,
        score_5m=score_5m,
        confidence=conf,
        state=classify(score_1m),
        reasons=reason_strings(feature_bar),
        ts_local=ts_local,
    )
