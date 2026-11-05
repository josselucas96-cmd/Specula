"""Hourly display quotes (utils.market.merge_live_quotes / parse_daily_closes).

The live overlay may only ever replace a nightly row with a quote that is
provably the same instrument, unit and share basis; anything else must fall
back to the nightly row, which is the behaviour the site had before.
"""
import numpy as np
import pandas as pd

from utils.market import merge_live_quotes, parse_daily_closes


def _eod(price, ccy="USD", chg=1.0):
    return {"price": price, "change_pct": chg, "market_cap": 1e9, "currency": ccy}


def test_live_quote_replaces_row_when_series_contains_nightly_close():
    # During the session: nightly row = yesterday's close (161.27), live last = today.
    eod = {"DUOL": _eod(161.27, chg=12.78)}
    live = {"DUOL": {"closes": [135.27, 128.00, 161.27, 158.00], "as_of": "2026-11-05T15:00+00:00"}}
    out = merge_live_quotes(eod, live)["DUOL"]
    assert out["price"] == 158.00
    assert out["change_pct"] == round((158.00 / 161.27 - 1) * 100, 2)
    assert out["as_of"] == "2026-11-05T15:00+00:00"


def test_today_pct_uses_official_closes_not_after_hours():
    # After the close: the 2026-11-04 session, 128.00 -> 161.27 = +25.99%,
    # where Yahoo's previous_close (after-hours ~143) gave +12.78%.
    eod = {"DUOL": _eod(161.27, chg=12.78)}
    live = {"DUOL": {"closes": [135.27, 128.00, 161.27], "as_of": "x"}}
    assert merge_live_quotes(eod, live)["DUOL"]["change_pct"] == 25.99


def test_large_genuine_move_today_is_accepted():
    eod = {"X": _eod(100.0)}
    live = {"X": {"closes": [98.0, 100.0, 140.0], "as_of": "x"}}
    assert merge_live_quotes(eod, live)["X"]["price"] == 140.0


def test_unapplied_split_keeps_nightly_row():
    # 3-for-2 split overnight: Yahoo's history is split-adjusted, the nightly
    # row (and the shares in the book) are not yet.
    eod = {"X": _eod(150.0)}
    live = {"X": {"closes": [99.0, 100.0, 101.0], "as_of": "x"}}
    out = merge_live_quotes(eod, live)["X"]
    assert out["price"] == 150.0 and out["as_of"] is None


def test_wrong_instrument_or_unit_keeps_nightly_row():
    # e.g. a mixed-up download (2026-09-23) or a pence/pound error (x100).
    eod = {"SWC.L": _eod(0.95, ccy="GBp")}
    live = {"SWC.L": {"closes": [95.0, 96.0, 94.0], "as_of": "x"}}
    assert merge_live_quotes(eod, live)["SWC.L"]["price"] == 0.95


def test_missing_live_quote_keeps_nightly_row():
    eod = {"NVDA": _eod(200.13, chg=-3.83)}
    out = merge_live_quotes(eod, {})["NVDA"]
    assert out == {**eod["NVDA"], "as_of": None}


def test_new_position_without_nightly_row():
    # Opened today: no current_prices row yet. A US listing (USD) takes the live
    # quote; a foreign listing, whose currency is unknown, does not.
    none_row = {"price": None, "change_pct": None, "market_cap": None, "currency": None}
    live = {"MU": {"closes": [795.23, 696.88], "as_of": "x"},
            "ABC.PA": {"closes": [10.0, 11.0], "as_of": "x"}}
    out = merge_live_quotes({"MU": dict(none_row), "ABC.PA": dict(none_row)}, live)
    assert out["MU"]["price"] == 696.88
    assert out["ABC.PA"]["price"] is None


def test_parse_daily_closes_multi_ticker_frame():
    idx = pd.to_datetime(["2026-11-03", "2026-11-04", "2026-11-05"])
    cols = pd.MultiIndex.from_product([["Close", "Open"], ["AAA", "BBB"]], names=["Price", "Ticker"])
    data = np.array([[10, 20, 9, 19], [11, np.nan, 10, 20], [12, 22, 11, 21]], dtype=float)
    df = pd.DataFrame(data, index=idx, columns=cols)
    out = parse_daily_closes(df, ["AAA", "BBB", "CCC"])
    assert out == {"AAA": [10.0, 11.0, 12.0], "BBB": [20.0, 22.0]}


def test_parse_daily_closes_single_ticker_and_empty():
    idx = pd.to_datetime(["2026-11-04", "2026-11-05"])
    df = pd.DataFrame({"Close": [5.0, 6.0], "Open": [4.0, 5.0]}, index=idx)
    assert parse_daily_closes(df, ["ONE"]) == {"ONE": [5.0, 6.0]}
    assert parse_daily_closes(pd.DataFrame(), ["ONE"]) == {}
    assert parse_daily_closes(None, ["ONE"]) == {}
