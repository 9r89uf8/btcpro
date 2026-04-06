"""Tests for family-level prediction validation."""

from app.features.validation import (
    DEBOUNCE_MS,
    compute_validation,
    _extract_transitions,
    _classify_verdict,
    _is_qualified,
    _lookup_price,
)


def _point(ts, perp_mid, state, score_1m=0.0, confidence=0.6):
    return {"ts": ts, "perp_mid": perp_mid, "state": state,
            "score_1m": score_1m, "confidence": confidence}


def _history(specs, start=0, interval=1000):
    """Build history from (state, price) or (state, price, conf, score) tuples."""
    result = []
    for i, s in enumerate(specs):
        if len(s) == 2:
            result.append(_point(start + i * interval, s[1], s[0]))
        else:
            result.append(_point(start + i * interval, s[1], s[0],
                                  confidence=s[2], score_1m=s[3]))
    return result


# ── Family collapsing ────────────────────────────────────────────────

def test_intra_family_no_transition():
    """mild_bearish → bearish_pressure is same family — no transition."""
    h = _history([
        ("neutral", 68000),
        ("mild_bearish", 68000),
        ("mild_bearish", 68000),
        ("bearish_pressure", 68000),
        ("bearish_pressure", 68000),
        ("bearish_pressure", 68000),
    ])
    ts = [p["ts"] for p in h]
    transitions = _extract_transitions(h, ts)
    # Only one transition: neutral → bearish family
    assert len(transitions) == 1
    assert transitions[0]["from_family"] == "neutral"
    assert transitions[0]["to_family"] == "bearish"


def test_family_boundary_neutral_to_bullish():
    h = _history([
        ("neutral", 68000),
        ("mild_bullish", 68000),
        ("mild_bullish", 68000),
        ("mild_bullish", 68000),
    ])
    ts = [p["ts"] for p in h]
    transitions = _extract_transitions(h, ts)
    assert len(transitions) == 1
    assert transitions[0]["to_family"] == "bullish"


def test_direct_bull_bear_flip():
    """Direct bullish → bearish (skipping neutral) is a valid family transition."""
    h = _history([
        ("neutral", 68000),
        ("mild_bullish", 68000),
        ("mild_bullish", 68000),
        ("mild_bullish", 68000),  # debounce met → bullish
        ("mild_bearish", 68000),
        ("mild_bearish", 68000),
        ("mild_bearish", 68000),  # debounce met → bearish
    ])
    ts = [p["ts"] for p in h]
    transitions = _extract_transitions(h, ts)
    assert len(transitions) == 2
    assert transitions[0]["to_family"] == "bullish"
    assert transitions[1]["from_family"] == "bullish"
    assert transitions[1]["to_family"] == "bearish"


# ── Debounce ─────────────────────────────────────────────────────────

def test_debounce_rejects_short_flicker():
    h = _history([
        ("neutral", 68000),
        ("mild_bullish", 68000),  # 1s only
        ("neutral", 68000),
        ("neutral", 68000),
    ])
    ts = [p["ts"] for p in h]
    assert len(_extract_transitions(h, ts)) == 0


def test_debounce_accepts_sufficient_persistence():
    h = _history([
        ("neutral", 68000),
        ("mild_bullish", 68000),
        ("mild_bullish", 68000),
        ("mild_bullish", 68000),  # 2s elapsed
    ])
    ts = [p["ts"] for p in h]
    transitions = _extract_transitions(h, ts)
    assert len(transitions) == 1
    assert transitions[0]["persistence_ms"] >= DEBOUNCE_MS


# ── Entry price at confirm_ts ────────────────────────────────────────

def test_entry_price_is_at_confirm_ts():
    h = [
        _point(0, 68000, "neutral"),
        _point(1000, 68010, "mild_bullish"),   # flip ts, price 68010
        _point(2000, 68020, "mild_bullish"),
        _point(3000, 68030, "mild_bullish"),   # confirm_ts, price 68030
    ]
    ts = [p["ts"] for p in h]
    transitions = _extract_transitions(h, ts)
    assert transitions[0]["entry_price"] == 68030  # confirm_ts price, not flip price
    assert transitions[0]["confirm_ts"] == 3000


def test_horizons_measured_from_confirm_ts():
    h = []
    # Neutral for 4s
    for i in range(4):
        h.append(_point(i * 1000, 68000, "neutral"))
    # Bullish starting at t=4000, confirmed at t=6000
    for i in range(3):
        h.append(_point((4 + i) * 1000, 68000, "mild_bullish"))
    # Price at confirm_ts+60s = t=66000
    for i in range(65):
        h.append(_point((7 + i) * 1000, 68000 + i * 0.5, "mild_bullish"))

    result = compute_validation(h, 200_000)
    t = result["recent_transitions"][0]
    # confirm_ts is 6000, so 1m horizon looks at t=66000
    assert t["confirm_ts"] == 6000
    assert t["verdict_1m"] != "pending"


# ── Qualification ────────────────────────────────────────────────────

def test_directional_qualified():
    assert _is_qualified("bullish", 0.85, 0.40) is True
    assert _is_qualified("bearish", 0.82, -0.45) is True


def test_directional_not_qualified_low_score():
    assert _is_qualified("bullish", 0.85, 0.25) is False


def test_directional_not_qualified_low_confidence():
    assert _is_qualified("bearish", 0.70, -0.50) is False


def test_neutral_qualified():
    assert _is_qualified("neutral", 0.76, 0.10) is True
    assert _is_qualified("neutral", 0.75, -0.05) is True


def test_neutral_not_qualified_high_score():
    assert _is_qualified("neutral", 0.76, 0.18) is False


def test_neutral_not_qualified_low_confidence():
    assert _is_qualified("neutral", 0.70, 0.10) is False


def test_missing_confidence_not_qualified():
    assert _is_qualified("bullish", None, 0.5) is False
    assert _is_qualified("neutral", 0.76, None) is False


def test_qualified_uses_confirm_ts_values():
    """Qualification uses confidence/score at confirm_ts, not initial flip."""
    h = [
        _point(0, 68000, "neutral", confidence=0.9, score_1m=0.0),
        _point(1000, 68000, "mild_bullish", confidence=0.5, score_1m=0.25),  # low at flip
        _point(2000, 68000, "mild_bullish", confidence=0.6, score_1m=0.30),
        _point(3000, 68000, "mild_bullish", confidence=0.85, score_1m=0.45), # high at confirm
    ]
    ts = [p["ts"] for p in h]
    transitions = _extract_transitions(h, ts)
    assert transitions[0]["confidence"] == 0.85
    assert transitions[0]["score_1m"] == 0.45
    assert transitions[0]["qualified"] is True


# ── Verdict classification ───────────────────────────────────────────

def test_bullish_verdicts_1m():
    # 1m threshold: ±1.5 bps
    assert _classify_verdict("bullish", 2.0, "1m") == "correct"
    assert _classify_verdict("bullish", -2.0, "1m") == "wrong"
    assert _classify_verdict("bullish", 1.0, "1m") == "unclear"


def test_bullish_verdicts_3m():
    # 3m threshold: ±3.0 bps
    assert _classify_verdict("bullish", 4.0, "3m") == "correct"
    assert _classify_verdict("bullish", -4.0, "3m") == "wrong"
    assert _classify_verdict("bullish", 2.0, "3m") == "unclear"


def test_bearish_verdicts():
    assert _classify_verdict("bearish", -2.0, "1m") == "correct"
    assert _classify_verdict("bearish", 2.0, "1m") == "wrong"
    assert _classify_verdict("bearish", -1.0, "1m") == "unclear"
    # 3m uses stricter threshold
    assert _classify_verdict("bearish", -2.0, "3m") == "unclear"
    assert _classify_verdict("bearish", -4.0, "3m") == "correct"


def test_neutral_verdicts():
    assert _classify_verdict("neutral", 1.0, "1m") == "correct"
    assert _classify_verdict("neutral", 6.0, "1m") == "wrong"
    assert _classify_verdict("neutral", 3.5, "1m") == "unclear"


# ── Lookup ───────────────────────────────────────────────────────────

def test_lookup_within_tolerance():
    assert _lookup_price([0, 1000, 60000], [100, 101, 110], 60000) == 110
    assert _lookup_price([0, 1000, 60000], [100, 101, 110], 59500) == 110


def test_lookup_beyond_tolerance():
    assert _lookup_price([0, 1000, 2000], [100, 101, 102], 60000) is None


# ── End-to-end ───────────────────────────────────────────────────────

def test_e2e_bullish_correct():
    h = []
    for i in range(4):
        h.append(_point(i * 1000, 68000, "neutral", confidence=0.8, score_1m=0.0))
    for i in range(4):
        h.append(_point((4 + i) * 1000, 68000, "mild_bullish", confidence=0.85, score_1m=0.45))
    for i in range(120):
        h.append(_point((8 + i) * 1000, 68000 + i * 0.5, "mild_bullish", confidence=0.85, score_1m=0.45))

    result = compute_validation(h, 200_000)
    assert len(result["recent_transitions"]) >= 1
    t = result["recent_transitions"][0]
    assert t["to_family"] == "bullish"
    assert t["verdict_1m"] == "correct"
    assert t["qualified"] is True
    # Qualified view should also have it
    assert len(result["qualified"]["recent_transitions"]) >= 1


def test_e2e_response_shape():
    h = _history([("neutral", 68000)] * 5 + [("mild_bullish", 68000)] * 5)
    result = compute_validation(h, 200_000)
    assert "horizons" in result
    assert "summary" in result
    assert "recent_transitions" in result
    assert "qualified" in result
    assert "horizons" in result["qualified"]
    # Family keys, not individual states
    if "1m" in result["horizons"]:
        assert "bullish" in result["horizons"]["1m"]
        assert "neutral" in result["horizons"]["1m"]
        assert "bearish" in result["horizons"]["1m"]
        assert "mild_bullish" not in result["horizons"]["1m"]


def test_zero_price_excluded():
    result = compute_validation([_point(0, 0, "neutral"), _point(1000, 0, "mild_bullish")], 500_000)
    assert result["recent_transitions"] == []


def test_degraded_excluded():
    result = compute_validation([
        _point(0, 68000, "neutral"),
        _point(1000, 68000, "degraded"),
        _point(2000, 68000, "degraded"),
        _point(3000, 68000, "degraded"),
    ], 500_000)
    assert result["recent_transitions"] == []
