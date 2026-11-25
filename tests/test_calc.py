import datetime as dt

import pytest

from finanzen import calc
from finanzen.db import Database
from finanzen.importer import import_workbook

from sample_sheet import TODAY, build


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    values, formulas = build()
    import_workbook(database, values, TODAY, formula_data=formulas)
    yield database
    database.close()


def _pos(db, name):
    return db.one("SELECT * FROM positions WHERE name = ?", (name,))


def test_portfolio_values(db):
    pf = calc.portfolio(db)
    assert pf["stocks"] == pytest.approx(50 * 10 + 150 * 1)
    assert pf["etfs"] == pytest.approx(100 * 5 + 20 * 3)
    assert pf["gold_value"] == pytest.approx(100 * 20)
    assert pf["invested"] == pytest.approx(1400)
    assert pf["result"] == pytest.approx(pf["investments"] - 1400)
    assert [p["name"] for p in pf["sold"]] == ["Alte Aktie"]


def test_buy_updates_cost_and_deposits(db):
    pos = _pos(db, "Welt ETF")
    calc.book_trade(db, "kauf", pos["id"], "2026-11-24", amount=100, price=100)
    pos = _pos(db, "Welt ETF")
    assert pos["shares"] == pytest.approx(6)
    assert pos["cost"] == pytest.approx((5 * 90 + 100) / 6)
    assert db.one("SELECT * FROM deposits WHERE month = '2026-11'")["depot"] == pytest.approx(100)


def test_full_sale_and_undo(db):
    pos = _pos(db, "Beispiel AG")
    calc.book_trade(db, "verkauf", pos["id"], "2026-11-24", shares=10, credit=480)
    sold = _pos(db, "Beispiel AG")
    assert sold["shares"] == 0 and sold["sold_at"] == "2026-11-24"
    assert sold["realized"] == pytest.approx(480 - 10 * 40)
    assert db.one("SELECT * FROM deposits WHERE month = '2026-11'")["depot"] == pytest.approx(-480)
    with pytest.raises(ValueError):
        calc.book_trade(db, "verkauf", pos["id"], "2026-11-24", shares=1, credit=50)
    change = db.one("SELECT id FROM changes ORDER BY id DESC LIMIT 1")
    db.undo(change["id"])
    restored = _pos(db, "Beispiel AG")
    assert restored["shares"] == 10 and restored["sold_at"] is None
    assert db.scalar("SELECT COUNT(*) FROM trades") == 0
    assert db.one("SELECT * FROM deposits WHERE month = '2026-11'")["depot"] == pytest.approx(0)


def test_partial_gold_sale(db):
    gold = _pos(db, "Gold")
    calc.book_trade(db, "verkauf", gold["id"], "2026-11-24", shares=5, credit=600)
    gold = _pos(db, "Gold")
    assert gold["shares"] == pytest.approx(15)
    assert gold["realized"] == pytest.approx(600 - 5 * 50)
    assert db.one("SELECT * FROM deposits WHERE month = '2026-11'")["gold"] == pytest.approx(-600)


def test_fixed_summary(db):
    sett = calc.settings(db)
    spend = calc.spending_summary(db, TODAY)
    # Average over the 6 complete months before November
    assert spend["avg_months"] == 6
    assert spend["avg_total"] == pytest.approx(400)
    assert spend["this_total"] == pytest.approx(190)
    fixed = calc.fixed_summary(db, TODAY, sett, spend)
    monthly = 9.99 + 30 / 12 + 12.5 + 800 + 120 / 12 + 60 + 50 / 12 + 15 / 12
    assert fixed["fixed"] == pytest.approx(monthly)
    assert fixed["total"] == pytest.approx(monthly + 400)
    assert fixed["rest"] == pytest.approx(2000 - monthly - 400)
    assert fixed["missing"] == ["Hausrat"]
    assert fixed["upcoming"][0]["name"] == "Server"


def test_month_list_with_carry_over(db):
    created = calc.ensure_month(db, "2026-12")
    payments = db.q("SELECT * FROM payments WHERE month = '2026-12' ORDER BY sort")
    names = [p["name"] for p in payments]
    assert "Versicherung" not in names  # due in November
    assert names.count("Miete (Rate 1 von 2)") == 1
    carried = [p for p in payments if p["carried"]]
    assert {p["name"] for p in carried} == {"Miete (Rate 2 von 2)", "Server"}
    assert created == len(payments)
    assert calc.ensure_month(db, "2026-12") == 0


def test_state_builds(db):
    state = calc.build_state(db, dt.datetime(2026, 11, 24, 12, 0), {"version": "test"})
    assert state["overview"]["open_count"] == 2
    assert state["month"]["label"] == "November 2026"
    assert len(state["depot"]["positions"]) == 4
    assert state["fixed"]["missing"] == ["Hausrat"]
    dep = state["depot"]
    assert dep["result_depot"] == pytest.approx(dep["depot_value"] - dep["invested_depot"])
    assert dep["result_gold"] == pytest.approx(dep["gold_value"] - dep["invested_gold"])
    assert dep["result_depot"] + dep["result_gold"] == pytest.approx(dep["result"])
