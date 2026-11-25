# -*- coding: utf-8 -*-
"""One-off repair of Le Visionnaire's ledger, 17-25 November 2026 (errata 2026-11-25).

    python scripts/repair_visionnaire_cash_2026_11.py            # dry run
    python scripts/repair_visionnaire_cash_2026_11.py --execute  # write

1. Writes the two missing days (17 and 18 November) from their own closes.
2. Sets the CASH line of the days after the gap to the cash left by the trades
   of 17 November. Only lines that hold exactly the pre-move amount are changed;
   share counts and prices are not touched.
Run once, with the owner's go, on 2026-11-25. Kept as the record of what was done.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)

import daily_refresh as dr                    # noqa: E402
from utils.data import get_client             # noqa: E402

PID, WRONG, FIRST, LAST = "visionnaire", 31_098.69, "2026-11-19", "2026-11-25"
GAP = ("2026-11-17", "2026-11-18")


def nav(sb, d):
    rows = sb.table("daily_holdings").select("value").eq("portfolio_id", PID).eq("date", d).execute().data
    return len(rows), sum(float(r["value"]) for r in rows)


def main() -> int:
    execute = "--execute" in sys.argv
    sb = get_client()
    cap = dr.get_initial_capital(sb, PID)
    right = round(dr.derive_cash_at_date(sb, PID, GAP[0], cap), 2)
    if abs(right - 3_349.32) > 0.05:
        raise SystemExit(f"cash after the move should be 3,349.32, computed {right}: stop")
    later = sb.table("transactions").select("id").eq("portfolio_id", PID).gt("date", GAP[0]).execute().data
    if later:
        raise SystemExit("trades after 17 November: the cash is no longer constant, stop")

    for d in GAP:
        n, _ = nav(sb, d)
        if n:
            print(f"{d}: already {n} rows, left alone"); continue
        if execute:
            r = dr.refresh_portfolio(sb, PID, d)
            print(f"{d}: wrote {r['rows_written']} rows  NAV ${r['nav']:,.2f}  cash ${r['cash']:,.2f}  skipped {r['skipped_tickers']}")
        else:
            print(f"{d}: missing, would be written")

    todo = (sb.table("daily_holdings").select("date,value").eq("portfolio_id", PID).eq("ticker", "CASH")
            .gte("date", FIRST).lte("date", LAST).order("date").execute().data)
    for r in todo:
        if abs(float(r["value"]) - WRONG) > 0.005:
            print(f"{r['date']}: cash {r['value']}, left alone"); continue
        if execute:
            (sb.table("daily_holdings").update({"shares": right, "value": right})
             .eq("portfolio_id", PID).eq("ticker", "CASH").eq("date", r["date"]).execute())
        print(f"{r['date']}: cash {r['value']} -> {right}{'' if execute else '  (dry run)'}")

    print("\nread back:")
    for d in ("2026-11-16", *GAP, "2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24", "2026-11-25"):
        n, v = nav(sb, d)
        c = sb.table("daily_holdings").select("value").eq("portfolio_id", PID).eq("ticker", "CASH").eq("date", d).execute().data
        print(f"  {d}  rows {n:2}  NAV ${v:>12,.2f}  cash {c[0]['value'] if c else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
