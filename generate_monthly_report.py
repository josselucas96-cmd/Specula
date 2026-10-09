"""Generate Le Visionnaire / Le Nakamoto Monthly Report (HTML).

Usage:
    python generate_monthly_report.py visionnaire --month 2026-06
    python generate_monthly_report.py --all --month 2026-08 --out reports/drafts
    python generate_monthly_report.py nakamoto --date 2026-05-31

Commentary lives OUTSIDE the script, one TOML per portfolio and month:
    reports/comments/<portfolio_id>/<YYYY-MM>.toml
        market_comment = ["...", "..."]
        mgmt_comment   = ["...", "..."]   # {pf_mtd_pct} {bench_mtd_pct} {alpha_mtd_pct} {cash_pct} {bench_pri_lbl} are filled in
When the file is missing the report is rendered as a DRAFT (visible banner)
so a draft can never be mistaken for a published report. Secrets come from
.streamlit/secrets.toml locally or SUPABASE_URL / SUPABASE_KEY in CI.

The HTML uses the portfolio's accent color from the `portfolios` table.
Output goes to: 01_THE PORTFOLIO PROJECT/Les portefeuilles/<Portfolio Name>/reports/.

To preview/export PDF: open the HTML in Chrome → Ctrl+P → 'Save as PDF',
A4 portrait, no margins, no headers/footers, enable background graphics.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import base64
import io
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from supabase import create_client

# ── Common palette ────────────────────────────────────────────────────────────
DARK_BG     = "#0E1117"
DARK_PANEL  = "#13181F"
TEXT_LIGHT  = "#F9FAFB"
TEXT_MID    = "#9CA3AF"
TEXT_DIM    = "#6B7280"
POSITIVE    = "#00D09C"
NEGATIVE    = "#FF4B4B"

# ── Per-portfolio configuration ───────────────────────────────────────────────
CONFIGS = {
    "visionnaire": {
        "report_date":      "2026-05-31",
        "subfolder":        "Le Visionnaire",
        "horizon":          "5 years minimum",
        "strategy_summary": "High-conviction equity (not UCITS)",
        "objective": (
            "Le Visionnaire is an unconstrained international equity paper portfolio focused "
            "on high-conviction, concentrated positions. It aims for long-term outperformance of "
            "the Nasdaq 100 over rolling 5-year horizons, through rigorous selection of companies "
            "that compound shareholder value through innovation and structural differentiation."
        ),
        "market_comment": [
            "May 2026 delivered a strong tape for US equities. The Nasdaq 100 led the rally on renewed enthusiasm for the AI capex cycle and a broadening of the Magnificent 7 outperformance into the second tier of large-cap tech.",
            "The Fed kept rates unchanged but the tone of the May minutes turned slightly more dovish, with the median Committee member now signaling one cut into year-end and softer-than-expected core PCE giving markets confidence to reprice the long end lower.",
            "Q1 earnings season closed with a positive-surprise ratio above historical averages. Guidance dispersion remains wide: AI infrastructure names lifted prints, while consumer discretionary continued to flag a more discerning end-customer.",
        ],
        "mgmt_comment": [
            "Le Visionnaire closes May at <strong>+23.68% since inception</strong> (April 13), with a monthly performance of <strong>{pf_mtd_pct}%</strong> versus <strong>{bench_mtd_pct}%</strong> for the {bench_pri_lbl} — alpha of <strong>{alpha_mtd_pct}pp</strong> for the month. The portfolio's high-Beta exposure to the AI and consumer-growth themes captured the broad market tailwind. Cash at month-end: <strong>{cash_pct}%</strong>.",
            "<strong>Rebalance #1 (May 14) — profit-taking on winners.</strong> Partial trims on TSLA (12% → 10%), AMD (7.6% → 5%), and RKLB (8% → 5%) to recycle capital into Celsius (CELH 7.2% → 10%) and Hims (HIMS 8.4% → 10%), both reinforced on unchanged fundamentals after price weakness. Cash rose to 14.2%.",
            "<strong>Rebalance #2 (May 26) — cash deployment.</strong> Four pure additions on validated theses: Nu Holdings (4%, LatAm fintech), SoFi (4%, US neo-bank, GAAP-profitable since 2024), NIO (1%, tactical EV China), and Super Micro (1%, AI hardware sized small given the risk). Cash drawn down from 14.5% to 4.5%.",
            "<strong>Construction discipline.</strong> The portfolio operates at the top of its position-count range (20 names including Moonshots) with no hard caps reached on any individual line. Conviction-driven sizing is being respected: NVDA, CELH, and HIMS sit at the largest weights, expressing the highest-confidence theses.",
        ],
    },
    "batisseur": {
        "report_date":      "2026-07-31",   # placeholder, update at launch
        "subfolder":        "Le Bâtisseur",
        "horizon":          "5 years minimum",
        "strategy_summary": "Quality compounders (UCITS 5/10/40-inspired)",
        "objective": (
            "Le Bâtisseur is an unconstrained equity paper portfolio focused on quality compounding "
            "and capital allocation discipline. The core targets high-grade businesses, exceptional "
            "capital allocators, and category leaders, paired with a tactical layer of thematic plays "
            "and select opportunities. The portfolio is designed for long-term capital growth with "
            "rigorous risk management."
        ),
        "market_comment": [
            "Placeholder market commentary — to be drafted at portfolio launch.",
        ],
        "mgmt_comment": [
            "Le Bâtisseur is in pre-launch phase. The Investment Policy Statement and initial composition are under construction.",
        ],
    },
    "nakamoto": {
        "report_date":      "2026-05-02",
        "subfolder":        "Le Nakamoto",
        "horizon":          "4 years minimum",
        "strategy_summary": "Digital Asset Treasuries (DAT)",
        "objective": (
            "Le Nakamoto is a digital-asset-treasury (DAT) paper portfolio designed to deliver "
            "amplified Bitcoin exposure through actively managed equity positions in companies "
            "holding BTC on their balance sheet. The portfolio aims for outperformance of Bitcoin spot "
            "over rolling 3- to 5-year horizons through rigorous DAT selection and tactical "
            "allocation between BTC DATs and an Income overlay."
        ),
        "market_comment": [
            "The Bitcoin ecosystem opened May 2026 with continued institutional flows and consolidating mNAV ratios across the DAT segment. BTC trades near the $100k mark, with macro liquidity conditions remaining supportive.",
            "Treasury operations across the DAT universe continue to mature. ATM-efficient programs, perpetual preferred issuances, and active capital structures increasingly separate the operationally robust names from passive accumulators.",
            "Geographic dispersion is widening. European and LatAm DAT segments are establishing distinct identities, beyond the US-centric dominance of recent quarters.",
        ],
        "mgmt_comment": [
            "Le Nakamoto's inception was May 1. Two trading days of history is too short for conclusions. The portfolio enters its first observation phase.",
            "<strong>Initial mode: Risk-on (default).</strong> ~100% allocation to BTC DATs, no Income overlay activated. Five anchor positions plus three exploratory names.",
            "<strong>Hard cap discipline.</strong> Single-position cap at 35% is respected at inception. The largest holding sits at 27%. Rotation will be triggered by mNAV cycle signals, not by calendar.",
            "<strong>Selection framework cleared.</strong> All eight names cleared the five-dimensional selection framework. None require operational re-evaluation in the first month.",
        ],
    },
}

# Project paths
PROJECT_ROOT = Path(r"C:\Users\USER\Desktop\Projet Claude\Claude Racine\01_THE PORTFOLIO PROJECT\Les portefeuilles")

# ── Data layer ────────────────────────────────────────────────────────────────
import os
_SECRETS = Path(__file__).resolve().parent / ".streamlit" / "secrets.toml"
if _SECRETS.exists():
    with open(_SECRETS, "rb") as f:
        secrets = tomllib.load(f)
    sb = create_client(secrets["supabase_url"], secrets["supabase_key"])
elif os.environ.get("SUPABASE_URL") and (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")):  # CI
    sb = create_client(os.environ["SUPABASE_URL"], os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"])
else:  # importable without credentials (unit tests of pure helpers); data calls will fail loudly
    sb = None

REPO_ROOT = Path(__file__).resolve().parent
COMMENTS_DIR = REPO_ROOT / "reports" / "comments"
DRAFT_MARKET = ["DRAFT — market commentary pending."]
DRAFT_MGMT = [
    "DRAFT — management commentary pending. Month-to-date performance: <strong>{pf_mtd_pct}%</strong> "
    "vs <strong>{bench_mtd_pct}%</strong> for the {bench_pri_lbl} (alpha <strong>{alpha_mtd_pct}pp</strong>). "
    "Cash at month-end: <strong>{cash_pct}%</strong>.",
]


def month_end(month: str) -> str:
    """'2026-06' -> '2026-06-30'."""
    return str(pd.Timestamp(month + "-01") + pd.offsets.MonthEnd(0))[:10]


EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]


def report_title(portfolio_name: str, month: str) -> str:
    """Title of the published report on the site: 'September 2026 — Monthly Report — Le Bâtisseur'.
    English like the report itself (French month names until 2026-10-09)."""
    return f"{EN_MONTHS[int(month[5:7]) - 1]} {month[:4]} — Monthly Report — {portfolio_name}"


def load_comments(portfolio_id: str, report_date: str) -> tuple[list, list, bool]:
    """(market_comment, mgmt_comment, is_draft) for that portfolio/month."""
    path = COMMENTS_DIR / portfolio_id / f"{report_date[:7]}.toml"
    if path.exists():
        with open(path, "rb") as f:
            c = tomllib.load(f)
        market, mgmt = list(c.get("market_comment") or []), list(c.get("mgmt_comment") or [])
        if market and mgmt:
            return market, mgmt, False
    return DRAFT_MARKET, DRAFT_MGMT, True


def fetch_portfolio(portfolio_id):
    return sb.table("portfolios").select("*").eq("id", portfolio_id).execute().data[0]


def fetch_positions(portfolio_id):
    return (
        sb.table("positions")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .eq("is_active", True)
        .execute()
        .data
    )


def fetch_positions_as_of(portfolio_id, as_of_date: str):
    """Positions that were ACTIVE on `as_of_date`. Used for retroactive monthly
    reports — picks up positions later closed (exit_date > as_of) and excludes
    positions opened after as_of."""
    rows = (
        sb.table("positions")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .lte("entry_date", as_of_date)
        .execute()
        .data
    )
    # Exclude:
    # - positions exited on/before as_of_date
    # - "ghost" positions (weight=0, no exit_date) — desactivated but never properly closed
    return [
        r for r in rows
        if (r.get("exit_date") is None or str(r["exit_date"]) > as_of_date)
        and float(r.get("weight") or 0) > 0
    ]


def fetch_prices_as_of(tickers: tuple, as_of_date: str, portfolio_id: str) -> dict:
    """Prices at as_of_date, read from `daily_holdings` (frozen close prices)."""
    if not tickers:
        return {}
    rows = (
        sb.table("daily_holdings")
        .select("ticker, price")
        .eq("portfolio_id", portfolio_id)
        .eq("date", as_of_date)
        .in_("ticker", list(tickers))
        .execute()
        .data
    )
    return {r["ticker"]: float(r["price"]) for r in rows if r.get("price")}


def fetch_t1_anchor(portfolio_id: str) -> str | None:
    """First date in daily_holdings = T-1 anchor (day before inception, cash-only)."""
    rows = (sb.table("daily_holdings").select("date")
            .eq("portfolio_id", portfolio_id)
            .order("date").limit(1).execute().data)
    return rows[0]["date"] if rows else None


def fetch_nav_and_cash_at(portfolio_id: str, as_of_date: str) -> tuple[float, float]:
    """Returns (nav_total, cash_value) from daily_holdings at as_of_date.
    Both in absolute $. Cash % = cash / nav * 100."""
    rows = (
        sb.table("daily_holdings")
        .select("ticker, value")
        .eq("portfolio_id", portfolio_id)
        .eq("date", as_of_date)
        .execute().data
    )
    if not rows:
        return 0.0, 0.0
    nav = sum(float(r["value"]) for r in rows)
    cash = next((float(r["value"]) for r in rows if r["ticker"] == "CASH"), 0.0)
    return nav, cash


def build_nav_series_from_holdings(portfolio_id: str, end_date: str) -> pd.Series:
    """True fund-accounting NAV time series from `daily_holdings`. Returns a
    date-indexed Series in base 100 at the first row (the T-1 anchor = initial
    capital). Handles Supabase 1000-row pagination."""
    rows = []
    offset = 0
    PAGE = 1000
    while True:
        chunk = (
            sb.table("daily_holdings")
            .select("date, value")
            .eq("portfolio_id", portfolio_id)
            .lte("date", end_date)
            # Stable total sort so page boundaries don't double-count rows
            # (unordered pagination duplicates boundary rows past 1000 → NAV spike).
            .order("date").order("ticker")
            .range(offset, offset + PAGE - 1)
            .execute().data
        )
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        offset += PAGE
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = df["value"].astype(float)
    daily_nav = df.groupby("date")["value"].sum().sort_index()
    if daily_nav.empty:
        return pd.Series(dtype=float)
    base = daily_nav.iloc[0]
    return daily_nav / base * 100


def fetch_history(tickers: tuple, start: str, end: str, benchmarks: tuple) -> pd.DataFrame:
    all_t = list(set(list(tickers) + list(benchmarks)))
    raw = yf.download(all_t, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw["Close"]
    if raw.index.tz is not None:
        raw.index = raw.index.tz_localize(None)
    raw.index = pd.to_datetime(raw.index.date)
    return raw.dropna(how="all")


def fetch_live_prices(tickers: tuple) -> dict:
    out = {}
    for t in tickers:
        try:
            out[t] = float(yf.Ticker(t).fast_info.last_price)
        except Exception:
            out[t] = None
    return out


# ── Computations ──────────────────────────────────────────────────────────────
def build_index(history: pd.DataFrame, positions: list) -> pd.Series:
    total_w = sum(p["weight"] for p in positions) or 1
    portfolio = pd.Series(0.0, index=history.index)
    contributed = False
    for p in positions:
        t = p["ticker"]
        if t not in history.columns:
            continue
        w = p["weight"] / total_w
        s = history[t].dropna()
        if s.empty:
            continue
        ed = pd.Timestamp(p["entry_date"])
        after = s[s.index >= ed]
        if after.empty:
            continue
        base = after.iloc[0]
        norm_after = after / base * 100
        before_idx = s.index[s.index < ed]
        norm_before = pd.Series(100.0, index=before_idx)
        full = pd.concat([norm_before, norm_after]).reindex(history.index).ffill().bfill()
        portfolio += full * w
        contributed = True
    return portfolio if contributed else pd.Series(dtype=float)


def compute_position_metrics(positions: list, prices: dict) -> tuple[list, float]:
    total_w = sum(p["weight"] for p in positions) or 1
    for p in positions:
        cp = prices.get(p["ticker"])
        p["current_price"] = cp
        if cp and p["entry_price"]:
            p["perf_pct"] = round((cp - p["entry_price"]) / p["entry_price"] * 100, 2)
            p["contribution"] = round(p["weight"] * p["perf_pct"] / total_w, 3)
        else:
            p["perf_pct"] = None
            p["contribution"] = None

    initial_cash = max(0.0, 100.0 - sum(p["weight"] for p in positions))
    for p in positions:
        if p.get("current_price") and p.get("entry_price"):
            p["current_value"] = p["weight"] * (p["current_price"] / p["entry_price"])
        else:
            p["current_value"] = p["weight"]
    total_cv = sum(p["current_value"] for p in positions) + initial_cash
    for p in positions:
        p["current_weight"] = round(p["current_value"] / total_cv * 100, 2)
    cash_pct = round(initial_cash / total_cv * 100, 1)
    return positions, cash_pct


def fetch_holdings_window(portfolio_id: str, start_date: str, end_date: str) -> list:
    """Every daily_holdings row of one portfolio between two dates, paginated."""
    rows, offset, PAGE = [], 0, 1000
    while True:
        chunk = (
            sb.table("daily_holdings")
            .select("date, ticker, shares, price, value")
            .eq("portfolio_id", portfolio_id)
            .gte("date", start_date).lte("date", end_date)
            .order("date").order("ticker")
            .range(offset, offset + PAGE - 1)
            .execute().data
        )
        rows += chunk
        if len(chunk) < PAGE:
            return rows
        offset += PAGE


# Share-count ratios a corporate action produces. A trade lands on these only
# by coincidence, and the value test below separates the two cases anyway.
SPLIT_RATIOS = (2, 3, 4, 5, 6, 8, 10, 20, 25, 50, 100)


def _looks_like_split(share_ratio: float, value_ratio: float) -> bool:
    """True when the share count changed by a whole factor and the position's
    value did not: a corporate action, not money moving in or out.

    Both conditions are needed. Doubling a position is also a share ratio of 2,
    but it doubles the value, so the value test rejects it. And testing only
    that value is preserved is not enough either: Capital B's 10-for-1 reverse
    split landed on a day the stock fell 7.2%, which a tolerance tight enough to
    be safe would have read as a sale of 117,614 shares — it credited the line
    with +112pp of a +45% month in the first run of the August report.
    """
    if abs(value_ratio - 1.0) > 0.30:        # a day's move on a volatile name
        return False
    return any(abs(share_ratio - r) / r < 0.02 or abs(share_ratio - 1.0 / r) * r < 0.02
               for r in SPLIT_RATIOS)


def compute_mtd_attribution(positions: list, portfolio_id: str, report_date: str) -> None:
    """Adds p['perf_mtd_pct'] and p['contribution_mtd'] (in pp of NAV start) to
    each position in-place, read from the ledger and nothing else.

    Contribution = (value at month-end - value at month-start - money put in
    during the month) / NAV at month-start. Every term comes from
    daily_holdings, which carries shares AND price on one consistent basis for
    every day, so the figure reconciles with the NAV series by construction.

    The previous version multiplied the position's CURRENT share count by the
    move from `entry_price`, which broke in two ways that both showed up when
    the retroactive reports were first generated (2026-09-23):

      * in a portfolio's inception month there is no month-start price, so the
        basis fell back to `entry_price` while the share count came from the
        as-of reconstruction — Le Nakamoto's May report credited Capital B with
        -9.19pp when the ledger says the position cost 2.2pp;
      * after a corporate action the two are on different bases entirely: a 1:10
        split multiplies the share count and divides the price, so the product
        is off by an order of magnitude.

    A split is recognised here rather than assumed away: when the share count
    changes but shares x price does not, no money moved, so it is not a flow.
    """
    rd = pd.Timestamp(report_date)
    month_start = (rd - pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d")

    nav_start, _ = fetch_nav_and_cash_at(portfolio_id, month_start)
    if nav_start <= 0:
        # Inception month: the portfolio starts at its initial capital.
        nav_start = 1_000_000.0

    rows = fetch_holdings_window(portfolio_id, month_start, report_date)
    by_ticker: dict[str, list] = {}
    for r in rows:
        if r["ticker"] == "CASH" or not r.get("price"):
            continue
        by_ticker.setdefault(r["ticker"], []).append(r)

    def line_pnl(series: list) -> tuple[float, float]:
        """(P&L in dollars, cumulative split factor) for one line over the window."""
        flow = 0.0            # money put into (or taken out of) the line
        split_factor = 1.0    # cumulative share multiplier from corporate actions
        for prev, cur in zip(series, series[1:]):
            s0, s1 = float(prev["shares"] or 0), float(cur["shares"] or 0)
            p0, p1 = float(prev["price"] or 0), float(cur["price"] or 0)
            if s0 <= 0 or p0 <= 0 or abs(s1 - s0) < 1e-9:
                continue
            if _looks_like_split(s1 / s0, (s1 * p1) / (s0 * p0)):
                split_factor *= s1 / s0
            else:
                flow += (s1 - s0) * p1
        v_end = float(series[-1]["value"]) if series[-1]["date"] == report_date else 0.0
        if series[-1]["date"] != report_date:
            flow -= float(series[-1]["value"])        # the line was sold out
        return v_end - float(series[0]["value"]) - flow, split_factor

    for p in positions:
        series = sorted(by_ticker.get(p["ticker"], []), key=lambda r: r["date"])
        if len(series) < 2 or series[-1]["date"] != report_date:
            p["perf_mtd_pct"] = None
            p["contribution_mtd"] = None
            continue

        pnl, split_factor = line_pnl(series)
        p["contribution_mtd"] = round(pnl / nav_start * 100, 3)

        # A 1:10 split multiplies the share count and divides the price, so the
        # month-start price has to be put on the post-split basis to compare.
        p_start = float(series[0]["price"]) / split_factor
        p_end = float(series[-1]["price"])
        p["perf_mtd_pct"] = round((p_end / p_start - 1) * 100, 2) if p_start > 0 else None

    # Reconciliation. Summed over every line the book held during the month —
    # including the ones sold out, which no longer appear in the table — the
    # contributions must add up to the NAV's own move. Both attribution bugs
    # found on 2026-09-23 would have failed this check loudly instead of
    # printing a plausible-looking table.
    nav_by_date: dict[str, float] = {}
    for r in rows:
        nav_by_date[r["date"]] = nav_by_date.get(r["date"], 0.0) + float(r["value"] or 0)
    if month_start in nav_by_date and report_date in nav_by_date:
        moved = (nav_by_date[report_date] - nav_by_date[month_start]) / nav_start * 100
        total = sum(line_pnl(sorted(s, key=lambda r: r["date"]))[0]
                    for s in by_ticker.values() if len(s) >= 2) / nav_start * 100
        if abs(total - moved) > 1.0:
            print(f"  WARN: contributions add up to {total:+.2f}pp but the NAV moved "
                  f"{moved:+.2f}pp — attribution does not reconcile.", flush=True)


def aggregate_alloc(positions: list, key: str, cash_pct: float) -> list[tuple[str, float]]:
    df = pd.DataFrame(positions)
    if key not in df.columns:
        return []
    grouped = df.groupby(key)["current_weight"].sum().reset_index()
    grouped = grouped.sort_values("current_weight", ascending=False)
    out = [(row[key] or "—", round(row["current_weight"], 1)) for _, row in grouped.iterrows()]
    if cash_pct > 0:
        out.append(("Cash", cash_pct))
    return out


def max_drawdown(series: pd.Series) -> float | None:
    if series.empty:
        return None
    peak = series.cummax()
    dd = (series - peak) / peak * 100
    return round(dd.min(), 2)


# ── Chart ─────────────────────────────────────────────────────────────────────
def generate_chart_b64(history, port_index, portfolio_name, benchmarks: list[tuple[str, str, str]],
                      accent: str) -> str:
    """benchmarks: list of (ticker, label, line_style) tuples."""
    fig, ax = plt.subplots(figsize=(8.5, 3.4), dpi=130)
    fig.patch.set_facecolor(DARK_BG)
    ax.set_facecolor(DARK_BG)

    ax.plot(port_index.index, port_index.values, color=accent, linewidth=2.4,
            label=portfolio_name, zorder=3)

    grey_shades = ["#9CA3AF", "#6B7280"]
    for i, (ticker, label, style) in enumerate(benchmarks):
        if ticker not in history.columns:
            continue
        s = history[ticker].dropna()
        if s.empty:
            continue
        # Anchor the benchmark on the portfolio's own base date. Normalising on
        # s.iloc[0] silently rebased the line whenever Yahoo answered the batch
        # download with a truncated column (see utils.market.get_benchmark_index
        # and the 2026-09-23 incident): the PDF then showed a benchmark starting
        # mid-chart at 100 and an inception return that was simply false.
        base_slice = s[s.index <= port_index.index[0]]
        if base_slice.empty:
            print(f"  WARN: {ticker} has no close on or before {port_index.index[0].date()} "
                  f"— benchmark line dropped from the chart rather than rebased.")
            continue
        idx = s[s.index >= base_slice.index[-1]] / float(base_slice.iloc[-1]) * 100
        ax.plot(idx.index, idx.values, color=grey_shades[i % 2], linewidth=1.3,
                linestyle=style, label=label, zorder=2 - i)

    ax.axhline(100, color="#374151", linestyle="--", linewidth=0.6, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#374151")
    ax.spines["bottom"].set_color("#374151")
    ax.tick_params(colors=TEXT_MID, labelsize=8)
    ax.grid(True, color="#1F2937", linewidth=0.5, alpha=0.5, zorder=0)
    ax.set_ylabel("Base 100", color=TEXT_MID, fontsize=8)
    ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=TEXT_LIGHT)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=DARK_BG, bbox_inches="tight", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


# ── HTML rendering ────────────────────────────────────────────────────────────
def render_html(ctx: dict) -> str:
    pf = ctx["portfolio"]
    cfg = ctx["config"]
    accent = pf.get("color_primary") or "#A78BFA"
    border = f"rgba({int(accent[1:3], 16)}, {int(accent[3:5], 16)}, {int(accent[5:7], 16)}, 0.18)"

    eyebrow_text = {
        "visionnaire": "HIGH CONVICTION EQUITY  ·  PAPER PORTFOLIO",
        "nakamoto":    "DIGITAL ASSET TREASURIES  ·  PAPER PORTFOLIO",
        "batisseur":   "QUALITY COMPOUNDERS  ·  PAPER PORTFOLIO",
    }.get(ctx["portfolio_id"], "PAPER PORTFOLIO")

    period_label = pd.Timestamp(cfg["report_date"]).strftime("%B %Y")
    horizon  = cfg["horizon"]

    css = f"""
    @page {{ size: A4 portrait; margin: 0; }}
    * {{ box-sizing: border-box;
         -webkit-print-color-adjust: exact;
         print-color-adjust: exact; }}
    html, body {{
        margin: 0; padding: 0;
        background: {DARK_BG};
        color: {TEXT_LIGHT};
        font-family: 'Helvetica Neue', 'Avenir Next', Avenir, Arial, sans-serif;
        font-size: 9.5px;
        line-height: 1.45;
    }}
    .page {{
        background: {DARK_BG};
        padding: 14mm 12mm;
        page-break-after: always;
        page-break-inside: avoid;
        width: 210mm;
        height: 297mm;
        margin: 0 auto;
        overflow: hidden;
    }}
    .page:last-child {{ page-break-after: auto; }}
    h1, h2, h3 {{ font-family: 'Cormorant Garamond', Georgia, serif; margin: 0; color: {TEXT_LIGHT}; }}

    .top-band {{
        border-top: 2px solid {accent};
        padding-top: 6px;
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 12px;
        font-size: 9px;
        color: {TEXT_MID};
        letter-spacing: 0.5px;
    }}
    .top-band b {{ color: {accent}; letter-spacing: 1px; }}

    .header-block {{ display: flex; justify-content: space-between; gap: 18px; margin-bottom: 12px; }}
    .header-left {{ flex: 1; }}
    .header-right {{ width: 200px; padding-left: 14px; border-left: 1px solid {border}; }}

    .eyebrow {{
        font-size: 8px;
        font-weight: 700;
        letter-spacing: 3px;
        color: {TEXT_MID};
        text-transform: uppercase;
        margin-bottom: 4px;
    }}
    .pf-title {{
        font-family: 'Cormorant Garamond', Georgia, serif;
        font-size: 28px;
        font-weight: 700;
        letter-spacing: -0.5px;
        color: {TEXT_LIGHT};
        line-height: 1;
        margin-bottom: 2px;
    }}
    .pf-subtitle {{
        font-family: 'Cormorant Garamond', Georgia, serif;
        font-size: 13px;
        font-style: italic;
        color: {accent};
        margin-bottom: 8px;
    }}
    .meta-row {{
        font-size: 8.5px;
        color: {TEXT_MID};
        letter-spacing: 0.4px;
        text-transform: uppercase;
    }}
    .meta-row b {{ color: {TEXT_LIGHT}; font-weight: 700; }}

    .risk-box {{
        background: {DARK_PANEL};
        border: 1px solid {border};
        border-radius: 4px;
        padding: 6px 8px;
        font-size: 8px;
    }}
    .risk-row {{ display: flex; gap: 4px; margin-top: 4px; }}
    .risk-cell {{
        flex: 1; text-align: center;
        background: #1A1F2A; padding: 3px 0;
        font-size: 8.5px; font-weight: 700;
        border-radius: 2px;
    }}
    .risk-cell.active {{ background: {accent}; color: {DARK_BG}; }}
    .risk-label {{
        display: flex; justify-content: space-between;
        font-size: 7.5px; color: {TEXT_DIM};
        text-transform: uppercase; letter-spacing: 0.5px;
        margin-bottom: 3px;
    }}
    .meta-table {{
        width: 100%;
        border-collapse: collapse;
        margin-top: 6px;
        font-size: 8.5px;
    }}
    .meta-table td {{ padding: 2px 0; color: {TEXT_MID}; }}
    .meta-table td:last-child {{ text-align: right; color: {TEXT_LIGHT}; font-weight: 600; }}

    .section-label {{
        color: {accent};
        text-transform: uppercase;
        letter-spacing: 1.8px;
        font-size: 8.5px;
        font-weight: 700;
        margin: 12px 0 5px 0;
        border-bottom: 1px solid {border};
        padding-bottom: 3px;
    }}
    .objective {{
        font-size: 9.5px;
        color: {TEXT_LIGHT};
        line-height: 1.55;
        margin-bottom: 4px;
    }}

    .two-col {{ display: flex; gap: 14px; margin-bottom: 6px; }}
    .col {{ flex: 1; }}
    .bullet-list {{ list-style: none; padding: 0; margin: 0; }}
    .bullet-list li {{
        position: relative;
        padding-left: 12px;
        margin-bottom: 4px;
        color: {TEXT_LIGHT};
        font-size: 9px;
        line-height: 1.5;
    }}
    .bullet-list li::before {{
        content: "▸";
        color: {accent};
        position: absolute;
        left: 0;
        font-size: 8px;
        top: 1px;
    }}
    .bullet-list li strong {{ color: {accent}; font-weight: 700; }}

    .chart-block {{ margin-top: 6px; padding: 4px 0; }}
    .chart-block img {{ width: 100%; display: block; }}

    .perf-table {{
        width: 100%;
        border-collapse: collapse;
        margin-top: 6px;
        font-size: 9px;
    }}
    .perf-table th {{
        background: {DARK_PANEL};
        color: {accent};
        text-transform: uppercase;
        letter-spacing: 1px;
        font-size: 7.5px;
        font-weight: 700;
        padding: 6px 5px;
        text-align: right;
        border-bottom: 1px solid {border};
    }}
    .perf-table th:first-child {{ text-align: left; }}
    .perf-table td {{
        padding: 5px 5px;
        border-bottom: 1px solid #1F2937;
        color: {TEXT_LIGHT};
        text-align: right;
    }}
    .perf-table td:first-child {{ text-align: left; color: {TEXT_MID}; }}
    .perf-table td.pos {{ color: {POSITIVE}; }}
    .perf-table td.neg {{ color: {NEGATIVE}; }}
    .perf-table td.bold {{ font-weight: 700; color: {TEXT_LIGHT}; }}
    .monthly-matrix th, .monthly-matrix td {{ font-size: 8px; padding: 4px 3px; }}
    .monthly-matrix th:first-child, .monthly-matrix td:first-child {{ width: 50px; }}

    .alloc-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr 1fr 1fr;
        gap: 8px;
        margin-bottom: 8px;
        align-items: start;
    }}
    .alloc-table {{ width: 100%; border-collapse: collapse; font-size: 8.5px; }}
    .alloc-table caption {{
        text-align: left;
        color: {accent};
        text-transform: uppercase;
        letter-spacing: 1.2px;
        font-size: 8px;
        font-weight: 700;
        padding-bottom: 4px;
        margin-bottom: 2px;
        border-bottom: 1px solid {border};
    }}
    .alloc-table td {{ padding: 2px 0; }}
    .alloc-table td:last-child {{ text-align: right; color: {TEXT_LIGHT}; font-weight: 600; }}
    .alloc-table td.cat {{ color: {TEXT_MID}; }}

    .holdings-table, .contrib-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 8.5px;
        margin-top: 4px;
    }}
    .holdings-table th, .contrib-table th {{
        background: {DARK_PANEL};
        color: {accent};
        text-transform: uppercase;
        letter-spacing: 0.8px;
        font-size: 7px;
        font-weight: 700;
        padding: 5px 6px;
        text-align: left;
    }}
    .holdings-table th.r, .contrib-table th.r {{ text-align: right; }}
    .holdings-table td, .contrib-table td {{
        padding: 3.5px 6px;
        border-bottom: 1px solid #1F2937;
        color: {TEXT_LIGHT};
    }}
    .holdings-table td.r, .contrib-table td.r {{ text-align: right; }}
    .holdings-table td.dim, .contrib-table td.dim {{ color: {TEXT_MID}; }}
    .contrib-table td.pos {{ color: {POSITIVE}; font-weight: 600; }}
    .contrib-table td.neg {{ color: {NEGATIVE}; font-weight: 600; }}

    .risk-metrics-grid {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 8px;
        margin-top: 8px;
    }}
    .metric-card {{
        background: {DARK_PANEL};
        border: 1px solid {border};
        border-radius: 4px;
        padding: 6px 8px;
    }}
    .metric-card .label {{
        font-size: 7px;
        color: {accent};
        text-transform: uppercase;
        letter-spacing: 1px;
        font-weight: 700;
        margin-bottom: 2px;
    }}
    .metric-card .value {{ font-size: 13px; color: {TEXT_LIGHT}; font-weight: 700; }}
    .metric-card .sub {{ font-size: 7px; color: {TEXT_DIM}; margin-top: 1px; }}

    .footer {{
        margin-top: 14px;
        padding-top: 6px;
        border-top: 1px solid #1F2937;
        font-size: 7px;
        color: {TEXT_DIM};
        line-height: 1.5;
    }}
    .footer strong {{ color: {accent}; }}
    .page-num {{ text-align: right; font-size: 7.5px; color: {TEXT_DIM}; margin-top: 6px; }}
    """

    # Key Figures box (replaces PRIIPS-style 1-7 risk indicator + recommended
    # horizon — removed for compliance). Shows the headline NAV/perf numbers.
    nav_eom_str = f"${ctx['nav_eom']:,.0f}" if ctx.get('nav_eom') else "—"
    mtd_str     = f"{ctx['pf_mtd_pct']:+.2f}%" if ctx.get('pf_mtd_pct') is not None else "—"
    itd_perf    = (ctx['port_index_last'] - 100) if ctx.get('port_index_last') else None
    itd_str     = f"{itd_perf:+.2f}%" if itd_perf is not None else "—"
    cash_str    = f"{ctx['cash_pct']:.2f}%"

    # Monthly returns matrix (year rows × 12 month columns)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    perf_rows = ""
    for year, month_dict in sorted(ctx["monthly_returns"].items()):
        cells = ""
        for m_idx in range(1, 13):
            v = month_dict.get(m_idx)
            if v is None:
                cells += "<td>—</td>"
            else:
                cls = "pos" if v >= 0 else "neg"
                cells += f"<td class='{cls}'>{v:+.2f}%</td>"
        perf_rows += f"<tr><td class='bold'>{year}</td>{cells}</tr>"

    # Allocation tables
    def alloc_rows(items):
        return "".join(f"<tr><td class='cat'>{cat}</td><td>{w:.1f}%</td></tr>"
                       for cat, w in items)
    alloc_layer    = alloc_rows(ctx["alloc_layer"])
    alloc_sector   = alloc_rows(ctx["alloc_sector"])
    alloc_geo      = alloc_rows(ctx["alloc_geo"])
    alloc_thematic = alloc_rows(ctx["alloc_thematic"])

    # Top 10 holdings
    holdings_rows = ""
    for p in ctx["top10"]:
        holdings_rows += (
            f"<tr><td><b>{p['ticker']}</b></td>"
            f"<td class='dim'>{p['name'][:24]}</td>"
            f"<td class='dim'>{p.get('geography') or '—'}</td>"
            f"<td class='dim'>{p.get('sector') or '—'}</td>"
            f"<td class='r'>{p['current_weight']:.2f}%</td></tr>"
        )

    # Top 5 contributors / detractors — use MTD in retro mode, ITD otherwise
    ck = ctx["contrib_key"]
    contrib_rows = ""
    for p in ctx["top5_contrib"]:
        v = p[ck]
        cls = "pos" if v >= 0 else "neg"
        sign = "+" if v >= 0 else ""
        contrib_rows += (
            f"<tr><td><b>{p['ticker']}</b></td>"
            f"<td class='dim'>{p['name'][:18]}</td>"
            f"<td class='r dim'>{p['current_weight']:.1f}%</td>"
            f"<td class='r {cls}'>{sign}{v:.2f}%</td></tr>"
        )
    detract_rows = ""
    for p in ctx["bottom5_contrib"]:
        v = p[ck]
        cls = "neg" if v < 0 else "pos"
        sign = "" if v < 0 else "+"
        detract_rows += (
            f"<tr><td><b>{p['ticker']}</b></td>"
            f"<td class='dim'>{p['name'][:18]}</td>"
            f"<td class='r dim'>{p['current_weight']:.1f}%</td>"
            f"<td class='r {cls}'>{sign}{v:.2f}%</td></tr>"
        )

    # Fill {alpha_mtd_pct}, {bench_mtd_pct}, {pf_mtd_pct}, {bench_pri_lbl} and
    # {cash_pct} placeholders in commentary bullets if present.
    fmt_kwargs = {
        "alpha_mtd_pct":  f"{ctx['alpha_mtd_pct']:+.2f}" if ctx['alpha_mtd_pct'] is not None else "—",
        "bench_mtd_pct":  f"{ctx['bench_mtd_pct']:+.2f}" if ctx['bench_mtd_pct'] is not None else "—",
        "pf_mtd_pct":     f"{ctx['pf_mtd_pct']:+.2f}" if ctx['pf_mtd_pct'] is not None else "—",
        "bench_pri_lbl":  ctx["bench_pri_lbl"],
        "cash_pct":       f"{ctx['cash_pct']:.2f}",
    }
    def _safe_format(s):
        try:
            return s.format(**fmt_kwargs)
        except (KeyError, IndexError):
            return s
    market_lis = "".join(f"<li>{_safe_format(b)}</li>" for b in cfg["market_comment"])
    mgmt_lis   = "".join(f"<li>{_safe_format(b)}</li>" for b in cfg["mgmt_comment"])
    chart_img  = f"data:image/png;base64,{ctx['chart_b64']}"

    html = f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<title>{pf['name']} — Monthly Report — {cfg['report_date']}</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>

<div class='page'>
  {"<div style='background:#B45309;color:#FFF7ED;text-align:center;font-weight:700;letter-spacing:2px;padding:6px 0;font-size:11px;'>DRAFT — MANAGEMENT COMMENTARY PENDING — NOT FOR DISTRIBUTION</div>" if cfg.get("is_draft") else ""}
  <div class='top-band'>
    <span><b>{pf['name'].upper()}</b> &nbsp;|&nbsp; Monthly Report</span>
    <span>Paper portfolio. Not financial advice. Personal views only.</span>
  </div>

  <div class='header-block'>
    <div class='header-left'>
      <div class='eyebrow'>{eyebrow_text}</div>
      <div class='pf-title'>{pf['name']}</div>
      <div class='pf-subtitle'>Monthly Report — {period_label}</div>
      <div class='meta-row'>
        Inception: <b>{pf['inception_date']}</b>
        &nbsp;·&nbsp; Reporting: <b>{cfg['report_date']}</b>
      </div>
    </div>
    <div class='header-right'>
      <div class='risk-box'>
        <div style='font-size:7.5px; color:{TEXT_DIM}; text-transform:uppercase; letter-spacing:1px; font-weight:700; margin-bottom:5px;'>Key Figures</div>
        <table class='meta-table'>
          <tr><td>NAV ({period_label.split()[0][:3]} {pd.Timestamp(cfg['report_date']).day})</td><td>{nav_eom_str}</td></tr>
          <tr><td>MTD return</td><td>{mtd_str}</td></tr>
          <tr><td>ITD return</td><td>{itd_str}</td></tr>
        </table>
      </div>
    </div>
  </div>

  <div class='section-label'>Investment Objective</div>
  <p class='objective'>{cfg['objective']}</p>

  <div class='two-col'>
    <div class='col'>
      <div class='section-label'>Market Commentary</div>
      <ul class='bullet-list'>{market_lis}</ul>
    </div>
    <div class='col'>
      <div class='section-label'>Management Commentary</div>
      <ul class='bullet-list'>{mgmt_lis}</ul>
    </div>
  </div>

  <div class='section-label'>Performance — {pf['name']} vs Benchmarks</div>
  <div class='chart-block'>
    <img src='{chart_img}' alt='Performance chart'/>
  </div>
  <div style='font-size:7px; color:{TEXT_DIM}; text-align:right; margin-top:-2px; font-style:italic;'>
    Both series indexed to 100 at the {pd.Timestamp(ctx['t1_anchor']).strftime('%B')} {pd.Timestamp(ctx['t1_anchor']).day} close — last trading day before the {pd.Timestamp(ctx['inception']).strftime('%B')} {pd.Timestamp(ctx['inception']).day} deployment.
  </div>

  <table class='perf-table monthly-matrix'>
    <thead>
      <tr><th></th>
        <th>Jan</th><th>Feb</th><th>Mar</th><th>Apr</th><th>May</th><th>Jun</th>
        <th>Jul</th><th>Aug</th><th>Sep</th><th>Oct</th><th>Nov</th><th>Dec</th>
      </tr>
    </thead>
    <tbody>{perf_rows}</tbody>
  </table>
  <div style='font-size:7.5px; color:{TEXT_DIM}; margin-top:3px; text-align:right;'>
    Net monthly returns (%). Empty cells = period not yet completed since inception.
  </div>

  <div class='page-num'>1 / 2</div>
</div>


<div class='page'>
  <div class='top-band'>
    <span><b>{pf['name'].upper()}</b> &nbsp;|&nbsp; Monthly Report — {period_label}</span>
    <span>Paper portfolio. Not financial advice. Personal views only.</span>
  </div>

  <div class='section-label'>Allocation</div>
  <div class='alloc-grid'>
    <table class='alloc-table'>
      <caption>Layer</caption>
      <tbody>{alloc_layer}</tbody>
    </table>
    <table class='alloc-table'>
      <caption>Sector</caption>
      <tbody>{alloc_sector}</tbody>
    </table>
    <table class='alloc-table'>
      <caption>Geography</caption>
      <tbody>{alloc_geo}</tbody>
    </table>
    <table class='alloc-table'>
      <caption>Theme</caption>
      <tbody>{alloc_thematic}</tbody>
    </table>
  </div>

  <div class='section-label'>Top 10 Holdings</div>
  <table class='holdings-table'>
    <thead>
      <tr><th>Ticker</th><th>Name</th><th>Country</th><th>Sector</th><th class='r'>Weight</th></tr>
    </thead>
    <tbody>{holdings_rows}</tbody>
  </table>

  <div class='two-col' style='margin-top: 10px;'>
    <div class='col'>
      <div class='section-label'>Top 5 Contributors</div>
      <table class='contrib-table'>
        <thead>
          <tr><th>Ticker</th><th>Name</th><th class='r'>Weight</th><th class='r'>Contrib</th></tr>
        </thead>
        <tbody>{contrib_rows}</tbody>
      </table>
    </div>
    <div class='col'>
      <div class='section-label'>Top 5 Detractors</div>
      <table class='contrib-table'>
        <thead>
          <tr><th>Ticker</th><th>Name</th><th class='r'>Weight</th><th class='r'>Contrib</th></tr>
        </thead>
        <tbody>{detract_rows}</tbody>
      </table>
    </div>
  </div>

  <div class='section-label'>Risk Metrics</div>
  <div class='risk-metrics-grid'>
    <div class='metric-card'>
      <div class='label'>Max Drawdown</div>
      <div class='value'>{ctx['mdd']:+.2f}%</div>
      <div class='sub'>Since inception</div>
    </div>
    <div class='metric-card'>
      <div class='label'>Top 3 Concentration</div>
      <div class='value'>{ctx['top3_pct']:.1f}%</div>
      <div class='sub'>{ctx['top3_names']}</div>
    </div>
    <div class='metric-card'>
      <div class='label'>Realized Vol (MTD)</div>
      <div class='value'>{f"{ctx['vol_mtd_ann_pct']:.1f}%" if ctx.get('vol_mtd_ann_pct') is not None else "—"}</div>
      <div class='sub'>Annualized, this month</div>
    </div>
    <div class='metric-card'>
      <div class='label'>Beta vs {ctx.get('bench_pri_lbl') or 'benchmark'} (MTD)</div>
      <div class='value'>{f"{ctx['beta_mtd']:.2f}" if ctx.get('beta_mtd') is not None else "—"}</div>
      <div class='sub'>Daily returns, this month</div>
    </div>
  </div>

  <div class='footer'>
    <strong>Disclaimer:</strong> {pf['name']} is a paper portfolio for educational and demonstrative
    purposes only. It does not constitute financial advice, an investment recommendation, or a solicitation
    to buy or sell any security or digital asset. Performance is paper-trading: no actual trades executed.
    Past performance is not indicative of future results. The author is not a registered investment advisor
    and may hold personal positions in securities mentioned. Readers should conduct their own due diligence.
    <br/><br/>
    <span style='color:{TEXT_DIM};'>
    <strong>Methodology:</strong> Performance computed from fund-accounting NAV (sum of position market values
    + cash) normalized base 100 at inception. Price data sourced from market data providers. MTD attribution
    approximates intra-month share count changes by the period-end share count.
    <div style='margin-top:5px;'><strong>Published:</strong> {ctx['publication_date']}</div>
    </span>
  </div>

  <div class='page-num'>2 / 2</div>
</div>

</body>
</html>"""
    return html


# ── Main ──────────────────────────────────────────────────────────────────────
def generate(portfolio_id: str, report_date: str | None = None, out_dir: str | Path | None = None) -> Path:
    if portfolio_id not in CONFIGS:
        raise ValueError(f"Unknown portfolio: {portfolio_id}. Use one of: {list(CONFIGS.keys())}")
    cfg = dict(CONFIGS[portfolio_id])
    if report_date:
        cfg["report_date"] = report_date
    # Commentary from reports/comments/<pid>/<YYYY-MM>.toml; DRAFT otherwise.
    # (The inline CONFIGS text only ever described May 2026 — kept as history.)
    cfg["market_comment"], cfg["mgmt_comment"], cfg["is_draft"] = load_comments(portfolio_id, cfg["report_date"])
    print(f"\n{'=' * 60}\nGenerating monthly report for: {portfolio_id}  ({cfg['report_date']}, "
          f"{'DRAFT' if cfg['is_draft'] else 'final'})\n{'=' * 60}")

    portfolio = fetch_portfolio(portfolio_id)
    inception = str(portfolio["inception_date"])
    # In retro mode: extend chart back to T-1 anchor so both portfolio and
    # benchmark are base 100 at the same starting point ($1M cash, pre-deployment).
    t1_anchor = fetch_t1_anchor(portfolio_id) or inception
    history_start = t1_anchor  # used for yfinance
    end = str(pd.Timestamp(cfg["report_date"]) + pd.Timedelta(days=1))[:10]

    # Retroactive vs live mode: if report_date is in the past, use the snapshot
    # state at that date (positions active then, prices = close of that day).
    today_iso = date.today().isoformat()
    retro_mode = cfg["report_date"] < today_iso
    if retro_mode:
        print(f"  Mode:      RETROACTIVE (state as of {cfg['report_date']})")
        positions = fetch_positions_as_of(portfolio_id, cfg["report_date"])
    else:
        print(f"  Mode:      LIVE (current state)")
        positions = fetch_positions(portfolio_id)

    print(f"  Portfolio: {portfolio['name']}")
    print(f"  Positions: {len(positions)}")
    print(f"  Period:    {inception} → {cfg['report_date']}")

    bench_pri = portfolio.get("benchmark_primary")
    bench_pri_lbl = portfolio.get("benchmark_primary_label") or bench_pri or ""
    bench_sec = portfolio.get("benchmark_secondary")
    bench_sec_lbl = portfolio.get("benchmark_secondary_label") or bench_sec or ""
    benchmarks_for_history = tuple(b for b in [bench_pri, bench_sec] if b)

    tickers = tuple(p["ticker"] for p in positions)
    if retro_mode:
        print("\n  Fetching as-of prices from daily_holdings...")
        prices = fetch_prices_as_of(tickers, cfg["report_date"], portfolio_id)
        missing = [t for t in tickers if t not in prices]
        if missing:
            print(f"  WARN: {len(missing)} ticker(s) missing in daily_holdings: {missing}")
    else:
        print("\n  Fetching live prices...")
        prices = fetch_live_prices(tickers)

    print("  Fetching history...")
    history = fetch_history(tickers, history_start, end, benchmarks_for_history)
    if history.empty:
        print("  ERROR: history empty")
        return

    print("  Computing metrics...")
    positions, cash_pct = compute_position_metrics(positions, prices)
    if retro_mode:
        nav_series = build_nav_series_from_holdings(portfolio_id, cfg["report_date"])
        if nav_series.empty:
            print("  ERROR: NAV series empty from daily_holdings")
            return
        # Use NAV series directly (T-1 anchor = base 100 = $1M cash pre-deployment).
        # Reindex to history.index for clean chart alignment with benchmarks.
        port_index = nav_series.reindex(history.index, method="ffill").dropna()
        # If port_index doesn't start at 100, the T-1 anchor wasn't in history.
        # Prepend it explicitly so the chart visually anchors at base 100.
        if not port_index.empty and abs(port_index.iloc[0] - 100.0) > 0.5:
            t1_ts = pd.Timestamp(t1_anchor)
            if t1_ts not in port_index.index and t1_ts in nav_series.index:
                port_index = pd.concat([
                    pd.Series([nav_series.loc[t1_ts]], index=[t1_ts]),
                    port_index,
                ]).sort_index()
        print(f"  NAV: base 100 at T-1 ({t1_anchor}) → {port_index.iloc[-1]:.2f} at {cfg['report_date']}")
        # Override cash_pct with the real value from daily_holdings (the legacy
        # formula `100 - sum(weights_DB)` is in inception-$ space, mis-states
        # cash by a wide margin once NAV drifts away from initial capital).
        nav_eom, cash_eom = fetch_nav_and_cash_at(portfolio_id, cfg["report_date"])
        if nav_eom > 0:
            cash_pct = round(cash_eom / nav_eom * 100, 2)
            print(f"  Cash: ${cash_eom:,.0f} = {cash_pct:.2f}% of NAV ${nav_eom:,.0f}")
        # MTD attribution using daily_holdings prices
        compute_mtd_attribution(positions, portfolio_id, cfg["report_date"])
    else:
        port_index = build_index(history, positions)
        nav_eom = None
    if port_index.empty:
        print("  ERROR: portfolio index empty")
        return

    positions_w = sorted(positions, key=lambda p: p["current_weight"] or 0, reverse=True)
    top10 = positions_w[:10]
    # Monthly attribution preferred (MTD). Fall back to inception-to-date
    # contribution if MTD wasn't computed (live mode or insufficient data).
    contrib_key = "contribution_mtd" if retro_mode else "contribution"
    valid_contrib = [p for p in positions if p.get(contrib_key) is not None]
    sorted_contrib = sorted(valid_contrib, key=lambda p: p[contrib_key], reverse=True)
    top5_contrib = sorted_contrib[:5]
    bottom5_contrib = sorted(valid_contrib, key=lambda p: p[contrib_key])[:5]

    alloc_layer    = aggregate_alloc(positions, "layer", cash_pct)
    alloc_sector   = aggregate_alloc(positions, "sector", cash_pct)
    alloc_geo      = aggregate_alloc(positions, "geography", cash_pct)
    alloc_thematic = aggregate_alloc(positions, "thematic", 0.0)  # cash has no thematic; don't double-count it

    mdd = max_drawdown(port_index) or 0
    top3 = positions_w[:3]
    top3_pct = sum(p["current_weight"] for p in top3)
    top3_names = " · ".join(p["ticker"] for p in top3)

    # Monthly returns matrix
    inception_dt = pd.Timestamp(inception)
    report_dt    = pd.Timestamp(cfg["report_date"])
    monthly_returns = {report_dt.year: {m: None for m in range(1, 13)}}
    monthly = port_index.resample("ME").last()
    for ts, val in monthly.items():
        first_of_month = ts.replace(day=1)
        if inception_dt <= first_of_month and ts <= report_dt:
            prev_ts = ts - pd.offsets.MonthEnd(1)
            prev_val = monthly.get(prev_ts)
            if prev_val is None or pd.isna(prev_val):
                prev_val = 100.0
            ret = round(float((val / prev_val - 1) * 100), 2)
            monthly_returns.setdefault(ts.year, {m: None for m in range(1, 13)})
            monthly_returns[ts.year][ts.month] = ret

    # Alpha vs primary benchmark (MTD). Computed in retro mode for the mgmt
    # commentary; passed through context for {alpha_mtd_pct} / {bench_mtd_pct}
    # template placeholders in the mgmt_comment bullets.
    # Also: realized volatility (MTD, annualized) and beta vs primary benchmark.
    pf_mtd_pct = None
    bench_mtd_pct = None
    alpha_mtd_pct = None
    vol_mtd_ann_pct = None
    beta_mtd = None
    if retro_mode and bench_pri and bench_pri in history.columns:
        month_start_ts = pd.Timestamp(cfg["report_date"]) - pd.offsets.MonthEnd(1)
        try:
            pf_start = port_index.asof(month_start_ts)
            pf_end   = port_index.iloc[-1]
            b_series = history[bench_pri].dropna()
            b_start  = b_series.asof(month_start_ts)
            b_end    = b_series.iloc[-1]
            if pd.notna(pf_start) and pd.notna(b_start):
                pf_mtd_pct    = round((pf_end / pf_start - 1) * 100, 2)
                bench_mtd_pct = round((b_end / b_start - 1) * 100, 2)
                alpha_mtd_pct = round(pf_mtd_pct - bench_mtd_pct, 2)
                print(f"  Alpha MTD: portfolio {pf_mtd_pct:+.2f}% vs {bench_pri_lbl} {bench_mtd_pct:+.2f}% = {alpha_mtd_pct:+.2f}pp")

            # Realized vol + beta on MTD daily returns
            mtd_mask    = (port_index.index >= month_start_ts) & (port_index.index <= pd.Timestamp(cfg["report_date"]))
            pf_mtd_ret  = port_index[mtd_mask].pct_change().dropna()
            b_mtd_ret   = b_series[(b_series.index >= month_start_ts) & (b_series.index <= pd.Timestamp(cfg["report_date"]))].pct_change().dropna()
            aligned     = pd.concat([pf_mtd_ret, b_mtd_ret], axis=1, join="inner").dropna()
            if len(aligned) >= 3:
                pf_returns = aligned.iloc[:, 0]
                b_returns  = aligned.iloc[:, 1]
                vol_mtd_ann_pct = round(float(pf_returns.std() * (252 ** 0.5) * 100), 2)
                b_var = float(b_returns.var())
                if b_var > 0:
                    beta_mtd = round(float(pf_returns.cov(b_returns) / b_var), 2)
                print(f"  Realized vol (MTD, ann.): {vol_mtd_ann_pct:.2f}%  ·  Beta MTD vs {bench_pri_lbl}: {beta_mtd}")
        except Exception as e:
            print(f"  WARN: alpha/vol/beta computation failed: {e}")

    print("  Generating chart...")
    accent = portfolio.get("color_primary") or "#A78BFA"
    # Chart: portfolio + PRIMARY benchmark only (secondary removed to keep the
    # comparison sharp — secondary was a regulator-style "anchor" not informative
    # for the active-management story).
    benchmark_lines = []
    if bench_pri and bench_pri in history.columns:
        benchmark_lines.append((bench_pri, bench_pri_lbl, "--"))
    chart_b64 = generate_chart_b64(history, port_index, portfolio["name"], benchmark_lines, accent)

    ctx = {
        "portfolio_id":      portfolio_id,
        "portfolio":         portfolio,
        "config":            cfg,
        "positions":         positions,
        "n_positions":       len(positions),
        "cash_pct":          cash_pct,
        "alloc_layer":       alloc_layer,
        "alloc_sector":      alloc_sector,
        "alloc_geo":         alloc_geo,
        "alloc_thematic":    alloc_thematic,
        "top10":             top10,
        "top5_contrib":      top5_contrib,
        "bottom5_contrib":   bottom5_contrib,
        "mdd":               mdd,
        "top3_pct":          top3_pct,
        "top3_names":        top3_names,
        "chart_b64":         chart_b64,
        "monthly_returns":   monthly_returns,
        "retro_mode":        retro_mode,
        "contrib_key":       contrib_key,
        "pf_mtd_pct":        pf_mtd_pct,
        "bench_mtd_pct":     bench_mtd_pct,
        "alpha_mtd_pct":     alpha_mtd_pct,
        "bench_pri_lbl":     bench_pri_lbl,
        "publication_date":  date.today().isoformat(),
        "nav_eom":           (nav_eom if retro_mode else None),
        "port_index_last":   float(port_index.iloc[-1]) if not port_index.empty else None,
        "t1_anchor":         t1_anchor,
        "inception":         inception,
        "vol_mtd_ann_pct":   vol_mtd_ann_pct,
        "beta_mtd":          beta_mtd,
    }

    print("  Rendering HTML...")
    html = render_html(ctx)

    if out_dir is not None:
        output_dir = Path(out_dir) / portfolio_id
    elif PROJECT_ROOT.exists():
        output_dir = PROJECT_ROOT / cfg["subfolder"] / "reports"
    else:
        output_dir = REPO_ROOT / "reports" / "drafts" / portfolio_id
    output_dir.mkdir(parents=True, exist_ok=True)
    import unicodedata
    _base = unicodedata.normalize("NFKD", cfg["subfolder"].replace(" ", "")).encode("ascii", "ignore").decode()
    out_path = output_dir / f"{_base}_Monthly_{cfg['report_date']}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[OK] Saved: {out_path.absolute()}")
    return out_path


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Generate Specula monthly reports (HTML).")
    ap.add_argument("portfolio", nargs="?", default=None, help="visionnaire | batisseur | nakamoto")
    ap.add_argument("--all", action="store_true", help="every active public portfolio")
    ap.add_argument("--month", help="YYYY-MM -> report at that month-end")
    ap.add_argument("--date", help="explicit report date YYYY-MM-DD (overrides --month)")
    ap.add_argument("--out", help="output directory (default: local Portfolio Project folder, else reports/drafts)")
    args = ap.parse_args()

    report_date = args.date or (month_end(args.month) if args.month else None)
    if args.all:
        pids = [r["id"] for r in sb.table("portfolios").select("id").eq("is_active", True).order("display_order").execute().data]
    else:
        pids = [args.portfolio or "visionnaire"]
    for pid in pids:
        generate(pid, report_date=report_date, out_dir=args.out)


if __name__ == "__main__":
    main()
