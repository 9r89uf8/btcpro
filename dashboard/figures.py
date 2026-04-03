"""Chart builders for the dashboard. Each returns a plotly Figure."""

from __future__ import annotations

import plotly.graph_objects as go

LAYOUT_DEFAULTS = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="#161b22",
    margin=dict(l=50, r=10, t=30, b=25),
    font=dict(size=10, family="Consolas, Monaco, monospace"),
    legend=dict(orientation="h", y=1.12, x=0.5, xanchor="center", font=dict(size=10)),
    uirevision="constant",
    xaxis=dict(gridcolor="#21262d"),
    yaxis=dict(gridcolor="#21262d"),
)


def _ts_labels(points: list[dict], key: str = "ts") -> list[str]:
    """Convert ms timestamps to HH:MM:SS labels."""
    from datetime import datetime
    return [datetime.fromtimestamp(p[key] / 1000).strftime("%H:%M:%S") for p in points]


def price_chart(display_points: list[dict], height: int = 260) -> go.Figure:
    # Filter out points where prices haven't arrived yet (book not synced)
    display_points = [p for p in display_points if p.get("perp_mid", 0) > 0]
    if not display_points:
        return _empty("Price", height)
    ts = _ts_labels(display_points)
    perp = [p["perp_mid"] for p in display_points]
    spot = [p["spot_mid"] for p in display_points]
    states = [p.get("state", "neutral") for p in display_points]

    fig = go.Figure()
    fig.add_trace(go.Scattergl(x=ts, y=perp, name="Perp Mid",
                                mode="lines", line=dict(color="#58a6ff", width=2)))
    fig.add_trace(go.Scattergl(x=ts, y=spot, name="Spot Mid",
                                mode="lines", line=dict(color="#f85149", width=1.5, dash="dot")))

    # State shading
    _add_state_shading(fig, ts, perp, states)

    fig.update_layout(**LAYOUT_DEFAULTS, height=height, title=dict(text="Price + State", font=dict(size=12)))
    return fig


def premium_chart(feature_bars: list[dict], height: int = 180) -> go.Figure:
    if not feature_bars:
        return _empty("Premium", height)
    ts = _ts_labels(feature_bars, "bar_ts")
    premium = [b["premium_bps"] for b in feature_bars]
    delta_prem = [b["delta_premium_bps_5s"] for b in feature_bars]

    fig = go.Figure()
    fig.add_trace(go.Scattergl(x=ts, y=premium, name="Premium bps",
                                mode="lines", line=dict(color="#d29922", width=1.8)))
    fig.add_trace(go.Scattergl(x=ts, y=delta_prem, name="Delta Premium 5s",
                                mode="lines", line=dict(color="#f0883e", width=1.2, dash="dot")))
    fig.add_hline(y=0, line_dash="dot", line_color="#30363d", line_width=1)

    fig.update_layout(**LAYOUT_DEFAULTS, height=height, title=dict(text="Premium", font=dict(size=12)))
    fig.update_yaxes(title_text="bps")
    return fig


def cvd_chart(feature_bars: list[dict], height: int = 200) -> go.Figure:
    if not feature_bars:
        return _empty("CVD", height)
    ts = _ts_labels(feature_bars, "bar_ts")
    perp_1 = [b["perp_cvd_1s"] for b in feature_bars]
    perp_5 = [b["perp_cvd_5s"] for b in feature_bars]
    perp_15 = [b["perp_cvd_15s"] for b in feature_bars]
    spot_1 = [b["spot_cvd_1s"] for b in feature_bars]
    spot_5 = [b["spot_cvd_5s"] for b in feature_bars]
    spot_15 = [b["spot_cvd_15s"] for b in feature_bars]

    fig = go.Figure()
    # Perp CVD — 5s emphasized, 1s/15s lighter
    fig.add_trace(go.Scattergl(x=ts, y=perp_1, name="Perp 1s",
                                mode="lines", line=dict(color="#58a6ff", width=0.7), opacity=0.3))
    fig.add_trace(go.Scattergl(x=ts, y=perp_5, name="Perp 5s",
                                mode="lines", line=dict(color="#58a6ff", width=2)))
    fig.add_trace(go.Scattergl(x=ts, y=perp_15, name="Perp 15s",
                                mode="lines", line=dict(color="#58a6ff", width=1, dash="dash"), opacity=0.5))
    # Spot CVD — same pattern
    fig.add_trace(go.Scattergl(x=ts, y=spot_1, name="Spot 1s",
                                mode="lines", line=dict(color="#f85149", width=0.7), opacity=0.3))
    fig.add_trace(go.Scattergl(x=ts, y=spot_5, name="Spot 5s",
                                mode="lines", line=dict(color="#f85149", width=1.8, dash="dot")))
    fig.add_trace(go.Scattergl(x=ts, y=spot_15, name="Spot 15s",
                                mode="lines", line=dict(color="#f85149", width=1, dash="dash"), opacity=0.5))
    fig.add_hline(y=0, line_dash="dot", line_color="#30363d", line_width=1)

    fig.update_layout(**LAYOUT_DEFAULTS, height=height, title=dict(text="CVD", font=dict(size=12)))
    fig.update_yaxes(title_text="$")
    return fig


def depth_chart(feature_bars: list[dict], height: int = 220) -> go.Figure:
    if not feature_bars:
        return _empty("Depth", height)
    ts = _ts_labels(feature_bars, "bar_ts")
    imb5 = [b["depth_imbalance_5bps"] for b in feature_bars]
    imb10 = [b["depth_imbalance_10bps"] for b in feature_bars]
    spread = [b["spread_bps"] for b in feature_bars]
    bid_depth = [b["near_touch_depth_bid_usd"] / 1e6 for b in feature_bars]  # in $M
    ask_depth = [b["near_touch_depth_ask_usd"] / 1e6 for b in feature_bars]

    fig = go.Figure()
    fig.add_trace(go.Scattergl(x=ts, y=imb5, name="Imbalance 5bps",
                                mode="lines", line=dict(color="#39d353", width=1.2)))
    fig.add_trace(go.Scattergl(x=ts, y=imb10, name="Imbalance 10bps",
                                mode="lines", line=dict(color="#3fb950", width=2)))
    fig.add_trace(go.Scattergl(x=ts, y=bid_depth, name="Bid Depth $M",
                                mode="lines", line=dict(color="#58a6ff", width=1), opacity=0.6,
                                yaxis="y2"))
    fig.add_trace(go.Scattergl(x=ts, y=ask_depth, name="Ask Depth $M",
                                mode="lines", line=dict(color="#f85149", width=1), opacity=0.6,
                                yaxis="y2"))
    fig.add_trace(go.Scattergl(x=ts, y=spread, name="Spread bps",
                                mode="lines", line=dict(color="#8b949e", width=0.8, dash="dot")))
    fig.add_hline(y=0, line_dash="dot", line_color="#30363d", line_width=1)

    fig.update_layout(
        **LAYOUT_DEFAULTS, height=height,
        title=dict(text="Depth / Spread", font=dict(size=12)),
        yaxis2=dict(overlaying="y", side="right", gridcolor="#21262d", title="depth $M"),
    )
    fig.update_yaxes(title_text="imbalance", selector=dict(side="left"))
    return fig


def oi_liq_chart(feature_bars: list[dict], height: int = 180) -> go.Figure:
    if not feature_bars:
        return _empty("OI / Liquidations", height)
    ts = _ts_labels(feature_bars, "bar_ts")
    oi = [b["oi_delta_30s"] for b in feature_bars]
    liq = [b["liq_skew_30s"] for b in feature_bars]
    liq_colors = ["#3fb950" if v >= 0 else "#f85149" for v in liq]

    fig = go.Figure()
    fig.add_trace(go.Scattergl(x=ts, y=oi, name="OI Delta 30s",
                                mode="lines", line=dict(color="#39d353", width=1.8)))
    fig.add_trace(go.Bar(x=ts, y=liq, name="Liq Skew 30s",
                          marker_color=liq_colors, opacity=0.6, yaxis="y2"))

    fig.update_layout(
        **LAYOUT_DEFAULTS, height=height,
        title=dict(text="OI / Liquidations", font=dict(size=12)),
        yaxis2=dict(overlaying="y", side="right", gridcolor="#21262d", title="liq $"),
    )
    fig.update_yaxes(title_text="OI (BTC)", selector=dict(side="left"))
    return fig


def _add_state_shading(fig: go.Figure, ts: list[str], prices: list[float], states: list[str]):
    """Add colored background bands for bull/bear state."""
    if not ts or not prices:
        return
    colors = {
        "bullish_pressure": "rgba(63,185,80,0.08)",
        "mild_bullish": "rgba(63,185,80,0.04)",
        "mild_bearish": "rgba(248,81,73,0.04)",
        "bearish_pressure": "rgba(248,81,73,0.08)",
        "degraded": "rgba(210,153,34,0.06)",
    }
    y_min = min(p for p in prices if p > 0) if any(p > 0 for p in prices) else 0
    y_max = max(prices) if prices else 0
    if y_min == y_max:
        return

    i = 0
    while i < len(states):
        state = states[i]
        color = colors.get(state)
        if not color:
            i += 1
            continue
        start = i
        while i < len(states) and states[i] == state:
            i += 1
        fig.add_vrect(x0=ts[start], x1=ts[min(i, len(ts) - 1)],
                      fillcolor=color, line_width=0, layer="below")


def _empty(title: str, height: int) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**LAYOUT_DEFAULTS, height=height,
                      title=dict(text=f"{title} — waiting for data", font=dict(size=12)))
    fig.add_annotation(x=0.5, y=0.5, xref="paper", yref="paper",
                       text="No data yet", showarrow=False,
                       font=dict(size=14, color="#8b949e"))
    return fig
