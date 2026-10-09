"""Pure metric helpers (utils/metrics.py) and the trading-calendar alignment."""
import numpy as np
import pandas as pd
import pytest

from utils.metrics import (
    daily_returns, sharpe_ratio, max_drawdown, beta_vs_spy,
    annualized_volatility, var_95, monthly_returns_table,
)
from utils.portfolio import align_to_equity_calendar


def _idx(values, start="2026-04-10", freq="D"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq), dtype=float)


def test_max_drawdown_peak_to_trough():
    s = _idx([100, 110, 121, 100, 105])
    assert max_drawdown(s) == pytest.approx(-17.36, abs=0.01)   # 121 -> 100


def test_max_drawdown_monotonic_is_zero():
    assert max_drawdown(_idx([100, 101, 102])) == 0.0


def test_sharpe_zero_vol_returns_none():
    assert sharpe_ratio(pd.Series([0.0, 0.0, 0.0])) is None


def test_beta_scales_with_market():
    rng = np.random.default_rng(0)
    m = pd.Series(rng.normal(0, 0.01, 300))
    p = 2.0 * m + rng.normal(0, 0.0001, 300)
    assert beta_vs_spy(p, m) == pytest.approx(2.0, abs=0.05)


def test_beta_needs_ten_points():
    assert beta_vs_spy(pd.Series([0.01] * 5), pd.Series([0.01] * 5)) is None


def test_weekend_zero_returns_dilute_vol_and_var():
    """Why align_to_equity_calendar exists: the same trading-day moves padded
    with flat weekend rows report a lower volatility and a milder VaR."""
    rng = np.random.default_rng(1)
    trading = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.02, 120)),
                        index=pd.bdate_range("2026-04-10", periods=120))
    padded = trading.reindex(pd.date_range(trading.index[0], trading.index[-1], freq="D")).ffill()
    vol_td, vol_77 = annualized_volatility(daily_returns(trading)), annualized_volatility(daily_returns(padded))
    assert vol_77 < vol_td * 0.9
    assert var_95(daily_returns(padded)) > var_95(daily_returns(trading))  # less negative


def test_align_to_equity_calendar_uses_benchmark_dates():
    port = _idx(np.linspace(100, 110, 21))                                  # 7/7 incl. weekends
    bench = pd.Series(1.0, index=pd.bdate_range("2026-04-10", periods=15))  # weekdays
    bench = bench.drop(pd.Timestamp("2026-04-17"))                          # a holiday
    out = align_to_equity_calendar(port, bench, None)
    assert (out.index.weekday < 5).all()
    assert pd.Timestamp("2026-04-17") not in out.index                      # holiday removed too


def test_align_ignores_24_7_benchmark_and_falls_back_to_weekdays():
    port = _idx(np.linspace(100, 110, 14))
    btc = pd.Series(1.0, index=pd.date_range("2026-04-10", periods=14, freq="D"))
    out = align_to_equity_calendar(port, btc, None)
    assert (out.index.weekday < 5).all() and len(out) == 10


def test_align_handles_empty():
    assert align_to_equity_calendar(pd.Series(dtype=float), None, None).empty


def test_monthly_returns_partial_inception_month_and_incomplete_month():
    # base 100 at T-1 (Apr 10), inception Apr 13; series runs to today so the
    # current month is incomplete and must be dropped.
    today = pd.Timestamp.today().normalize()
    idx = pd.date_range("2026-04-10", today, freq="D")
    s = pd.Series(np.linspace(100, 130, len(idx)), index=idx)
    t = monthly_returns_table(s, inception_date="2026-04-13")
    assert 2026 in t.index
    apr_close = s[s.index.month == 4].iloc[-1]
    assert t.loc[2026, "Apr"] == pytest.approx(apr_close - 100, abs=1e-6)  # partial month vs base 100
    cur = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][today.month - 1]
    if today.day < 28:
        assert pd.isna(t.loc[2026, cur])                                       # incomplete month hidden


# ── Beta and Jensen's alpha against the portfolio's own benchmark ────────────
# 2026-09-23, from a reader: the site showed beta against the secondary index
# (Le Visionnaire 2.04 vs the S&P 500 while its benchmark is the Nasdaq 100) and
# called the plain difference with the benchmark "alpha".
import pandas as pd
import pytest

from utils.metrics import aligned_returns, beta_vs_spy, jensen_alpha


def test_a_24_7_benchmark_is_read_on_the_portfolio_s_trading_days():
    """Monday's Bitcoin return must cover Friday to Monday, like the portfolio's."""
    port = pd.Series([100.0, 101.0, 102.0],
                     index=pd.to_datetime(["2026-09-18", "2026-09-21", "2026-09-22"]))   # Fri, Mon, Tue
    btc = pd.Series([100.0, 150.0, 90.0, 110.0, 121.0],
                    index=pd.to_datetime(["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22"]))
    pr, br = aligned_returns(port, btc)
    assert list(br.round(4)) == [0.10, 0.10]           # Fri->Mon 100->110, Mon->Tue 110->121
    assert len(pr) == len(br) == 2


def test_beta_of_a_portfolio_that_moves_twice_its_benchmark():
    days = pd.bdate_range("2026-01-01", periods=40)
    bench = pd.Series([100 * (1.01 if i % 2 else 0.995) ** i for i in range(40)], index=days)
    rets = bench.pct_change().fillna(0)
    port = 100 * (1 + 2 * rets).cumprod()
    pr, br = aligned_returns(port, bench)
    assert beta_vs_spy(pr, br) == pytest.approx(2.0, abs=0.01)


def test_jensen_alpha_removes_what_beta_explains():
    """Up 30% when the benchmark is up 20% with a beta of 1.5 and no risk-free
    rate: all of the excess return is explained by beta, alpha is zero."""
    idx = pd.to_datetime(["2026-01-02", "2026-07-02"])
    port = pd.Series([100.0, 130.0], index=idx)
    bench = pd.Series([100.0, 120.0], index=idx)
    assert jensen_alpha(port, bench, 1.5, risk_free_annual=0.0) == pytest.approx(0.0, abs=1e-9)
    assert jensen_alpha(port, bench, 1.0, risk_free_annual=0.0) == pytest.approx(10.0)


def test_each_index_keeps_its_own_line_color():
    # Until 2026-10-09 every benchmark was a gray told apart by dashes only.
    from utils.portfolio import benchmark_color
    from utils.theme import BENCHMARK_FALLBACK
    assert benchmark_color("S&P 500", "primary") == benchmark_color("S&P 500", "secondary")
    assert len({benchmark_color(l, "extra") for l in ("S&P 500", "Nasdaq 100", "S&P 500 Equal Weight")}) == 3
    assert benchmark_color("Unknown index", "secondary") == BENCHMARK_FALLBACK["secondary"]
