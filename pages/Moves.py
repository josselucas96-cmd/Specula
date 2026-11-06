"""Moves — every trade of a portfolio, on the NAV curve and as a log.

Public page (requested 2026-06-13: "let visitors see all the moves of a given
portfolio"). Built on the fund-accounting model: NAV from daily_holdings,
benchmark from the portfolio's own configuration, trades from `transactions`.
Select a portfolio with the buttons or ?pf=<id>.
"""
import re
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import SPECULA_ICON
from utils.data import get_portfolios, get_positions, get_transactions
from utils.market import get_benchmark_index, BenchmarkUnavailable
from utils.nav import render_nav
from utils.moves import BOTH, BUY, SELL, batch_numbers, group_moves_by_date, trades_only
from utils.nav_history import get_nav_from_holdings
from utils.portfolio import align_to_equity_calendar
from utils.theme import BG, TEXT_MID, BENCHMARK_LINE, action_colors, chart_layout

st.set_page_config(
    page_title="Moves | Specula",
    page_icon=SPECULA_ICON,
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown("""
<style>
    [data-testid="stSidebar"] { display: none; }
    .block-container { padding-top: 3.5rem; padding-bottom: 2rem; }
    .mv-eyebrow { font-size: 0.7rem; font-weight: 700; letter-spacing: 2px; color: #00D09C; text-transform: uppercase; }
    .mv-title { font-size: 2.2rem; font-weight: 800; letter-spacing: -0.5px; line-height: 1.2; margin-bottom: 0.2rem; }
    .mv-sub { color: #9CA3AF; font-size: 0.9rem; margin-bottom: 1rem; }
    .disclaimer { font-size: 0.72rem; color: #444; margin-top: 3rem; border-top: 1px solid #1A1F26; padding-top: 1rem; line-height: 1.5; }
</style>
""", unsafe_allow_html=True)

render_nav("moves")
st.write("")

ACTION_LABELS = {"IN": "Buy / reinforce", "TRIM": "Trim", "OUT": "Close", "SWITCH": "Switch"}
ACTION_COLORS = action_colors()


def _clean_reason(reason: str | None) -> str:
    """Cockpit rationales are written for the operator, not for a reader:
    "Move from cockpit" is noise, and reinforcement percentages carried 15
    decimals before 2026-09-22."""
    r = (reason or "").strip()
    if r.lower() in ("move from cockpit", "moved from cockpit"):
        return ""
    return re.sub(r"(\d+\.\d{3,})%", lambda m: f"{float(m.group(1)):.2f}%", r)

# ── Portfolio selection (?pf=… or buttons) ───────────────────────────────────
portfolios = get_portfolios(active_only=True)
if not portfolios:
    st.info("No portfolio yet.")
    st.stop()
ids = [p["id"] for p in portfolios]
_qp = st.query_params.get("pf")
if "moves_pf" not in st.session_state:
    st.session_state["moves_pf"] = _qp if _qp in ids else ids[0]


def _select_portfolio(portfolio_id: str) -> None:
    """on_click callback. Streamlit runs callbacks BEFORE re-running the script,
    so the buttons are drawn with the new selection. Setting the state inside
    the `if st.button(...)` branch left the highlight one click behind: the
    page showed Le Bâtisseur while the Visionnaire button stayed lit."""
    st.session_state["moves_pf"] = portfolio_id
    st.query_params["pf"] = portfolio_id


chosen = st.session_state["moves_pf"]
cols = st.columns(len(ids) + 3)
for i, p in enumerate(portfolios):
    cols[i].button(p["name"], key=f"moves_pf_{p['id']}", width="stretch",
                   type="primary" if p["id"] == chosen else "secondary",
                   on_click=_select_portfolio, args=(p["id"],))
pf = next(p for p in portfolios if p["id"] == chosen)
pid, accent = pf["id"], pf.get("color_primary") or "#A78BFA"

# ── Data ─────────────────────────────────────────────────────────────────────
# Decisions only: splits and dividends are corporate actions, not moves.
txns = trades_only(get_transactions(portfolio_id=pid))
positions_all = get_positions(active_only=False, portfolio_id=pid)
inception = str(pf.get("inception_date") or "")
# Inception buys are a block of IN rows on the inception date — shown once, not as 16 markers.
moves = [t for t in txns if str(t.get("date")) != inception]
inception_lines = [t for t in txns if str(t.get("date")) == inception]
# Le Bâtisseur / Le Nakamoto were seeded without inception transaction rows:
# count the positions opened that day instead, so the page never claims "0".
n_initial = len(inception_lines) or sum(
    1 for p in positions_all if str(p.get("entry_date") or "") == inception)

st.markdown(f'<div class="mv-eyebrow">Moves · {pf["name"]}</div>', unsafe_allow_html=True)
_n_trades = len(moves)
_title = f"{_n_trades} trade{'s' if _n_trades != 1 else ''} since inception"
st.markdown(f'<div class="mv-title">{_title}</div>', unsafe_allow_html=True)
_n_dates = len({str(t.get("date")) for t in moves})
_head = f'Inception {inception} with {n_initial} initial position{"s" if n_initial != 1 else ""}'
if _n_trades == 0:
    _body = ' · no trade since inception.'
else:
    _body = (f' · {_n_trades} trade{"s" if _n_trades != 1 else ""} grouped in '
             f'{_n_dates} rebalance{"s" if _n_dates != 1 else ""} — one marker per rebalance below, '
             f'hover it for the detail.')
_body += ' Every trade is timestamped in the ledger and mirrored in a public post.'
st.markdown(f'<div class="mv-sub">{_head}{_body}</div>', unsafe_allow_html=True)

# ── NAV curve with trade markers ─────────────────────────────────────────────
port_index = get_nav_from_holdings(pid)
bench = pf.get("benchmark_primary")
bench_lbl = pf.get("benchmark_primary_label") or bench or ""
bench_index = None
if bench and port_index is not None and not port_index.empty:
    # Anchored and validated against the portfolio's base date — never
    # normalised on whatever row a throttled Yahoo answer happens to start on.
    try:
        bench_index = get_benchmark_index(bench, port_index.index[0].strftime("%Y-%m-%d"))
    except BenchmarkUnavailable:
        bench_index = None
if port_index is not None and not port_index.empty:
    port_index = align_to_equity_calendar(port_index, bench_index, None)
    if bench_index is not None:
        bench_index = bench_index[bench_index.index <= port_index.index[-1]]

if port_index is None or port_index.empty:
    st.info("No performance data yet.")
else:
    fig = go.Figure()
    if bench_index is not None and not bench_index.empty:
        fig.add_trace(go.Scatter(x=bench_index.index, y=bench_index.values, name=bench_lbl,
                                 line=dict(color=BENCHMARK_LINE, width=1.5, dash="dash", shape="spline", smoothing=0.6),
                                 hovertemplate="%{x|%b %d, %Y}<br>" + bench_lbl + ": %{y:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=port_index.index, y=port_index.values, name=pf["name"],
                             line=dict(color=accent, width=3, shape="spline", smoothing=0.8),
                             hovertemplate="%{x|%b %d, %Y}<br>NAV: %{y:.1f}<extra></extra>"))
    # ONE MARKER PER TRADE DATE. A rebalance bundles several trades on one day;
    # drawn individually they land on the same point of the curve and hide each
    # other (Le Visionnaire: 13 trades on 4 dates). The glyph says what the day
    # was: a green disc (bought only), a red disc (sold only), or a disc split
    # red-over-green when the day did both. Markers are deliberately large — they are the subject of
    # this page, not a decoration.
    BUY_COLOR = ACTION_COLORS.get("IN", "#00D09C")
    SELL_COLOR = ACTION_COLORS.get("OUT", "#FF4B4B")

    # Marker size in pixels: visible from the first trade, bigger for a big
    # rebalance. The two-tone glyph is a font character whose disc measures
    # about 0.66 of its font size, hence the ratio below.
    GLYPH_RATIO = 0.66
    UPPER_HALF, LOWER_HALF = "\u25d3", "\u25d2"   # ◓ upper half filled, ◒ lower half filled

    def _diameter(n: int) -> float:
        return min(22.0 + 2.0 * (n - 1), 34.0)

    def _side_lines(trades, color, heading):
        out = ["<span style='color:%s'><b>%s</b></span>" % (color, heading)]
        for t in trades:
            a = (t.get("action") or "").upper()
            tk = t.get("ticker_in") or t.get("ticker_out") or ""
            w = t.get("weight_in") if a in ("IN", "SWITCH") else t.get("weight_out")
            px = t.get("price_in") if a in ("IN", "SWITCH") else t.get("price_out")
            bits = [f"<b>{tk}</b>"]
            if w:  bits.append(f"{float(w):.2f}% of capital")
            if px: bits.append(f"${float(px):,.2f}")
            out.append("&nbsp;&nbsp;&nbsp;" + " · ".join(bits))
        return out

    def _tooltip(g) -> str:
        ts = pd.Timestamp(g["date"])
        counts = []
        if g["sells"]:
            counts.append(f"{len(g['sells'])} sell" + ("s" if len(g["sells"]) > 1 else ""))
        if g["buys"]:
            counts.append(f"{len(g['buys'])} buy" + ("s" if len(g["buys"]) > 1 else ""))
        lines = [f"<b>{ts:%d %B %Y}</b>",
                 f"<span style='color:#9CA3AF'>{' and '.join(counts)} that day</span>", ""]
        if g["sells"]:
            lines += _side_lines(g["sells"], SELL_COLOR, "▼  SOLD / REDUCED")
            if g["buys"]:
                lines.append("")
        if g["buys"]:
            lines += _side_lines(g["buys"], BUY_COLOR, "▲  BOUGHT / REINFORCED")
        return "<br>".join(lines)

    placed = []
    for g in group_moves_by_date(moves):
        on_or_after = port_index[port_index.index >= pd.Timestamp(g["date"])]
        if on_or_after.empty:
            continue
        placed.append((on_or_after.index[0], float(on_or_after.iloc[0]), g))

    # Days with one side only: a plain disc, green or red, in the legend.
    for kind, color, label in ((BUY, BUY_COLOR, "Buy / reinforce"),
                               (SELL, SELL_COLOR, "Sell / reduce")):
        pts = [v for v in placed if v[2]["kind"] == kind]
        if not pts:
            continue
        fig.add_trace(go.Scatter(
            x=[v[0] for v in pts], y=[v[1] for v in pts], mode="markers", name=label,
            marker=dict(color=color, size=[_diameter(v[2]["n"]) for v in pts],
                        symbol="circle", line=dict(color=BG, width=2)),
            hovertext=[_tooltip(v[2]) for v in pts], hovertemplate="%{hovertext}<extra></extra>",
        ))

    # Days that sold AND bought: one disc cut in two, red on top, green below.
    # Plotly has no two-tone marker and its pixel-sized path shapes do not
    # render, so the glyph is a pair of font characters drawn at the same point
    # — the green lower half first, the red upper half over it — with a
    # transparent marker on top to carry the tooltip.
    both = [v for v in placed if v[2]["kind"] == BOTH]
    if both:
        _x = [v[0] for v in both]
        _y = [v[1] for v in both]
        _sizes = [_diameter(v[2]["n"]) / GLYPH_RATIO for v in both]
        for glyph, color in ((LOWER_HALF, BUY_COLOR), (UPPER_HALF, SELL_COLOR)):
            fig.add_trace(go.Scatter(
                x=_x, y=_y, mode="text", text=[glyph] * len(both), textposition="middle center",
                textfont=dict(size=_sizes, color=color), showlegend=False, hoverinfo="skip",
            ))
        fig.add_trace(go.Scatter(
            x=_x, y=_y, mode="markers", showlegend=False,
            marker=dict(color="rgba(0,0,0,0)", size=[_diameter(v[2]["n"]) for v in both],
                        symbol="circle", line=dict(width=0)),
            hovertext=[_tooltip(v[2]) for v in both], hovertemplate="%{hovertext}<extra></extra>",
        ))
    layout = chart_layout()
    layout["height"] = 420
    layout["legend"] = dict(orientation="h", yanchor="top", y=-0.16, xanchor="center", x=0.5, font=dict(size=10), bgcolor="rgba(0,0,0,0)")
    layout["margin"]["b"] = 60
    layout["yaxis"]["title"] = "Base 100"
    layout["hoverlabel"] = dict(bgcolor="#0B0F16", bordercolor="#334155", align="left",
                                font=dict(size=13, color="#E5E7EB"))
    layout["hovermode"] = "closest"
    fig.update_layout(**layout)
    st.plotly_chart(fig, width="stretch")
    st.caption("Each marker is one trading day: a green disc when the day only bought or reinforced, "
               "a red disc when it only sold or reduced, and a disc split red-over-green when it did both. "
               "The bigger the disc, the more trades that day — hover it for the list.")

# ── Trade log ────────────────────────────────────────────────────────────────
st.markdown("#### Trade log")
if not moves:
    st.caption("No trade since inception.")
else:
    # Rebalance number per date, so a batch reads as one decision in the log.
    _batch_no = batch_numbers(moves)
    rows = []
    for t in sorted(moves, key=lambda t: (str(t.get("date")), str(t.get("executed_at") or "")), reverse=True):
        a = (t.get("action") or "").upper()
        is_in = a in ("IN", "SWITCH")
        ts = t.get("executed_at")
        when = pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M UTC") if ts else str(t.get("date"))
        rows.append({
            "Batch": f"#{_batch_no[str(t.get('date'))]}",
            "When": when,
            "Action": ACTION_LABELS.get(a, a),
            "Ticker": t.get("ticker_in") or t.get("ticker_out") or "",
            "Size (% capital)": float(t.get("weight_in") if is_in else (t.get("weight_out") or 0) or 0),
            "Price": float(t.get("price_in") if is_in else (t.get("price_out") or 0) or 0),
            "Rationale": _clean_reason(t.get("reason")),
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.format({"Size (% capital)": lambda v: f"{v:.2f}%" if v else "—", "Price": lambda v: f"${v:,.2f}" if v else "—"}, na_rep="—"),
        hide_index=True, width="stretch", height=min(60 + 35 * len(df), 520),
    )

# ── Position timeline ────────────────────────────────────────────────────────
st.markdown("#### Position timeline")
tl = []
for p in positions_all:
    if not p.get("entry_date"):
        continue
    if not p.get("is_active") and not p.get("exit_price"):
        continue  # deactivated without a sale = reset, not a trade
    tl.append({"Ticker": p["ticker"], "Start": pd.Timestamp(p["entry_date"]),
               "End": pd.Timestamp(p["exit_date"]) if p.get("exit_date") else pd.Timestamp(date.today()),
               "Status": "Open" if p.get("is_active") else "Closed", "Layer": p.get("layer") or ""})
if tl:
    tdf = pd.DataFrame(tl).sort_values(["Start", "Ticker"])
    figt = go.Figure()
    for status, color in (("Open", accent), ("Closed", "#6B7280")):
        sub = tdf[tdf["Status"] == status]
        if sub.empty:
            continue
        figt.add_trace(go.Bar(
            base=sub["Start"], x=(sub["End"] - sub["Start"]).dt.total_seconds() * 1000, y=sub["Ticker"],
            orientation="h", name=status, marker=dict(color=color, opacity=0.85 if status == "Open" else 0.5),
            customdata=list(zip(sub["Start"].dt.strftime("%Y-%m-%d"), sub["End"].dt.strftime("%Y-%m-%d"), sub["Layer"])),
            hovertemplate="<b>%{y}</b> · %{customdata[2]}<br>%{customdata[0]} → %{customdata[1]}<extra></extra>",
        ))
    layout = chart_layout()
    layout["height"] = max(260, 22 * len(tdf) + 80)
    layout["barmode"] = "overlay"
    layout["xaxis"]["type"] = "date"
    layout["yaxis"]["autorange"] = "reversed"
    layout["legend"] = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10), bgcolor="rgba(0,0,0,0)")
    figt.update_layout(**layout)
    st.plotly_chart(figt, width="stretch")

st.markdown("""
<div class="disclaimer">
These portfolios are paper trading simulations and do not involve real financial assets. Nothing published
here constitutes financial, investment, or legal advice. I am not a registered financial advisor.
</div>
""", unsafe_allow_html=True)
