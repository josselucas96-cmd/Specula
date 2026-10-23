"""Order planning for a move: from target weights to the dollar orders.

Pure functions, no database and no prices fetched here, so the arithmetic that
decides what gets bought and sold is tested on its own (tests/test_rebalance.py).
scripts/execute_moves.py does the I/O and calls the cockpit's write functions.

Two conventions that caused mistakes before this module existed:
  * Targets are in % of the CURRENT portfolio (what a reader sees). The cockpit
    functions take % of the INITIAL capital at cost: selling AMD from 6.3% to 4%
    of the portfolio on 2026-09-23 meant passing 1.14, not 2.3.
  * A paper book cannot go overdrawn: buys are scaled so that cash never ends
    below a small buffer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FULL_EXIT_EPS = 0.0005      # a target below 0.05% of the portfolio is a full exit


@dataclass
class Plan:
    nav: float
    cash_before: float
    sells: list = field(default_factory=list)   # (ticker, usd > 0, current value)
    buys: list = field(default_factory=list)    # (ticker, usd > 0, current value)

    @property
    def cash_after(self) -> float:
        return self.cash_before + sum(u for _, u, _ in self.sells) - sum(u for _, u, _ in self.buys)


def plan_orders(values: dict[str, float], cash: float, targets: dict[str, float],
                fund_from: str | None = None, remainder: str | None = None,
                buffer: float = 50.0) -> Plan:
    """values: current USD value per held ticker; targets: % of portfolio.
    fund_from: a held line sold by exactly what the buys need beyond cash.
    remainder: a line (target or not) that absorbs whatever cash is left."""
    if fund_from and fund_from in targets:
        raise ValueError(f"{fund_from} cannot be both a target and the funding line")
    if any(t < 0 for t in targets.values()):
        raise ValueError("negative target weight")
    nav = sum(values.values()) + cash
    plan = Plan(nav=nav, cash_before=cash)
    for t, pct in targets.items():
        cur = values.get(t, 0.0)
        delta = pct / 100.0 * nav - cur
        if delta < 0:
            plan.sells.append((t, -delta, cur))
        elif delta > 0 and t != remainder:
            plan.buys.append((t, delta, cur))
    if fund_from:
        need = sum(u for _, u, _ in plan.buys) + buffer - cash - sum(u for _, u, _ in plan.sells)
        if remainder:
            need = max(need, 0.0)
        if need > 0:
            if need > values.get(fund_from, 0.0):
                raise ValueError(f"{fund_from} is too small to fund the buys")
            plan.sells.append((fund_from, need, values[fund_from]))
    if remainder:
        left = plan.cash_after - buffer
        if left > 0:
            plan.buys.append((remainder, left, values.get(remainder, 0.0)))
    overdraft = sum(u for _, u, _ in plan.buys) - (cash + sum(u for _, u, _ in plan.sells) - buffer)
    if overdraft > 0:                     # scale the last buy down, never below zero
        t, u, c = plan.buys[-1]
        if u - overdraft <= 0:
            raise ValueError("not enough cash for the buys: free more cash first")
        plan.buys[-1] = (t, u - overdraft, c)
    return plan


def is_full_exit(current_value: float, sell_usd: float, nav: float) -> bool:
    return (current_value - sell_usd) / nav < FULL_EXIT_EPS


def cockpit_sell_weight(sell_usd: float, price: float, avg_cost: float, initial_capital: float) -> float:
    """% of initial capital at cost that trim_position expects for a dollar sale."""
    return round((sell_usd / price) * avg_cost / initial_capital * 100.0, 4)


def cockpit_buy_weight(buy_usd: float, initial_capital: float) -> float:
    """% of initial capital that add_position expects for a dollar purchase."""
    return round(buy_usd / initial_capital * 100.0, 4)
