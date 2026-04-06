"""BTC Microstructure Dashboard — Section 9."""

from __future__ import annotations

from datetime import datetime

import requests
from dash import Dash, Input, Output, dcc, html, no_update

from dashboard.figures import price_chart, premium_chart, cvd_chart, depth_chart, oi_liq_chart

API_BASE = "http://localhost:8000"

app = Dash(__name__, update_title=None)

# ── Layout ───────────────────────────────────────────────────────────

app.layout = html.Div(className="main-container", children=[
    # Intervals
    dcc.Interval(id="fast-poll", interval=5000, n_intervals=0),
    dcc.Interval(id="slow-poll", interval=10000, n_intervals=0),

    # Ribbon (full width)
    html.Div(id="ribbon", className="ribbon"),

    # Left: charts
    html.Div(className="charts-column", children=[
        dcc.Graph(id="chart-price", config={"displayModeBar": False}),
        dcc.Graph(id="chart-premium", config={"displayModeBar": False}),
        dcc.Graph(id="chart-cvd", config={"displayModeBar": False}),
        dcc.Graph(id="chart-depth", config={"displayModeBar": False}),
        dcc.Graph(id="chart-oi-liq", config={"displayModeBar": False}),
    ]),

    # Right column
    html.Div(className="right-column", children=[
        html.Div(className="panel", children=[
            html.Div("Reasons", className="panel-title"),
            html.Div(id="reasons-list"),
        ]),
        html.Div(className="panel", children=[
            html.Div("Warnings", className="panel-title"),
            html.Div(id="warnings-list"),
        ]),
        html.Div(className="panel", children=[
            html.Div("Recent State Changes", className="panel-title"),
            html.Div(id="state-changes"),
        ]),
        html.Div(className="panel", children=[
            html.Div("Feed Health", className="panel-title"),
            html.Div(id="feed-health"),
        ]),
        html.Div(className="panel", children=[
            html.Div("Prediction Accuracy", className="panel-title"),
            html.Div(id="validation-panel"),
        ]),
    ]),
])


# ── Helpers ──────────────────────────────────────────────────────────

def _api_get(path: str, timeout: float = 3) -> dict | None:
    try:
        return requests.get(f"{API_BASE}{path}", timeout=timeout).json()
    except Exception:
        return None


def _ribbon_card(label: str, value: str, color: str = "#c9d1d9") -> html.Div:
    return html.Div(className="ribbon-card", children=[
        html.Div(label, className="label"),
        html.Div(value, className="value", style={"color": color}),
    ])


def _state_badge(state: str) -> html.Span:
    badge_map = {
        "bullish_pressure": "badge-bullish",
        "mild_bullish": "badge-mild-bullish",
        "neutral": "badge-neutral",
        "mild_bearish": "badge-mild-bearish",
        "bearish_pressure": "badge-bearish",
        "degraded": "badge-degraded",
    }
    cls = badge_map.get(state, "badge-neutral")
    return html.Span(state.replace("_", " ").upper(), className=f"badge {cls}")


# ── Ribbon (fast poll) ───────────────────────────────────────────────

@app.callback(Output("ribbon", "children"), Input("fast-poll", "n_intervals"))
def update_ribbon(_):
    data = _api_get("/latest/all")
    if not data:
        return [_ribbon_card("Status", "Waiting...")]

    features = data.get("features", {})
    score = data.get("score", {})
    bbo_f = data.get("bbo_futures", {})
    bbo_s = data.get("bbo_spot", {})
    book_f = data.get("book_futures", {})
    book_s = data.get("book_spot", {})
    mark = data.get("mark_index", {})

    perp_mid = bbo_f.get("mid_px") or mark.get("mark_price") or 0
    spot_mid = bbo_s.get("mid_px") or 0
    premium = features.get("premium_bps", 0)
    state = score.get("state", "?")
    s1m = score.get("score_1m", 0)
    s3m = score.get("score_3m", 0)
    s5m = score.get("score_5m", 0)
    conf = score.get("confidence", 0)
    lag = features.get("feed_lag_ms_p95", 0)

    score_color = "#3fb950" if s1m > 0.2 else "#f85149" if s1m < -0.2 else "#8b949e"

    return [
        _ribbon_card("Perp Mid", f"${perp_mid:,.2f}" if perp_mid else "—", "#58a6ff"),
        _ribbon_card("Spot Mid", f"${spot_mid:,.2f}" if spot_mid else "—", "#f85149"),
        _ribbon_card("Premium", f"{premium:+.2f} bps", "#d29922"),
        html.Div(className="ribbon-card", children=[
            html.Div("State", className="label"),
            _state_badge(state),
        ]),
        _ribbon_card("Score 1m", f"{s1m:+.3f}", score_color),
        _ribbon_card("Score 3m", f"{s3m:+.3f}", score_color),
        _ribbon_card("Score 5m", f"{s5m:+.3f}", score_color),
        _ribbon_card("Confidence", f"{conf:.2f}", "#58a6ff" if conf > 0.5 else "#d29922"),
        _ribbon_card("Lag p95", f"{lag:.0f}ms",
                     "#3fb950" if lag < 500 else "#d29922" if lag < 2000 else "#f85149"),
        _ribbon_card("F-Book", "OK" if book_f.get("synced") else "DESYNC",
                     "#3fb950" if book_f.get("synced") else "#f85149"),
        _ribbon_card("S-Book", "OK" if book_s.get("synced") else "DESYNC",
                     "#3fb950" if book_s.get("synced") else "#f85149"),
    ]


# ── Charts (slow poll) ──────────────────────────────────────────────

@app.callback(Output("chart-price", "figure"), Input("slow-poll", "n_intervals"))
def update_price(_):
    data = _api_get("/history/display?minutes=10")
    return price_chart((data or {}).get("points", []))


@app.callback(Output("chart-premium", "figure"), Input("slow-poll", "n_intervals"))
def update_premium(_):
    data = _api_get("/history/features?minutes=10")
    return premium_chart((data or {}).get("bars", []))


@app.callback(Output("chart-cvd", "figure"), Input("slow-poll", "n_intervals"))
def update_cvd(_):
    data = _api_get("/history/features?minutes=10")
    return cvd_chart((data or {}).get("bars", []))


@app.callback(Output("chart-depth", "figure"), Input("slow-poll", "n_intervals"))
def update_depth(_):
    data = _api_get("/history/features?minutes=10")
    return depth_chart((data or {}).get("bars", []))


@app.callback(Output("chart-oi-liq", "figure"), Input("slow-poll", "n_intervals"))
def update_oi_liq(_):
    data = _api_get("/history/features?minutes=10")
    return oi_liq_chart((data or {}).get("bars", []))


# ── Right column (fast poll) ────────────────────────────────────────

@app.callback(Output("reasons-list", "children"), Input("fast-poll", "n_intervals"))
def update_reasons(_):
    data = _api_get("/latest/score")
    if not data:
        return html.Div("Waiting...", style={"color": "#8b949e"})

    reasons = data.get("reasons", [])
    if not reasons:
        return html.Div("No signals", style={"color": "#8b949e"})

    warning_keywords = ["stale", "not synchronized", "degraded", "elevated"]
    items = []
    for r in reasons:
        if any(w in r.lower() for w in warning_keywords):
            continue
        color = "#3fb950" if any(w in r for w in ["positive", "buying", "rising", "bid depth", "short liq"]) \
                else "#f85149" if any(w in r for w in ["negative", "selling", "falling", "ask depth", "long liq", "declining"]) \
                else "#c9d1d9"
        items.append(html.Div(r, className="reason-item", style={"color": color}))
    return items if items else html.Div("No signals", style={"color": "#8b949e"})


@app.callback(Output("warnings-list", "children"), Input("fast-poll", "n_intervals"))
def update_warnings(_):
    data = _api_get("/latest/score")
    if not data:
        return html.Div("Waiting...", style={"color": "#8b949e"})

    reasons = data.get("reasons", [])
    state = data.get("state", "")
    warning_keywords = ["stale", "not synchronized", "degraded", "elevated"]
    warnings = [r for r in reasons if any(w in r.lower() for w in warning_keywords)]
    if state == "degraded":
        warnings.insert(0, "DEGRADED STATE")
    if not warnings:
        return html.Div("None", style={"color": "#3fb950"})
    return [html.Div(w, className="warning-item") for w in warnings]


@app.callback(Output("state-changes", "children"), Input("slow-poll", "n_intervals"))
def update_state_changes(_):
    data = _api_get("/history/score?minutes=10")
    scores = (data or {}).get("scores", [])
    if not scores:
        return html.Div("No history yet", style={"color": "#8b949e"})

    changes = []
    prev_state = None
    for s in scores:
        state = s.get("state", "")
        if state != prev_state and prev_state is not None:
            ts = datetime.fromtimestamp(s.get("ts_local", 0) / 1000).strftime("%H:%M:%S")
            badge = _state_badge(state)
            changes.append(html.Div([html.Span(f"{ts} ", style={"color": "#8b949e"}), badge],
                                     className="state-change-item"))
        prev_state = state

    if not changes:
        return html.Div("No state changes", style={"color": "#8b949e"})
    return changes[-20:]


@app.callback(Output("feed-health", "children"), Input("fast-poll", "n_intervals"))
def update_health(_):
    data = _api_get("/health")
    if not data:
        return html.Div("Waiting...", style={"color": "#8b949e"})

    def _row(label, value, ok=True):
        color = "#3fb950" if ok else "#f85149"
        return html.Div(className="health-row", children=[
            html.Span(label),
            html.Span(str(value), className="health-value", style={"color": color}),
        ])

    ft_lag = data.get("futures_trade_lag_ms")
    st_lag = data.get("spot_trade_lag_ms")
    bbo_f = data.get("bbo_futures_lag_ms")
    bbo_s = data.get("bbo_spot_lag_ms")
    mi_lag = data.get("mark_index_lag_ms")
    oi_lag = data.get("oi_lag_ms")

    rows = [
        _row("Futures Trade", f"{ft_lag:.0f}ms" if ft_lag is not None else "—", (ft_lag or 0) < 2000),
        _row("Spot Trade", f"{st_lag:.0f}ms" if st_lag is not None else "—", (st_lag or 0) < 2000),
        _row("Futures BBO", f"{bbo_f:.0f}ms" if bbo_f is not None else "—", (bbo_f or 0) < 2000),
        _row("Spot BBO", f"{bbo_s:.0f}ms" if bbo_s is not None else "—", (bbo_s or 0) < 2000),
        _row("Mark/Index", f"{mi_lag:.0f}ms" if mi_lag is not None else "—", (mi_lag or 0) < 2000),
        _row("OI", f"{oi_lag:.0f}ms" if oi_lag is not None else "—", not data.get("oi_stale", False)),
        _row("F-Book", "Synced" if data.get("book_sync_ok") else "Desynced", data.get("book_sync_ok", False)),
        _row("S-Book", "Synced" if data.get("spot_book_sync_ok") else "Desynced", data.get("spot_book_sync_ok", False)),
    ]

    age = data.get("last_event_age_ms")
    if age is not None:
        rows.append(_row("Last Event", f"{age:.0f}ms ago", age < 5000))

    return rows


@app.callback(Output("validation-panel", "children"), Input("slow-poll", "n_intervals"))
def update_validation(_):
    data = _api_get("/history/validation?minutes=30")
    if not data:
        return html.Div("Waiting...", style={"color": "#8b949e"})

    transitions = data.get("recent_transitions", [])
    qualified = data.get("qualified", {})
    q_summary = qualified.get("summary", {})
    raw_summary = data.get("summary", {})

    has_any = (
        any(q_summary.get(l, {}).get("directional_hit_rate") is not None for l in ["1m", "3m", "5m"])
        or any(raw_summary.get(l, {}).get("directional_hit_rate") is not None for l in ["1m", "3m", "5m"])
        or len(transitions) > 0
    )
    if not has_any:
        return html.Div("Waiting for state transitions...", style={"color": "#8b949e"})

    # Headline: qualified hit rates (trusted signals)
    def _hr_span(label, summary_dict, horizons_dict, prefix=""):
        s = summary_dict.get(label, {})
        hr = s.get("directional_hit_rate")
        h = horizons_dict.get(label, {})
        n = sum(h.get(f, {}).get("count", 0) for f in ["bullish", "bearish"])
        if hr is not None:
            color = "#3fb950" if hr > 0.55 else "#f85149" if hr < 0.45 else "#8b949e"
            return html.Span(f"{prefix}{label}: {hr:.0%} ({n})", style={"color": color, "marginRight": "10px", "fontWeight": "bold"})
        return html.Span(f"{prefix}{label}: —", style={"color": "#8b949e", "marginRight": "10px"})

    q_horizons = qualified.get("horizons", {})
    raw_horizons = data.get("horizons", {})

    headlines = [
        html.Div([
            html.Span("Trusted ", style={"color": "#58a6ff", "fontSize": "10px"}),
            *[_hr_span(l, q_summary, q_horizons) for l in ["1m", "3m", "5m"]],
        ], style={"marginBottom": "3px"}),
        html.Div([
            html.Span("Raw    ", style={"color": "#8b949e", "fontSize": "10px"}),
            *[_hr_span(l, raw_summary, raw_horizons) for l in ["1m", "3m", "5m"]],
        ], style={"marginBottom": "6px", "opacity": "0.7"}),
    ]

    # State accuracy table (from raw view — shows everything)
    h1m = raw_horizons.get("1m", {})
    h3m = raw_horizons.get("3m", {})
    h5m = raw_horizons.get("5m", {})

    table_rows = []
    for state_name in ["bullish", "neutral", "bearish"]:
        display_name = state_name.title()
        cols = [html.Td(display_name, style={"fontWeight": "bold"})]
        for h in [h1m, h3m, h5m]:
            s = h.get(state_name, {})
            hr = s.get("hit_rate")
            count = s.get("count", 0)
            if hr is not None and count > 0:
                color = "#3fb950" if hr > 0.55 else "#f85149" if hr < 0.45 else "#8b949e"
                cols.append(html.Td(f"{hr:.0%} ({count})", style={"color": color, "textAlign": "center"}))
            else:
                cols.append(html.Td("—", style={"color": "#8b949e", "textAlign": "center"}))
        table_rows.append(html.Tr(cols))

    table = html.Table([
        html.Thead(html.Tr([
            html.Th("State"),
            html.Th("1m", style={"textAlign": "center"}),
            html.Th("3m", style={"textAlign": "center"}),
            html.Th("5m", style={"textAlign": "center"}),
        ])),
        html.Tbody(table_rows),
    ], style={"width": "100%", "fontSize": "11px", "borderCollapse": "collapse"})

    # Recent transitions with confidence + qualified badge
    recent = []
    for t in transitions[-5:]:
        conf = t.get("confidence")
        is_qualified = t.get("qualified", False)

        verdict_badges = []
        for label in ["1m", "3m", "5m"]:
            v = t.get(f"verdict_{label}", "pending")
            bps = t.get(f"outcome_{label}_bps")
            badge_colors = {
                "correct": "#3fb950", "wrong": "#f85149",
                "unclear": "#d29922", "pending": "#8b949e",
            }
            text = f"{label}:{v[0].upper()}"
            if bps is not None:
                text += f" {bps:+.1f}"
            verdict_badges.append(html.Span(
                text,
                style={"color": badge_colors.get(v, "#8b949e"), "marginRight": "6px", "fontSize": "10px"},
            ))

        ts_str = datetime.fromtimestamp(t["confirm_ts"] / 1000).strftime("%H:%M:%S") if "confirm_ts" in t else datetime.fromtimestamp(t["ts"] / 1000).strftime("%H:%M:%S")
        conf_str = f" c={conf:.2f}" if conf is not None else ""
        score_str = f" s={t.get('score_1m', 0):.2f}" if t.get("score_1m") is not None else ""
        q_badge = html.Span(" Q", style={"color": "#58a6ff", "fontWeight": "bold", "fontSize": "9px"}) if is_qualified else html.Span("")
        from_fam = t.get("from_family", t.get("from_state", "?"))[:4]
        to_fam = t.get("to_family", t.get("to_state", "?"))[:4]

        recent.append(html.Div([
            html.Span(f"{ts_str} ", style={"color": "#8b949e"}),
            html.Span(f"{from_fam}>{to_fam}{conf_str}{score_str} ",
                       style={"color": "#c9d1d9", "fontSize": "10px"}),
            q_badge,
            *verdict_badges,
        ], style={"marginBottom": "3px"}))

    return html.Div([
        html.Div(headlines),
        table,
        html.Div("Recent Transitions", style={"fontSize": "10px", "color": "#8b949e",
                  "marginTop": "8px", "marginBottom": "4px", "textTransform": "uppercase"}),
        html.Div(recent if recent else html.Div("No transitions yet", style={"color": "#8b949e"})),
    ])


if __name__ == "__main__":
    app.run(debug=False, port=8050)
