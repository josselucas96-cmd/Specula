"""Methodology & corrections log — how every number on the site is produced.

Reachable at /Methodology. Not linked from the nav until reviewed (add
("Methodology", "/Methodology", "methodology") to simple_pages in utils/nav.py).
The corrections log is read from errata.toml (repo root), newest first.
"""
import tomllib
from pathlib import Path

import streamlit as st

from utils import SPECULA_ICON
from utils.nav import render_nav

st.set_page_config(
    page_title="Methodology | Specula",
    page_icon=SPECULA_ICON,
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    [data-testid="stSidebar"] { display: none; }
    .block-container { padding-top: 3.5rem; padding-bottom: 2rem; max-width: 820px; }
    .about-label { font-size: 0.7rem; font-weight: 700; letter-spacing: 2px; color: #00D09C;
                   text-transform: uppercase; margin-bottom: 0.6rem; }
    .about-title { font-size: 2.2rem; font-weight: 800; letter-spacing: -0.5px; margin-bottom: 1.2rem; line-height: 1.2; }
    .about-body  { font-size: 0.95rem; color: #CCC; line-height: 1.85; }
    .about-body p { margin-bottom: 1.1rem; }
    .about-body li { margin-bottom: 0.35rem; }
    .section-title { font-size: 1.1rem; font-weight: 700; margin-top: 2.2rem; margin-bottom: 0.6rem; color: #EEE; }
    .kv { display: grid; grid-template-columns: 190px 1fr; gap: 0.35rem 1rem; font-size: 0.9rem; color: #CCC; margin: 0.6rem 0 1.2rem; }
    .kv b { color: #EEE; }
    .errata { border: 1px solid #1F2530; border-radius: 8px; padding: 0.9rem 1.1rem; margin-bottom: 0.9rem; background: #0F131A; }
    .errata .meta { font-size: 0.72rem; letter-spacing: 1.5px; text-transform: uppercase; color: #00D09C; }
    .errata .ttl  { font-size: 1rem; font-weight: 700; color: #EEE; margin: 0.25rem 0 0.5rem; }
    .errata .row  { font-size: 0.88rem; color: #BBB; line-height: 1.65; margin-bottom: 0.3rem; }
    .errata .row b { color: #DDD; }
    .disclaimer { font-size: 0.72rem; color: #444; margin-top: 3rem; border-top: 1px solid #1A1F26; padding-top: 1rem; line-height: 1.5; }
</style>
""", unsafe_allow_html=True)

render_nav("methodology")
st.write("")

st.markdown('<div class="about-label">Specula</div>', unsafe_allow_html=True)
st.markdown('<div class="about-title">Methodology &amp; corrections log</div>', unsafe_allow_html=True)

st.markdown("""
<div class="about-body">

<p>
Every figure on this site is derived from one ledger, written once a day by an automated job and never
edited afterwards. This page describes how that ledger is built, how performance and risk are computed
from it, and — because a public track record is only worth what its accountability is worth — every
correction ever applied to it.
</p>

<div class="section-title">1. The paper portfolios</div>
<p>
Each portfolio starts with a notional <strong>USD 1,000,000</strong> deposited the evening before its
inception date. No real money is involved. Trades are simulated at the quoted price at the time the
decision is recorded; there are no transaction costs, no slippage, no taxes and no financing costs.
Positions are sized as a percentage of the initial capital and expressed in fractional shares.
</p>

<div class="section-title">2. The ledger</div>
<p>
The ledger holds, for each portfolio and each calendar day, one row per position
(<em>shares × closing price = value</em>) plus one cash row. The net asset value (NAV) of a day is the
sum of those rows. Rows are written after the US close by a scheduled job (22:07 UTC) and are
<strong>immutable</strong>: a trade made today changes today's row and the following ones, never the past.
</p>
<div class="kv">
<b>Price source</b><span>Yahoo Finance end-of-day closes (unadjusted for dividends).</span>
<b>Currency</b><span>All values in USD. Foreign listings are converted at that day's exchange rate; London prices quoted in pence are divided by 100.</span>
<b>Weekends &amp; holidays</b><span>A row is written every calendar day so that the Bitcoin benchmark (24/7) and the equity books stay on the same timeline. On non-trading days the last close is carried forward — those days carry no return.</span>
<b>Missing bars</b><span>If the provider has no close for a listing on a trading day, the last valid close is carried forward and the row is marked as propagated.</span>
<b>Cash</b><span>Derived from the previous day's cash row and the day's trades. A purchase that would take cash below zero is refused.</span>
</div>

<div class="section-title">3. Performance</div>
<p>
The chart and the headline return are indexed to <strong>100 on the last trading day before inception</strong>
(the "T-1 anchor": capital deposited the evening before, invested at the next open). The headline
figure is <em>NAV on the latest ledger day ÷ 1,000,000 − 1</em>. Benchmarks are indexed to 100 on the
same anchor day, and the <em>excess return</em> is the difference between the two on the <strong>same date</strong>. It is not risk-adjusted: Jensen's alpha, shown with the risk statistics, removes the part of the difference explained by the portfolio's beta.
</p>
<div class="kv">
<b>Le Visionnaire</b><span>Nasdaq 100 (QQQ), secondary S&amp;P 500 (SPY)</span>
<b>Le Bâtisseur</b><span>S&amp;P 500 (SPY), secondary Nasdaq 100 (QQQ)</span>
<b>Le Nakamoto</b><span>Bitcoin (BTC-USD), secondary Strategy (MSTR)</span>
</div>
<p>
<strong>A known asymmetry, disclosed rather than hidden:</strong> the benchmark ETFs are measured on a
total-return basis (dividends reinvested), while the portfolios are measured on a price basis — cash
dividends are <em>not</em> credited to the ledger. This biases the comparison <em>against</em> the
portfolios, by roughly the dividend yield of the book (most material for Le Bâtisseur). Crediting
dividends at the ex-date is on the roadmap; when it lands it will be logged below. The "Return %" shown
per position does include reinvested dividends.
</p>

<div class="section-title">4. Risk statistics</div>
<p>
All statistics are computed on <strong>trading days only</strong> (the calendar of the equity benchmark),
from the daily NAV series, and are shown once 60 trading days are available.
</p>
<div class="kv">
<b>Volatility</b><span>Standard deviation of daily returns × √252.</span>
<b>Sharpe ratio</b><span>Annualised mean excess return ÷ annualised volatility, risk-free rate 5%.</span>
<b>Max drawdown</b><span>Largest peak-to-trough decline of the NAV series.</span>
<b>VaR 95% (1-day)</b><span>Historical: the 5th percentile of daily returns.</span>
<b>Beta</b><span>Regression slope of portfolio daily returns on the benchmark's daily returns, measured over the same intervals (for Bitcoin, the level on each trading day of the portfolio).</span>
<b>Jensen's alpha</b><span>Portfolio return minus [risk-free + beta × (benchmark return − risk-free)] since inception, risk-free 5% as for the Sharpe ratio. Indicative with less than a year of history.</span>
<b>Correlation</b><span>Pairwise correlation of the constituents' daily returns over the trailing 12 months (or since inception).</span>
</div>

<div class="section-title">5. Trades</div>
<p>
Every trade is recorded with its timestamp, price, size and rationale, and mirrored in a public post.
Reinforcements average the cost basis; trims and closes keep it. After each execution the tool re-reads
the database and flags any trade that did not land. The portfolio pages show current weights (market
value ÷ NAV), not target weights.
</p>

<div class="section-title">6. Corporate actions</div>
<div class="kv">
<b>Splits</b><span>Applied at the split ratio to shares and cost basis — value-neutral. The nightly job refuses to write a position whose value would jump more than 2.5× overnight, the signature of an unapplied split.</span>
<b>Dividends</b><span>Not credited to NAV (see §3). Reinvested in the per-position "Return %".</span>
<b>Mergers, delistings, ticker changes</b><span>Handled manually at the terms of the transaction and logged as trades.</span>
</div>

<div class="section-title">7. Public ledger &amp; Bitcoin timestamps</div>
<p>
The whole ledger is exported every night to the public GitHub repository
(<a href="https://github.com/josselucas96-cmd/Specula/tree/ledger/ledger" target="_blank" style="color:#00D09C;">ledger branch</a>):
daily holdings, positions, the trade log. The git history is an independent, tamper-evident copy of the
track record, and the SHA-256 of each day's export is anchored in the Bitcoin blockchain with
<a href="https://opentimestamps.org" target="_blank" style="color:#00D09C;">OpenTimestamps</a>. Anyone can
verify that a given day's figures existed by a given block and have not changed since — the README in
that folder gives the three commands. What the timestamp proves is existence, not correctness: that is
what the methodology above and the corrections log below are for.
</p>

<div class="section-title">8. Monthly reports</div>
<p>
Reports are generated from the ledger at each month-end close (the numbers can be re-derived at any time)
and completed with a management commentary. A report without commentary is a draft and is not published.
</p>

</div>
""", unsafe_allow_html=True)

# ── Corrections & mistake log ─────────────────────────────────────────────────
st.markdown('<div class="about-body"><div class="section-title">9. Corrections &amp; mistake log</div>'
            '<p>Past rows are never silently edited. When an error is found, the fix and its effect on '
            'published figures are recorded here, newest first.</p></div>', unsafe_allow_html=True)

_errata_path = Path(__file__).resolve().parent.parent / "errata.toml"
try:
    with open(_errata_path, "rb") as f:
        _entries = tomllib.load(f).get("entry", [])
except Exception:
    _entries = []

if not _entries:
    st.info("No corrections logged.")
for e in sorted(_entries, key=lambda x: x.get("date", ""), reverse=True):
    st.markdown(
        f'<div class="errata">'
        f'<div class="meta">{e.get("date","")} &nbsp;·&nbsp; {e.get("scope","")} &nbsp;·&nbsp; {e.get("status","")}</div>'
        f'<div class="ttl">{e.get("title","")}</div>'
        f'<div class="row"><b>What was wrong.</b> {e.get("what","")}</div>'
        f'<div class="row"><b>What was done.</b> {e.get("fix","")}</div>'
        f'<div class="row"><b>Effect on published figures.</b> {e.get("impact","")}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

st.markdown("""
<div class="disclaimer">
These portfolios are paper trading simulations and do not involve real financial assets. Nothing
published here constitutes financial, investment, or legal advice. I am not a registered financial
advisor. The author may hold personal positions in securities mentioned on this site.
</div>
""", unsafe_allow_html=True)
