from __future__ import annotations

from collections import deque
from datetime import datetime

import requests
from dash import Dash, Input, Output, dcc, html, no_update
import plotly.graph_objects as go
from plotly.subplots import make_subplots

API_BASE = "http://localhost:8000"
MAX_POINTS = 150

timestamps: deque[str] = deque(maxlen=MAX_POINTS)
perp_mids: deque[float] = deque(maxlen=MAX_POINTS)
spot_mids: deque[float] = deque(maxlen=MAX_POINTS)
premiums: deque[float] = deque(maxlen=MAX_POINTS)
live_basis_vals: deque[float] = deque(maxlen=MAX_POINTS)
perp_cvd_vals: deque[float] = deque(maxlen=MAX_POINTS)
spot_cvd_vals: deque[float] = deque(maxlen=MAX_POINTS)
oi_delta_vals: deque[float] = deque(maxlen=MAX_POINTS)
liq_skew_vals: deque[float] = deque(maxlen=MAX_POINTS)

app = Dash(__name__, update_title=None)  # disable "Updating..." tab title
app.layout = html.Div([
    html.H3("BTC Live Data Monitor"),
    html.Pre(id="status-bar", style={"margin": "0 0 10px 0", "fontSize": "13px", "lineHeight": "1.45"}),
    dcc.Graph(id="price-chart", config={"displayModeBar": False, "staticPlot": False}),
    dcc.Interval(id="poll", interval=5000, n_intervals=0),
], style={"padding": "15px", "backgroundColor": "#111", "color": "#eee", "minHeight": "100vh",
          "fontFamily": "monospace"})


@app.callback(
    Output("price-chart", "figure"),
    Output("status-bar", "children"),
    Input("poll", "n_intervals"),
)
def refresh(_):
    try:
        data = requests.get(f"{API_BASE}/latest/all", timeout=3).json()
    except Exception as e:
        return no_update, f"API error: {e}"

    features = data.get("features", {})
    score = data.get("score", {})
    bbo_f = data.get("bbo_futures", {})
    bbo_s = data.get("bbo_spot", {})
    book_f = data.get("book_futures", {})
    book_s = data.get("book_spot", {})
    mark = data.get("mark_index", {})
    trade_f = data.get("trade_futures", {})
    trade_s = data.get("trade_spot", {})

    using_synced_book = bool(book_f.get("synced")) and bool(bbo_f.get("mid_px"))
    perp_mid = bbo_f.get("mid_px", 0) if using_synced_book else 0
    if perp_mid == 0:
        perp_mid = mark.get("mark_price", 0) or trade_f.get("price", 0)
    using_synced_spot_book = bool(book_s.get("synced")) and bool(bbo_s.get("mid_px"))
    spot_mid = bbo_s.get("mid_px", 0) if using_synced_spot_book else 0
    if spot_mid == 0:
        spot_mid = trade_s.get("price", 0)

    if perp_mid == 0:
        return no_update, "Waiting for price data..."

    timestamps.append(datetime.now().strftime("%H:%M:%S"))
    perp_mids.append(perp_mid)
    spot_mids.append(spot_mid)
    premiums.append(features.get("premium_bps", 0))
    live_basis = 10000.0 * (perp_mid - spot_mid) / spot_mid if spot_mid else 0.0
    live_basis_vals.append(live_basis)
    perp_cvd_vals.append(features.get("perp_cvd_5s", 0))
    spot_cvd_vals.append(features.get("spot_cvd_5s", 0))
    oi_delta_vals.append(features.get("oi_delta_30s", 0))
    liq_skew_vals.append(features.get("liq_skew_30s", 0))

    ts = list(timestamps)

    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=("Price (Perp vs Spot)", "Live Basis vs Exchange Premium (bps)", "Perp vs Spot CVD 5s", "OI Delta 30s + Liq Skew 30s"),
        row_heights=[0.42, 0.18, 0.2, 0.2],
        specs=[[{}], [{}], [{}], [{"secondary_y": True}]],
    )

    fig.add_trace(go.Scattergl(x=ts, y=list(perp_mids), name="Perp",
                                mode="lines", line=dict(color="#00d4ff", width=2)), row=1, col=1)
    if spot_mid > 0:
        fig.add_trace(go.Scattergl(x=ts, y=list(spot_mids), name="Spot",
                                    mode="lines", line=dict(color="#ff6b6b", width=1.5, dash="dot")), row=1, col=1)

    fig.add_trace(
        go.Scattergl(
            x=ts,
            y=list(live_basis_vals),
            name="Live Basis",
            mode="lines",
            line=dict(color="#ff9f1c", width=1.8),
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scattergl(
            x=ts,
            y=list(premiums),
            name="Exchange Premium",
            mode="lines",
            line=dict(color="#ffd93d", width=1.5, dash="dot"),
        ),
        row=2,
        col=1,
    )

    perp_cvd_list = list(perp_cvd_vals)
    spot_cvd_list = list(spot_cvd_vals)
    liq_skew_list = list(liq_skew_vals)
    perp_colors = ["#00ff88" if v >= 0 else "#ff4444" for v in perp_cvd_list]
    liq_colors = ["#00ff88" if v >= 0 else "#ff4444" for v in liq_skew_list]

    fig.add_trace(
        go.Bar(x=ts, y=perp_cvd_list, name="Perp CVD 5s", marker_color=perp_colors, opacity=0.75),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Scattergl(x=ts, y=spot_cvd_list, name="Spot CVD 5s", mode="lines",
                     line=dict(color="#ffa94d", width=1.8)),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Scattergl(x=ts, y=list(oi_delta_vals), name="OI Delta 30s", mode="lines",
                     line=dict(color="#7ee787", width=1.8)),
        row=4,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scattergl(
            x=ts,
            y=[0.0] * len(ts),
            name="Liq Zero",
            mode="lines",
            line=dict(color="#5f6c8a", width=1, dash="dot"),
            showlegend=False,
        ),
        row=4,
        col=1,
        secondary_y=True,
    )
    fig.add_trace(
        go.Bar(x=ts, y=liq_skew_list, name="Liq Skew 30s", marker_color=liq_colors, opacity=0.55),
        row=4,
        col=1,
        secondary_y=True,
    )

    fig.update_layout(
        template="plotly_dark", paper_bgcolor="#111", plot_bgcolor="#1a1a2e",
        margin=dict(l=60, r=20, t=35, b=30), height=900,
        showlegend=True, legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
        font=dict(size=11, family="monospace"),
        uirevision="constant",  # prevents chart from resetting zoom on update
    )
    fig.update_yaxes(title_text="bps", row=2, col=1)
    fig.update_yaxes(title_text="CVD", row=3, col=1)
    fig.update_yaxes(title_text="OI", row=4, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Liq", row=4, col=1, secondary_y=True)

    if liq_skew_list and all(abs(v) < 1e-9 for v in liq_skew_list):
        fig.add_annotation(
            x=0.99,
            y=0.08,
            xref="paper",
            yref="paper",
            text="No liquidation events in window",
            showarrow=False,
            font=dict(size=10, color="#9aa4bf"),
            align="right",
        )

    state = score.get("state", "?")
    s1m = score.get("score_1m", 0)
    conf = score.get("confidence", 0)
    lag = features.get("feed_lag_ms_p95", 0)
    spread = bbo_f.get("spread_bps", 0) if using_synced_book else features.get("spread_bps", 0)
    perp_book_status = book_f.get("sync_status", "synced" if book_f.get("synced") else "unsynced")
    perp_book_reason = book_f.get("sync_reason", "")
    spot_book_status = book_s.get("sync_status", "synced" if book_s.get("synced") else "unsynced")
    spot_book_reason = book_s.get("sync_reason", "")
    perp_px_source = "local_book" if using_synced_book else "fallback"
    spot_px_source = "local_book" if using_synced_spot_book else "trade"

    line1 = (
        f"${perp_mid:,.2f}  |  {state}  |  "
        f"score: {s1m:.3f}  |  conf: {conf:.2f}  |  "
        f"lag: {lag:.0f}ms  |  spread: {spread:.3f}bps"
    )
    line2 = f"perp_book: {perp_book_status}"
    if perp_book_reason:
        line2 += f" ({perp_book_reason})"
    line2 += f"  |  perp_px: {perp_px_source}"

    line3 = f"spot_book: {spot_book_status}"
    if spot_book_reason:
        line3 += f" ({spot_book_reason})"
    line3 += f"  |  spot_px: {spot_px_source}"

    status = f"{line1}\n{line2}\n{line3}"

    return fig, status


if __name__ == "__main__":
    app.run(debug=False, port=8050)
