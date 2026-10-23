"""Order planning (utils/rebalance.py). No network, no database."""
import pytest

from utils.rebalance import (plan_orders, is_full_exit, cockpit_sell_weight,
                             cockpit_buy_weight)


def _orders(plan):
    return ({t: round(u) for t, u, _ in plan.sells}, {t: round(u) for t, u, _ in plan.buys})


def test_targets_are_percent_of_the_current_portfolio():
    plan = plan_orders({"A": 600.0, "B": 300.0}, cash=100.0, targets={"A": 40.0, "B": 40.0}, buffer=0)
    assert plan.nav == 1000.0
    assert _orders(plan) == ({"A": 200}, {"B": 100})
    assert round(plan.cash_after) == 200


def test_fund_from_sells_exactly_what_the_buys_need():
    # 23 Oct 2026, Le Bâtisseur: Oracle to 4%, Visa sold for the rest, cash to ~0.
    plan = plan_orders({"V": 49_100.0, "X": 928_169.0}, cash=22_731.0,
                       targets={"ORCL": 4.0}, fund_from="V", buffer=50)
    sells, buys = _orders(plan)
    assert buys == {"ORCL": 40_000}
    assert sells == {"V": 40_000 + 50 - 22_731}
    assert round(plan.cash_after) == 50


def test_remainder_absorbs_the_cash_left():
    plan = plan_orders({"A": 500.0, "B": 400.0}, cash=100.0, targets={"A": 30.0}, remainder="B", buffer=10)
    assert _orders(plan) == ({"A": 200}, {"B": 290})
    assert round(plan.cash_after) == 10


def test_last_buy_is_scaled_down_to_the_cash_available():
    plan = plan_orders({"A": 900.0}, cash=100.0, targets={"B": 6.0, "C": 6.0}, buffer=0)
    assert _orders(plan)[1] == {"B": 60, "C": 40}
    assert round(plan.cash_after) == 0


def test_impossible_plan_is_refused():
    with pytest.raises(ValueError):
        plan_orders({"A": 990.0}, cash=10.0, targets={"B": 50.0, "C": 50.0}, buffer=0)
    with pytest.raises(ValueError):
        plan_orders({"A": 100.0}, cash=0.0, targets={"A": 10.0}, fund_from="A")


def test_cockpit_conversions():
    # AMD, 23 Sep 2026: sell 46.34 sh at $616.82, avg cost $245.03, capital $1M -> 1.1355
    assert cockpit_sell_weight(46.34 * 616.82, 616.82, 245.03, 1_000_000) == pytest.approx(1.1355, abs=1e-4)
    assert cockpit_buy_weight(41_627.0, 1_000_000) == 4.1627
    assert is_full_exit(5_000.0, 5_000.0, 1_000_000.0) and not is_full_exit(5_000.0, 2_000.0, 1_000_000.0)
