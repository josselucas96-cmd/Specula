import threading

import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime, date, timedelta


@st.cache_data(ttl=600)  # 10 min — cron writes daily, no need to refresh more often
def get_valuation_fundamentals_from_db(tickers: tuple) -> dict:
    """[NEW] Read pre-computed valuation fundamentals from Supabase.

    Same signature and return shape as `get_valuation_fundamentals` so callers
    can swap with a one-line import change.

    Populated nightly by daily_refresh.py cron (which calls the slow tk.info
    yfinance endpoint). Frontend reads in <100ms; no rate-limit risk.

    Missing tickers fall back to all-None (same as the legacy fn).
    """
    from utils.data import get_client
    if not tickers:
        return {}
    sb = get_client()

    try:
        rows = (
            sb.table("fundamentals")
            .select("ticker, market_cap, enterprise_value, revenue_ttm, ebitda, "
                    "gross_margin, operating_margin, free_cashflow, fcf_margin, "
                    "forward_pe, trailing_pe, analyst_rg")
            .in_("ticker", list(tickers))
            .execute()
            .data
        )
    except Exception:
        return {tk: _empty_fundamentals() for tk in tickers}

    by_ticker = {r["ticker"]: r for r in rows}
    result = {}
    for tk in tickers:
        r = by_ticker.get(tk)
        if r is None:
            result[tk] = _empty_fundamentals()
        else:
            result[tk] = {
                "market_cap":       float(r["market_cap"]) if r["market_cap"] is not None else None,
                "enterprise_value": float(r["enterprise_value"]) if r["enterprise_value"] is not None else None,
                "revenue_ttm":      float(r["revenue_ttm"]) if r["revenue_ttm"] is not None else None,
                "ebitda":           float(r["ebitda"]) if r["ebitda"] is not None else None,
                "gross_margin":     float(r["gross_margin"]) if r["gross_margin"] is not None else None,
                "operating_margin": float(r["operating_margin"]) if r["operating_margin"] is not None else None,
                "free_cashflow":    float(r["free_cashflow"]) if r["free_cashflow"] is not None else None,
                "fcf_margin":       float(r["fcf_margin"]) if r["fcf_margin"] is not None else None,
                "forward_pe":       float(r["forward_pe"]) if r["forward_pe"] is not None else None,
                "trailing_pe":      float(r["trailing_pe"]) if r["trailing_pe"] is not None else None,
                "analyst_rg":       float(r["analyst_rg"]) if r["analyst_rg"] is not None else None,
            }
    return result


def _empty_fundamentals() -> dict:
    return {"market_cap": None, "enterprise_value": None,
            "revenue_ttm": None, "ebitda": None,
            "gross_margin": None, "operating_margin": None,
            "free_cashflow": None, "fcf_margin": None,
            "forward_pe": None, "trailing_pe": None,
            "analyst_rg": None}


@st.cache_data(ttl=3600)  # Refresh every hour — fundamentals don't move intraday
def get_valuation_fundamentals(tickers: tuple) -> dict:
    """[LEGACY — live yfinance] For each ticker, return a dict with the fields
    needed by the Valo Tracking table. Margins are returned as ratios 0-1.

    Slow at scale (~5-10s per ticker via tk.info, high rate-limit risk).
    See `get_valuation_fundamentals_from_db` for the pre-cached Supabase version.
    """
    result = {}
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            info = tk.info
            rev = info.get("totalRevenue")
            fcf = info.get("freeCashflow")
            fcf_margin = (fcf / rev) if (rev and fcf is not None and rev > 0) else None

            # Analyst consensus revenue growth (forward, +1y).
            analyst_rg = None
            try:
                rev_est = tk.revenue_estimate
                if rev_est is not None and not rev_est.empty:
                    if "+1y" in rev_est.index and "growth" in rev_est.columns:
                        g = rev_est.loc["+1y", "growth"]
                        if pd.notna(g):
                            analyst_rg = float(g) * 100
                    if analyst_rg is None and "+1y" in rev_est.index and "0y" in rev_est.index:
                        cur = rev_est.loc["0y", "avg"]
                        nxt = rev_est.loc["+1y", "avg"]
                        if pd.notna(cur) and pd.notna(nxt) and cur > 0:
                            analyst_rg = (float(nxt) - float(cur)) / float(cur) * 100
            except Exception:
                pass
            # Fallback to TTM revenue growth from .info (backward-looking but better than nothing)
            if analyst_rg is None and info.get("revenueGrowth") is not None:
                analyst_rg = float(info["revenueGrowth"]) * 100

            result[t] = {
                "market_cap":       info.get("marketCap"),
                "enterprise_value": info.get("enterpriseValue"),
                "revenue_ttm":      rev,
                "ebitda":           info.get("ebitda"),
                "gross_margin":     info.get("grossMargins"),
                "operating_margin": info.get("operatingMargins"),
                "free_cashflow":    fcf,
                "fcf_margin":       fcf_margin,
                "forward_pe":       info.get("forwardPE"),
                "trailing_pe":      info.get("trailingPE"),
                "analyst_rg":       analyst_rg,
            }
        except Exception:
            result[t] = {"market_cap": None, "enterprise_value": None,
                         "revenue_ttm": None, "ebitda": None,
                         "gross_margin": None, "operating_margin": None,
                         "free_cashflow": None, "fcf_margin": None,
                         "forward_pe": None, "trailing_pe": None,
                         "analyst_rg": None}
    return result


@st.cache_data(ttl=300)  # Refresh every 5 minutes
def get_prices(tickers: tuple) -> dict:
    """[LEGACY — live yfinance] Current price, daily % change, market cap
    (in listing currency) and currency code for each ticker.

    Each call hits yfinance N times (sequential). Slow at scale + risk of
    Yahoo rate-limiting. See `get_prices_from_db` for the cached version
    that reads from Supabase (populated by daily_refresh.py cron).

    Kept for the Admin cockpit move-execution flow which needs truly live
    prices at commit time (not EOD).
    """
    result = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            price = info.last_price
            prev = info.previous_close
            mc   = getattr(info, "market_cap", None)
            ccy  = getattr(info, "currency", None)
            result[ticker] = {
                "price": round(price, 2),
                "change_pct": round((price - prev) / prev * 100, 2),
                "market_cap": float(mc) if mc is not None else None,
                "currency":   ccy or "USD",
            }
        except Exception:
            result[ticker] = {"price": None, "change_pct": None,
                              "market_cap": None, "currency": None}
    _convert_prices_to_usd(result)
    return result


def _convert_prices_to_usd(result: dict) -> None:
    """In-place: convert each entry's `price` (and `market_cap`) to USD.
    Positions/NAV are USD-denominated, so foreign listings MUST be converted
    (else value = shares × price is in the listing currency = the FX bug).
    USD tickers get factor 1.0 (no change → Le Visionnaire untouched). If the FX
    rate is unknown, the entry is left native (better than a wrong USD figure).

    NOTE: the `currency` label is left as the NATIVE listing currency on purpose.
    No caller applies FX to `price` (that omission WAS the bug), so there's no
    double-conversion risk — and the native label is still needed elsewhere
    (e.g. Admin's EV/mNAV ratio converts enterprise_value using it)."""
    ccys = tuple({r.get("currency") for r in result.values() if r.get("currency")})
    if not any(c and c != "USD" for c in ccys):
        return  # all USD — nothing to do (e.g. Le Visionnaire)
    fx = get_fx_to_usd(ccys)
    for r in result.values():
        f = usd_factor(r.get("currency"), fx)
        if r.get("price") is not None and f is not None and f != 1.0:
            r["price"] = round(r["price"] * f, 4)
            if r.get("market_cap") is not None:
                r["market_cap"] = r["market_cap"] * f


@st.cache_data(ttl=600)  # Refresh every 10 min — cron writes every 24h
def get_prices_from_db(tickers: tuple) -> dict:
    """[NEW] Read prices from Supabase `current_prices` table.

    Populated by daily_refresh.py cron (GitHub Action, 22:07 UTC daily).
    No yfinance calls here → fast (single Supabase query) and no rate-limit
    risk. Trade-off: prices are EOD (last close), not intraday-live.

    Returns the same dict shape as `get_prices` so callers can swap with a
    one-line import change.

    Tickers missing from the table (e.g., newly added position not yet
    fetched by the cron) get {price: None, ...} so caller can fall back.
    """
    from utils.data import get_client
    sb = get_client()
    if not tickers:
        return {}

    try:
        rows = (
            sb.table("current_prices")
            .select("ticker, price, change_pct, market_cap, currency")
            .in_("ticker", list(tickers))
            .execute()
            .data
        )
    except Exception:
        # If the table doesn't exist yet or DB is down, return all-None
        # so the caller can fall back to get_prices (yfinance) gracefully.
        return {tk: {"price": None, "change_pct": None,
                     "market_cap": None, "currency": None} for tk in tickers}

    by_ticker = {r["ticker"]: r for r in rows}
    result = {}
    for tk in tickers:
        r = by_ticker.get(tk)
        if r is None:
            result[tk] = {"price": None, "change_pct": None,
                          "market_cap": None, "currency": None}
        else:
            result[tk] = {
                "price":      round(float(r["price"]), 2) if r["price"] is not None else None,
                "change_pct": round(float(r["change_pct"]), 2) if r["change_pct"] is not None else None,
                "market_cap": float(r["market_cap"]) if r["market_cap"] is not None else None,
                "currency":   r["currency"] or "USD",
            }
    _convert_prices_to_usd(result)
    return result


# ── Hourly display quotes ─────────────────────────────────────────────────────
# The positions table used to show `current_prices` as written by the nightly
# cron: yesterday's close, and a "Today %" measured against Yahoo's
# previous_close, which includes after-hours trading (DUOL showed +12.8% on
# 2026-11-04 for a +26.0% session). Pages now refresh those two columns from
# Yahoo's daily bars at most once an hour, for display only. The ledger, the
# NAV chart and every published performance figure still come from the
# nightly closes in daily_holdings; nothing here is written anywhere.
#
# The worst case is the old behaviour: any ticker whose live quote is missing,
# late, or fails the check below keeps its `current_prices` row.
LIVE_TTL_S = 3600          # one Yahoo pass per ticker and hour at most
LIVE_RETRY_S = 600         # after a failed pass, wait before asking again
LIVE_DEADLINE_S = 10       # longest a page waits for that pass
LIVE_MATCH_TOL = 0.03      # the stored close must be found in the live series


def parse_daily_closes(df, tickers) -> dict:
    """{ticker: [close, ...]} oldest first, from a yf.download daily frame.
    Handles the multi-ticker layout (Close × ticker) and the single-ticker one;
    drops empty rows. Tickers without at least two closes are left out."""
    import pandas as pd
    out = {}
    if df is None or getattr(df, "empty", True):
        return out
    close = df["Close"] if "Close" in df.columns.get_level_values(0) else None
    if close is None:
        return out
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    for t in tickers:
        if t not in close.columns:
            continue
        s = pd.to_numeric(close[t], errors="coerce").dropna()
        s = s[s > 0]
        if len(s) >= 2:
            out[t] = [float(v) for v in s.tolist()]
    return out


def merge_live_quotes(eod: dict, live: dict, tol: float = LIVE_MATCH_TOL) -> dict:
    """Overlay hourly quotes on the nightly rows, ticker by ticker.

    eod:  {ticker: {price, change_pct, market_cap, currency}}, prices in USD
          (get_prices_from_db).
    live: {ticker: {"closes": [...] in USD, oldest first, "as_of": iso}}.

    A live quote replaces the nightly row only when its series contains the
    nightly price (within `tol`): that proves it is the same instrument, in the
    same unit and on the same share basis. It rejects a mixed-up download (the
    2026-09-23 incident), a currency or pence error, and a split the nightly
    job has not applied yet, while still accepting a genuine large move today.
    A ticker with no nightly row (a position opened today) takes the live quote
    only if it is a US listing (no exchange suffix), whose price is in USD.
    """
    out = {}
    for t, row in eod.items():
        merged = dict(row)
        merged["as_of"] = None
        q = live.get(t)
        if q and len(q.get("closes") or []) >= 2:
            closes = q["closes"]
            ref = row.get("price")
            if ref:
                ok = any(abs(c / ref - 1) <= tol for c in closes[-4:])
            else:
                ok = "." not in t
            if ok:
                last, prev = closes[-1], closes[-2]
                merged["price"] = round(last, 4)
                merged["change_pct"] = round((last / prev - 1) * 100, 2)
                merged["as_of"] = q.get("as_of")
        out[t] = merged
    return out


@st.cache_resource
def _live_store() -> dict:
    """One store per server process, shared by every visitor."""
    return {"quotes": {}, "failed": {}, "lock": threading.Lock()}


def _refresh_live(tickers: tuple, currencies: dict) -> None:
    """Fetch the daily bars of the stale tickers once, if no other visitor is
    already doing it. Never raises: a failure only delays the next attempt."""
    import time
    from datetime import datetime, timezone
    store = _live_store()
    now = time.time()
    stale = [t for t in tickers
             if now - store["quotes"].get(t, {}).get("ts", 0) > LIVE_TTL_S
             and now - store["failed"].get(t, 0) > LIVE_RETRY_S]
    if not stale or not store["lock"].acquire(blocking=False):
        return
    try:
        try:
            df = yf_download(stale, period="7d", interval="1d", auto_adjust=False,
                             progress=False, group_by="column", deadline=LIVE_DEADLINE_S)
            closes = parse_daily_closes(df, stale)
        except Exception:
            closes = {}
        fx = get_fx_to_usd(tuple({c for c in currencies.values() if c}))
        as_of = datetime.now(timezone.utc).isoformat(timespec="minutes")
        for t in stale:
            ccy = currencies.get(t)
            f = usd_factor(ccy, fx) if ccy else None     # unknown currency: no live quote
            if t in closes and f is not None:
                store["quotes"][t] = {"closes": [c * f for c in closes[t]],
                                      "as_of": as_of, "ts": now}
                store["failed"].pop(t, None)
            else:
                store["failed"][t] = now
    finally:
        store["lock"].release()


def get_display_prices(tickers: tuple) -> dict:
    """Prices for the public positions table: the nightly rows, overlaid with
    hourly Yahoo quotes where those pass `merge_live_quotes`'s check. Same
    shape as get_prices_from_db, plus "as_of" (ISO time of the live quote, or
    None when the nightly row is shown)."""
    eod = get_prices_from_db(tickers)
    currencies = {t: (r.get("currency") or ("USD" if "." not in t else None))
                  for t, r in eod.items()}
    try:
        _refresh_live(tickers, currencies)
    except Exception:
        pass
    quotes = _live_store()["quotes"]
    return merge_live_quotes(eod, {t: quotes[t] for t in tickers if t in quotes})


# Approximate BTC holdings per Nakamoto position. Estimates as of Q3 2025
# disclosures — UPDATE FROM ACTUAL FILINGS as positions evolve. STRC is a
# preferred share with no direct BTC exposure (holders earn USD coupons).
# BTC held per Nakamoto constituent. Feeds the cockpit's EV/mNAV. NOT auto-
# updated: refresh at each monthly checklist (issue) from the companies' own
# disclosures. Values below verified on 2026-09-21 against press releases /
# bitcointreasuries.net; the previous set (Q3 2025 estimates) was off by up
# to 5x (e.g. Strive 5,816 vs 25,000).
BTC_HOLDINGS_AS_OF = "2026-09-21"
BTC_HOLDINGS_NAKAMOTO = {
    "MSTR":     846000,  # Strategy — 21 Sep 2026 purchase disclosure (846,000 BTC)
    "MTPLF":     43000,  # Metaplanet — 2 Jul 2026 (43,000 BTC), unchanged per 4 Sep coverage
    "ASST":      25000,  # Strive — 11 Sep 2026 (25,000 BTC after 469 BTC buy)
    "ALCPB.PA":   3525,  # Capital B — 14 Sep 2026 press release (3,525 BTC)
    "OBTC3.SA":   3918,  # OranjeBTC — Sep 2026 (3,918 BTC)
    "GS9.F":      3506,  # H100 Group — 3,506.4 BTC after NSD acquisition (10 Aug 2026)
    "CASH3.SA":    600,  # Méliuz — 596-605 BTC (Apr 2026 data; buyback may have sold some — TO CONFIRM)
    "SWC.L":      2747,  # Smarter Web Company — 2 Sep 2026 (2,747 BTC; later headlines up to 2,878 — TO CONFIRM)
    "STRC":          0,  # Preferred — no direct BTC exposure
}


@st.cache_data(ttl=300)  # Refresh every 5 minutes
def get_bitcoin_price() -> float | None:
    """Current BTC-USD spot price. Used for Nakamoto NAV computations."""
    try:
        return float(yf.Ticker("BTC-USD").fast_info.last_price)
    except Exception:
        return None


# Currencies quoted in a MINOR unit (1/100 of the major unit). e.g. London
# small-caps quote in pence (GBp/GBX) = GBP/100; some JSE lines in ZAc, etc.
# yfinance reports the listing price in these minor units, so the USD factor
# is (major-currency rate) / 100.
_MINOR_UNIT = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ZAX": "ZAR", "ILA": "ILS"}


def usd_factor(currency: str | None, fx: dict) -> float | None:
    """USD conversion factor for a listing currency, given an fx dict from
    `get_fx_to_usd`. USD -> 1.0. Minor-unit quotes (pence etc.) divide by 100.
    Returns None when the rate is unknown so callers can avoid a wrong figure.
    """
    if not currency or currency == "USD":
        return 1.0
    rate = fx.get(currency)
    if rate is None:
        return None
    return rate / 100.0 if currency in _MINOR_UNIT else rate


@st.cache_data(ttl=3600)  # FX rates don't move much intraday
def get_fx_to_usd(currencies: tuple) -> dict:
    """Returns {currency_code: rate_to_usd}. USD maps to 1.0; unknown / failed
    lookups map to None so the caller can show '—' instead of a wrong figure.

    For minor-unit currencies (GBp pence, etc.) we fetch the MAJOR currency rate
    and store it under the original key; the /100 is applied by `usd_factor`.
    """
    result = {"USD": 1.0}
    for ccy in currencies:
        if not ccy or ccy == "USD" or ccy in result:
            continue
        major = _MINOR_UNIT.get(ccy, ccy)  # GBp -> GBP for the FX pair lookup
        try:
            pair = f"{major}USD=X"
            info = yf.Ticker(pair).fast_info
            rate = info.last_price
            result[ccy] = float(rate) if rate else None
        except Exception:
            result[ccy] = None
    return result


# Longest a page will wait for Yahoo on one call.
YF_DEADLINE_S = 20

# yf.download is not thread-safe: it gathers each call's results in
# module-level dicts (yfinance.shared._DFS / _ERRORS) that the next call
# resets. Streamlit serves every visitor from its own thread of one process,
# so two pages loading at the same moment overwrite each other's downloads.
# Measured on 2026-09-23: a 'SPY' download run alongside Le Bâtisseur's
# holdings came back as Circle in six trials out of six, and the live page
# published "S&P 500 -17.64%, alpha +19.00%" (Circle's return) where the
# truth was +7.41% and -6.05%. The same collision produced the morning's
# Nasdaq 100 anchored on 5 May, Le Bâtisseur's start date. Every download
# made by the app goes through yf_download and therefore through this lock.
_YF_LOCK = threading.Lock()


def yf_download(*args, deadline: float | None = None, **kwargs):
    """yf.download with a hard deadline. Raises TimeoutError past it.

    A public page must never wait on Yahoo without limit. On 2026-09-23 Le
    Bâtisseur's page sat on "Running get_history(...)" for more than five
    minutes on Streamlit Cloud, while the same 28-ticker download took about a
    second from any other machine: Yahoo throttles the shared cloud IP and
    yfinance keeps retrying. Worse, st.cache_data serialises concurrent calls
    with the same arguments, so one stuck download kept every visitor waiting
    behind it. The call now runs in its own daemon thread; past the deadline
    the page moves on without that data (and the stray thread dies on its own
    request timeout, without blocking anything).
    """
    kwargs.setdefault("timeout", 10)
    # yf.download collects its results in module-level dicts
    # (yfinance.shared._DFS) with no lock: two calls running at the same time
    # in one process overwrite each other's results. See _YF_LOCK.
    return _with_deadline(lambda: yf.download(*args, **kwargs), deadline, lock=_YF_LOCK)


def yf_ticker_history(ticker: str, deadline: float | None = None, **kwargs):
    """Ticker(t).history(...) with the same hard deadline, and no lock.

    Used for single instruments such as benchmarks. Unlike yf.download it does
    not go through the shared module-level dicts: measured on 2026-09-23, it
    returned the right series six times out of six while a 28-ticker
    yf.download ran alongside. Keeping it off _YF_LOCK also means a stuck
    holdings download can never hold up the benchmark, which is the figure
    that matters most on the page.
    """
    return _with_deadline(lambda: yf.Ticker(ticker).history(**kwargs), deadline)


def _with_deadline(fn, deadline: float | None, lock: "threading.Lock | None" = None):
    deadline = YF_DEADLINE_S if deadline is None else deadline
    box: dict = {}

    def _run():
        if lock is not None and not lock.acquire(timeout=deadline):
            box["err"] = TimeoutError("another Yahoo download is still running")
            return
        try:
            box["df"] = fn()
        except Exception as exc:          # surfaced to the caller below
            box["err"] = exc
        finally:
            if lock is not None:
                lock.release()

    worker = threading.Thread(target=_run, daemon=True, name="yf-call")
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        raise TimeoutError(f"no answer from Yahoo within {deadline:.0f}s")
    if "err" in box:
        raise box["err"]
    return box.get("df")


class BenchmarkUnavailable(RuntimeError):
    """The benchmark price series does not cover the portfolio's base date.

    Raised instead of returning a series that would be normalised on the wrong
    day. See `get_benchmark_index`.
    """


# How far before the anchor we ask for data, so that a market holiday on the
# anchor day still leaves a close to normalise on.
BENCH_ANCHOR_BUFFER_DAYS = 12
# Share of the business days between the first and last point a healthy series
# must contain (holidays cost ~4%, a throttled response costs far more).
BENCH_MIN_COVERAGE = 0.80


@st.cache_data(ttl=3600)
def get_benchmark_index(ticker: str, anchor: str, end: str | None = None):
    """Base-100 index of one benchmark, anchored on the close of `anchor` (T-1).

    Fetched on its own, NOT as a column of the holdings batch, and validated
    before it is returned:

      * the series must reach back to the anchor day (or the last close before
        it, for a holiday);
      * it must contain at least BENCH_MIN_COVERAGE of the business days it
        spans.

    Why this is not paranoia. Until 2026-09-23 the benchmark was read from the
    same 20+ ticker `yf.download` as the holdings and normalised on whatever
    row came first (`raw.iloc[0]`). Yahoo throttles those batches from cloud
    IPs and answers with a truncated column; nothing said so. On that day the
    live site anchored Le Visionnaire's Nasdaq 100 on 2026-05-05 instead of
    2026-04-10 and published "Nasdaq 100 +9.02%, alpha +11.31%" where the truth
    was +21.60% and -1.27%. A silently wrong alpha is the single worst number
    this site can print, so an incomplete answer now raises and the page shows
    no benchmark at all. st.cache_data does not memoise exceptions, so the
    fetch is simply retried on the next run.
    """
    anchor_ts = pd.Timestamp(anchor)
    start = (anchor_ts - pd.Timedelta(days=BENCH_ANCHOR_BUFFER_DAYS)).date().isoformat()
    end = end or (date.today() + timedelta(days=1)).isoformat()

    last_error = None
    for attempt in range(2):
        try:
            raw = yf_ticker_history(ticker, start=start, end=end, auto_adjust=True)
        except TimeoutError as exc:       # Yahoo is not answering: do not wait twice
            last_error = f"{ticker}: {exc}"
            break
        except Exception as exc:          # network / parsing failure
            last_error = f"{ticker}: download failed ({exc})"
            continue
        if raw is None or raw.empty or "Close" not in raw:
            last_error = f"{ticker}: empty response"
            continue

        s = raw["Close"]
        if isinstance(s, pd.DataFrame):
            # Take the column by name, never "the first one": a response that
            # does not carry the requested ticker is someone else's data.
            if ticker not in s.columns:
                last_error = (f"{ticker}: response carried {list(s.columns)[:3]} "
                              f"instead of the requested ticker")
                continue
            s = s[ticker]
        s = s.dropna()
        if s.empty:
            last_error = f"{ticker}: no close in response"
            continue
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)
        s.index = pd.to_datetime(s.index.date)

        at_or_before = s[s.index <= anchor_ts]
        if at_or_before.empty:
            last_error = (f"{ticker}: series starts {s.index[0].date()}, after the "
                          f"base date {anchor_ts.date()}")
            continue

        base_date = at_or_before.index[-1]
        base = float(at_or_before.iloc[-1])
        if not base > 0:
            last_error = f"{ticker}: non-positive close on {base_date.date()}"
            continue

        idx = s[s.index >= base_date] / base * 100.0
        expected = len(pd.bdate_range(idx.index[0], idx.index[-1]))
        if expected and len(idx) < BENCH_MIN_COVERAGE * expected:
            last_error = (f"{ticker}: only {len(idx)} of ~{expected} sessions "
                          f"returned between {idx.index[0].date()} and {idx.index[-1].date()}")
            continue
        return idx

    raise BenchmarkUnavailable(last_error or f"{ticker}: unavailable")


@st.cache_data(ttl=3600)  # Refresh every hour
def _get_history_cached(tickers: tuple, start: str, benchmarks: tuple) -> pd.DataFrame:
    """The download itself. Raises on failure so that st.cache_data does not
    memoise an empty frame for an hour (see get_history)."""
    end = datetime.today().strftime("%Y-%m-%d")
    all_tickers = list(set(list(tickers) + list(benchmarks)))

    raw = yf_download(all_tickers, start=start, end=end, auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        raise RuntimeError("empty response")

    # yfinance returns MultiIndex columns when multiple tickers
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw["Close"]
        foreign = [c for c in raw.columns if c not in all_tickers]
        if foreign:                       # another call's data: refuse it
            raise RuntimeError(f"response carried unrequested tickers {foreign[:3]}")
    else:
        # Single ticker: raw is a DataFrame with OHLCV columns
        raw = raw[["Close"]].rename(columns={"Close": all_tickers[0]})

    # Normalize index to date-only (strip timezone + time component)
    if raw.index.tz is not None:
        raw.index = raw.index.tz_localize(None)
    raw.index = pd.to_datetime(raw.index.date)
    return raw.dropna(how="all")


def get_history(tickers: tuple, start: str, benchmarks: tuple = ("SPY", "QQQ")) -> pd.DataFrame:
    """
    Daily closing prices for tickers + benchmarks from start to today.
    Returns a DataFrame with tickers as columns, date as index, or an empty
    DataFrame when Yahoo does not answer in time. Missing tickers are silently
    dropped.

    `benchmarks` defaults to ('SPY', 'QQQ') for backwards compat (Visionnaire).
    Pass ('BTC-USD', 'MSTR') for Le Nakamoto, etc.
    """
    try:
        return _get_history_cached(tickers, start, benchmarks)
    except Exception:
        return pd.DataFrame()


get_history.clear = _get_history_cached.clear   # callers used to clear the cache directly


# STRC monthly dividend schedule: ~$0.9583/share on the last business day of each month
# (11.5% annual on $100 par = $11.50/yr / 12 months)
STRC_MONTHLY_DIV = 100.0 * 0.115 / 12  # ≈ 0.9583 per share per month


def _strc_dividend_dates(entry_str: str) -> list[tuple]:
    """
    Return list of (payment_date, div_per_share) for STRC since entry_date.
    STRC pays on the last business day of each month.
    Only includes months that have fully elapsed (payment date <= today).
    """
    from pandas.tseries.offsets import BMonthEnd
    entry = pd.Timestamp(entry_str)
    today = pd.Timestamp(date.today())
    payments = []
    # Start from end of month of entry
    payment = entry + BMonthEnd(1)
    while payment <= today:
        payments.append((payment, STRC_MONTHLY_DIV))
        payment = payment + BMonthEnd(1)
    return payments


@st.cache_data(ttl=600)  # 10 min — cron rewrites every 24h, no need to refresh more often
def get_total_return_factor_from_db(tickers: tuple, entry_dates: tuple,
                                    prices_at_entry: tuple) -> dict:
    """[NEW] Read pre-computed dividend reinvestment factors from Supabase.

    Same signature and return shape as `get_total_return_factor` so callers
    can swap with a one-line import change.

    Reads `dividend_factors` table populated by daily_refresh.py cron.
    Fallback to {shares_factor: 1.0, div_return_pct: 0.0} for any pair
    missing from the table (e.g., newly added position before next cron run).

    `prices_at_entry` is accepted for signature compatibility but unused —
    the factor depends only on (ticker, entry_date) since the cron uses the
    actual reinvestment prices at each dividend payment date.
    """
    from utils.data import get_client
    if not tickers:
        return {}

    sb = get_client()
    pairs = list(zip(tickers, entry_dates))
    unique_tickers = sorted(set(tickers))

    try:
        rows = (
            sb.table("dividend_factors")
            .select("ticker, entry_date, shares_factor, div_return_pct")
            .in_("ticker", unique_tickers)
            .execute()
            .data
        )
    except Exception:
        # Graceful fallback: table missing or DB down → all factors = 1.0
        return {tk: {"shares_factor": 1.0, "div_return_pct": 0.0} for tk in tickers}

    by_pair = {(r["ticker"], r["entry_date"]): r for r in rows}
    result = {}
    for tk, ed in pairs:
        r = by_pair.get((tk, ed))
        if r is None:
            result[tk] = {"shares_factor": 1.0, "div_return_pct": 0.0}
        else:
            result[tk] = {
                "shares_factor":  float(r["shares_factor"]),
                "div_return_pct": float(r["div_return_pct"]),
            }
    return result


@st.cache_data(ttl=3600)
def get_total_return_factor(tickers: tuple, entry_dates: tuple, prices_at_entry: tuple) -> dict:
    """[LEGACY — live yfinance] Compute total return factor for each ticker
    assuming dividend reinvestment.

    For each dividend payment since entry:
        shares multiplied by (1 + div_per_share / price_on_payment_date)

    Slow at scale (~30s for 50 tickers) — see `get_total_return_factor_from_db`
    for the pre-computed Supabase version (read in <100ms).

    Returns dict {ticker: {"shares_factor": float, "div_return_pct": float}}
    where shares_factor = accumulated shares per initial share (>= 1.0)
    and div_return_pct = (shares_factor - 1) * 100
    """
    result = {}
    today_str = date.today().isoformat()

    for ticker, entry_str, entry_price in zip(tickers, entry_dates, prices_at_entry):
        try:
            if ticker == "STRC":
                payments = _strc_dividend_dates(entry_str)
                if not payments:
                    result[ticker] = {"shares_factor": 1.0, "div_return_pct": 0.0}
                    continue
                # Need price history for STRC on payment dates
                hist = yf_download("STRC", start=entry_str, end=today_str,
                                   auto_adjust=True, progress=False)
                if isinstance(hist.columns, pd.MultiIndex):
                    hist = hist["Close"]["STRC"]
                else:
                    hist = hist["Close"]
                hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index

                shares = 1.0
                for pay_date, div in payments:
                    # Price on or before payment date
                    available = hist[hist.index <= pay_date]
                    price_on_day = float(available.iloc[-1]) if not available.empty else 100.0
                    shares *= (1 + div / price_on_day)

                result[ticker] = {
                    "shares_factor": round(shares, 6),
                    "div_return_pct": round((shares - 1) * 100, 4),
                }
                continue

            entry_ts = pd.Timestamp(entry_str)
            divs = yf.Ticker(ticker).dividends
            if divs.empty:
                result[ticker] = {"shares_factor": 1.0, "div_return_pct": 0.0}
                continue

            # Normalize timezone
            if divs.index.tz is not None:
                divs.index = divs.index.tz_localize(None)
            since = divs[divs.index >= entry_ts]

            if since.empty:
                result[ticker] = {"shares_factor": 1.0, "div_return_pct": 0.0}
                continue

            # Get price history for reinvestment pricing
            hist = yf_download(ticker, start=entry_str, end=today_str,
                               auto_adjust=True, progress=False)
            if isinstance(hist.columns, pd.MultiIndex):
                hist = hist["Close"][ticker]
            else:
                hist = hist["Close"]
            if hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)

            shares = 1.0
            for pay_date, div in since.items():
                available = hist[hist.index <= pay_date]
                if available.empty:
                    continue
                price_on_day = float(available.iloc[-1])
                if price_on_day > 0:
                    shares *= (1 + div / price_on_day)

            result[ticker] = {
                "shares_factor": round(shares, 6),
                "div_return_pct": round((shares - 1) * 100, 4),
            }

        except Exception:
            result[ticker] = {"shares_factor": 1.0, "div_return_pct": 0.0}

    return result
