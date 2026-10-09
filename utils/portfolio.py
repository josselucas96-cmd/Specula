"""Generic portfolio page renderer. Used by pages/Visionnaire.py, pages/Nakamoto.py, etc.

The function reads portfolio metadata from the `portfolios` Supabase table
(benchmark tickers, inception, name, etc.) and renders the full page.
Section visibility is controlled via the `options` dict.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, timedelta

from utils.data import get_positions, get_setting, get_portfolio, get_cash_amount
from utils.market import (
    get_prices_from_db as get_prices,
    get_history,
    get_benchmark_index,
    BenchmarkUnavailable,
    get_total_return_factor_from_db as get_total_return_factor,
)
from utils.metrics import (
    daily_returns,
    sharpe_ratio, max_drawdown, beta_vs_spy, aligned_returns, jensen_alpha,
    annualized_volatility, var_95, correlation_matrix, avg_pairwise_correlation,
    monthly_returns_table,
)
from utils.nav_history import get_nav_from_holdings
from utils.line_returns import adjusted_returns
from utils.research import get_research
from utils.nav import render_nav
from utils.theme import (
    BG, ACCENT, POSITIVE, NEGATIVE, TRIM,
    TEXT_MID, PORTFOLIO_LINE, BENCHMARK_LINE, HLINE_COLOR,
    BENCHMARK_COLORS, BENCHMARK_FALLBACK,
    chart_layout,
)


def benchmark_color(label: str | None, role: str) -> str:
    """Color of a benchmark line: fixed per index, fallback by role."""
    return BENCHMARK_COLORS.get(label or "", BENCHMARK_FALLBACK[role])


# ── Color palettes (shared across portfolios) ────────────────────────────────
_THEMATIC_COLORS = {
    # ── Tech / Internet ──
    "AI / Semi":              "#1E3A8A",  # deep navy
    "Software":               "#3B82F6",  # bright blue
    "Software / SaaS":        "#3B82F6",  # legacy alias
    "Cloud":                  "#0EA5E9",  # sky cyan
    "Cloud / Infrastructure": "#0EA5E9",  # legacy alias
    "Cybersecurity":          "#A855F7",  # violet
    "Social Platform":        "#EC4899",  # magenta
    "Robotics / Automation":  "#9333EA",  # purple
    # ── Health ──
    "Healthcare Equipment":   "#14B8A6",  # teal
    "Digital Health":         "#34D399",  # mint
    "Biotech":                "#059669",  # forest
    "Obesity":                "#A78BFA",  # lavender
    # ── Finance ──
    "Fintech":                "#6366F1",  # indigo
    "Fintech / Payments":     "#6366F1",  # legacy alias
    "Banking":                "#1E293B",  # near-black slate
    "Crypto Currencies Play": "#F59E0B",  # amber
    # ── Consumer ──
    "Consumer Growth":        "#FCA5A5",  # peach (legacy preserved)
    "Luxury":                 "#92400E",  # wine
    # ── Energy / Utilities ──
    "Energy Transition":      "#FCD34D",  # yellow
    "Clean Energy":           "#4ADE80",  # light green
    # ── Other / Misc ──
    "Space / Defense":        "#374151",  # graphite
    "EV / China":             "#86EFAC",  # mint
    "Other":                  "#64748B",  # slate
    "Cash":                   "#CBD5E1",
    "Cash/Equivalent":        "#CBD5E1",
}
_SECTOR_COLORS = {
    # ── Simplified naming (Visionnaire / Nakamoto legacy) ──
    "Tech":          "#3B82F6",
    "Healthcare":    "#10B981",
    "Finance":       "#6366F1",
    "Communication": "#8B5CF6",
    "Industrials":   "#64748B",
    "Consumer":      "#F97316",
    "Energy":        "#DC2626",
    "Materials":     "#A8A29E",
    "Real Estate":   "#EC4899",
    "Utilities":     "#06B6D4",
    # ── GICS naming (Bâtisseur) ──
    "Information Technology": "#3B82F6",  # bright blue
    "Communication Services": "#8B5CF6",  # purple
    "Consumer Discretionary": "#F97316",  # orange
    "Consumer Staples":        "#A16207",  # dark amber
    "Financials":             "#6366F1",  # indigo
    # Healthcare / Industrials / Energy / Materials / Real Estate / Utilities
    # use the simplified key (same name in GICS).
    # ── Cash ──
    "Cash":          "#CBD5E1",
    "Cash/Equivalent": "#94A3B8",
}
_GEO_COLORS = {
    "USA":              "#1E40AF",  # deep blue
    "Canada":           "#DC2626",  # red (flag)
    "Europe":           "#3B82F6",  # mid blue
    "Japan":            "#FDBA74",  # peach
    "Asia ex-Japan":    "#F59E0B",  # amber
    "China":            "#991B1B",  # dark red
    "Emerging Markets": "#FCD34D",  # yellow
    "LatAm":            "#10B981",  # emerald
    "Global":           "#A78BFA",  # lavender
    "Other":            "#6B7280",  # grey
    "USD":              "#94A3B8",  # medium grey (visible vs background)
}
_LAYER_COLORS = {
    # Le Visionnaire
    "Core":             "#1E40AF",
    "Conviction":       "#F97316",
    "Moonshot":         "#34D399",
    # Le Nakamoto
    "Anchor":           "#1E40AF",
    "Exploratory":      "#F97316",
    "Income":           "#34D399",
    # Le Bâtisseur — internal taxonomy
    "Obvious":          "#1E40AF",
    "Haute Qualité":    "#6366F1",
    "Diversification":  "#34D399",
    "Tactical":         "#F97316",
    # Le Bâtisseur — public taxonomy (collapsed via layer_map)
    "Quality Compounders": "#3B82F6",
    # Cash (all portfolios)
    "Cash":             "#CBD5E1",
    "Cash/Equivalent":  "#CBD5E1",
}
_COLOR_MAPS = {
    "Sector":    _SECTOR_COLORS,
    "Geography": _GEO_COLORS,
    "Thematic":  _THEMATIC_COLORS,
    "Layer":     _LAYER_COLORS,
}

# Eyebrow text per portfolio (matches the IPS document tagline)
_EYEBROW = {
    "visionnaire": "HIGH CONVICTION EQUITY  ·  PAPER PORTFOLIO",
    "nakamoto":    "DIGITAL ASSET TREASURIES  ·  PAPER PORTFOLIO",
    "batisseur":   "QUALITY COMPOUNDERS  ·  PAPER PORTFOLIO",
}


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert #RRGGBB to rgba(r, g, b, a) for CSS."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _is_light_color(hex_color: str) -> bool:
    """Return True if the color is light (use dark text on it)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return luminance > 0.55


def _donut_chart(df, col, title):
    grouped = df.groupby(col)["Alloc."].sum().reset_index()
    cmap = _COLOR_MAPS.get(col, {})
    color_map = {cat: cmap.get(cat, "#6B7280") for cat in grouped[col].unique()}
    fig = px.pie(
        grouped, values="Alloc.", names=col, title=title,
        hole=0.52, color=col, color_discrete_map=color_map,
    )
    fig.update_traces(
        textinfo="percent",
        hovertemplate="%{label}: %{value:.2f}%<extra></extra>",
    )
    fig.update_layout(
        plot_bgcolor=BG, paper_bgcolor=BG,
        font=dict(color=TEXT_MID),
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(font=dict(size=11)),
        title_font_size=14,
    )
    return fig


def align_to_equity_calendar(port_index, primary_index=None, secondary_index=None):
    """Return `port_index` restricted to the days the equity market actually traded.

    daily_holdings carries a row for EVERY calendar day (7/7 cron: Friday's
    close propagated through weekends AND market holidays). Those flat days
    are fine in the DB but must not feed anything computed on daily returns:
    they dangle past the benchmark on the chart, and they dilute volatility,
    VaR and Sharpe (2/7 zero-return days annualised with sqrt(252) understated
    vol by ~15% until 2026-09-19). The trading calendar is taken from the
    equity benchmark(s) — handles weekends and holidays automatically. A 24/7
    benchmark (Bitcoin: its index contains weekend dates) is NOT used as the
    calendar. Without any equity benchmark, fall back to weekdays only.
    """
    if port_index is None or port_index.empty:
        return port_index

    def _is_24_7(idx):
        return len(idx) > 0 and bool((idx.weekday >= 5).any())

    equity_dates = None
    for _b in (primary_index, secondary_index):
        if _b is not None and not _b.empty and not _is_24_7(_b.index):
            equity_dates = _b.index if equity_dates is None else equity_dates.union(_b.index)

    if equity_dates is not None and len(equity_dates) > 0:
        return port_index[port_index.index.isin(equity_dates)]
    return port_index[port_index.index.weekday < 5]


def render_performance_chart_section(
    portfolio_name: str,
    accent_color: str,
    inception_date: str,
    bench_pri_lbl: str | None,
    bench_sec_lbl: str | None,
    port_index,
    primary_index,
    secondary_index,
    min_days_stats: int = 60,
    extra_indices=None,
):
    """Render the Performance content: chart + Sharpe/Max DD/Beta + Monthly Returns
    table.

    Pure rendering function — the caller provides pre-computed series (port_index,
    primary_index, secondary_index) and is responsible for wrapping this call in
    a `with st.expander(...)` or other container.

    Used by both the public portfolio pages and the Admin cockpit so the visual
    stays in sync.

    Sharpe and Beta are gated behind `min_days_stats` trading days of history
    (default 60) to avoid showing meaningless stats on a young portfolio.
    """
    if port_index is None or port_index.empty:
        st.info("No performance data yet.")
        return

    port_index = align_to_equity_calendar(port_index, primary_index, secondary_index)
    if port_index.empty:
        st.info("No performance data yet.")
        return

    _last_common = port_index.index[-1]

    def _align_bench(bench):
        """Clip every benchmark to the portfolio's last shown date so no line
        dangles past the portfolio. A 24/7 benchmark (Bitcoin) keeps its interior
        weekend points (the weekend moves still show) — it just no longer trails
        several days past the portfolio's last day (e.g. on Le Nakamoto)."""
        if bench is None or bench.empty:
            return bench
        return bench[bench.index <= _last_common]

    primary_index   = _align_bench(primary_index)
    secondary_index = _align_bench(secondary_index)
    # Optional reference lines (page option `extra_benchmarks`): chart only,
    # hidden until clicked in the legend; no metric is computed on them.
    extra_indices = [(lbl, _align_bench(idx)) for lbl, idx in (extra_indices or [])
                     if idx is not None and not idx.empty]

    n_returns = len(port_index.pct_change().dropna())
    stats_ready = n_returns >= min_days_stats
    stats_help = f"Available after {min_days_stats} trading days (currently {n_returns})"

    # ── Chart ──
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=port_index.index, y=port_index.values,
        name=portfolio_name,
        line=dict(color=accent_color, width=3, shape="spline", smoothing=0.8),
        hovertemplate="%{x|%b %d, %Y}<br>Portfolio: %{y:.1f}<extra></extra>",
    ))
    if secondary_index is not None and not secondary_index.empty:
        fig.add_trace(go.Scatter(
            x=secondary_index.index, y=secondary_index.values,
            name=bench_sec_lbl,
            visible="legendonly",
            line=dict(color=benchmark_color(bench_sec_lbl, "secondary"), width=1.8, dash="dot",
                      shape="spline", smoothing=0.6),
            hovertemplate=f"%{{x|%b %d, %Y}}<br>{bench_sec_lbl}: %{{y:.1f}}<extra></extra>",
        ))
    for _lbl, _idx in extra_indices:
        fig.add_trace(go.Scatter(
            x=_idx.index, y=_idx.values,
            name=_lbl,
            visible="legendonly",
            line=dict(color=benchmark_color(_lbl, "extra"), width=1.8, dash="dashdot",
                      shape="spline", smoothing=0.6),
            hovertemplate=f"%{{x|%b %d, %Y}}<br>{_lbl}: %{{y:.1f}}<extra></extra>",
        ))
    if primary_index is not None and not primary_index.empty:
        fig.add_trace(go.Scatter(
            x=primary_index.index, y=primary_index.values,
            name=bench_pri_lbl,
            line=dict(color=benchmark_color(bench_pri_lbl, "primary"), width=1.8, dash="dash",
                      shape="spline", smoothing=0.6),
            hovertemplate=f"%{{x|%b %d, %Y}}<br>{bench_pri_lbl}: %{{y:.1f}}<extra></extra>",
        ))
    fig.add_hline(y=100, line_dash="dash", line_color=HLINE_COLOR, line_width=1)
    layout = chart_layout(height=380)
    layout["hovermode"] = "x unified"
    layout["yaxis"]["title"] = "Base 100"
    layout["legend"] = dict(
        orientation="h",
        yanchor="top", y=-0.18,
        xanchor="center", x=0.5,
        font=dict(size=10),
        bgcolor="rgba(0,0,0,0)",
    )
    layout["margin"]["b"] = 60
    fig.update_layout(**layout)
    # x-axis padding so the chart doesn't end glued to the right edge.
    # Right edge = latest date across all shown series, so a 24/7 benchmark
    # (Bitcoin) extending past the portfolio's last weekday isn't clipped.
    _right_end = port_index.index[-1]
    for _b in (primary_index, secondary_index, *(idx for _, idx in extra_indices)):
        if _b is not None and not _b.empty:
            _right_end = max(_right_end, _b.index[-1])
    _span = _right_end - port_index.index[0]
    _pad = max(_span * 0.04, pd.Timedelta(days=1))
    fig.update_xaxes(range=[
        port_index.index[0] - _pad,
        _right_end + _pad,
    ])
    st.plotly_chart(fig, width="stretch")

    # ── Sharpe / Max Drawdown / Beta / Jensen's alpha ──
    # Beta and alpha are measured against the portfolio's own benchmark (the
    # primary one). Until 2026-09-23 beta used the secondary benchmark: the
    # Visionnaire showed 2.04 against the S&P 500 while its benchmark is the
    # Nasdaq 100 (1.17), Le Bâtisseur its beta against the Nasdaq 100, Le
    # Nakamoto against Strategy instead of Bitcoin. Reported by a reader.
    port_ret = daily_returns(port_index)
    _pr, _br = aligned_returns(port_index, primary_index)

    r1, r2, r3, r4 = st.columns(4)
    with r1:
        if stats_ready:
            s = sharpe_ratio(port_ret)
            st.metric("Sharpe Ratio (ann.)",
                      f"{s:.2f}" if s is not None else "—",
                      help="Annualized Sharpe, risk-free rate 5%")
        else:
            st.metric("Sharpe Ratio (ann.)", "—", help=stats_help)
    with r2:
        md = max_drawdown(port_index)
        st.metric("Max Drawdown", f"{md:.2f}%" if md is not None else "—")
    _beta = beta_vs_spy(_pr, _br) if stats_ready else None
    with r3:
        beta_label = f"Beta vs {bench_pri_lbl}" if bench_pri_lbl else "Beta"
        if stats_ready:
            st.metric(beta_label, f"{_beta:.2f}" if _beta is not None else "—",
                      help=f"Sensitivity of the portfolio's daily returns to the {bench_pri_lbl or 'benchmark'}'s")
        else:
            st.metric(beta_label, "—", help=stats_help)
    with r4:
        if stats_ready:
            ja = jensen_alpha(port_index, primary_index, _beta)
            st.metric("Jensen's Alpha", f"{ja:+.2f}%" if ja is not None else "—",
                      help=("Return not explained by market exposure since inception: portfolio return "
                            "minus [risk-free + beta x (benchmark return - risk-free)], risk-free 5% as "
                            "for the Sharpe ratio. Indicative only with less than a year of history."))
        else:
            st.metric("Jensen's Alpha", "—", help=stats_help)

    # ── Monthly returns table ──
    st.write("")
    st.markdown('<div class="pf-section-label">Monthly Returns (%)</div>', unsafe_allow_html=True)
    mrt = monthly_returns_table(port_index, inception_date=inception_date)
    if not mrt.empty:
        _MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        inc_ts   = pd.Timestamp(inception_date)
        inc_col  = _MONTHS[inc_ts.month - 1]
        inc_year = inc_ts.year

        def _color_monthly(col):
            return [
                "color: #00D09C" if pd.notna(v) and v > 0
                else "color: #FF4B4B" if pd.notna(v) and v < 0
                else "" for v in col
            ]
        fmt = {m: (lambda v: f"{v:+.1f}" if pd.notna(v) else "") for m in mrt.columns}
        styled_mrt = mrt.style.format(fmt, na_rep="").apply(_color_monthly)
        if inc_year in mrt.index and inc_col in mrt.columns:
            styled_mrt = styled_mrt.format(
                lambda v: f"{v:+.1f}*" if pd.notna(v) else "",
                subset=pd.IndexSlice[[inc_year], [inc_col]], na_rep="",
            )
        st.dataframe(styled_mrt, width="stretch",
                     height=38 + min(len(mrt), 10) * 35)
        st.caption(f"\\* Partial month — return from inception ({inception_date}) to month-end.")


def render_portfolio_page(portfolio_id: str, options: dict | None = None):
    """Render the full portfolio page for a given portfolio.

    Args:
        portfolio_id: slug, e.g. 'visionnaire' or 'nakamoto'
        options: section visibility config:
            show_donuts (list[str]): subset of {"Layer","Sector","Geography","Thematic"}
                                     default: all four
            show_risk_analysis (bool): default True
            show_research_teaser (bool): default True
            show_documents_section (bool): default True
            show_disclaimer_banner (bool): default True
    """
    options = options or {}
    show_donuts = options.get("show_donuts", ["Layer", "Sector", "Thematic", "Geography"])
    show_risk_analysis = options.get("show_risk_analysis", True)
    show_research_teaser = options.get("show_research_teaser", True)
    show_documents_section = options.get("show_documents_section", True)
    show_disclaimer_banner = options.get("show_disclaimer_banner", True)
    show_layer_column = options.get("show_layer_column", True)
    layer_map = options.get("layer_map", {})  # internal → public layer rename

    # ── Portfolio metadata ────────────────────────────────────────────────────
    pf = get_portfolio(portfolio_id)
    if not pf:
        st.error(f"Portfolio '{portfolio_id}' not found.")
        st.stop()

    portfolio_name = pf["name"]
    inception_date = str(pf["inception_date"])
    bench_pri      = pf.get("benchmark_primary")
    bench_pri_lbl  = pf.get("benchmark_primary_label") or bench_pri or ""
    bench_sec      = pf.get("benchmark_secondary")
    bench_sec_lbl  = pf.get("benchmark_secondary_label") or bench_sec or ""

    # ── Research papers count (for the research teaser label) ────────────────
    _published = [p for p in get_research()
                  if p["status"] in ("published", "locked")
                  and p.get("doc_type", "Stock Paper") == "Stock Paper"]
    _published_count = len(_published)
    papers_label = (f"{_published_count} paper{'s' if _published_count != 1 else ''} available"
                    if _published_count else "Research")

    # ── Top nav ───────────────────────────────────────────────────────────────
    render_nav(portfolio_id)

    # ── Disclaimer banner (collapsible, fixed) ───────────────────────────────
    if show_disclaimer_banner:
        accent_color = pf.get("color_primary") or "#A78BFA"
        accent_border = _hex_to_rgba(accent_color, 0.15)
        accent_border_light = _hex_to_rgba(accent_color, 0.10)
        st.markdown(f"""
<style>
.disc-wrap {{ margin: 0; padding: 0; }}
.disc-sum {{
    position: fixed;
    top: 0; right: 1.5rem;
    height: 52px;
    z-index: 9999999;
    display: flex;
    align-items: center;
    list-style: none;
    cursor: pointer;
    font-size: 0.6rem;
    color: #374151;
    user-select: none;
    padding: 0 0.4rem;
    gap: 4px;
}}
.disc-sum::-webkit-details-marker {{ display: none; }}
.disc-sum:hover {{ color: #6B7280; }}
.disc-wrap[open] .disc-sum::after {{ content: "▼ disclaimer"; }}
.disc-wrap:not([open]) .disc-sum::after {{ content: "▲ disclaimer"; }}
.disc-body {{
    position: fixed;
    top: 52px; left: 0; right: 0;
    z-index: 99998;
    background: rgba(6, 9, 18, 0.97);
    border-bottom: 1px solid {accent_border};
    padding: 0.45rem 2.5rem 0.5rem 2.5rem;
    font-size: 0.72rem;
    color: #7A8595;
    line-height: 1.5;
}}
@media (max-width: 768px) {{
    .disc-sum {{
        top: 85px; right: 0;
        height: 28px; width: 36px;
        justify-content: center;
        background: rgba(6,9,18,0.97);
        border-left: 1px solid {accent_border_light};
        border-bottom: 1px solid {accent_border_light};
        border-radius: 0 0 0 6px;
    }}
    .disc-wrap[open] .disc-sum::after {{ content: "✕"; }}
    .disc-wrap:not([open]) .disc-sum::after {{ content: "i"; }}
    .disc-body {{
        top: 85px;
        padding: 0.4rem 2.8rem 0.4rem 1rem;
        font-size: 0.65rem;
    }}
}}
</style>
<details class="disc-wrap" open>
<summary class="disc-sum"></summary>
<div class="disc-body">
<strong style="color:#9EAAB8;">Disclaimer</strong> —
{portfolio_name} is a personal paper portfolio shared for educational and informational purposes only.
It does not constitute investment advice or a recommendation to buy or sell any security or digital asset.
I am not a registered financial advisor. Past performance is not indicative of future results.
The author may hold personal positions in securities mentioned on this page; readers should consider this potential conflict of interest. Full disclosure in the Investment Policy Statement.
Always conduct your own due diligence before making any investment decision.
</div>
</details>
""", unsafe_allow_html=True)

    # ── Page styles (with portfolio-specific accent color) ───────────────────
    accent = pf.get("color_primary") or "#A78BFA"
    st.markdown(f"""
<style>
    [data-testid="stSidebar"] {{ display: none; }}
    .block-container {{ padding-top: 6.8rem; padding-bottom: 2rem; }}
    @media (max-width: 768px) {{
        .block-container {{ padding-top: 10rem !important; }}
    }}
    .portfolio-title {{ font-size: 3rem; font-weight: 900; letter-spacing: -1px; margin-bottom: 0; }}
    .section-header {{ font-size: 1.5rem; font-weight: 800; letter-spacing: -0.3px; }}
    [data-testid="stExpander"] summary p {{
        font-size: 1.35rem !important;
        font-weight: 800 !important;
        letter-spacing: -0.3px !important;
    }}
    .disclaimer {{ font-size: 0.72rem; color: #4A5568; margin-top: 3rem;
                  border-top: 1px solid #161D2E; padding-top: 1rem; line-height: 1.5; }}
    [data-testid="stRadio"] label div[data-testid="stMarkdownContainer"] {{ color: inherit; }}
    [data-baseweb="radio"] [data-checked="true"] div {{ background-color: {accent} !important; border-color: {accent} !important; }}
    [data-baseweb="radio"] div:focus-within {{ border-color: {accent} !important; }}

    /* ── Portfolio accent (IPS-inspired): eyebrow + title + metric labels ── */
    .pf-eyebrow {{
        color: {accent};
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 3px;
        text-transform: uppercase;
        margin-bottom: 0.7rem;
        margin-top: 0.2rem;
    }}
    /* Portfolio header title — force Cormorant Garamond / Georgia serif.
       Class selector + !important beats the global p rule from nav.py. */
    p.pf-title,
    .pf-title {{
        color: {accent} !important;
        font-family: 'Cormorant Garamond', Georgia, 'Times New Roman', serif !important;
        font-size: 3.5rem !important;
        font-weight: 700 !important;
        letter-spacing: -1px !important;
        line-height: 1.1 !important;
        margin-bottom: 0 !important;
    }}
    /* Metric labels: accent, uppercase, letter-spaced (eyebrow style) */
    [data-testid="stMetric"] [data-testid="stMetricLabel"] p {{
        color: {accent} !important;
        text-transform: uppercase !important;
        letter-spacing: 1.5px !important;
        font-size: 0.68rem !important;
        font-weight: 700 !important;
    }}
    /* Metric values: stay bright white for contrast */
    [data-testid="stMetric"] [data-testid="stMetricValue"] {{
        color: #F9FAFB !important;
    }}
    .pf-section-label {{
        color: {accent} !important;
        font-weight: 700;
        font-size: 0.78rem;
        letter-spacing: 2px;
        text-transform: uppercase;
        margin-bottom: 0.6rem;
    }}

    /* Section dividers (st.divider) themed in accent color */
    hr,
    [data-testid="stHorizontalDivider"],
    [data-testid="stHorizontalBlock"] hr {{
        border: none !important;
        border-top: 2px solid {accent} !important;
        background: transparent !important;
        margin: 1.5rem 0 !important;
        opacity: 1 !important;
    }}
</style>
""", unsafe_allow_html=True)

    # ── Data ──────────────────────────────────────────────────────────────────
    positions = get_positions(portfolio_id=portfolio_id)
    if not positions:
        st.title(portfolio_name)
        st.info("No positions loaded yet. Check back soon.")
        st.stop()

    tickers = tuple(p["ticker"] for p in positions)
    prices  = get_prices(tickers)

    entry_dates    = tuple(p["entry_date"] for p in positions)
    entry_prices_t = tuple(float(p["entry_price"]) for p in positions)
    tr_factors     = get_total_return_factor(tickers, entry_dates, entry_prices_t)

    for p in positions:
        live = prices.get(p["ticker"], {})
        p["current_price"] = live.get("price")
        p["change_today"]  = live.get("change_pct")
        factor = tr_factors.get(p["ticker"], {"shares_factor": 1.0, "div_return_pct": 0.0})
        p["div_return"] = factor["div_return_pct"]
        if p["current_price"] and p["entry_price"]:
            price_return = (p["current_price"] - p["entry_price"]) / p["entry_price"] * 100
            total_return = (factor["shares_factor"] * p["current_price"] / p["entry_price"] - 1) * 100
            p["price_return"] = round(price_return, 2)
            p["perf_pct"]     = round(total_return, 2)
        else:
            p["perf_pct"]     = None
            p["price_return"] = None
            p["div_return"]   = None

    # Return including what was realised on partial sales ("—" for lines that
    # were only ever bought: their figure equals Return %). See utils.line_returns.
    _adj = adjusted_returns(portfolio_id, positions)
    for p in positions:
        p["adj_return"] = _adj.get(p["ticker"])

    valid   = [p for p in positions if p["perf_pct"] is not None]
    total_w = sum(p["weight"] for p in valid) or 1
    portfolio_perf = sum(p["weight"] * p["perf_pct"] / total_w for p in valid)

    # Fund-accounting allocation (real $). Each position $-value = shares × price;
    # cash $ derived from daily_holdings (see data.get_cash_amount). current_weight
    # = position$ / NAV × 100. Was previously cost-basis (weight × price/PRU) — that
    # under-counted realized gains sitting in cash post-rebalance.
    initial_capital = float(pf.get("initial_capital") or 1_000_000)
    cash_amount     = get_cash_amount(portfolio_id)
    for p in positions:
        cp = p.get("current_price")
        shares = float(p.get("shares") or 0)
        if cp and shares > 0:
            p["current_value_usd"] = shares * float(cp)
        elif cp and p.get("entry_price"):
            # Fallback for positions without a shares value (pre-backfill rows).
            cost = float(p.get("weight") or 0) * initial_capital / 100.0
            p["current_value_usd"] = cost * (float(cp) / float(p["entry_price"]))
        else:
            p["current_value_usd"] = float(p.get("weight") or 0) * initial_capital / 100.0
    nav_now = sum(p["current_value_usd"] for p in positions) + cash_amount
    if nav_now <= 0:
        nav_now = initial_capital
    for p in positions:
        p["current_weight"] = round(p["current_value_usd"] / nav_now * 100, 2)
    current_cash_pct = round(cash_amount / nav_now * 100, 1)

    # Chart starts at T-1 (last weekday before inception) so benchmarks
    # naturally normalize to the same anchor as the portfolio (which has
    # a CASH row at T-1 in daily_holdings = $initial_capital = base 100).
    def _previous_trading_day(d_str):
        c = pd.Timestamp(d_str) - pd.Timedelta(days=1)
        while c.weekday() >= 5:  # Sat=5, Sun=6
            c -= pd.Timedelta(days=1)
        return c.date().isoformat()

    chart_start = _previous_trading_day(inception_date)

    # One live download per page, for the correlation matrix only: prices,
    # NAV and returns come from the ledger, benchmarks from get_benchmark_index.
    # The "since inception" view is a slice of the same frame. (Until
    # 2026-09-23 a second 28-ticker download from inception ran here as well;
    # it was only still used for that slice, and it is the call that left Le
    # Bâtisseur's page spinning for minutes on Streamlit Cloud.)
    corr_start   = (date.today() - timedelta(days=365)).isoformat()
    history_corr = get_history(tickers, corr_start, benchmarks=())
    history = (history_corr[history_corr.index >= pd.Timestamp(chart_start)]
               if not history_corr.empty else history_corr)

    # Build portfolio index + benchmark indices (parameterized)
    primary_perf   = None
    primary_index  = None
    secondary_perf = None
    secondary_index = None
    # "Last updated" is the date of the ledger's last row, set further down.
    last_updated = "—"

    # Benchmarks are fetched one by one and validated against the T-1 anchor
    # (see get_benchmark_index). They are deliberately NOT read out of the
    # holdings batch any more: Yahoo answers a throttled 20-ticker download
    # with a silently truncated column, and normalising on its first row is
    # what published "Nasdaq 100 +9.02%, alpha +11.31%" on 2026-09-23 when the
    # truth was +21.60% and -1.27%. When a benchmark cannot be trusted the page
    # now shows no benchmark and no alpha rather than a wrong one.
    bench_error = None
    if bench_pri:
        try:
            primary_index = get_benchmark_index(bench_pri, chart_start)
            primary_perf  = round(float(primary_index.iloc[-1] - 100), 2)
        except BenchmarkUnavailable as exc:
            bench_error = str(exc)
    if bench_sec:
        try:
            secondary_index = get_benchmark_index(bench_sec, chart_start)
            secondary_perf  = round(float(secondary_index.iloc[-1] - 100), 2)
        except BenchmarkUnavailable as exc:
            bench_error = bench_error or str(exc)
    # Display-only reference lines (e.g. the equal-weight S&P 500 on Le
    # Bâtisseur). Same validation as the benchmarks; one that cannot be trusted
    # is simply left out, and none of them can shorten the portfolio's line.
    extra_indices = []
    for _tk, _lbl in options.get("extra_benchmarks", []):
        try:
            extra_indices.append((_lbl, get_benchmark_index(_tk, chart_start)))
        except BenchmarkUnavailable:
            pass

    # Read NAV series from daily_holdings (real fund accounting).
    # The series naturally starts at 100 on T-1 (the CASH anchor row in DB).
    # Benchmarks also start at 100 on T-1 because history is fetched from T-1
    # and primary_index/secondary_index are normalized to raw.iloc[0] = close[T-1].
    port_index = get_nav_from_holdings(portfolio_id)

    # Align port_index end date with benchmarks' last available date, so the
    # portfolio line can never be longer than the benchmark line (e.g. a
    # weekend row written by the 7/7 cron before yfinance has the index).
    _bench_end_dates = []
    if primary_index is not None and not primary_index.empty:
        _bench_end_dates.append(primary_index.index[-1])
    if secondary_index is not None and not secondary_index.empty:
        _bench_end_dates.append(secondary_index.index[-1])
    if _bench_end_dates and port_index is not None and not port_index.empty:
        _common_end = min(_bench_end_dates)
        port_index = port_index[port_index.index <= _common_end]

    _data_age_days = None
    # Use chart's base-100 method for the headline metric (consistent with chart)
    if port_index is not None and not port_index.empty:
        portfolio_perf = round(float(port_index.iloc[-1] - 100), 2)

        # Benchmark headline + alpha must be read at the SAME date as the
        # portfolio's last row. yfinance history runs to yesterday whatever the
        # state of daily_holdings; when the pipeline stalled (Sep 2026) the site
        # compared a 1-Sep portfolio with an 18-Sep Nasdaq (alpha +2.73% shown
        # vs +4.99% same-date). Same for "Last updated": it is the date of the
        # portfolio data, not the date yfinance answered.
        _port_last = port_index.index[-1]

        def _perf_at(idx):
            if idx is None or idx.empty:
                return None
            s = idx[idx.index <= _port_last]
            return round(float(s.iloc[-1] - 100), 2) if not s.empty else None

        primary_perf   = _perf_at(primary_index)
        secondary_perf = _perf_at(secondary_index)
        last_updated   = _port_last.strftime("%b %d, %Y")
        _data_age_days = (date.today() - _port_last.date()).days

    # No benchmark, no alpha. `portfolio_perf - 0` would have printed the
    # portfolio's own return in the Alpha box.
    alpha = None if primary_perf is None else round(portfolio_perf - primary_perf, 2)

    _n_returns = len(port_index.pct_change().dropna()) if (port_index is not None and not port_index.empty) else 0
    _MIN_DAYS_STATS = 60

    # ── Header ────────────────────────────────────────────────────────────────
    hcol1, hcol2 = st.columns([5, 1])
    with hcol1:
        eyebrow = _EYEBROW.get(portfolio_id, "PAPER PORTFOLIO")
        st.markdown(
            f'<div class="pf-eyebrow">{eyebrow}</div>'
            f'<p class="pf-title" style="font-family:\'Cormorant Garamond\', Georgia, serif !important; '
            f'font-size:3.5rem; font-weight:700; letter-spacing:-1px; '
            f'margin-bottom:0; line-height:1.1;">{portfolio_name}</p>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Inception {inception_date} · {len(positions)} positions · "
            f"Benchmark: {bench_pri_lbl}"
        )
        st.markdown(
            "<div style='font-size:0.7rem; color:#555; margin-top:-0.4rem; line-height:1.4;'>"
            "* Performance calculated from the previous trading day's close, "
            "simulating capital deposited the evening before, ready to invest "
            "at next session's open."
            "</div>",
            unsafe_allow_html=True,
        )
    with hcol2:
        st.markdown(
            f"<div style='text-align:right; padding-top:0.4rem;'>"
            f"<span style='font-size:0.7rem; color:#555;'>Last updated</span><br>"
            f"<span style='font-size:0.82rem; color:#888;'>{last_updated}</span></div>",
            unsafe_allow_html=True,
        )

    # Never let the page look current when the pipeline is behind: a weekend
    # plus Monday morning is 3 days, anything beyond means the nightly
    # refresh has not written for at least one trading day.
    if bench_error:
        st.warning(
            f"Benchmark data unavailable right now ({bench_error}). "
            f"The benchmark and excess return are hidden rather than shown against the "
            f"wrong base date; the portfolio figures are unaffected.",
            icon="⚠️",
        )

    if _data_age_days is not None and _data_age_days > 3:
        st.warning(
            f"Data as of {last_updated} — the nightly update is {_data_age_days} days behind. "
            f"Figures below are correct for that date but not current.",
            icon="⚠️",
        )

    metric_cols = st.columns(4)
    with metric_cols[0]:
        sign = "+" if portfolio_perf >= 0 else ""
        st.metric("Portfolio (inception)", f"{sign}{portfolio_perf:.2f}%")
    with metric_cols[1]:
        s = "+" if (primary_perf or 0) >= 0 else ""
        st.metric(f"{bench_pri_lbl} (inception)",
                  f"{s}{primary_perf:.2f}%" if primary_perf is not None else "—")
    with metric_cols[2]:
        if alpha is None:
            st.metric("Excess Return", "—")
        else:
            a = "+" if alpha >= 0 else ""
            st.metric("Excess Return", f"{a}{alpha:.2f}%",
                      help=f"Portfolio return minus the {bench_pri_lbl} return since inception. "
                           f"Not risk-adjusted: see Jensen's alpha in the Performance section.")
    with metric_cols[3]:
        today_valid = [p for p in positions if p["change_today"] is not None]
        if today_valid:
            # Weight today's moves by the CURRENT allocation (what the Positions
            # table shows), not the cost-basis weight: on drifted books (e.g.
            # Nakamoto ASST 15% at cost vs 24% today) the two differ materially.
            _w_today = sum(p.get("current_weight") or 0 for p in today_valid) or 1
            avg_today = sum((p.get("current_weight") or 0) * p["change_today"] for p in today_valid) / _w_today
            s = "+" if avg_today >= 0 else ""
            st.metric("Today", f"{s}{avg_today:.2f}%")
        else:
            st.metric("Today", "—")

    # ── Performance (shared with Admin via render_performance_chart_section) ──
    with st.expander("Performance", expanded=True):
        render_performance_chart_section(
            portfolio_name=portfolio_name,
            accent_color=accent,
            inception_date=inception_date,
            bench_pri_lbl=bench_pri_lbl,
            bench_sec_lbl=bench_sec_lbl,
            port_index=port_index,
            primary_index=primary_index,
            secondary_index=secondary_index,
            extra_indices=extra_indices,
        )

    st.divider()

    # ── Positions ─────────────────────────────────────────────────────────────
    with st.expander("Positions", expanded=True):
        df = pd.DataFrame(positions)
        df = df.sort_values("current_weight", ascending=False)
        _display_cols = [
            "ticker", "name", "layer", "current_weight", "entry_price", "current_price",
            "perf_pct", "adj_return", "change_today",
            "sector", "geography", "thematic", "thesis_short",
        ]
        if not show_layer_column:
            _display_cols = [c for c in _display_cols if c != "layer"]
        display = df[[c for c in _display_cols if c in df.columns]].rename(columns={
            "ticker":         "Ticker",
            "name":           "Name",
            "layer":          "Layer",
            "current_weight": "Alloc.",
            "entry_price":    "PRU",
            "current_price":  "Price",
            "perf_pct":       "Return %",
            "adj_return":     "Adj. Return %",
            "change_today":   "Today %",
            "sector":         "Sector",
            "geography":      "Geography",
            "thematic":       "Thematic",
            "thesis_short":   "Thesis",
        })
        display = display.drop(columns=["Thesis"], errors="ignore")
        # Collapse internal Layer values into public-facing names if configured
        if layer_map and "Layer" in display.columns:
            display["Layer"] = display["Layer"].apply(lambda v: layer_map.get(v, v))

        def color_signed(col):
            return [
                f"color: {POSITIVE}" if isinstance(v, (int, float)) and v > 0
                else f"color: {NEGATIVE}" if isinstance(v, (int, float)) and v < 0
                else "" for v in col
            ]

        # The spacer and CASH rows are no longer part of the table: st.dataframe
        # prints "None" in every empty numeric cell, and a visitor sorting a
        # column sent CASH into the middle of the positions. Cash is stated in
        # the caption right below the table.
        display_full = display.reset_index(drop=True)

        # st.dataframe prints any missing number as a literal "None", whatever
        # the Styler's na_rep says. Adj. Return % is empty for most lines by
        # design, so it is rendered as text: "—" where there is nothing to add.
        if "Adj. Return %" in display_full.columns:
            display_full["Adj. Return %"] = display_full["Adj. Return %"].map(
                lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) and v == v else "—")

        styled = display_full.style.format({
            "Alloc.":       lambda v: f"{v:.2f}%" if isinstance(v, (int, float)) else "",
            "PRU":          lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "—",
            "Price":        lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "—",
            "Return %": lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else "—",
            "Today %":      lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else "—",
        }, na_rep="—").apply(color_signed, subset=["Return %", "Today %"])
        if "Adj. Return %" in display_full.columns:
            styled = styled.apply(lambda col: [
                f"color: {POSITIVE}" if str(v).startswith("+")
                else f"color: {NEGATIVE}" if str(v).startswith("-")
                else "" for v in col], subset=["Adj. Return %"])

        table_height = 38 + len(display) * 35 + 4
        _col_cfg = {}
        try:   # right-align the text column like the numbers; skip on older Streamlit
            import inspect as _inspect
            if "alignment" in _inspect.signature(st.column_config.TextColumn).parameters:
                _col_cfg["Adj. Return %"] = st.column_config.TextColumn(alignment="right")
        except Exception:
            pass
        st.dataframe(styled, width="stretch", hide_index=True, height=table_height,
                     column_config=_col_cfg or None)
        st.caption(f"Cash / Equivalent — Current: {current_cash_pct:.1f}%")
        if display["Adj. Return %"].notna().any() if "Adj. Return %" in display.columns else False:
            st.caption("Adj. Return %: return on all the capital invested in the position, "
                       "including gains or losses realised on partial sales. Shown only for "
                       "positions that were reduced; for the others it equals Return %.")

        # Research teaser (optional)
        if show_research_teaser:
            st.write("")
            gradient_tint   = _hex_to_rgba(accent, 0.18)  # accent-tinted dark for gradient start
            accent_radial   = _hex_to_rgba(accent, 0.12)
            accent_border_subtle = _hex_to_rgba(accent, 0.25)
            btn_text_color = "#0E1117" if _is_light_color(accent) else "#FFFFFF"
            st.markdown(f"""
<div style="
    background: linear-gradient(135deg, {gradient_tint} 0%, #0E1117 60%);
    border: 1px solid {accent_border_subtle};
    border-radius: 14px;
    padding: 2.2rem 2.4rem;
    margin: 1rem 0 0.5rem 0;
    position: relative;
    overflow: hidden;
">
    <div style="
        position: absolute; top: -60px; right: -60px;
        width: 280px; height: 280px;
        background: radial-gradient(circle, {accent_radial} 0%, transparent 70%);
        border-radius: 50%;
    "></div>
    <div style="font-size:0.7rem; font-weight:700; letter-spacing:2px;
                color:{accent}; text-transform:uppercase; margin-bottom:0.5rem;">
        Research · {papers_label}
    </div>
    <div style="font-size:1.6rem; font-weight:800; letter-spacing:-0.5px; margin-bottom:0.6rem;">
        Stock Papers
    </div>
    <div style="font-size:0.88rem; color:#888; line-height:1.65; max-width:480px;">
        In-depth equity analysis on portfolio positions and market themes.
    </div>
</div>
<a href="/Research" target="_self" style="
    display: inline-block;
    background: {accent};
    color: {btn_text_color};
    font-weight: 800;
    font-size: 0.95rem;
    padding: 0.65rem 1.6rem;
    border-radius: 8px;
    text-decoration: none;
    letter-spacing: 0.2px;
    margin-bottom: 0.5rem;
">Read the papers →</a>
""", unsafe_allow_html=True)

    st.divider()

    # ── Allocation ────────────────────────────────────────────────────────────
    with st.expander("Allocation", expanded=True):
        display_donut = display.copy()
        if current_cash_pct > 0:
            cash_row = pd.DataFrame([{
                "Ticker": "CASH", "Name": "Cash (USD)", "Layer": "Cash", "Alloc.": current_cash_pct,
                "PRU": None, "Price": None, "Return %": None, "Today %": None,
                "Sector": "Cash/Equivalent", "Geography": "USD",
                "Thematic": "Cash/Equivalent",
            }])
            display_alloc = pd.concat([display_donut, cash_row], ignore_index=True)
        else:
            display_alloc = display_donut

        # Render only the donuts requested
        n_donuts = len([d for d in show_donuts if d == "Layer" or d in display_alloc.columns])
        if n_donuts > 0:
            cols = st.columns(n_donuts)
            i = 0
            for donut_type in show_donuts:
                if donut_type == "Layer":
                    if "Layer" in display_alloc.columns:
                        df_layer = display_alloc.copy()
                        df_layer["Layer"] = df_layer["Layer"].replace("Cash", "Cash/Equivalent")
                        grouped = df_layer.groupby("Layer")["Alloc."].sum().reset_index()
                        if not grouped.empty:
                            color_map = {c: _LAYER_COLORS.get(c, "#6B7280") for c in grouped["Layer"].unique()}
                            fig = px.pie(grouped, values="Alloc.", names="Layer", title="Portfolio Layer",
                                         hole=0.52, color="Layer", color_discrete_map=color_map)
                            fig.update_traces(textinfo="percent",
                                              hovertemplate="%{label}: %{value:.2f}%<extra></extra>")
                            fig.update_layout(plot_bgcolor=BG, paper_bgcolor=BG, font=dict(color=TEXT_MID),
                                              margin=dict(l=0, r=0, t=40, b=0),
                                              legend=dict(font=dict(size=11)), title_font_size=14)
                            with cols[i]:
                                st.plotly_chart(fig, width="stretch")
                            i += 1
                elif donut_type in display_alloc.columns:
                    with cols[i]:
                        st.plotly_chart(_donut_chart(display_alloc, donut_type, donut_type),
                                        width="stretch")
                    i += 1

    # ── Risk Analysis (optional) ──────────────────────────────────────────────
    if show_risk_analysis:
        st.divider()
        with st.expander("Risk Analysis", expanded=True):
            if port_index is not None and not port_index.empty:
                # Same trading-day series as the Performance section, so that
                # volatility / VaR here and Sharpe / beta above are computed on
                # ONE series (they diverged until 2026-09-19: 7/7 calendar rows
                # here understated vol by ~15% next to a Sharpe on trading days).
                _port_index_td = align_to_equity_calendar(port_index, primary_index, secondary_index)
                port_ret = daily_returns(_port_index_td)
                # Benchmark volatility for the portfolio's own benchmark, over the
                # same intervals (was the secondary benchmark until 2026-09-23).
                _, bench_ret = aligned_returns(_port_index_td, primary_index)

                ra1, ra2, ra3, ra4 = st.columns(4)
                _stats_ready = _n_returns >= _MIN_DAYS_STATS
                _stats_help = f"Available after {_MIN_DAYS_STATS} trading days (currently {_n_returns})"
                with ra1:
                    if _stats_ready:
                        pv = annualized_volatility(port_ret)
                        st.metric("Portfolio Volatility (ann.)",
                                  f"{pv:.1f}%" if pv is not None else "—",
                                  help="Annualized standard deviation of daily returns")
                    else:
                        st.metric("Portfolio Volatility (ann.)", "—", help=_stats_help)
                with ra2:
                    sec_vol_label = f"{bench_pri_lbl} Volatility (ann.)" if bench_pri_lbl else "Benchmark Volatility (ann.)"
                    if _stats_ready:
                        sv = annualized_volatility(bench_ret)
                        st.metric(sec_vol_label,
                                  f"{sv:.1f}%" if sv is not None else "—")
                    else:
                        st.metric(sec_vol_label, "—", help=_stats_help)
                with ra3:
                    if _stats_ready:
                        v = var_95(port_ret)
                        st.metric("VaR 95% (1-day)",
                                  f"{v:.2f}%" if v is not None else "—",
                                  help="Historical VaR: worst daily loss in 95% of scenarios")
                    else:
                        st.metric("VaR 95% (1-day)", "—", help=_stats_help)
                with ra4:
                    top3 = display.nlargest(3, "Alloc.")[["Ticker", "Alloc."]]
                    top3_pct = top3["Alloc."].sum()
                    st.metric("Top 3 Concentration", f"{top3_pct:.1f}%",
                              help=" · ".join(top3["Ticker"].tolist()) + " (current weights)")

                corr_mode = st.radio(
                    "Correlation window",
                    ["Trailing 12 months", "Since inception"],
                    horizontal=True, index=0, label_visibility="collapsed",
                )
                use_inception = corr_mode == "Since inception"
                h_for_corr = history if use_inception else history_corr
                corr     = correlation_matrix(h_for_corr, positions, inception=use_inception)
                avg_corr = avg_pairwise_correlation(h_for_corr, positions, inception=use_inception)
                if avg_corr is not None:
                    if avg_corr < 0.3:
                        corr_label, corr_color = "Low — well diversified", POSITIVE
                    elif avg_corr < 0.6:
                        corr_label, corr_color = "Moderate", TRIM
                    else:
                        corr_label, corr_color = "High — concentrated risk", NEGATIVE
                    st.markdown(
                        f"**Avg Pairwise Correlation** &nbsp; "
                        f"<span style='font-size:1.6rem; font-weight:800;'>{avg_corr}</span>"
                        f"&nbsp; <span style='color:{corr_color}; font-size:0.85rem;'>{corr_label}</span>"
                        f"<br><span style='font-size:0.75rem; color:#666;'>"
                        f"Average correlation between all position pairs. "
                        f"Closer to 0 = positions move independently (better diversification). "
                        f"Closer to 1 = positions move together (concentrated risk)."
                        f"</span>",
                        unsafe_allow_html=True,
                    )

                if not corr.empty:
                    label = "trailing 12 months" if not use_inception else "since inception"
                    st.markdown(f"**Correlation Matrix** (daily returns, {label})")
                    st.markdown(f"""
<style>
@media (max-width: 768px) and (orientation: portrait) {{
    .corr-rotate-hint {{ display: block !important; }}
}}
.corr-rotate-hint {{ display: none; }}
</style>
<div class="corr-rotate-hint" style="font-size:0.75rem; color:{accent}; margin-bottom:0.5rem;">
    Rotate your screen for a better view of the matrix.
</div>
""", unsafe_allow_html=True)
                    fig_corr = go.Figure(data=go.Heatmap(
                        z=corr.values,
                        x=corr.columns.tolist(),
                        y=corr.index.tolist(),
                        colorscale=[[0.0, NEGATIVE], [0.5, BG], [1.0, ACCENT]],
                        zmin=-1, zmax=1,
                        text=corr.values.round(2),
                        texttemplate="%{text}",
                        textfont=dict(size=11),
                        hovertemplate="%{y} / %{x}: %{z:.2f}<extra></extra>",
                    ))
                    fig_corr.update_layout(
                        plot_bgcolor=BG, paper_bgcolor=BG,
                        font=dict(color=TEXT_MID),
                        height=380,
                        margin=dict(l=0, r=0, t=10, b=0),
                        xaxis=dict(side="bottom"),
                    )
                    st.plotly_chart(fig_corr, width="stretch")

    # ── Documents (portfolio-specific only) ───────────────────────────────────
    if show_documents_section:
        all_docs = [p for p in get_research() if p["status"] in ("published", "locked")]
        portfolio_docs = [d for d in all_docs if d.get("portfolio_id") == portfolio_id]
        if portfolio_docs:
            st.divider()
            with st.expander("Documents", expanded=True):
                for d in portfolio_docs:
                    c1, c2 = st.columns([6, 1])
                    with c1:
                        st.markdown(
                            f"**{d['title']}**  \n"
                            f"<span style='font-size:0.78rem; color:#555;'>{d.get('published_at','')}"
                            f"{(' · ' + (d.get('summary') or '')[:80] + '…') if d.get('summary') else ''}</span>",
                            unsafe_allow_html=True,
                        )
                    with c2:
                        if d.get("file_url") and d["status"] == "published":
                            st.link_button("Open →", d["file_url"])
                        elif d["status"] == "locked":
                            st.markdown(
                                "<span style='display:inline-flex; align-items:center; gap:6px; "
                                "color:#6B7280; font-size:0.8rem; font-weight:600; letter-spacing:0.5px;'>"
                                "<svg xmlns='http://www.w3.org/2000/svg' width='18' height='18' "
                                "viewBox='0 0 24 24' fill='none' stroke='#6B7280' stroke-width='2' "
                                "stroke-linecap='round' stroke-linejoin='round'>"
                                "<rect x='3' y='11' width='18' height='11' rx='2' ry='2'></rect>"
                                "<path d='M7 11V7a5 5 0 0 1 10 0v4'></path></svg>RESTRICTED</span>",
                                unsafe_allow_html=True,
                            )
                    st.write("")

    # ── Bottom disclaimer ─────────────────────────────────────────────────────
    st.markdown("""
<div class="disclaimer">
<strong>Disclaimer:</strong> This is a paper trading simulation and does not involve real financial assets.
All content published here is for educational and informational purposes only and does not constitute
financial, investment, or legal advice. I am not a registered financial advisor. The author may hold
personal positions in securities mentioned on this page; readers should consider this potential conflict
of interest. Investing involves significant risk, including the possible loss of principal. Always
conduct your own due diligence before making any investment decisions.
</div>
""", unsafe_allow_html=True)
