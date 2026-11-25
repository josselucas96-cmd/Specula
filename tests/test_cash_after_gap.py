"""Regressions of 17-25 November 2026 (found 2026-11-25).

The Visionnaire move of 17 November took Bitmine from 1.5% to 4% of the book.
The nightly job took the x2.58 in value for an unapplied split and wrote nothing
for three nights; when it resumed, it started from the cash of the 16th and
never looked at the trades of the 17th. $27,749 of cash that had been spent
stayed in the NAV (+2.9%). Meanwhile the runs ended green.
"""
import pytest

import daily_refresh as dr

CAP = 1_000_000.0
MOVE = [  # weights in % of initial capital, as the move engine records them
    {"portfolio_id": "visionnaire", "date": "2026-11-17", "action": "TRIM", "ticker_out": "ZETA",
     "weight_out": 1.66738, "price_out": 35.08, "entry_price_out": 22.8198},
    {"portfolio_id": "visionnaire", "date": "2026-11-17", "action": "IN", "ticker_in": "BMNR",
     "weight_in": 2.5546, "price_in": 16.31},
]
NET = 1.66738 * 10_000 / 22.8198 * 35.08 - 25_546.0


def _sb(fake_sb, cash_rows, txns=MOVE):
    return fake_sb({
        "daily_holdings": [{"portfolio_id": "visionnaire", "date": d, "ticker": "CASH", "value": v} for d, v in cash_rows],
        "transactions": txns})


def test_cash_after_a_missing_day_still_sees_the_trades(fake_sb):
    sb = _sb(fake_sb, [("2026-11-16", 31_098.69)])                      # no row for the 17th and 18th
    assert dr.derive_cash_at_date(sb, "visionnaire", "2026-11-19", CAP) == pytest.approx(31_098.69 + NET, abs=0.5)


def test_cash_on_the_day_of_the_move_is_unchanged(fake_sb):
    sb = _sb(fake_sb, [("2026-11-16", 31_098.69)])
    assert dr.derive_cash_at_date(sb, "visionnaire", "2026-11-17", CAP) == pytest.approx(31_098.69 + NET, abs=0.5)


def test_trades_are_never_counted_twice(fake_sb):
    sb = _sb(fake_sb, [("2026-11-16", 31_098.69), ("2026-11-17", 31_098.69 + NET)])
    assert dr.derive_cash_at_date(sb, "visionnaire", "2026-11-18", CAP) == pytest.approx(31_098.69 + NET, abs=0.5)


def test_first_day_only_counts_its_own_trades(fake_sb):
    sb = _sb(fake_sb, [])
    assert dr.derive_cash_at_date(sb, "visionnaire", "2026-11-17", CAP) == pytest.approx(CAP + NET, abs=0.5)
    assert dr.derive_cash_at_date(sb, "visionnaire", "2026-11-18", CAP) == pytest.approx(CAP)


def test_reinforcing_a_line_is_not_a_split():
    assert not dr.is_suspicious_jump(15_500, 39_960, 16.26, 15.86)      # Bitmine, 16 -> 17 November
    assert not dr.is_suspicious_jump(40_000, 12_000, 100.0, 98.0)       # a large trim


def test_an_unapplied_split_is_still_caught():
    assert dr.is_suspicious_jump(86_535, 778_870, 0.662, 5.96)          # ALCPB.PA, September 2026
    assert dr.is_suspicious_jump(50_000, 5_000, 200.0, 20.0)            # 10-for-1
    assert dr.is_suspicious_jump(50_000, 500_000, None, 20.0)           # no previous price: strict


def test_small_or_ordinary_changes_are_ignored():
    assert not dr.is_suspicious_jump(1_000, 9_000, 1.0, 9.0)            # under JUMP_MIN_USD
    assert not dr.is_suspicious_jump(100_719, 130_000, 156.14, 201.0)   # +29% day


def test_a_hole_that_cannot_be_filled_is_recorded(fake_sb, monkeypatch):
    sb = fake_sb({"daily_holdings": [{"portfolio_id": "p", "date": "2026-11-16", "ticker": "CASH", "value": 1.0}]})
    monkeypatch.setattr(dr, "session_close_published", lambda d: (d, None))
    monkeypatch.setattr(dr, "refresh_portfolio", lambda *a: (_ for _ in ()).throw(dr.SuspiciousValueJump("x2.58")))
    monkeypatch.setattr(dr, "BACKFILL_ERRORS", [])
    assert dr.backfill_recent_gaps(sb, ["p"], "2026-11-18", lookback_days=1) == []
    assert dr.BACKFILL_ERRORS == ["p 2026-11-17: x2.58"]
