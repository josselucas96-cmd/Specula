# -*- coding: utf-8 -*-
"""Execute a validated move: bring a portfolio to target weights at live prices.

    python scripts/execute_moves.py PLAN.json             # dry run (default)
    python scripts/execute_moves.py PLAN.json --execute   # write

PLAN.json:
    {"portfolio": "batisseur",
     "targets":   {"ORCL": 4.0},            # % of the CURRENT portfolio
     "fund_from": "V",                       # optional: sold by exactly what the buys need
     "remainder": null,                      # optional: line that absorbs leftover cash
     "cash_buffer_usd": 50,
     "new":     {"ORCL": {"name": "...", "layer": "...", "sector": "...",
                          "geography": "...", "thematic": "...", "thesis_short": "..."}},
     "reasons": {"ORCL": "public rationale shown on /Moves", "V": "..."}}

Why a script and not the Admin cockpit: the cockpit loses purchases in a batch
commit (open since 2026-06-04) and takes weights in % of initial capital at
cost, a frequent source of sizing errors. Every move since 2026-09-23 has gone
through this logic. It uses the same write functions as the cockpit, so the
records are identical, and it:
  * refuses to run twice the same day for a portfolio,
  * checks every live price against the last ledger close (FX or unit error),
  * sells before it buys and never leaves the book overdrawn,
  * re-reads the book, cash and trades from the database afterwards,
  * rebuilds the audit workbook (daily_holdings_audit.xlsx).
Commit the snapshot CSV it leaves in snapshots/<portfolio>/ afterwards.
"""
import json, os, subprocess, sys
PLAN_PATH = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)

from datetime import date, datetime, timezone

from utils.data import (get_positions, get_cash_amount, get_client, _get_initial_capital,
                        trim_position, close_position, add_position)
from utils.market import get_prices
from utils.rebalance import plan_orders, is_full_exit, cockpit_sell_weight, cockpit_buy_weight

PRICE_TOLERANCE = 0.25      # live vs last ledger close; larger means a wrong quote


def main() -> int:
    if not PLAN_PATH:
        print(__doc__); return 2
    plan_in = json.load(open(PLAN_PATH, encoding="utf-8"))
    execute = "--execute" in sys.argv
    pid, targets = plan_in["portfolio"], plan_in["targets"]
    fund, rem = plan_in.get("fund_from"), plan_in.get("remainder")
    new, reasons = plan_in.get("new", {}), plan_in.get("reasons", {})
    today = date.today().isoformat()
    sb = get_client(); ic = _get_initial_capital(sb, pid)

    pos = {p["ticker"]: p for p in get_positions(portfolio_id=pid)}
    wanted = set(targets) | ({rem} if rem else set()) | ({fund} if fund else set())
    for t in wanted:
        if t not in pos and t not in new:
            raise SystemExit(f"{t}: not held and no metadata for a new position")
    if execute and sb.table("transactions").select("id").eq("portfolio_id", pid).eq("date", today).execute().data:
        raise SystemExit("transactions already dated today for this portfolio: refusing to run twice")

    px = get_prices(tuple(set(pos) | wanted))
    last = (sb.table("daily_holdings").select("date").eq("portfolio_id", pid)
            .order("date", desc=True).limit(1).execute().data[0]["date"])
    ledger = {r["ticker"]: float(r["price"]) for r in sb.table("daily_holdings").select("ticker,price")
              .eq("portfolio_id", pid).eq("date", last).execute().data if r["ticker"] != "CASH"}
    for t in set(pos) | wanted:
        if not px[t]["price"]:
            raise SystemExit(f"no live price for {t}")
        if t in ledger and abs(px[t]["price"] / ledger[t] - 1) > PRICE_TOLERANCE:
            raise SystemExit(f"{t}: live {px[t]['price']} vs ledger {ledger[t]} ({last}) - FX or unit problem?")

    cash0 = get_cash_amount(pid)
    values = {t: float(p["shares"]) * px[t]["price"] for t, p in pos.items()}
    plan = plan_orders(values, cash0, targets, fund_from=fund, remainder=rem,
                       buffer=float(plan_in.get("cash_buffer_usd", 50)))
    nav = plan.nav
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{pid}] {stamp}  NAV ${nav:,.0f}  cash ${cash0:,.0f} ({cash0 / nav * 100:.2f}%)  "
          f"ledger {last}  {'EXECUTE' if execute else 'DRY RUN'}")
    for t, usd, cur in plan.sells:
        full = is_full_exit(cur, usd, nav)
        print(f"  SELL {t:<9} {cur / nav * 100:5.2f}% -> {(cur - usd) / nav * 100:5.2f}%  ${usd:,.0f} "
              f"@ {px[t]['price']}{'  (close)' if full else ''}")
    for t, usd, cur in plan.buys:
        print(f"  BUY  {t:<9} {cur / nav * 100:5.2f}% -> {(cur + usd) / nav * 100:5.2f}%  ${usd:,.0f} "
              f"@ {px[t]['price']}{'  (new)' if t not in pos else ''}")
    print(f"  cash after ~ ${plan.cash_after:,.0f} ({plan.cash_after / nav * 100:.2f}%)")
    if not execute:
        return 0

    for t, usd, cur in plan.sells:
        p, price = pos[t], px[t]["price"]
        reason = reasons.get(t, "Reduced.")
        if is_full_exit(cur, usd, nav):
            close_position(p["id"], price, today, reason)
        else:
            trim_position(p["id"], cockpit_sell_weight(usd, price, float(p["entry_price"]), ic), price, today, reason)
    for t, usd, cur in plan.buys:
        data = {"ticker": t, "entry_price": px[t]["price"], "entry_date": today,
                "weight": cockpit_buy_weight(usd, ic)}
        if t not in pos:
            data.update({**new[t], "is_active": True})
        add_position(data, portfolio_id=pid)
        if t in reasons:
            (sb.table("transactions").update({"reason": reasons[t]}).eq("portfolio_id", pid)
             .eq("date", today).eq("action", "IN").eq("ticker_in", t).execute())

    # Read everything back from the database: the cockpit once lost buys silently.
    pos2 = {p["ticker"]: p for p in get_positions(portfolio_id=pid)}
    get_cash_amount.clear(); cash2 = get_cash_amount(pid)
    vals2 = {t: float(p["shares"]) * px[t]["price"] for t, p in pos2.items()}
    nav2 = sum(vals2.values()) + cash2
    print(f"\n[{pid}] AFTER: {len(pos2)} lines, cash ${cash2:,.2f} ({cash2 / nav2 * 100:.2f}%), NAV ${nav2:,.0f}")
    for t in sorted(set(targets) | {x for x, _, _ in plan.sells} | {x for x, _, _ in plan.buys}):
        print(f"  {t:<9} " + (f"{vals2[t] / nav2 * 100:5.2f}%  avg cost {float(pos2[t]['entry_price']):.4f}"
                              if t in pos2 else "closed"))
    print(f"  lines >5%: {sum(v / nav2 * 100 for v in vals2.values() if v / nav2 * 100 > 5):.2f}%")
    tx = (sb.table("transactions").select("executed_at,action,ticker_in,ticker_out,price_in,price_out,reason")
          .eq("portfolio_id", pid).eq("date", today).order("executed_at").execute().data)
    expected = len(plan.sells) + len(plan.buys)
    print(f"  transactions today: {len(tx)} (expected {expected})")
    for x in tx:
        print(f"    {x['executed_at'][11:19]} UTC  {x['action']:<5} {x['ticker_out'] or x['ticker_in']:<9} "
              f"@ {x['price_out'] or x['price_in']}  {x['reason']}")
    if len(tx) < expected or cash2 < -1:
        print("  !! MISMATCH: check the database before anything else")
        return 1
    subprocess.run([sys.executable, "build_audit_workbook.py"], check=False)
    print(f"\nNext: commit snapshots/{pid}/{today}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
