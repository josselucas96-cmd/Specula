"""Cockpit cash guard (utils/data.add_position) and monthly-report helpers."""
import pytest

import utils.data as data
import generate_monthly_report as gmr


def _buy(ticker="ZZZ", weight=1.0, price=10.0):
    return {"ticker": ticker, "name": "t", "weight": weight, "entry_price": price, "layer": "Tactical",
            "sector": "Tech", "geography": "USA", "thematic": "Other", "entry_date": "2026-09-19"}


def test_add_position_refuses_overdraft(fake_sb, monkeypatch):
    sb = fake_sb({"positions": [], "transactions": [], "settings": []})
    monkeypatch.setattr(data, "get_client", lambda: sb)
    monkeypatch.setattr(data, "get_cash_amount", lambda pid: 100.0)          # almost empty book
    monkeypatch.setattr(data, "_get_initial_capital", lambda sb_, pid: 1_000_000.0)
    with pytest.raises(ValueError, match="Insufficient cash"):
        data.add_position(_buy(weight=1.0), "test")                           # $10,000 > $100
    assert sb.writes == []                                                    # nothing written


def test_add_position_tolerates_rounding(fake_sb, monkeypatch):
    sb = fake_sb({"positions": [], "transactions": [], "settings": []})
    monkeypatch.setattr(data, "get_client", lambda: sb)
    _cash = lambda pid: 9_999.50                                            # 50 cents short: within $1 tolerance
    _cash.clear = lambda: None                                                # the real one is st.cache_data-wrapped
    monkeypatch.setattr(data, "get_cash_amount", _cash)
    monkeypatch.setattr(data, "_get_initial_capital", lambda sb_, pid: 1_000_000.0)
    monkeypatch.setattr(data, "_snapshot_positions", lambda *a, **k: None)
    data.add_position(_buy(weight=1.0), "test")
    kinds = [w[1] for w in sb.writes]
    assert "positions" in kinds and "transactions" in kinds


def test_month_end():
    assert gmr.month_end("2026-06") == "2026-06-30"
    assert gmr.month_end("2026-02") == "2026-02-28"


def test_missing_commentary_renders_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(gmr, "COMMENTS_DIR", tmp_path)
    market, mgmt, draft = gmr.load_comments("visionnaire", "2026-06-30")
    assert draft and market == gmr.DRAFT_MARKET


def test_commentary_file_makes_it_final(tmp_path, monkeypatch):
    monkeypatch.setattr(gmr, "COMMENTS_DIR", tmp_path)
    (tmp_path / "visionnaire").mkdir()
    (tmp_path / "visionnaire" / "2026-06.toml").write_text(
        'market_comment = ["a", "b"]\nmgmt_comment = ["c {pf_mtd_pct}"]\n', encoding="utf-8")
    market, mgmt, draft = gmr.load_comments("visionnaire", "2026-06-30")
    assert not draft and market == ["a", "b"] and mgmt == ["c {pf_mtd_pct}"]


def test_empty_commentary_lists_still_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(gmr, "COMMENTS_DIR", tmp_path)
    (tmp_path / "nakamoto").mkdir()
    (tmp_path / "nakamoto" / "2026-06.toml").write_text('market_comment = []\nmgmt_comment = ["x"]\n', encoding="utf-8")
    assert gmr.load_comments("nakamoto", "2026-06-30")[2] is True


# ── Monthly attribution must be read from the ledger ─────────────────────────
# Before 2026-09-23 the contribution of a position was its CURRENT share count
# times the move from `entry_price`. That broke whenever the two were on
# different bases: Le Nakamoto's May report credited Capital B with -9.19pp of a
# -9.52% month when the ledger says the line cost 2.24pp, because the month
# began before the portfolio existed and a 1:10 split had since multiplied the
# share count. The figure now comes from daily_holdings alone.
def _rows(pid, recs):
    return {"daily_holdings": [
        {"portfolio_id": pid, "date": d, "ticker": t, "shares": s, "price": p, "value": s * p}
        for d, t, s, p in recs]}


def _setup(monkeypatch, sb, nav_start):
    monkeypatch.setattr(gmr, "sb", sb)
    monkeypatch.setattr(gmr, "fetch_nav_and_cash_at", lambda pid, d: (nav_start, 0.0))


def test_contribution_is_value_change_net_of_money_put_in(fake_sb, monkeypatch):
    """A line reinforced mid-month: the cash added is not performance."""
    sb = fake_sb(_rows("p", [
        ("2026-05-31", "AAA", 100.0, 10.0),     # 1,000 at month start
        ("2026-06-15", "AAA", 200.0, 10.0),     # +100 shares bought at 10 -> +1,000 in
        ("2026-06-30", "AAA", 200.0, 11.0),     # 2,200 at month end
    ]))
    _setup(monkeypatch, sb, 100_000.0)
    positions = [{"ticker": "AAA", "entry_date": "2026-01-01", "entry_price": 5.0, "shares": 200.0}]

    gmr.compute_mtd_attribution(positions, "p", "2026-06-30")

    # 2,200 - 1,000 - 1,000 = 200 of P&L on a 100,000 NAV
    assert positions[0]["contribution_mtd"] == pytest.approx(0.2)
    assert positions[0]["perf_mtd_pct"] == pytest.approx(10.0)


def test_a_split_is_not_a_purchase(fake_sb, monkeypatch):
    """1:10 split: ten times the shares at a tenth of the price. No money moved,
    no contribution, and the price return is still read correctly."""
    sb = fake_sb(_rows("p", [
        ("2026-08-31", "BBB", 100.0, 50.0),     # 5,000
        ("2026-09-08", "BBB", 1000.0, 5.0),     # 5,000, split
        ("2026-09-30", "BBB", 1000.0, 6.0),     # 6,000
    ]))
    _setup(monkeypatch, sb, 100_000.0)
    positions = [{"ticker": "BBB", "entry_date": "2026-01-01", "entry_price": 40.0, "shares": 1000.0}]

    gmr.compute_mtd_attribution(positions, "p", "2026-09-30")

    assert positions[0]["contribution_mtd"] == pytest.approx(1.0)    # 1,000 / 100,000
    assert positions[0]["perf_mtd_pct"] == pytest.approx(20.0)       # 50 -> 60 post-split


def test_a_position_opened_during_the_month_is_not_credited_with_its_purchase(fake_sb, monkeypatch):
    sb = fake_sb(_rows("p", [
        ("2026-06-10", "CCC", 100.0, 20.0),     # bought, first appearance
        ("2026-06-30", "CCC", 100.0, 22.0),
    ]))
    _setup(monkeypatch, sb, 100_000.0)
    positions = [{"ticker": "CCC", "entry_date": "2026-06-10", "entry_price": 20.0, "shares": 100.0}]

    gmr.compute_mtd_attribution(positions, "p", "2026-06-30")

    assert positions[0]["contribution_mtd"] == pytest.approx(0.2)    # 200 of gain, not 2,200
    assert positions[0]["perf_mtd_pct"] == pytest.approx(10.0)


def test_a_position_closed_before_month_end_reports_nothing(fake_sb, monkeypatch):
    sb = fake_sb(_rows("p", [("2026-05-31", "DDD", 100.0, 10.0), ("2026-06-05", "DDD", 100.0, 11.0)]))
    _setup(monkeypatch, sb, 100_000.0)
    positions = [{"ticker": "DDD", "entry_date": "2026-01-01", "entry_price": 5.0, "shares": 0.0}]

    gmr.compute_mtd_attribution(positions, "p", "2026-06-30")

    assert positions[0]["contribution_mtd"] is None


def test_published_report_title_is_in_english():
    # The site listed "Juin 2026 — Monthly Report — …" until 2026-10-09.
    assert gmr.report_title("Le Bâtisseur", "2026-09") == "September 2026 — Monthly Report — Le Bâtisseur"
    assert gmr.report_title("Le Nakamoto", "2026-05").startswith("May 2026")
