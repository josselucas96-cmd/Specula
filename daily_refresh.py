"""Daily refresh of daily_holdings — designed to run from a GitHub Action cron.

Goal
----
After every US market close (or on weekends/holidays), write the day's row
in `daily_holdings` for each portfolio. This makes the public chart EOD-
accurate without depending on visitor traffic (`lazy_write_holdings`, the
visitor-triggered version, has holes when nobody visits the site between
close and midnight).

Runs 7/7 since 2026-05-16. Weekend/holiday rationale: the Nakamoto
benchmark is Bitcoin (24/7). On Sat/Sun, the BTC line extends past the
portfolio line, creating a visual disconnect. By writing weekend rows
that propagate the latest equity close, the portfolio line aligns
timewise with Bitcoin (flat over the weekend, since equities don't move).
Visionnaire/Bâtisseur are unaffected (their benchmarks are 5/7 too).

Run
---
Locally (debug):
    SUPABASE_URL=... SUPABASE_KEY=... python daily_refresh.py
    # or pass --date 2026-05-15 to backfill a specific past day

GitHub Action (production):
    See .github/workflows/daily-refresh.yml — runs daily 22:07 UTC
    (~23-00:00 CH, ~1-2h after US market close on weekdays).

What it writes
--------------
For each portfolio (visionnaire, batisseur, nakamoto):
  - 1 row per active ticker: shares × latest_close ≤ target_date = value
  - 1 CASH row: shares = $ cash, price = 1.0

On weekend/holiday: yfinance has no Close for target_date, so we fall
back to the latest available close ≤ target_date (= previous trading
day). Shares and cash are still derived from the CURRENT positions/
transactions (reflects any moves committed at any time of day).

Cash $ is derived (same logic as utils/data.get_cash_amount):
  - baseline = latest CASH row strictly before target_date
  - + Σ same-day transaction deltas (TRIM/CLOSE proceeds, IN/SWITCH costs)

Idempotent (upsert on portfolio_id, date, ticker).
"""
import argparse
import os
import sys
from datetime import date as _date

import yfinance as yf
from supabase import create_client


INITIAL_CAPITAL_FALLBACK = 1_000_000.0

# ── FX (standalone copy of utils.market.usd_factor) ──────────────────────────
# The cron can't import utils.market (no streamlit installed in the Action), so
# this mirrors that logic. Positions/NAV are USD; foreign listings must be
# converted to USD. Minor-unit quotes (pence GBp etc.) divide by 100.
_MINOR_UNIT = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ZAX": "ZAR", "ILA": "ILS"}


def _fx_to_usd(currencies, as_of: str | None = None) -> dict:
    """{currency: USD rate}. With `as_of` (YYYY-MM-DD) use the FX close ≤ that
    date so a backfilled row is valued at that day's rate, not today's (the
    live rate was applied to every date before 2026-09-19, which left ~0.1-1%
    noise on CSU.TO / EUR names in past rows). Falls back to the live quote."""
    rates = {"USD": 1.0}
    for ccy in set(c for c in currencies if c):
        if ccy == "USD" or ccy in rates:
            continue
        major = _MINOR_UNIT.get(ccy, ccy)
        pair = f"{major}USD=X"
        rate = None
        if as_of:
            try:
                from datetime import datetime, timedelta
                import pandas as pd
                t = datetime.strptime(as_of, "%Y-%m-%d")
                df = yf.download(pair, start=(t - timedelta(days=10)).strftime("%Y-%m-%d"),
                                 end=(t + timedelta(days=1)).strftime("%Y-%m-%d"),
                                 progress=False, auto_adjust=False)
                if not df.empty and "Close" in df.columns:
                    s = df["Close"]
                    if isinstance(s, pd.DataFrame):
                        s = s.iloc[:, 0]
                    # yfinance FX series carry occasional NaN rows (e.g. EURUSD=X
                    # on 2026-09-17): skip them, otherwise the NaN propagates into
                    # price_usd and the upsert fails ("not JSON compliant").
                    s = s[s.index <= pd.Timestamp(as_of)].dropna()
                    if not s.empty:
                        rate = float(s.iloc[-1])
            except Exception:
                rate = None
        if rate is None or rate != rate:  # None or NaN
            try:
                rate = float(yf.Ticker(pair).fast_info.last_price)
                if rate != rate:
                    rate = None
            except Exception:
                rate = None
        rates[ccy] = rate
    return rates


def _usd_factor(ccy, fx) -> float | None:
    if not ccy or ccy == "USD":
        return 1.0
    r = fx.get(ccy)
    if r is None:
        return None
    return r / 100.0 if ccy in _MINOR_UNIT else r


def _currency_map(sb, tickers) -> dict:
    """{ticker: listing currency} read from current_prices (written by the cron).
    Currency is static per ticker, so a prior run's value is fine."""
    out = {}
    if not tickers:
        return out
    try:
        for r in (sb.table("current_prices").select("ticker, currency")
                  .in_("ticker", list(tickers)).execute().data):
            out[r["ticker"]] = r.get("currency") or "USD"
    except Exception:
        pass
    # Fallback for brand-new tickers not yet in current_prices: fetch live, so a
    # foreign newcomer isn't written without FX (which would inflate it).
    for tk in tickers:
        if tk not in out:
            try:
                out[tk] = getattr(yf.Ticker(tk).fast_info, "currency", None) or "USD"
            except Exception:
                out[tk] = "USD"
    return out

# PORTFOLIOS is now read dynamically from the `portfolios` table at runtime
# (see `get_all_portfolio_ids` below). This means any new portfolio added to
# the DB — including hidden sandboxes like `test` — is automatically picked
# up on the next cron run without needing a code push.


def get_all_portfolio_ids(sb) -> list[str]:
    """Return all portfolio IDs from the DB (active and inactive).

    Inactive portfolios (e.g., the `test` sandbox with is_active=False) are
    included so the cron keeps their daily_holdings up to date. The Admin
    cockpit shows them; only the public landing filters them out.
    """
    rows = sb.table("portfolios").select("id").order("display_order").execute().data
    return [r["id"] for r in rows]


def fetch_close_price(ticker: str, target_date_str: str, retries: int = 3) -> tuple[float | None, str | None]:
    """Fetch the latest Close ≤ target_date_str.

    On a trading day: returns the close for that day.
    On weekend/holiday: returns the close from the most recent trading day
    before target_date_str (so equity tickers carry forward Friday's close
    into Sat/Sun rows).

    Retries on transient failures (empty result / network / rate-limit).
    yfinance gets rate-limited from GitHub Actions' shared IPs, which caused
    5 days of missing data 2026-05-18→22 (cf. the self-healing backfill below).

    Returns (price, actual_close_date_str) or (None, None) if no data found.
    """
    from datetime import datetime, timedelta
    import time
    import pandas as pd

    target = datetime.strptime(target_date_str, "%Y-%m-%d")
    start = (target - timedelta(days=10)).strftime("%Y-%m-%d")
    end = (target + timedelta(days=1)).strftime("%Y-%m-%d")

    for attempt in range(1, retries + 1):
        try:
            # Fetch a 10-day window ending at target_date to find the latest available
            df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
            if df.empty or "Close" not in df.columns:
                raise ValueError("empty frame")

            # Keep only rows with date ≤ target_date_str and a REAL close. Yahoo
            # sometimes returns a row for a date with an empty (NaN) Close on
            # European/Brazilian listings (seen 2026-09-17 on EL.PA, IBE.MC,
            # RMS.PA, ALCPB.PA, CASH3.SA): treat it like a non-trading day for
            # that ticker and carry the last valid close forward (reported as
            # "propagated") instead of writing NaN or dropping the position.
            close_col = df["Close"]
            if isinstance(close_col, pd.DataFrame):
                close_col = close_col.iloc[:, 0]
            close_col = close_col[close_col.index <= pd.Timestamp(target_date_str)].dropna()
            if close_col.empty:
                return None, None  # genuinely no data ≤ target (e.g. pre-listing)

            last_idx = close_col.index[-1]
            return float(close_col.iloc[-1]), last_idx.strftime("%Y-%m-%d")
        except Exception as e:
            if attempt < retries:
                time.sleep(2 * attempt)  # 2s, 4s backoff — let rate-limit cool off
                continue
            print(f"  ! {ticker}: yfinance fetch error after {retries} tries ({e})", flush=True)
            return None, None


def get_initial_capital(sb, portfolio_id: str) -> float:
    """Read initial_capital_<pid> setting (with fallback to legacy key for visionnaire)."""
    keys = [f"initial_capital_{portfolio_id}"]
    if portfolio_id == "visionnaire":
        keys.append("initial_capital")
    for k in keys:
        row = sb.table("settings").select("value").eq("key", k).execute().data
        if row and row[0].get("value"):
            try:
                return float(row[0]["value"])
            except (TypeError, ValueError):
                continue
    return INITIAL_CAPITAL_FALLBACK


def derive_cash_at_date(sb, portfolio_id: str, target_date_str: str,
                       initial_capital: float) -> float:
    """Derive cash $ at end of target_date_str, replicating get_cash_amount logic.

    1. Baseline = latest CASH row strictly before target_date_str (or initial_capital)
    2. + Σ same-day transaction $-deltas
    """
    rows = (
        sb.table("daily_holdings")
        .select("date, value")
        .eq("portfolio_id", portfolio_id)
        .eq("ticker", "CASH")
        .lt("date", target_date_str)
        .order("date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    baseline = float(rows[0]["value"]) if rows else float(initial_capital)

    txns = (
        sb.table("transactions")
        .select("action, weight_in, weight_out, price_out, entry_price_out")
        .eq("portfolio_id", portfolio_id)
        .eq("date", target_date_str)
        .execute()
        .data
    )
    delta = 0.0
    for t in txns:
        a = (t.get("action") or "").upper()
        if a == "IN":
            delta -= float(t.get("weight_in") or 0) * initial_capital / 100.0
        elif a in ("TRIM", "OUT"):
            w = float(t.get("weight_out") or 0)
            pru = float(t.get("entry_price_out") or 0)
            p = float(t.get("price_out") or 0)
            if pru > 0:
                delta += (w * initial_capital / 100.0 / pru) * p
        elif a == "SWITCH":
            w_in = float(t.get("weight_in") or 0)
            w_out = float(t.get("weight_out") or 0)
            pru = float(t.get("entry_price_out") or 0)
            p = float(t.get("price_out") or 0)
            if pru > 0:
                delta += (w_out * initial_capital / 100.0 / pru) * p
            delta -= w_in * initial_capital / 100.0
        # DRIP / SPLIT: cash-neutral
    return baseline + delta


class BookChangedAfterDate(Exception):
    """Raised when asked to write a PAST date for a portfolio that had a move
    (transaction) after that date. `positions` only holds the CURRENT book, so
    such a row would mix post-move shares with pre-move cash — exactly what
    corrupted the Bâtisseur 2026-06-03 row (+3.5% phantom NAV). Those dates
    need an explicit as-of reconstruction, never an automatic backfill."""


class SuspiciousValueJump(Exception):
    """Raised when a position's value would jump by more than JUMP_RATIO vs its
    last stored value. A stock split / reverse split that was not applied to
    `positions.shares` shows up exactly like this (yfinance restates history,
    shares don't): e.g. ALCPB.PA 1:10 on 2026-09-08 would have written a
    +$770K phantom on the Nakamoto. Run utils.data.apply_split first, or pass
    --allow-jumps if the move is genuine."""


ALLOW_JUMPS = False       # set by --allow-jumps
JUMP_RATIO = 2.5          # value_new / value_prev outside [1/2.5, 2.5] → suspicious
JUMP_MIN_USD = 5_000.0    # ignore tiny positions where ratios are noisy


def _has_moves_after(sb, portfolio_id: str, target_date_str: str) -> list[str]:
    """Tickers with a transaction dated strictly after target_date (cash-neutral
    SPLIT/DRIP rows excluded, they don't change the $ book)."""
    rows = (
        sb.table("transactions")
        .select("date, action, ticker_in, ticker_out")
        .eq("portfolio_id", portfolio_id)
        .gt("date", target_date_str)
        .execute()
        .data
    )
    out = []
    for t in rows:
        if (t.get("action") or "").upper() in ("SPLIT", "DRIP"):
            continue
        out.append(t.get("ticker_in") or t.get("ticker_out") or "?")
    return sorted(set(out))



def _split_ratio_from_reason(reason: str | None) -> float | None:
    """SPLIT transactions carry the ratio in their reason: '0.1-for-1 split …'."""
    import re
    m = re.search(r"(\d*\.?\d+)-for-1", reason or "")
    return float(m.group(1)) if m else None


def book_as_of(sb, portfolio_id: str, target_date_str: str, initial_capital: float) -> tuple[dict, int]:
    """Reconstruct {ticker: shares} as it stood at the END of target_date by
    reversing, newest first, every transaction dated after it onto the current
    book. Uses the same share arithmetic as the trade functions
    (IN: weight×capital/price_in; TRIM/OUT: weight×capital/PRU; splits are
    kept on the post-split basis because the price feed restates history).
    Returns (book, number_of_transactions_reversed). This is what makes a
    late backfill safe: on 2026-06-04 the missing 3 June row was rebuilt
    with the post-rebalance positions and produced a +3.5% phantom NAV."""
    book = {}
    for p in (sb.table("positions").select("ticker, shares").eq("portfolio_id", portfolio_id)
              .eq("is_active", True).execute().data):
        if p.get("ticker") and float(p.get("shares") or 0) > 0:
            book[p["ticker"]] = float(p["shares"])
    txns = (sb.table("transactions")
            .select("date, executed_at, action, ticker_in, ticker_out, weight_in, weight_out, price_in, entry_price_out, reason")
            .eq("portfolio_id", portfolio_id).gt("date", target_date_str).execute().data)
    txns.sort(key=lambda t: (t.get("date") or "", t.get("executed_at") or ""), reverse=True)
    cap = float(initial_capital)
    for t in txns:
        a = (t.get("action") or "").upper()
        if a in ("IN", "SWITCH") and t.get("ticker_in"):
            price = float(t.get("price_in") or 0)
            if price > 0:
                tk = t["ticker_in"]
                book[tk] = book.get(tk, 0.0) - float(t.get("weight_in") or 0) * cap / 100.0 / price
                if book[tk] <= 1e-6:
                    book.pop(tk, None)
        if a in ("TRIM", "OUT", "SWITCH") and t.get("ticker_out"):
            pru = float(t.get("entry_price_out") or 0)
            if pru > 0:
                tk = t["ticker_out"]
                book[tk] = book.get(tk, 0.0) + float(t.get("weight_out") or 0) * cap / 100.0 / pru
        # SPLIT: deliberately NOT reversed. The price feed restates the whole
        # history on the post-split basis, so a pre-split date must be valued
        # with post-split shares x restated close (value-identical). Reversing
        # the split here would overvalue that day by 1/ratio (ALCPB.PA: x10).
        # DRIP: cash-neutral and tiny; ignored in the reconstruction.
    return {k: round(v, 8) for k, v in book.items()}, len(txns)



def detect_split(ticker: str, around_date_str: str, window_days: int = 14):
    """(ratio, split_date) of the most recent split in the `window_days` before
    around_date (inclusive), from Yahoo's corporate-actions feed, else None.
    Ratio as Yahoo reports it: 0.1 for a 1-for-10 reverse split, 2.0 for 2-for-1."""
    from datetime import datetime, timedelta
    import pandas as pd
    try:
        t = datetime.strptime(around_date_str, "%Y-%m-%d")
        df = yf.download(ticker, start=(t - timedelta(days=window_days)).strftime("%Y-%m-%d"),
                         end=(t + timedelta(days=1)).strftime("%Y-%m-%d"),
                         actions=True, auto_adjust=False, progress=False)
        if df.empty or "Stock Splits" not in df.columns:
            return None
        col = df["Stock Splits"]
        if isinstance(col, pd.DataFrame):
            col = col.iloc[:, 0]
        ev = col[col.fillna(0) != 0]
        if ev.empty:
            return None
        return float(ev.iloc[-1]), ev.index[-1].strftime("%Y-%m-%d")
    except Exception:
        return None


def apply_split_db(sb, portfolio_id: str, ticker: str, ratio: float, split_date: str, note: str = "") -> bool:
    """shares x ratio, units x ratio, PRU / ratio (value-neutral) + a SPLIT
    transaction dated split_date. Idempotent per (portfolio, ticker, date)."""
    already = (sb.table("transactions").select("id").eq("portfolio_id", portfolio_id)
               .eq("action", "SPLIT").eq("ticker_in", ticker).eq("date", split_date).execute().data)
    if already:
        return False
    pos = (sb.table("positions").select("id, shares, units, entry_price").eq("portfolio_id", portfolio_id)
           .eq("ticker", ticker).eq("is_active", True).execute().data)
    if not pos:
        return False
    p = pos[0]
    upd = {"shares": round(float(p.get("shares") or 0) * ratio, 8),
           "entry_price": round(float(p.get("entry_price") or 0) / ratio, 6)}
    if p.get("units") is not None:
        upd["units"] = round(float(p["units"]) * ratio, 8)
    sb.table("positions").update(upd).eq("id", p["id"]).execute()
    from datetime import datetime, timezone
    sb.table("transactions").insert({
        "portfolio_id": portfolio_id, "date": split_date, "action": "SPLIT", "ticker_in": ticker,
        "price_in": upd["entry_price"],
        "reason": f"{ratio}-for-1 split (auto-detected by daily_refresh{(' ' + note) if note else ''}): shares x {ratio}, PRU / {ratio}",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return True


def _previous_values(sb, portfolio_id: str, target_date_str: str) -> dict:
    """{ticker: value} of the latest stored row strictly before target_date."""
    last = (
        sb.table("daily_holdings")
        .select("date")
        .eq("portfolio_id", portfolio_id)
        .lt("date", target_date_str)
        .order("date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not last:
        return {}
    rows = (
        sb.table("daily_holdings")
        .select("ticker, value")
        .eq("portfolio_id", portfolio_id)
        .eq("date", last[0]["date"])
        .execute()
        .data
    )
    return {r["ticker"]: float(r["value"] or 0) for r in rows}


def refresh_portfolio(sb, portfolio_id: str, target_date_str: str) -> dict:
    """Write daily_holdings rows for one portfolio. Returns summary dict.

    Raises BookChangedAfterDate when target_date is in the past and the book
    moved since (see class docstring) and SuspiciousValueJump on a >JUMP_RATIO
    change in any position's value (unapplied split) unless ALLOW_JUMPS.
    """
    initial_capital = get_initial_capital(sb, portfolio_id)
    as_of_note = ""
    if target_date_str < _date.today().isoformat() and _has_moves_after(sb, portfolio_id, target_date_str):
        # The current book is NOT the book of that day: rebuild it from the trade log.
        book, n_rev = book_as_of(sb, portfolio_id, target_date_str, initial_capital)
        if not book or any(v <= 0 for v in book.values()):
            raise BookChangedAfterDate(
                f"{portfolio_id}: could not reconstruct the book as of {target_date_str} "
                f"({n_rev} later transactions) -- refusing to write."
            )
        positions = [{"ticker": tk, "shares": sh} for tk, sh in book.items()]
        as_of_note = f" (as-of book: {n_rev} later transaction(s) reversed)"
        print(f"  {portfolio_id} {target_date_str}: book reconstructed as of that date{as_of_note}", flush=True)
    else:
        positions = (
            sb.table("positions")
            .select("ticker, shares")
            .eq("portfolio_id", portfolio_id)
            .eq("is_active", True)
            .execute()
            .data
        )
    cash_amount = derive_cash_at_date(sb, portfolio_id, target_date_str, initial_capital)

    # FX: convert each foreign ticker's native close to USD (value = shares ×
    # price_usd). Currency per ticker from current_prices; usd_factor handles
    # pence. FX is taken at target_date so backfilled rows use that day's rate.
    ccy_map = _currency_map(sb, [p["ticker"] for p in positions if p.get("ticker")])
    fx = _fx_to_usd(ccy_map.values(), as_of=target_date_str)
    prev_values = _previous_values(sb, portfolio_id, target_date_str)

    rows = [{
        "portfolio_id": portfolio_id,
        "date":         target_date_str,
        "ticker":       "CASH",
        "shares":       round(cash_amount, 2),
        "price":        1.0,
        "value":        round(cash_amount, 2),
    }]
    nav = cash_amount
    skipped = []
    propagated = []  # tickers where price comes from a date before target
    for p in positions:
        tk = p["ticker"]
        shares = float(p.get("shares") or 0)
        if shares <= 0 or not tk:
            continue
        price, actual_date = fetch_close_price(tk, target_date_str)
        if price is None:
            skipped.append(tk)
            continue
        if actual_date != target_date_str:
            propagated.append((tk, actual_date))
        f = _usd_factor(ccy_map.get(tk, "USD"), fx) or 1.0
        price_usd = price * f
        value = shares * price_usd
        nav += value
        rows.append({
            "portfolio_id": portfolio_id,
            "date":         target_date_str,
            "ticker":       tk,
            "shares":       round(shares, 8),
            "price":        round(price_usd, 4),
            "value":        round(value, 2),
        })

    # Sanity: a position whose $ value jumps >JUMP_RATIO vs its last stored row
    # is almost always an unapplied split (or a wrong currency), not a real move.
    # Refuse the whole portfolio write rather than freeze a phantom NAV.
    if not ALLOW_JUMPS and prev_values:
        jumps = []
        for r in rows:
            if r["ticker"] == "CASH":
                continue
            prev = prev_values.get(r["ticker"])
            if not prev or prev < JUMP_MIN_USD:
                continue
            ratio = r["value"] / prev
            if ratio > JUMP_RATIO or ratio < 1.0 / JUMP_RATIO:
                # Is it a split the book does not know about yet? Yahoo restates
                # prices on the split date, so value_new/value_prev ~= 1/split_ratio.
                sp = detect_split(r["ticker"], target_date_str)
                if sp and abs(ratio * sp[0] - 1.0) < 0.25:
                    sp_ratio, sp_date = sp
                    if apply_split_db(sb, portfolio_id, r["ticker"], sp_ratio, sp_date, note=f"on {target_date_str}"):
                        print(f"  {portfolio_id}: SPLIT {sp_ratio}-for-1 on {sp_date} detected for {r['ticker']} "
                              f"-> applied (shares x {sp_ratio}, PRU / {sp_ratio})", flush=True)
                    r["shares"] = round(r["shares"] * sp_ratio, 8)
                    r["value"] = round(r["shares"] * r["price"], 2)
                    nav += r["value"] - prev * ratio  # nav was accumulated with the pre-split value
                    continue
                jumps.append(f"{r['ticker']} ${prev:,.0f} -> ${r['value']:,.0f} (x{ratio:.2f})")
        if jumps:
            raise SuspiciousValueJump(
                f"{portfolio_id} {target_date_str}: {'; '.join(jumps)} -- "
                f"unapplied split? run apply_split, or re-run with --allow-jumps."
            )

    # Upsert all rows, then make this write authoritative for the day: any row
    # of that (portfolio, date) for a ticker NOT in the book is stale (e.g. a
    # position that was not yet bought that day but had been written by an
    # earlier backfill with the wrong book) and must go, otherwise it is
    # double-counted in the NAV.
    sb.table("daily_holdings").upsert(
        rows, on_conflict="portfolio_id,date,ticker"
    ).execute()
    written = {r["ticker"] for r in rows}
    stale = [r["ticker"] for r in sb.table("daily_holdings").select("ticker")
             .eq("portfolio_id", portfolio_id).eq("date", target_date_str).execute().data
             if r["ticker"] not in written]
    if stale:
        sb.table("daily_holdings").delete().eq("portfolio_id", portfolio_id)           .eq("date", target_date_str).in_("ticker", stale).execute()
        print(f"  {portfolio_id} {target_date_str}: removed stale row(s) {stale}", flush=True)

    return {
        "portfolio": portfolio_id,
        "rows_written": len(rows),
        "nav": nav,
        "cash": cash_amount,
        "skipped_tickers": skipped,
        "propagated": propagated,
    }


def session_close_published(target_date_str: str, now_utc=None) -> tuple[bool, str | None]:
    """Has the provider published the official close of `target_date_str` yet?

    Returns (published, reference_close_date). SPY is the reference session.

    This exists because "no close for that date" has two very different causes
    and the cron used to treat them alike:

      * the date is not a trading day (weekend, US holiday) -> the previous
        close must be carried forward, which is what the 7/7 ledger wants;
      * the date IS a trading day but the provider has not written its daily
        bar yet -> carrying the previous close forward stamps a stale price on
        a real session.

    The second case is what happened on 2026-09-21 and 2026-09-22: Yahoo was
    still publishing the daily bar more than four hours after the close (the
    intraday bars were there, the daily bar was NaN), the cron ran just after
    midnight UTC because GitHub had drifted the schedule, and both rows were
    written with the previous session's closes. The ledger — and the public
    site — ran a full session behind while looking current.

    A weekday whose close is still missing less than ~36h after the fact is
    read as "not published yet", not as a holiday: the row is left unwritten
    and `backfill_recent_gaps` fills it on a later run. A hole is visible and
    self-heals; a stale row is invisible and permanent.
    """
    from datetime import datetime, timezone, timedelta
    now_utc = now_utc or datetime.now(timezone.utc)
    _, ref_date = fetch_close_price("SPY", target_date_str)
    if ref_date == target_date_str:
        return True, ref_date
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    if target.weekday() >= 5:                       # weekend: nothing to wait for
        return False, ref_date
    if (now_utc.date() - target) > timedelta(days=1):
        return False, ref_date                      # old enough to be a real holiday
    return None, ref_date                           # trading day, data not in yet


def find_stale_rows(sb, portfolio_ids: list[str], target_date_str: str,
                    lookback_days: int = 3) -> list[dict]:
    """Recent rows whose prices do not belong to their own session.

    `backfill_recent_gaps` only fills holes; it never re-reads a row that
    exists. That is why the 21 and 22 September rows, written with the previous
    session's closes while the provider was publishing late, would have stayed
    wrong for ever — nothing ever looked at them again.

    So each run re-prices the last `lookback_days` rows of every portfolio
    against the actual close of their own session. Deliberately narrow:

      * weekends are skipped — they legitimately carry Friday forward;
      * a day whose close is still unpublished is left alone;
      * every US-listed position is checked, in one download per portfolio.
        Until 2026-10-23 only the largest one was probed: on 6 October Strive
        was stamped with the previous session's close while the probe was
        right, and only the morning watchdog noticed. Foreign listings are
        skipped (their stored price includes that day's FX rate).

    Returns the (portfolio, date) pairs that need rewriting, each with the
    position that deviates most.
    """
    from datetime import datetime, timedelta
    import pandas as pd

    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    dates = [(target - timedelta(days=k)).isoformat() for k in range(lookback_days, 0, -1)]
    dates = [d for d in dates if datetime.strptime(d, "%Y-%m-%d").weekday() < 5]
    dates = [d for d in dates if session_close_published(d)[0] is True]
    if not dates:
        return []

    stale = []
    for pid in portfolio_ids:
        rows = (sb.table("daily_holdings").select("date,ticker,price,value")
                .eq("portfolio_id", pid).gte("date", dates[0]).lte("date", dates[-1])
                .execute().data)
        usable = [r for r in rows
                  if r["ticker"] != "CASH" and "." not in r["ticker"] and r.get("price")]
        if not usable:
            continue
        tickers = sorted({r["ticker"] for r in usable})
        end = (datetime.strptime(dates[-1], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            hist = yf.download(tickers if len(tickers) > 1 else tickers[0], start=dates[0], end=end,
                               progress=False, auto_adjust=False)
            close = hist["Close"]
            if isinstance(close, pd.Series):
                close = close.to_frame(name=tickers[0])
            close.index = pd.to_datetime(close.index.date)
        except Exception as e:
            print(f"  [{pid}] could not re-price its US positions: {e}", flush=True)
            continue

        written = {(r["date"], r["ticker"]): float(r["price"]) for r in usable}
        for d in dates:
            ts = pd.Timestamp(d)
            worst = None
            for t in tickers:
                # A column missing from a truncated answer is simply not checked.
                if (d, t) not in written or t not in close.columns or ts not in close.index:
                    continue
                actual = close.at[ts, t]
                if pd.isna(actual) or float(actual) <= 0:
                    continue
                dev = abs(float(actual) - written[(d, t)]) / float(actual)
                if dev > 0.005 and (worst is None or dev > worst[0]):
                    worst = (dev, t, written[(d, t)], float(actual))
            if worst:
                stale.append({"portfolio": pid, "date": d, "probe": worst[1],
                              "written": worst[2], "actual": worst[3]})
    return stale


def repair_stale_rows(sb, portfolio_ids: list[str], target_date_str: str,
                      lookback_days: int = 3) -> list[dict]:
    """Rewrite the rows `find_stale_rows` flags.

    Share counts, cash and the trade log are never touched: this corrects
    prices that were stamped from the wrong session. The rewrite goes through
    refresh_portfolio, so the book is reconstructed as of that date and the
    usual jump guard still applies.
    """
    repaired = []
    for s in find_stale_rows(sb, portfolio_ids, target_date_str, lookback_days):
        print(f"  [{s['portfolio']}] {s['date']}: {s['probe']} written at {s['written']:.2f}, "
              f"that session closed at {s['actual']:.2f} -> rewriting the day.", flush=True)
        try:
            r = refresh_portfolio(sb, s["portfolio"], s["date"])
            repaired.append({**s, "rows": r["rows_written"]})
        except Exception as e:
            print(f"  [{s['portfolio']}] {s['date']}: repair refused ({e})", flush=True)
    return repaired


def backfill_recent_gaps(sb, portfolio_ids: list[str], target_date_str: str,
                         lookback_days: int = 7) -> list[dict]:
    """Self-heal: fill any missing (portfolio, date) rows in the last N days.

    Why: a single failed cron run (e.g. yfinance rate-limit from GitHub Actions'
    shared IPs) used to leave a PERMANENT hole — nothing ever went back to fill
    it (cf. the 2026-05-18→22 gap on batisseur/nakamoto). With this, every run
    looks back `lookback_days` calendar days and writes any date a portfolio is
    missing, so a transient failure self-corrects on the next successful run.

    Dates are processed chronologically per portfolio so `derive_cash_at_date`
    chains cash baselines correctly. Existing rows are never overwritten
    (respects the daily_holdings immutability rule — we only ADD missing days).
    """
    from datetime import datetime, timedelta
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    # Candidate dates: [target-lookback, target-1]. `target` itself is written
    # by the main loop, so we don't duplicate it here.
    candidates = [
        (target - timedelta(days=k)).isoformat()
        for k in range(lookback_days, 0, -1)
    ]

    healed = []
    for pid in portfolio_ids:
        # Which of the candidate dates already have rows?
        existing = (
            sb.table("daily_holdings")
            .select("date")
            .eq("portfolio_id", pid)
            .gte("date", candidates[0])
            .lte("date", candidates[-1])
            .execute()
            .data
        )
        present = {r["date"] for r in existing}
        missing = [d for d in candidates if d not in present]
        for d in missing:
            if session_close_published(d)[0] is None:
                print(f"  [{pid}] {d}: close not published yet, leaving the gap open.", flush=True)
                continue
            try:
                r = refresh_portfolio(sb, pid, d)
                healed.append({"portfolio": pid, "date": d, "rows": r["rows_written"]})
                print(f"  [backfill] {pid} {d}: wrote {r['rows_written']} rows, "
                      f"NAV=${r['nav']:,.0f}", flush=True)
            except BookChangedAfterDate as e:
                # Not an error to retry: this date can only be rebuilt by hand
                # from the transactions log (as-of book). Leave the hole visible.
                print(f"  [backfill] {pid} {d}: SKIPPED -- {e}", flush=True)
            except Exception as e:
                print(f"  [backfill] {pid} {d}: ERROR {e}", flush=True)
    return healed


def _compute_dividend_factor(ticker: str, entry_date_str: str) -> tuple[float, float]:
    """Compute (shares_factor, div_return_pct) for one (ticker, entry_date) pair.

    Replicates utils.market.get_total_return_factor logic without Streamlit deps.
    For each dividend paid since entry_date: shares *= (1 + div / price_on_pay_date).

    Returns (1.0, 0.0) if no dividends, ticker has no history, or any error.
    """
    try:
        import pandas as pd
        from datetime import date as _dt
        today_str = _dt.today().isoformat()
        entry_ts = pd.Timestamp(entry_date_str)

        divs = yf.Ticker(ticker).dividends
        if divs.empty:
            return 1.0, 0.0
        if divs.index.tz is not None:
            divs.index = divs.index.tz_localize(None)
        since = divs[divs.index >= entry_ts]
        if since.empty:
            return 1.0, 0.0

        hist = yf.download(ticker, start=entry_date_str, end=today_str,
                           auto_adjust=True, progress=False)
        if hist.empty:
            return 1.0, 0.0
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
                shares *= (1 + float(div) / price_on_day)

        return round(shares, 6), round((shares - 1) * 100, 4)
    except Exception as e:
        print(f"  ! {ticker}@{entry_date_str}: dividend computation error ({e})", flush=True)
        return 1.0, 0.0


def refresh_dividend_factors(sb, portfolio_ids: list[str]) -> dict:
    """Upsert dividend reinvestment factor per (ticker, entry_date) into
    `dividend_factors`.

    Why: utils.market.get_total_return_factor is the biggest cold-start
    bottleneck on the public site — it calls `yf.Ticker(t).dividends` and
    `yf.download(t, ...)` for each (ticker, entry_date) pair (~50 pairs,
    ~30s total). With this table pre-computed by the cron, the site reads
    it in <100ms.

    Schema: (ticker, entry_date) PK + shares_factor, div_return_pct, fetched_at.
    """
    pairs: set[tuple[str, str]] = set()
    for pid in portfolio_ids:
        rows = (
            sb.table("positions").select("ticker, entry_date")
            .eq("portfolio_id", pid).eq("is_active", True)
            .execute().data
        )
        for r in rows:
            tk = r.get("ticker")
            ed = r.get("entry_date")
            if tk and ed:
                pairs.add((tk, ed))

    print(f"\n[dividend_factors] refreshing {len(pairs)} unique (ticker, entry_date) pairs...", flush=True)

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = []
    failed = []

    for ticker, entry_date in sorted(pairs):
        shares_factor, div_return_pct = _compute_dividend_factor(ticker, entry_date)
        if shares_factor == 1.0 and div_return_pct == 0.0:
            # Either truly no dividends (write the row so caller knows it was
            # checked), or an error was logged above. Always write — fallback
            # logic in callers handles both.
            pass
        payload.append({
            "ticker":         ticker,
            "entry_date":     entry_date,
            "shares_factor":  shares_factor,
            "div_return_pct": div_return_pct,
            "fetched_at":     now_iso,
        })

    if payload:
        sb.table("dividend_factors").upsert(
            payload, on_conflict="ticker,entry_date"
        ).execute()
        print(f"[dividend_factors] upserted {len(payload)} rows.", flush=True)
    if failed:
        print(f"[dividend_factors] FAILED: {failed}", flush=True)

    return {"ok": len(payload), "failed": failed}


def refresh_fundamentals(sb, portfolio_ids: list[str]) -> dict:
    """Upsert valuation fundamentals per unique ticker into `fundamentals`.

    Why: utils.market.get_valuation_fundamentals calls `yf.Ticker(t).info` per
    ticker — the SLOWEST yfinance endpoint (5-10s/call) and the most prone to
    rate-limiting. With this table pre-computed nightly, the site reads it in
    <100ms and the cockpit's P/S, Fwd PE, EV/mNAV columns finally fill in
    reliably.

    Fundamentals don't move intraday — daily refresh is plenty.

    Schema: ticker PK + market_cap, enterprise_value, revenue_ttm, ebitda,
    margins (ratios 0-1), free_cashflow, forward_pe, trailing_pe, analyst_rg,
    fetched_at.
    """
    import pandas as pd
    all_tickers: set[str] = set()
    for pid in portfolio_ids:
        rows = (
            sb.table("positions").select("ticker")
            .eq("portfolio_id", pid).eq("is_active", True)
            .execute().data
        )
        for r in rows:
            tk = r.get("ticker")
            if tk:
                all_tickers.add(tk)

    print(f"\n[fundamentals] refreshing {len(all_tickers)} unique tickers (tk.info — slow)...", flush=True)

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = []
    failed = []

    for tk in sorted(all_tickers):
        try:
            ticker = yf.Ticker(tk)
            info = ticker.info
            rev = info.get("totalRevenue")
            fcf = info.get("freeCashflow")
            fcf_margin = (fcf / rev) if (rev and fcf is not None and rev > 0) else None

            # Analyst consensus revenue growth (forward, +1y)
            analyst_rg = None
            try:
                rev_est = ticker.revenue_estimate
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
            if analyst_rg is None and info.get("revenueGrowth") is not None:
                analyst_rg = float(info["revenueGrowth"]) * 100

            payload.append({
                "ticker":           tk,
                "market_cap":       float(info["marketCap"]) if info.get("marketCap") is not None else None,
                "enterprise_value": float(info["enterpriseValue"]) if info.get("enterpriseValue") is not None else None,
                "revenue_ttm":      float(rev) if rev is not None else None,
                "ebitda":           float(info["ebitda"]) if info.get("ebitda") is not None else None,
                "gross_margin":     float(info["grossMargins"]) if info.get("grossMargins") is not None else None,
                "operating_margin": float(info["operatingMargins"]) if info.get("operatingMargins") is not None else None,
                "free_cashflow":    float(fcf) if fcf is not None else None,
                "fcf_margin":       float(fcf_margin) if fcf_margin is not None else None,
                "forward_pe":       float(info["forwardPE"]) if info.get("forwardPE") is not None else None,
                "trailing_pe":      float(info["trailingPE"]) if info.get("trailingPE") is not None else None,
                "analyst_rg":       float(analyst_rg) if analyst_rg is not None else None,
                "fetched_at":       now_iso,
            })
        except Exception as e:
            print(f"  ! {tk}: tk.info error ({e})", flush=True)
            failed.append(tk)

    if payload:
        # tk.info can hand back NaN/inf (e.g. forwardPE on a loss-maker); JSON
        # can't carry them and the whole upsert used to fail ("Out of range
        # float values are not JSON compliant"), leaving fundamentals stale.
        import math
        for row in payload:
            for k, v in row.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    row[k] = None
        sb.table("fundamentals").upsert(payload, on_conflict="ticker").execute()
        print(f"[fundamentals] upserted {len(payload)} rows.", flush=True)
    if failed:
        print(f"[fundamentals] FAILED: {failed}", flush=True)

    return {"ok": len(payload), "failed": failed}


def refresh_current_prices(sb, portfolio_ids: list[str]) -> dict:
    """Upsert latest price + metadata per unique ticker into `current_prices`.

    Why: visitor pages currently call yfinance directly (1 call per ticker
    × N visitors / cache_TTL). With this table populated nightly, the site
    can read prices from Supabase in 1 query — eliminating Yahoo rate-limit
    risk and dropping page load from ~30s to ~2s.

    Uses fast_info (same source as utils/market.get_prices). During off-hours
    (when this cron runs) fast_info returns the last close.

    Schema: ticker PK + price, change_pct, market_cap, currency, fetched_at.
    """
    # Gather all unique tickers across active positions in all portfolios
    all_tickers: set[str] = set()
    for pid in portfolio_ids:
        rows = (
            sb.table("positions").select("ticker")
            .eq("portfolio_id", pid).eq("is_active", True)
            .execute().data
        )
        for r in rows:
            tk = r.get("ticker")
            if tk:
                all_tickers.add(tk)

    print(f"\n[current_prices] refreshing {len(all_tickers)} unique tickers...", flush=True)

    payload = []
    failed = []
    now_iso = None
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    for tk in sorted(all_tickers):
        try:
            info = yf.Ticker(tk).fast_info
            price = info.last_price
            prev = info.previous_close
            if price is None or prev is None:
                failed.append(tk)
                continue
            mc = getattr(info, "market_cap", None)
            ccy = getattr(info, "currency", None) or "USD"
            payload.append({
                "ticker":     tk,
                "price":      round(float(price), 4),
                "change_pct": round((float(price) - float(prev)) / float(prev) * 100, 4) if prev else None,
                "market_cap": float(mc) if mc is not None else None,
                "currency":   ccy,
                "fetched_at": now_iso,
            })
        except Exception as e:
            print(f"  ! {tk}: fast_info error ({e})", flush=True)
            failed.append(tk)

    if payload:
        sb.table("current_prices").upsert(payload, on_conflict="ticker").execute()
        print(f"[current_prices] upserted {len(payload)} rows.", flush=True)
    if failed:
        print(f"[current_prices] FAILED to fetch: {failed}", flush=True)

    return {"ok": len(payload), "failed": failed}


def resolve_target_date(now_utc, explicit: str | None = None) -> str:
    """Which date does this run owe? An explicit --date wins. Otherwise: the US
    session of `today` (UTC) cannot be closed before ~21:00 UTC, so a run that
    starts before 21:30 UTC (GitHub delayed the 22:07 cron past midnight in
    Aug 2026) must write YESTERDAY, not yesterday's close under today's date."""
    from datetime import timedelta
    if explicit:
        return explicit
    if (now_utc.hour, now_utc.minute) < (21, 30):
        return (now_utc.date() - timedelta(days=1)).isoformat()
    return now_utc.date().isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date", default=None,
        help="Target date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written; don't touch DB.",
    )
    parser.add_argument(
        "--allow-jumps", action="store_true",
        help="Bypass the >2.5x position-value jump guard (only after checking it is a genuine move, not an unapplied split).",
    )
    args = parser.parse_args()

    global ALLOW_JUMPS
    ALLOW_JUMPS = bool(args.allow_jumps)

    url = os.environ.get("SUPABASE_URL")
    # service_role (bypasses RLS) once the lock-down is applied; anon until then.
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_KEY) env vars are required.", file=sys.stderr)
        sys.exit(2)

    sb = create_client(url, key)

    # Target date. The cron is scheduled 22:07 UTC (after the US close), but
    # GitHub can delay scheduled runs by hours — end of Aug 2026 it fired at
    # 00:12–06:04 UTC, i.e. on the NEXT calendar day before that day's session.
    # `today` then had no close yet and "latest close ≤ today" silently wrote
    # yesterday's prices under today's date (Visionnaire 27/08→01/09 shifted by
    # one day). Rule: without --date, if it's before 21:30 UTC the US session of
    # `today` cannot be closed, so the row we owe is yesterday's.
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    target = resolve_target_date(now_utc, args.date)
    if not args.date and target != now_utc.date().isoformat():
        print(f"[daily_refresh] started {now_utc:%H:%M} UTC, before today's US close "
              f"-> targeting yesterday {target}", flush=True)
    print(f"[daily_refresh] target date: {target}", flush=True)

    # Probe: can we find any close ≤ target for SPY? (sanity check for very
    # early dates or yfinance being totally down).
    probe_price, probe_date = fetch_close_price("SPY", target)
    if probe_price is None:
        print(f"[daily_refresh] yfinance has no SPY close at all near {target}. "
              f"Likely yfinance is down or target is before 1993. Exiting.", flush=True)
        sys.exit(0)
    # Whether today's target row gets written. A trading day whose close is not
    # published yet is skipped, but ONLY that day: the backfill and repair
    # below still run for the previous days. Until 2026-09-25 this case called
    # sys.exit(0) before the backfill; Yahoo publishes after the cron's usual
    # start time, so every night stopped early and no row was written after
    # 22 September while each run reported success.
    write_target = True
    if probe_date != target:
        published, _ = session_close_published(target, now_utc)
        if published is None:
            write_target = False
            print(f"[daily_refresh] {target} is a trading day but its official close is not "
                  f"published yet (latest available: {probe_date}). Not writing {target} rather "
                  f"than stamping {probe_date}'s prices on it; earlier days are still filled and "
                  f"checked below, and {target} is filled on a later run.", flush=True)
        else:
            from datetime import datetime
            weekday = datetime.strptime(target, "%Y-%m-%d").weekday()
            label = "weekend" if weekday >= 5 else "US holiday"
            print(f"[daily_refresh] {target} is not a trading day ({label}). "
                  f"Will propagate the latest close from {probe_date} for all equity tickers.", flush=True)

    if args.dry_run:
        print("[daily_refresh] DRY RUN — no DB writes will happen.", flush=True)

    # Read portfolio IDs from DB dynamically (so new portfolios are auto-picked
    # up without code change — e.g., the `test` sandbox).
    portfolio_ids = get_all_portfolio_ids(sb)
    print(f"[daily_refresh] portfolios to refresh: {portfolio_ids}", flush=True)

    # Self-heal: backfill any missing days from the last week BEFORE writing
    # today's row (chronological order keeps cash baselines correct). This makes
    # the cron resilient to transient failures — a missed day fills itself on the
    # next run instead of leaving a permanent hole.
    if not args.dry_run:
        print(f"\n[daily_refresh] checking last 7 days for gaps to backfill...", flush=True)
        healed = backfill_recent_gaps(sb, portfolio_ids, target, lookback_days=7)
        if healed:
            print(f"[daily_refresh] backfilled {len(healed)} missing (portfolio, date) rows.", flush=True)
        else:
            print(f"[daily_refresh] no gaps found.", flush=True)

        # Filling holes is not enough: a row written with the wrong session's
        # closes exists, so nothing ever went back to look at it (21-22 Sept).
        print(f"\n[daily_refresh] re-pricing the last 3 days against their own closes...", flush=True)
        repaired = repair_stale_rows(sb, portfolio_ids, target, lookback_days=3)
        if repaired:
            print("[daily_refresh] repaired {} stale (portfolio, date) rows: {}".format(
                len(repaired), ", ".join(f"{r['portfolio']}/{r['date']}" for r in repaired)), flush=True)
        else:
            print(f"[daily_refresh] no stale rows.", flush=True)

    results = []
    if not write_target:
        print(f"\n[daily_refresh] {target} left unwritten (close not published yet).", flush=True)
    for pid in (portfolio_ids if write_target else []):
        print(f"\n[{pid}] refreshing {target}...", flush=True)
        try:
            r = refresh_portfolio(sb, pid, target)
            results.append(r)
            print(f"  written {r['rows_written']} rows, NAV=${r['nav']:,.2f}, cash=${r['cash']:,.2f}", flush=True)
            if r.get("propagated"):
                propagated_str = ", ".join(f"{t}<-{d}" for t, d in r["propagated"][:3])
                more = f" (+{len(r['propagated'])-3} more)" if len(r["propagated"]) > 3 else ""
                print(f"  PROPAGATED prices (target was non-trading day): {propagated_str}{more}", flush=True)
            if r["skipped_tickers"]:
                print(f"  SKIPPED (no yfinance data found): {r['skipped_tickers']}", flush=True)
        except Exception as e:
            print(f"  ERROR on {pid}: {e}", file=sys.stderr, flush=True)
            results.append({"portfolio": pid, "error": str(e)})

    # Refresh current_prices (latest price/metadata per ticker, for frontend)
    try:
        cp_result = refresh_current_prices(sb, portfolio_ids)
    except Exception as e:
        print(f"\n[current_prices] refresh failed: {e}", file=sys.stderr, flush=True)
        cp_result = {"ok": 0, "failed": ["(crashed)"]}

    # Refresh dividend_factors (precomputed total return factor per ticker/entry_date)
    try:
        df_result = refresh_dividend_factors(sb, portfolio_ids)
    except Exception as e:
        print(f"\n[dividend_factors] refresh failed: {e}", file=sys.stderr, flush=True)
        df_result = {"ok": 0, "failed": ["(crashed)"]}

    # Refresh fundamentals (P/S, Fwd PE, EV/mNAV, margins, etc — slow tk.info path)
    try:
        fd_result = refresh_fundamentals(sb, portfolio_ids)
    except Exception as e:
        print(f"\n[fundamentals] refresh failed: {e}", file=sys.stderr, flush=True)
        fd_result = {"ok": 0, "failed": ["(crashed)"]}

    # Summary
    print(f"\n[daily_refresh] done. Summary:")
    for r in results:
        if "error" in r:
            print(f"  {r['portfolio']:13} ERROR — {r['error']}")
        else:
            print(f"  {r['portfolio']:13} {r['rows_written']:3} rows  NAV ${r['nav']:>11,.2f}")
    failed_n = len(cp_result.get("failed", []))
    failed_suffix = f" ({failed_n} failed)" if failed_n else ""
    print(f"  current_prices    {cp_result['ok']:3} tickers updated{failed_suffix}")
    df_failed_n = len(df_result.get("failed", []))
    df_failed_suffix = f" ({df_failed_n} failed)" if df_failed_n else ""
    print(f"  dividend_factors  {df_result['ok']:3} pairs updated{df_failed_suffix}")
    fd_failed_n = len(fd_result.get("failed", []))
    fd_failed_suffix = f" ({fd_failed_n} failed)" if fd_failed_n else ""
    print(f"  fundamentals      {fd_result['ok']:3} tickers updated{fd_failed_suffix}")

    # Exit with non-zero if any portfolio failed
    if any("error" in r for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
