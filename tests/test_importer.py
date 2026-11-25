import pytest

from finanzen.db import Database
from finanzen.importer import googlefinance_to_yahoo, import_workbook, parse_price_formula

from sample_sheet import TODAY, build


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    values, formulas = build()
    import_workbook(database, values, TODAY, formula_data=formulas)
    yield database
    database.close()


def test_ticker_mapping():
    assert googlefinance_to_yahoo("ETR:VOW") == "VOW.DE"
    assert googlefinance_to_yahoo("FRA:34Q0") == "34Q0.F"
    assert googlefinance_to_yahoo("SWX:DAXEX") == "DAXEX.SW"
    assert googlefinance_to_yahoo("MIGA:FRA") == "MIGA.F"
    assert googlefinance_to_yahoo("NVDA") == "NVDA"


def test_formula_with_fallback():
    formula = ('=IFERROR(__xludf.DUMMYFUNCTION("LET(g; ISTNV(GOOGLEFINANCE(""ETR:RWE"")); '
               'WENN(g; IMPORTXML(""https://x.test/rwe""; ""/html/body/span[1]""); GOOGLEFINANCE(""ETR:RWE"")))"),56.9)')
    source = parse_price_formula(formula)
    assert source["source"] == "yahoo"
    assert source["symbol"] == "RWE.DE"
    assert source["fallback_url"] == "https://x.test/rwe"
    assert source["fallback_xpath"] == "/html/body/span[1]"


def test_fixed_costs(db):
    rows = {r["name"]: r for r in db.q("SELECT * FROM fixed_costs")}
    assert len(rows) == 9
    assert rows["Bankkarte"]["due"] == "21.01." and rows["Bankkarte"]["note"] == ""
    assert rows["Musikdienst"]["due"] == "05." and rows["Musikdienst"]["interval"] == "monatlich"
    assert rows["Cloudspeicher"]["due"] == "14.03." and rows["Cloudspeicher"]["interval"] == "jaehrlich"
    assert rows["Server"]["due"] == "27.–30."
    assert rows["Zahnarzt"]["note"] == "70€ - 20€ Erstattung"
    assert rows["Hausrat"]["amount"] is None
    assert rows["Miete"]["installments"] == 2
    assert rows["Musikdienst"]["grp"] == "abo" and rows["Miete"]["grp"] == "sonstiges"


def test_positions_and_sources(db):
    pos = {r["name"]: r for r in db.q("SELECT * FROM positions")}
    assert pos["Beispiel AG"]["source"] == "yahoo" and pos["Beispiel AG"]["symbol"] == "BSP.DE"
    assert pos["Beispiel AG"]["fallback_url"] == "https://example.com/bsp"
    assert pos["Welt ETF"]["kind"] == "etf" and pos["Welt ETF"]["symbol"] == "WELT.AS"
    niche = pos["Nischen ETF"]
    assert niche["source"] == "tradegate" and niche["isin"] == "IE00B5L8K969"
    assert niche["fallback_url"] == "https://example.com/etf/nischen-etf-ie00b5l8k969"
    assert niche["fallback_xpath"] == "//div[@class='kurs']/text()"
    assert niche["kind"] == "etf"
    assert pos["US Tech"]["symbol"] == "TECH" and pos["US Tech"]["kind"] == "aktie"
    assert pos["Beispiel AG"]["isin"] == ""
    # "Wert ETF's, Aktien, Crypto Aktuell" sums everything and must not mark stocks as crypto
    assert {p["kind"] for p in pos.values()} == {"aktie", "etf", "gold"}
    assert pos["Alte Aktie"]["shares"] == 0
    gold = pos["Gold"]
    assert gold["kind"] == "gold" and gold["shares"] == 20
    assert gold["source"] == "tradegate" and gold["isin"] == "DE000A0S9GB0"
    assert gold["fallback_url"] == "https://example.com/gold"
    assert gold["factor"] == pytest.approx(1 / 31.1035)
    assert gold["cost"] == pytest.approx(1000 / 20)


def test_other_tables(db):
    assert db.scalar("SELECT SUM(gold + depot) FROM deposits") == pytest.approx(1400)
    assert db.scalar("SELECT COUNT(*) FROM dividends") == 2
    assert [a["name"] for a in db.q("SELECT name FROM accounts ORDER BY sort")] == ["Topf „Rücklagen“", "Girokonto"]
    assert db.scalar("SELECT COUNT(*) FROM daily") == 2
    assert db.one("SELECT * FROM daily WHERE date = '2026-11-22'")["total"] == 4000
    assert db.get_setting("salary_net") == 2000
    cats = db.q("SELECT * FROM categories ORDER BY sort")
    assert [c["name"] for c in cats] == ["Essen", "Tanken"] and cats[0]["account"] == "Bank A"
    assert db.scalar("SELECT COUNT(DISTINCT month) FROM spending") == 7
    payments = db.q("SELECT * FROM payments ORDER BY sort")
    assert [p["name"] for p in payments] == ["Musikdienst", "Miete (Rate 1 von 2)", "Miete (Rate 2 von 2)", "Server"]
    assert [p["paid"] for p in payments] == [1, 1, 0, 0]
    assert payments[3]["due"] == "27.–30."
