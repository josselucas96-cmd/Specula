"""Batching of the public Moves page (utils/moves.py).

Regression: trades of the same rebalance were drawn one by one, landing on the
same point of the NAV curve and hiding each other — Le Visionnaire's 13 trades
sit on 4 dates, so the chart showed 4 dots for 13 trades (reported 2026-09-22).
The page now draws exactly one marker per trading day, whose glyph says whether
the day bought, sold, or did both.
"""
from utils.moves import BOTH, BUY, SELL, batch_numbers, group_moves_by_date, side_of, trades_only


def _t(date, action, ticker):
    key = "ticker_in" if action in ("IN", "SWITCH", "SPLIT") else "ticker_out"
    return {"date": date, "action": action, key: ticker}


# The real Visionnaire book: 13 trades on 4 dates.
VISIONNAIRE = [
    _t("2026-05-14", "TRIM", "AMD"), _t("2026-05-14", "IN", "CELH"), _t("2026-05-14", "IN", "HIMS"),
    _t("2026-05-14", "TRIM", "RKLB"), _t("2026-05-14", "TRIM", "TSLA"),
    _t("2026-05-26", "IN", "NIO"), _t("2026-05-26", "IN", "NU"),
    _t("2026-05-26", "IN", "SMCI"), _t("2026-05-26", "IN", "SOFI"),
    _t("2026-06-01", "IN", "ZETA"), _t("2026-06-01", "OUT", "AMZN"),
    _t("2026-06-12", "IN", "MTPLF"), _t("2026-06-12", "IN", "ZETA"),
]


def test_one_marker_per_day_and_no_trade_left_behind():
    groups = group_moves_by_date(VISIONNAIRE)
    assert len(groups) == 4                                    # 4 markers
    assert sum(g["n"] for g in groups) == len(VISIONNAIRE)     # covering all 13 trades


def test_kind_says_what_the_day_did():
    kinds = {g["date"]: g["kind"] for g in group_moves_by_date(VISIONNAIRE)}
    assert kinds["2026-05-14"] == BOTH      # 3 trims + 2 buys -> split disc
    assert kinds["2026-05-26"] == BUY       # 4 buys -> green disc
    assert kinds["2026-06-01"] == BOTH      # a close and a buy
    assert kinds["2026-06-12"] == BUY


def test_each_side_is_sorted_by_ticker_for_a_stable_tooltip():
    may14 = next(g for g in group_moves_by_date(VISIONNAIRE) if g["date"] == "2026-05-14")
    assert [t.get("ticker_in") for t in may14["buys"]] == ["CELH", "HIMS"]
    assert [t.get("ticker_out") for t in may14["sells"]] == ["AMD", "RKLB", "TSLA"]
    assert may14["n"] == 5


def test_groups_are_chronological():
    assert [g["date"] for g in group_moves_by_date(VISIONNAIRE)] == [
        "2026-05-14", "2026-05-26", "2026-06-01", "2026-06-12"]


def test_sides():
    assert side_of(_t("2026-01-01", "IN", "X")) == BUY
    assert side_of(_t("2026-01-01", "SWITCH", "X")) == BUY
    assert side_of(_t("2026-01-01", "TRIM", "X")) == SELL
    assert side_of(_t("2026-01-01", "OUT", "X")) == SELL
    assert side_of(_t("2026-01-01", "SPLIT", "X")) is None


def test_a_sell_only_day():
    groups = group_moves_by_date([_t("2026-07-01", "OUT", "AAA"), _t("2026-07-01", "TRIM", "BBB")])
    assert [(g["kind"], g["n"]) for g in groups] == [(SELL, 2)]
    assert groups[0]["buys"] == []


def test_corporate_actions_are_never_plotted():
    moves = VISIONNAIRE + [_t("2026-09-08", "SPLIT", "ALCPB.PA")]
    assert sum(g["n"] for g in group_moves_by_date(moves)) == 13      # split excluded


def test_corporate_actions_are_not_moves():
    """Regression 2026-11-06: the Moves page listed the ALCPB.PA split in its
    title and trade log, and numbered Le Nakamoto's only rebalance #2."""
    book = [_t("2026-09-08", "SPLIT", "ALCPB.PA"), _t("2026-09-30", "DRIP", "MSTR"),
                _t("2026-10-23", "IN", "ALCPB.PA"), _t("2026-10-23", "IN", "MSTR"),
                _t("2026-10-23", "TRIM", "AAA"), _t("2026-10-23", "OUT", "BBB")]
    moves = trades_only(book)
    assert len(moves) == 4
    assert all(t["action"] not in ("SPLIT", "DRIP") for t in moves)
    assert batch_numbers(moves) == {"2026-10-23": 1}
    assert trades_only([]) == []


def test_batch_numbers_are_oldest_first():
    assert batch_numbers(VISIONNAIRE) == {
        "2026-05-14": 1, "2026-05-26": 2, "2026-06-01": 3, "2026-06-12": 4}


def test_empty_book():
    assert group_moves_by_date([]) == []
