import asyncio

import pytest

from finanzen import prices
from finanzen.util import isin_valid


@pytest.mark.parametrize(
    ("isin", "valid"),
    [
        ("DE0007164600", True),   # SAP
        ("US67066G1040", True),   # NVIDIA
        ("IE00B5L8K969", True),
        ("DE000A0S9GB0", True),   # Xetra-Gold
        ("DE0007164601", False),  # wrong check digit
        ("goldpreis123", False),
        ("", False),
    ],
)
def test_isin_valid(isin, valid):
    assert isin_valid(isin) is valid


def test_parse_tradegate_last_trade():
    quote = prices.parse_tradegate({"bid": 134.44, "ask": 134.48, "last": 134.48, "close": 139.49})
    assert quote == {"price": 134.48, "prev_close": 139.49, "currency": "EUR"}


def test_parse_tradegate_without_trade_today():
    quote = prices.parse_tradegate({"bid": 4.42, "ask": 4.52, "last": "./.", "close": 4.48})
    assert quote["price"] == pytest.approx(4.47)
    assert quote["prev_close"] == 4.48


def test_parse_tradegate_german_numbers():
    quote = prices.parse_tradegate({"bid": "205,30", "ask": "205,30", "last": "205,30", "close": 207.65})
    assert quote["price"] == pytest.approx(205.3)


def test_parse_tradegate_empty():
    with pytest.raises(prices.PriceError):
        prices.parse_tradegate({"bid": "./.", "ask": "./.", "last": "./.", "close": "./."})


def test_migration_adds_isin(tmp_path):
    import sqlite3

    from finanzen.db import Database

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT "
                 "'aktie', shares REAL NOT NULL DEFAULT 0, cost REAL NOT NULL DEFAULT 0, source TEXT NOT NULL "
                 "DEFAULT 'yahoo', symbol TEXT NOT NULL DEFAULT '')")
    conn.execute("INSERT INTO positions (name, shares) VALUES ('Alt', 1)")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.one("SELECT isin FROM positions WHERE name = 'Alt'")["isin"] == ""
    db.close()


def test_upgrade_sources_of_version_1_0(tmp_path):
    from finanzen.db import Database
    from finanzen.importer import upgrade_sources

    db = Database(tmp_path / "u.db")
    db.insert("positions", {"name": "Asien ETF", "kind": "etf", "shares": 5, "source": "web",
                            "url": "https://www.finanzen.net/etf/ishares-msci-em-asia-etf-ie00b5l8k969",
                            "xpath": "/html/body/span"})
    db.insert("positions", {"name": "Gold", "kind": "gold", "shares": 20, "source": "web",
                            "url": "https://example.com/gold", "xpath": "//x", "factor": 1 / 31.1035})
    db.insert("positions", {"name": "Aktie", "kind": "aktie", "shares": 1, "source": "yahoo", "symbol": "X.DE"})
    assert sorted(upgrade_sources(db)) == ["Asien ETF", "Gold"]
    rows = {r["name"]: r for r in db.q("SELECT * FROM positions")}
    assert rows["Asien ETF"]["source"] == "tradegate" and rows["Asien ETF"]["isin"] == "IE00B5L8K969"
    assert rows["Asien ETF"]["fallback_url"].endswith("ie00b5l8k969")
    assert rows["Gold"]["isin"] == "DE000A0S9GB0" and rows["Gold"]["fallback_xpath"] == "//x"
    assert rows["Aktie"]["source"] == "yahoo"
    assert upgrade_sources(db) == []  # only once
    change = db.one("SELECT id FROM changes ORDER BY id DESC LIMIT 1")
    db.undo(change["id"])
    assert db.one("SELECT source FROM positions WHERE name = 'Gold'")["source"] == "web"
    db.close()


def test_tradegate_used_as_fallback(monkeypatch):
    async def yahoo(session, symbol):
        raise prices.PriceError("Yahoo blockiert den Abruf (HTTP 429)")

    async def tradegate(session, isin):
        assert isin == "DE0007164600"
        return {"price": 190.0, "prev_close": 188.0, "currency": "EUR"}

    monkeypatch.setattr(prices, "yahoo_quote", yahoo)
    monkeypatch.setattr(prices, "tradegate_quote", tradegate)
    pos = {"source": "yahoo", "symbol": "SAP.DE", "isin": "DE0007164600", "factor": 0.5,
           "fallback_url": "", "fallback_xpath": ""}
    quote = asyncio.run(prices.quote_position(None, pos, {}))
    assert quote["price"] == 190.0  # factor does not apply to Tradegate
    assert quote["via"] == "Tradegate"
