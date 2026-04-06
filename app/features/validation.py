"""Retroactive validation of state predictions.

Evaluates debounced family-level state transitions against realized
price movements at 1m, 3m, and 5m horizons using thresholded correctness.

Family mapping:
  bullish_pressure, mild_bullish → "bullish"
  neutral → "neutral"
  mild_bearish, bearish_pressure → "bearish"

Two views:
  - raw: all debounced family transitions
  - qualified: directional (conf >= 0.80, |score| >= 0.35)
               neutral   (conf >= 0.75, |score| <= 0.15)
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any

# Verdict thresholds (bps) per horizon
THRESHOLDS = {
    "1m": {"directional_correct": 1.5, "directional_wrong": -1.5, "neutral_correct": 2.0, "neutral_wrong": 5.0},
    "3m": {"directional_correct": 3.0, "directional_wrong": -3.0, "neutral_correct": 2.0, "neutral_wrong": 5.0},
    "5m": {"directional_correct": 3.0, "directional_wrong": -3.0, "neutral_correct": 2.0, "neutral_wrong": 5.0},
}

DEBOUNCE_MS = 2000

# Qualification thresholds
DIRECTIONAL_CONF_THRESHOLD = 0.80
DIRECTIONAL_SCORE_THRESHOLD = 0.35
NEUTRAL_CONF_THRESHOLD = 0.75
NEUTRAL_SCORE_CEILING = 0.15

LOOKUP_TOLERANCE_MS = 10_000

HORIZONS = {"1m": 60_000, "3m": 180_000, "5m": 300_000}

FAMILY_MAP = {
    "bullish_pressure": "bullish",
    "mild_bullish": "bullish",
    "neutral": "neutral",
    "mild_bearish": "bearish",
    "bearish_pressure": "bearish",
}
SKIP_STATES = {"degraded"}
DIRECTIONAL_FAMILIES = {"bullish", "bearish"}
FAMILIES = ["bullish", "neutral", "bearish"]


def compute_validation(display_history: list[dict], now_ms: int) -> dict:
    points = [p for p in display_history if p.get("perp_mid", 0) > 0 and p.get("state") not in SKIP_STATES]
    if len(points) < 2:
        empty = _empty_view()
        return {**empty, "qualified": _empty_view()}

    timestamps = [p["ts"] for p in points]
    prices = [p["perp_mid"] for p in points]

    transitions = _extract_transitions(points, timestamps)

    for t in transitions:
        for label, horizon_ms in HORIZONS.items():
            if now_ms - t["confirm_ts"] < horizon_ms:
                t[f"outcome_{label}_bps"] = None
                t[f"verdict_{label}"] = "pending"
                continue

            future_price = _lookup_price(timestamps, prices, t["confirm_ts"] + horizon_ms)
            if future_price is None:
                t[f"outcome_{label}_bps"] = None
                t[f"verdict_{label}"] = "pending"
            else:
                ret_bps = 10000.0 * (future_price - t["entry_price"]) / t["entry_price"]
                t[f"outcome_{label}_bps"] = round(ret_bps, 2)
                t[f"verdict_{label}"] = _classify_verdict(t["to_family"], ret_bps, label)

    all_view = _aggregate(transitions)
    qualified_transitions = [t for t in transitions if t.get("qualified", False)]
    qualified_view = _aggregate(qualified_transitions)

    return {**all_view, "qualified": qualified_view}


def _extract_transitions(points: list[dict], timestamps: list[int]) -> list[dict]:
    families = [FAMILY_MAP.get(p.get("state", ""), "neutral") for p in points]

    transitions: list[dict] = []
    prev_family = families[0]
    candidate_start: int | None = None
    candidate_family: str | None = None

    for i in range(1, len(points)):
        fam = families[i]
        if fam != prev_family and candidate_family is None:
            candidate_start = i
            candidate_family = fam
        elif candidate_start is not None and fam == candidate_family:
            elapsed = timestamps[i] - timestamps[candidate_start]
            if elapsed >= DEBOUNCE_MS:
                conf = points[i].get("confidence")
                score = points[i].get("score_1m")
                transitions.append({
                    "ts": timestamps[candidate_start],
                    "confirm_ts": timestamps[i],
                    "persistence_ms": elapsed,
                    "from_family": prev_family,
                    "to_family": candidate_family,
                    "entry_price": points[i]["perp_mid"],
                    "confidence": conf,
                    "score_1m": score,
                    "qualified": _is_qualified(candidate_family, conf, score),
                })
                prev_family = candidate_family
                candidate_start = None
                candidate_family = None
        elif candidate_start is not None and fam != candidate_family:
            candidate_start = None
            candidate_family = None
            if fam != prev_family:
                candidate_start = i
                candidate_family = fam

    return transitions


def _is_qualified(family: str, confidence: float | None, score_1m: float | None) -> bool:
    if confidence is None or score_1m is None:
        return False
    if family in DIRECTIONAL_FAMILIES:
        return confidence >= DIRECTIONAL_CONF_THRESHOLD and abs(score_1m) >= DIRECTIONAL_SCORE_THRESHOLD
    if family == "neutral":
        return confidence >= NEUTRAL_CONF_THRESHOLD and abs(score_1m) <= NEUTRAL_SCORE_CEILING
    return False


def _lookup_price(timestamps: list[int], prices: list[float], target_ms: int) -> float | None:
    idx = bisect_left(timestamps, target_ms)
    if idx >= len(timestamps):
        return None
    if abs(timestamps[idx] - target_ms) <= LOOKUP_TOLERANCE_MS:
        return prices[idx]
    if idx > 0 and abs(timestamps[idx - 1] - target_ms) <= LOOKUP_TOLERANCE_MS:
        return prices[idx - 1]
    return None


def _classify_verdict(family: str, return_bps: float, horizon: str = "1m") -> str:
    t = THRESHOLDS.get(horizon, THRESHOLDS["1m"])
    correct_bps = t["directional_correct"]
    wrong_bps = t["directional_wrong"]
    if family == "bullish":
        if return_bps >= correct_bps:
            return "correct"
        if return_bps <= wrong_bps:
            return "wrong"
        return "unclear"
    if family == "bearish":
        if return_bps <= -correct_bps:
            return "correct"
        if return_bps >= -wrong_bps:
            return "wrong"
        return "unclear"
    if family == "neutral":
        if abs(return_bps) <= t["neutral_correct"]:
            return "correct"
        if abs(return_bps) > t["neutral_wrong"]:
            return "wrong"
        return "unclear"
    return "unclear"


def _aggregate(transitions: list[dict]) -> dict:
    horizons_result: dict[str, dict] = {}

    for label in HORIZONS:
        verdict_key = f"verdict_{label}"
        outcome_key = f"outcome_{label}_bps"
        family_stats: dict[str, dict] = {}

        for fam in FAMILIES:
            matching = [t for t in transitions if t["to_family"] == fam and t[verdict_key] != "pending"]
            family_stats[fam] = _compute_stats(matching, outcome_key, verdict_key)

        horizons_result[label] = family_stats

    summary: dict[str, dict] = {}
    for label in HORIZONS:
        stats = horizons_result[label]
        bull = stats.get("bullish", {})
        bear = stats.get("bearish", {})
        neu = stats.get("neutral", {})

        dir_correct = bull.get("correct", 0) + bear.get("correct", 0)
        dir_wrong = bull.get("wrong", 0) + bear.get("wrong", 0)
        dir_decided = dir_correct + dir_wrong
        dir_total = bull.get("count", 0) + bear.get("count", 0)

        neu_correct = neu.get("correct", 0)
        neu_wrong = neu.get("wrong", 0)
        neu_decided = neu_correct + neu_wrong

        total_evaluable = dir_total + neu.get("count", 0)
        total_decided = dir_decided + neu_decided

        summary[label] = {
            "directional_hit_rate": round(dir_correct / dir_decided, 3) if dir_decided > 0 else None,
            "neutral_hit_rate": round(neu_correct / neu_decided, 3) if neu_decided > 0 else None,
            "coverage": round(total_decided / total_evaluable, 3) if total_evaluable > 0 else None,
        }

    return {
        "horizons": horizons_result,
        "summary": summary,
        "recent_transitions": transitions[-20:],
    }


def _compute_stats(matching: list[dict], outcome_key: str, verdict_key: str) -> dict:
    correct = sum(1 for t in matching if t[verdict_key] == "correct")
    wrong = sum(1 for t in matching if t[verdict_key] == "wrong")
    unclear = sum(1 for t in matching if t[verdict_key] == "unclear")
    decided = correct + wrong
    returns = [t[outcome_key] for t in matching if t[outcome_key] is not None]
    return {
        "count": len(matching),
        "correct": correct,
        "wrong": wrong,
        "unclear": unclear,
        "hit_rate": round(correct / decided, 3) if decided > 0 else None,
        "avg_return_bps": round(sum(returns) / len(returns), 2) if returns else None,
    }


def _empty_view() -> dict:
    return {"horizons": {}, "summary": {}, "recent_transitions": []}
