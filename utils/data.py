"""Supabase data layer with portfolio scoping (multi-portfolio aware).

Move semantics (PR-3):
- Every move on a position keeps `weight`, `entry_price` (PRU) AND `units`
  in sync. `units = weight / PRU` for invariance.
- PRU on REINFORCE uses the shares-weighted average (total cost / total
  units) — the mathematically correct formula.

Cash tracking (post-2026-05-15):
- `portfolios.cash_units` / `portfolios.cash_amount` are NOT maintained.
  RLS on `portfolios` blocks anon-key UPDATEs silently, so we derive cash
  on read instead. Source of truth = `daily_holdings` CASH row +
  same-day transactions. See `get_cash_amount`.

Audit trail (PR-5):
- Every move stamps `executed_at` (TIMESTAMPTZ) on the transaction row.
- Every move triggers a `position_snapshots` row per active ticker.
- A CSV mirror of each snapshot is written to ../snapshots/<portfolio>/.
"""
import csv
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions

from utils.sb_http import make_http_client


def new_client(key: str):
    """Supabase client over the hardened HTTP layer of utils/sb_http.py
    (survives a connection dropped while idle; see the 2026-10-09 traceback)."""
    return create_client(st.secrets["supabase_url"], key,
                         options=SyncClientOptions(httpx_client=make_http_client()))


@st.cache_resource
def get_client():
    """Supabase client for the app. Prefers the service_role key when it is
    configured in the (server-side) Streamlit secrets: once the RLS lock-down
    (supabase/migrations/2026-09-21_rls_lockdown.sql) is applied, the anon key
    can only read, and every write of the cockpit needs this key. The key never
    reaches the browser — Streamlit renders server-side."""
    key = st.secrets.get("supabase_service_key") or st.secrets["supabase_key"]
    return new_client(key)


# ── Internal helpers ──────────────────────────────────────────────────────────
def _get_initial_capital(sb, portfolio_id: str) -> float:
    """Read initial_capital_<pid> setting (fallback to legacy 'initial_capital'
    for Visionnaire, then default $1M)."""
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
    return 1_000_000.0


def _existing_units(row: dict) -> float:
    """Resolve units from a position row. Falls back to weight/entry_price
    when the column was not yet backfilled (defensive)."""
    u = row.get("units")
    if u is not None:
        return float(u)
    w = float(row.get("weight") or 0)
    p = float(row.get("entry_price") or 0)
    return (w / p) if p > 0 else 0.0


def _existing_shares(row: dict, initial_capital: float) -> float:
    """Resolve shares from a position row. Falls back to (weight × capital / 100) / PRU
    when the column was not yet backfilled."""
    s = row.get("shares")
    if s is not None and float(s) > 0:
        return float(s)
    w = float(row.get("weight") or 0)
    p = float(row.get("entry_price") or 0)
    if p <= 0 or w <= 0:
        return 0.0
    return (w * initial_capital / 100.0) / p


def _now_utc_iso() -> str:
    """Current UTC timestamp as ISO 8601 (with timezone)."""
    return datetime.now(timezone.utc).isoformat()


@st.cache_data(ttl=120)
def get_cash_amount(portfolio_id: str) -> float:
    """Derive current cash $ for a portfolio from immutable sources.

    `portfolios.cash_amount` is unreliable (RLS blocks anon-key writes silently
    since 2026-05-15). Instead we derive on read from:
      1. Latest `daily_holdings` CASH row strictly BEFORE today (= yesterday's
         close cash, or initial_capital if no prior row exists).
      2. + Σ today's transaction $-deltas (TRIM/CLOSE proceeds, IN/SWITCH costs).

    DRIP and SPLIT are cash-neutral by design (skipped).

    Cached 2 min; callers must `.clear()` after a move (handled inside the
    move helpers below).
    """
    from datetime import date as _date
    sb = get_client()
    initial_capital = _get_initial_capital(sb, portfolio_id)
    today_str = _date.today().isoformat()

    rows = (
        sb.table("daily_holdings")
        .select("date, value")
        .eq("portfolio_id", portfolio_id)
        .eq("ticker", "CASH")
        .lt("date", today_str)
        .order("date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    baseline = float(rows[0]["value"]) if rows else float(initial_capital)

    txns = (
        sb.table("transactions")
        .select("action, weight_in, weight_out, price_in, price_out, entry_price_out")
        .eq("portfolio_id", portfolio_id)
        .eq("date", today_str)
        .execute()
        .data
    )
    delta = 0.0
    for t in txns:
        action = (t.get("action") or "").upper()
        if action == "IN":
            w_in = float(t.get("weight_in") or 0)
            delta -= w_in * initial_capital / 100.0
        elif action in ("TRIM", "OUT"):
            w_out = float(t.get("weight_out") or 0)
            pru_out = float(t.get("entry_price_out") or 0)
            p_out = float(t.get("price_out") or 0)
            if pru_out > 0:
                shares_sold = (w_out * initial_capital / 100.0) / pru_out
                delta += shares_sold * p_out
        elif action == "SWITCH":
            w_in = float(t.get("weight_in") or 0)
            w_out = float(t.get("weight_out") or 0)
            pru_out = float(t.get("entry_price_out") or 0)
            p_out = float(t.get("price_out") or 0)
            if pru_out > 0:
                shares_sold = (w_out * initial_capital / 100.0) / pru_out
                delta += shares_sold * p_out
            delta -= w_in * initial_capital / 100.0
        # DRIP / SPLIT: cash-neutral, skip.

    return round(baseline + delta, 2)


def _snapshot_positions(sb, portfolio_id: str, snapshot_type: str = "move",
                        when: str | None = None) -> None:
    """Snapshot the post-move state of all active positions for this portfolio.

    Writes to:
    - Supabase `position_snapshots` table (upsert on portfolio_id, date, ticker)
    - CSV mirror at ../snapshots/<portfolio>/<date>.csv (best-effort, non-fatal)

    `snapshot_type` ∈ {'move', 'monthly', 'manual', 'initial'}.
    """
    from datetime import date as _date
    when = when or _date.today().isoformat()
    rows = (
        sb.table("positions")
        .select("ticker, weight, units, entry_price")
        .eq("portfolio_id", portfolio_id)
        .eq("is_active", True)
        .order("ticker")
        .execute()
        .data
    )
    if not rows:
        return
    payload = [{
        "portfolio_id":  portfolio_id,
        "date":          when,
        "ticker":        r["ticker"],
        "weight":        float(r.get("weight") or 0),
        "units":         float(r.get("units") or 0),
        "entry_price":   float(r.get("entry_price") or 0),
        "snapshot_type": snapshot_type,
    } for r in rows]
    try:
        sb.table("position_snapshots").upsert(
            payload, on_conflict="portfolio_id,date,ticker"
        ).execute()
    except Exception:
        # Non-fatal: missing table on legacy deploys shouldn't break a move
        pass
    _write_snapshot_csv(portfolio_id, when, payload)


def _write_snapshot_csv(portfolio_id: str, date_str: str, rows: list) -> None:
    """Best-effort CSV backup of a snapshot in Streamlit_project/snapshots/.

    Silent on filesystem errors (Streamlit Cloud is read-only at runtime;
    the DB row in `position_snapshots` is the source of truth, CSV is mirror)."""
    try:
        base = Path(__file__).resolve().parent.parent / "snapshots" / portfolio_id
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{date_str}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["ticker", "weight", "units", "entry_price", "snapshot_type"],
            )
            w.writeheader()
            for r in rows:
                w.writerow({k: r[k] for k in w.fieldnames})
    except Exception:
        pass


# ── Portfolios ────────────────────────────────────────────────────────────────
def get_portfolios(active_only=True):
    """List all portfolios, ordered by display_order."""
    sb = get_client()
    query = sb.table("portfolios").select("*").order("display_order")
    if active_only:
        query = query.eq("is_active", True)
    return query.execute().data


def get_portfolio(portfolio_id: str):
    """Fetch a single portfolio's metadata by id (slug)."""
    sb = get_client()
    result = sb.table("portfolios").select("*").eq("id", portfolio_id).execute().data
    return result[0] if result else None


def update_portfolio(portfolio_id: str, fields: dict):
    """Update fields on a portfolio row (name, inception_date, etc.)."""
    sb = get_client()
    sb.table("portfolios").update(fields).eq("id", portfolio_id).execute()


def update_position_valuation(position_id: int, growth, gm, om, fcf):
    """Persist the user's expected forward valuation inputs on a position.

    growth, gm, om, fcf are percentages (e.g. 20.0 = 20%). None values are
    stored as NULL — clearing a cell removes the estimate.
    """
    sb = get_client()
    sb.table("positions").update({
        "expected_revenue_growth": growth,
        "expected_gross_margin":   gm,
        "expected_op_margin":      om,
        "expected_fcf_margin":     fcf,
    }).eq("id", position_id).execute()


# ── Positions ─────────────────────────────────────────────────────────────────
def get_positions(active_only=True, portfolio_id: str = "visionnaire"):
    sb = get_client()
    query = sb.table("positions").select("*").eq("portfolio_id", portfolio_id)
    if active_only:
        query = query.eq("is_active", True)
    return query.order("ticker").execute().data


def get_transactions(portfolio_id: str = "visionnaire"):
    sb = get_client()
    return (
        sb.table("transactions")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .order("date", desc=True)
        .execute()
        .data
    )


# ── Settings (global for now; will scope later if needed) ─────────────────────
def get_setting(key, default=None):
    sb = get_client()
    result = sb.table("settings").select("value").eq("key", key).execute().data
    return result[0]["value"] if result else default


def upsert_setting(key, value):
    sb = get_client()
    sb.table("settings").upsert({"key": key, "value": str(value)}).execute()


# ── Position write operations ─────────────────────────────────────────────────
def add_position(data: dict, portfolio_id: str = "visionnaire", executed_at: str | None = None):
    sb = get_client()
    data = {**data, "portfolio_id": portfolio_id}
    initial_capital = _get_initial_capital(sb, portfolio_id)

    existing = (
        sb.table("positions")
        .select("id, weight, entry_price, units, shares")
        .eq("portfolio_id", portfolio_id)
        .eq("ticker", data["ticker"])
        .eq("is_active", True)
        .execute()
        .data
    )
    new_w = float(data["weight"])
    new_p = float(data["entry_price"])
    new_units = round(new_w / new_p, 8) if new_p > 0 else 0.0
    # Real $ cost basis of this addition (cash flowing out of the account)
    dollar_cost = new_w * initial_capital / 100.0
    new_shares_added = dollar_cost / new_p if new_p > 0 else 0.0

    # A paper book cannot go overdrawn. Weight-based sizing (x% of initial
    # capital) is blind to the cash actually left, which is how the Bâtisseur
    # ended at -$18.54 on 2026-06-12 (NOW reinforcement 0.80% > cash). Refuse
    # here — the single write path for buys — so the cockpit banner shows why.
    # $1 tolerance absorbs rounding; anything beyond is a real overdraft.
    cash_available = get_cash_amount(portfolio_id)
    if dollar_cost > cash_available + 1.0:
        max_w = max(cash_available, 0.0) / initial_capital * 100.0
        raise ValueError(
            f"Insufficient cash for {data['ticker']}: this buy costs "
            f"${dollar_cost:,.2f} ({new_w:.2f}% of initial capital) but only "
            f"${cash_available:,.2f} is available (max {max_w:.2f}%). "
            f"Reduce the weight or free cash first."
        )

    if existing:
        # REINFORCE — shares-weighted PRU averaging
        ex = existing[0]
        old_w = float(ex["weight"])
        old_units = _existing_units(ex)
        old_shares = _existing_shares(ex, initial_capital)
        total_w = round(old_w + new_w, 4)
        total_units = round(old_units + new_units, 8)
        total_shares = round(old_shares + new_shares_added, 8)
        new_pru = round(total_w / total_units, 6) if total_units > 0 else 0.0

        update_data = {
            "weight":      total_w,
            "entry_price": new_pru,
            "units":       total_units,
            "shares":      total_shares,
        }
        new_thesis = (data.get("thesis_short") or "").strip()
        if new_thesis:
            update_data["thesis_short"] = new_thesis
        sb.table("positions").update(update_data).eq("id", ex["id"]).execute()
        sb.table("transactions").insert({
            "portfolio_id": portfolio_id,
            "date":         data.get("entry_date"),
            "action":       "IN",
            "ticker_in":    data["ticker"],
            "price_in":     new_p,
            "weight_in":    new_w,
            "reason":       f"Reinforced to {float(new_w):.2f}% (new PRU: {new_pru:.4f})",
            "executed_at":  executed_at or _now_utc_iso(),
        }).execute()
    else:
        # NEW POSITION
        data = {**data, "units": new_units, "shares": round(new_shares_added, 8)}
        sb.table("positions").insert(data).execute()
        sb.table("transactions").insert({
            "portfolio_id": portfolio_id,
            "date":         data.get("entry_date"),
            "action":       "IN",
            "ticker_in":    data.get("ticker"),
            "price_in":     new_p,
            "weight_in":    new_w,
            "reason":       "New position",
            "executed_at":  executed_at or _now_utc_iso(),
        }).execute()

    # Cash is derived on read from daily_holdings + today's transactions
    # (see `get_cash_amount`). The transaction we just inserted records the
    # cost basis and price, so no portfolios.cash_* write is needed.
    get_cash_amount.clear()
    _snapshot_positions(sb, portfolio_id)


def trim_position(position_id: int, weight_sold: float, exit_price: float,
                  exit_date: str, reason: str, executed_at: str | None = None):
    sb = get_client()
    pos = sb.table("positions").select("*").eq("id", position_id).execute().data[0]
    portfolio_id = pos.get("portfolio_id", "visionnaire")
    initial_capital = _get_initial_capital(sb, portfolio_id)

    old_w = float(pos["weight"])
    new_w = round(old_w - weight_sold, 4)
    old_units = _existing_units(pos)
    # Proportional unit reduction → PRU intact
    new_units = round(old_units * (new_w / old_w), 8) if old_w > 0 else 0.0

    # Real-$ shares accounting: cost-basis trimmed = weight_sold × capital / 100,
    # shares sold = that $ ÷ PRU, real proceeds = shares × exit_price.
    old_shares = _existing_shares(pos, initial_capital)
    pru = float(pos["entry_price"])
    shares_sold = (weight_sold * initial_capital / 100.0) / pru if pru > 0 else 0.0
    new_shares = max(0.0, old_shares - shares_sold)
    dollar_proceeds = shares_sold * exit_price

    perf = round((exit_price - pru) / pru * 100, 2) if pru > 0 else 0.0

    sb.table("positions").update({
        "weight": new_w,
        "units":  new_units,
        "shares": round(new_shares, 8),
    }).eq("id", position_id).execute()
    sb.table("transactions").insert({
        "portfolio_id":    portfolio_id,
        "date":            exit_date,
        "action":          "TRIM",
        "ticker_out":      pos["ticker"],
        "price_out":       exit_price,
        "entry_price_out": pru,
        "weight_out":      weight_sold,
        "perf_pct":        perf,
        "reason":          reason,
        "executed_at":     executed_at or _now_utc_iso(),
    }).execute()

    # Cash derived on read (see `get_cash_amount`).
    get_cash_amount.clear()
    _snapshot_positions(sb, portfolio_id)


def close_position(position_id: int, exit_price: float, exit_date: str, reason: str, executed_at: str | None = None):
    sb = get_client()
    pos = sb.table("positions").select("*").eq("id", position_id).execute().data[0]
    portfolio_id = pos.get("portfolio_id", "visionnaire")
    initial_capital = _get_initial_capital(sb, portfolio_id)

    old_w = float(pos["weight"])
    pru = float(pos["entry_price"])
    perf = round((exit_price - pru) / pru * 100, 2) if pru > 0 else 0.0

    old_shares = _existing_shares(pos, initial_capital)
    dollar_proceeds = old_shares * exit_price

    sb.table("transactions").insert({
        "portfolio_id":    portfolio_id,
        "date":            exit_date,
        "action":          "OUT",
        "ticker_out":      pos["ticker"],
        "price_out":       exit_price,
        "entry_price_out": pru,
        "weight_out":      old_w,
        "perf_pct":        perf,
        "reason":          reason,
        "executed_at":     executed_at or _now_utc_iso(),
    }).execute()
    sb.table("positions").update({
        "is_active":  False,
        "exit_price": exit_price,
        "exit_date":  exit_date,
        "units":      0,
        "shares":     0,
    }).eq("id", position_id).execute()

    # Cash derived on read (see `get_cash_amount`).
    get_cash_amount.clear()
    _snapshot_positions(sb, portfolio_id)


def switch_position(out_id: int, out_price: float, in_data: dict,
                    date: str, reason: str):
    sb = get_client()
    pos_out = sb.table("positions").select("*").eq("id", out_id).execute().data[0]
    portfolio_id = pos_out.get("portfolio_id", "visionnaire")
    initial_capital = _get_initial_capital(sb, portfolio_id)

    out_w = float(pos_out["weight"])
    out_pru = float(pos_out["entry_price"])
    perf = round((out_price - out_pru) / out_pru * 100, 2) if out_pru > 0 else 0.0

    out_shares = _existing_shares(pos_out, initial_capital)
    dollar_proceeds = out_shares * out_price

    sb.table("transactions").insert({
        "portfolio_id":    portfolio_id,
        "date":            date,
        "action":          "SWITCH",
        "ticker_out":      pos_out["ticker"],
        "price_out":       out_price,
        "weight_out":      out_w,
        "ticker_in":       in_data["ticker"],
        "price_in":        in_data["entry_price"],
        "weight_in":       in_data["weight"],
        "entry_price_out": out_pru,
        "perf_pct":        perf,
        "reason":          reason,
        "executed_at":     _now_utc_iso(),
    }).execute()
    sb.table("positions").update({
        "is_active":  False,
        "exit_price": out_price,
        "exit_date":  date,
        "units":      0,
        "shares":     0,
    }).eq("id", out_id).execute()

    new_w = float(in_data["weight"])
    new_p = float(in_data["entry_price"])
    new_units = round(new_w / new_p, 8) if new_p > 0 else 0.0
    dollar_cost = new_w * initial_capital / 100.0
    new_shares = dollar_cost / new_p if new_p > 0 else 0.0
    sb.table("positions").insert({
        **in_data, "portfolio_id": portfolio_id,
        "units":  new_units,
        "shares": round(new_shares, 8),
    }).execute()

    # Cash derived on read (see `get_cash_amount`).
    get_cash_amount.clear()
    _snapshot_positions(sb, portfolio_id)


# ── Corporate actions ─────────────────────────────────────────────────────────
def apply_dividend_drip(portfolio_id: str, ticker: str, div_per_share: float,
                        reinvest_price: float | None = None,
                        payment_date: str | None = None):
    """Apply a dividend with automatic reinvestment (DRIP). Cash-neutral.

    cash_received$ = shares × div_per_share (real $)
    new_shares = cash_received$ / reinvest_price
    PRU recomputes via shares-weighted average. Cash-neutral (no $-delta).
    Returns cash_received_dollars.
    """
    sb = get_client()
    pos = (
        sb.table("positions")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .eq("ticker", ticker)
        .eq("is_active", True)
        .execute()
        .data
    )
    if not pos:
        return None
    p = pos[0]
    initial_capital = _get_initial_capital(sb, portfolio_id)
    old_shares = _existing_shares(p, initial_capital)
    old_units  = _existing_units(p)
    old_weight = float(p["weight"])
    old_pru    = float(p["entry_price"])

    if old_shares <= 0 or div_per_share <= 0:
        return None
    if reinvest_price is None or reinvest_price <= 0:
        reinvest_price = old_pru

    cash_received_dollars = old_shares * div_per_share
    new_shares_added      = cash_received_dollars / reinvest_price
    total_shares          = round(old_shares + new_shares_added, 8)
    # New PRU via real $ cost basis
    new_pru = round((old_shares * old_pru + cash_received_dollars) / total_shares, 6) \
              if total_shares > 0 else 0.0

    # Legacy bookkeeping
    weight_added = cash_received_dollars * 100.0 / initial_capital
    new_weight   = round(old_weight + weight_added, 4)
    new_units    = round(new_weight / new_pru, 8) if new_pru > 0 else 0.0

    sb.table("positions").update({
        "shares":      total_shares,
        "weight":      new_weight,
        "units":       new_units,
        "entry_price": new_pru,
    }).eq("id", p["id"]).execute()

    if payment_date is None:
        from datetime import date as _date
        payment_date = _date.today().isoformat()

    sb.table("transactions").insert({
        "portfolio_id": portfolio_id,
        "date":         payment_date,
        "action":       "DRIP",
        "ticker_in":    ticker,
        "price_in":     reinvest_price,
        "weight_in":    round(weight_added, 4),
        "reason":       (f"DRIP ${div_per_share:.4f}/share × {old_shares:.4f} shares "
                         f"= ${cash_received_dollars:.2f} reinvested @ ${reinvest_price:.4f}"),
        "executed_at":  _now_utc_iso(),
    }).execute()
    # DRIP is cash-neutral (no $-delta vs derived cash).
    _snapshot_positions(sb, portfolio_id)
    return cash_received_dollars


def apply_split(portfolio_id: str, ticker: str, ratio: float,
                split_date: str | None = None):
    """Apply a stock split (or reverse split if ratio < 1).

    shares × ratio, units × ratio, PRU ÷ ratio. weight + cash unchanged
    (cost basis preserved: shares × PRU stays constant).
    """
    sb = get_client()
    pos = (
        sb.table("positions")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .eq("ticker", ticker)
        .eq("is_active", True)
        .execute()
        .data
    )
    if not pos or ratio <= 0:
        return None
    p = pos[0]
    initial_capital = _get_initial_capital(sb, portfolio_id)
    old_shares = _existing_shares(p, initial_capital)
    old_units  = _existing_units(p)
    new_shares = round(old_shares * ratio, 8)
    new_units  = round(old_units * ratio, 8)
    new_pru    = round(float(p["entry_price"]) / ratio, 6)

    sb.table("positions").update({
        "shares":      new_shares,
        "units":       new_units,
        "entry_price": new_pru,
    }).eq("id", p["id"]).execute()

    if split_date is None:
        from datetime import date as _date
        split_date = _date.today().isoformat()

    sb.table("transactions").insert({
        "portfolio_id": portfolio_id,
        "date":         split_date,
        "action":       "SPLIT",
        "ticker_in":    ticker,
        "price_in":     new_pru,
        "reason":       f"{ratio}-for-1 split: units × {ratio}, PRU ÷ {ratio}",
        "executed_at":  _now_utc_iso(),
    }).execute()
    _snapshot_positions(sb, portfolio_id)
    return new_units


# ── Events (global for now) ───────────────────────────────────────────────────
def get_events():
    sb = get_client()
    return sb.table("events").select("*").order("event_date").execute().data


def add_event(data: dict):
    sb = get_client()
    sb.table("events").insert(data).execute()


def delete_event(event_id: int):
    sb = get_client()
    sb.table("events").delete().eq("id", event_id).execute()


# ── Reset ─────────────────────────────────────────────────────────────────────
def reset_portfolio(today_str: str, prices: dict, portfolio_id: str = "visionnaire"):
    """
    Reinitialize portfolio for a fresh start:
    - Reset entry_price + entry_date to today's price for all active positions
    - Recompute units (legacy) AND shares (new model) from new weight/entry_price
    - Deactivate STRC cleanly (no exit transaction — it's a reset, not a trade)
    - Update inception_date in portfolios table

    Note: portfolios.cash_* are not maintained (RLS-blocked, see get_cash_amount).
    Note: portfolios.inception_date update is also RLS-blocked via anon key —
    if the UI reset doesn't take effect, run the corresponding UPDATE in the
    Supabase SQL editor as a one-shot.
    """
    sb = get_client()
    initial_capital = _get_initial_capital(sb, portfolio_id)
    positions = get_positions(portfolio_id=portfolio_id)
    for p in positions:
        ticker = p["ticker"]
        if ticker == "STRC":
            sb.table("positions").update({
                "is_active": False, "units": 0, "shares": 0,
            }).eq("id", p["id"]).execute()
            continue
        current_price = prices.get(ticker)
        if not current_price:
            continue
        w = float(p.get("weight") or 0)
        units  = round(w / current_price, 8) if current_price > 0 else 0.0
        shares = round((w * initial_capital / 100.0) / current_price, 8) if current_price > 0 else 0.0
        sb.table("positions").update({
            "entry_price": current_price,
            "entry_date":  today_str,
            "units":       units,
            "shares":      shares,
        }).eq("id", p["id"]).execute()
    sb.table("portfolios").update({
        "inception_date": today_str,
    }).eq("id", portfolio_id).execute()
