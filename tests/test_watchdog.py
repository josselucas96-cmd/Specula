"""Morning watchdog (scripts/watchdog.py). No network."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import watchdog as wd  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    wd.problems.clear(); wd.notes.clear(); wd.alerts.clear()


def _sb(fake_sb, last_date):
    return fake_sb({"portfolios": [{"id": "batisseur"}],
                    "daily_holdings": [{"portfolio_id": "batisseur", "date": last_date}]})


def test_yesterday_written_is_green(fake_sb):
    wd.check_holdings(_sb(fake_sb, "2026-09-30"), today=date(2026, 10, 1))
    assert not wd.problems


def test_yesterday_left_to_the_morning_pass_is_not_a_failure(fake_sb):
    # 1 Oct 2026: the 01:10 UTC run found the 30 Sept close unpublished, the
    # morning pass only ran at 16:00 UTC, the watchdog at 13:00 UTC.
    wd.check_holdings(_sb(fake_sb, "2026-09-29"), today=date(2026, 10, 1))
    assert not wd.problems
    assert any("morning pass" in n for n in wd.notes)


def test_two_sessions_behind_fails(fake_sb):
    wd.check_holdings(_sb(fake_sb, "2026-09-28"), today=date(2026, 10, 1))
    assert wd.problems


def test_workflow_installs_what_the_watchdog_imports():
    # Every run crashed 23 Sept - 1 Oct 2026 on `import pandas`: the check
    # existed, the dependency was never installed in the workflow.
    yml = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "watchdog.yml").read_text()
    src = (Path(__file__).resolve().parent.parent / "scripts" / "watchdog.py").read_text()
    for pkg in ("pandas", "yfinance", "supabase"):
        if f"import {pkg}" in src or f"from {pkg}" in src:
            assert pkg in yml, f"watchdog.py imports {pkg} but the workflow does not install it"
