import pytest

from finanzen import trimport
from finanzen.db import Database

from sample_tr import ALPHA, BETA, DELTA, GAMMA, ROWS, tr_csv

TABLES = ("positions", "dividends", "trades", "deposits", "tr_transactions", "changes")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    with database.transaction():
        alpha = database.insert("positions", {"name": "Alpha Motoren", "kind": "aktie", "shares": 20, "cost": 11.0,
                                              "source": "tradegate", "isin": ALPHA, "price": 14.0, "sort": 1})
        database.insert("positions", {"name": "Delta Post AG", "kind": "aktie", "shares": 0, "cost": 30.0,
                                      "source": "manual", "sort": 2})
        database.insert("positions", {"name": "Gold", "kind": "gold", "shares": 100, "cost": 50.0, "source": "tradegate",
                                      "isin": "XF0000000999", "price": 60.0, "sort": 3})
        database.insert("positions", {"name": "Omega Krypto", "kind": "crypto", "shares": 1, "cost": 100.0,
                                      "source": "manual", "isin": "XF0000000052", "price": 120.0, "sort": 4})
        database.insert("dividends", {"date": "2024-04-01", "position": "Alpha", "amount": 3.68})
        database.insert("dividends", {"date": "2023-12-01", "position": "Zeta", "amount": 1.0})
        for month, gold, depot in (("2023-12", 10, 50), ("2024-01", 20, 400), ("2024-02", 0, 171), ("2024-03", 0, 0),
                                   ("2024-04", 0, 30)):
            database.insert("deposits", {"month": month, "gold": gold, "depot": depot})
        database.insert("trades", {"date": "2024-02-12", "kind": "kauf", "position_id": alpha,
                                   "position": "Alpha Motoren", "shares": 10, "amount": 120.0})
    yield database
    database.close()


def counts(db):
    return {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in TABLES}


def pos(db, isin):
    return db.one("SELECT * FROM positions WHERE isin = ?", (isin,))


def test_parse_reads_only_securities():
    rows, info = trimport.parse_csv(tr_csv())
    assert {r["type"] for r in rows} == {"BUY", "SELL", "DIVIDEND", "SPLIT", "BENEFITS_SAVEBACK", "STOCKPERK"}
    assert len(rows) == 13
    assert all("counterparty_iban" not in r for r in rows)
    assert info["first"] == "2024-01-05"
    assert info["export_end"] == "2024-06-20"
    assert info["rows"] == len(ROWS)


def test_parse_semicolon_and_wrong_file():
    rows, _info = trimport.parse_csv(tr_csv(delimiter=";"))
    assert len(rows) == 13
    with pytest.raises(ValueError, match="kein Transaktionsexport"):
        trimport.parse_csv(b"Datum;Betrag\n2024-01-01;5\n")


def test_compute_history():
    rows, _info = trimport.parse_csv(tr_csv())
    history = trimport.compute(rows)
    sec = history["securities"]
    alpha = sec[ALPHA]
    assert alpha["shares"] == pytest.approx(15)
    assert alpha["cost_total"] / alpha["shares"] == pytest.approx(11.1)  # 222 € incl. fees for 20, 5 sold
    assert alpha["realized"] == pytest.approx(67 - 55.5)  # credit after fee and tax minus average cost
    assert [d["amount"] for d in alpha["dividends"]] == [3.68, 2.0]
    assert sec[BETA]["shares"] == pytest.approx(2.16)
    assert sec[BETA]["cost_total"] == pytest.approx(54)
    assert sec[GAMMA]["shares"] == pytest.approx(0.3)  # split 1:10
    assert sec[DELTA]["sold_at"] == "2024-05-10"
    assert sec[DELTA]["realized"] == pytest.approx(48)
    assert history["bonus"] == pytest.approx(13)
    months = {k: round(v, 2) for k, v in history["months"].items()}
    assert months == {"2024-01": 402.0, "2024-02": 171.0, "2024-03": -67.0, "2024-05": -349.0}


def test_preview_changes_nothing(db):
    before = counts(db)
    alpha_before = pos(db, ALPHA)
    report = trimport.preview(db, tr_csv())
    assert counts(db) == before
    assert pos(db, ALPHA) == alpha_before
    assert report["totals"]["before"]["invested_depot"] == pytest.approx(651)
    assert report["totals"]["after"]["invested_depot"] == pytest.approx(50 + 157)
    by_isin = {p["isin"]: p for p in report["positions"]}
    assert by_isin[ALPHA]["match"] == "isin"
    assert by_isin[ALPHA]["old"]["shares"] == 20 and by_isin[ALPHA]["new"]["shares"] == pytest.approx(15)
    assert by_isin[DELTA]["match"] == "name" and by_isin[DELTA]["name"] == "Delta Post AG"
    assert by_isin[BETA]["match"] == "neu" and by_isin[GAMMA]["match"] == "neu"
    assert [s["name"] for s in report["sales"]] == ["Alpha Motoren", "Delta Post AG"]
    assert report["dividends_matched"] == 1
    assert [d["amount"] for d in report["dividends_new"]] == [2.0]
    assert report["dividends_app_only"] == [{"date": "2023-12-01", "position": "Zeta", "amount": 1.0}]
    assert report["not_in_export"] == ["Omega Krypto"]
    assert len(report["trades_replaced"]) == 1
    assert report["deposit_range"] == {"first": "2024-01", "last": "2024-06"}
    assert any("Omega Krypto" in w for w in report["warnings"])


def test_apply_and_undo(db):
    before = counts(db)
    result = trimport.apply(db, tr_csv())
    assert result["changes"] > 0
    alpha = pos(db, ALPHA)
    assert alpha["shares"] == pytest.approx(15)
    assert alpha["cost"] == pytest.approx(11.1)
    assert alpha["realized"] == pytest.approx(11.5)
    delta = db.one("SELECT * FROM positions WHERE name = 'Delta Post AG'")
    assert delta["isin"] == DELTA and delta["shares"] == 0 and delta["sold_at"] == "2024-05-10"
    beta = pos(db, BETA)
    assert beta["kind"] == "etf" and beta["source"] == "tradegate" and beta["cost"] == pytest.approx(25)
    assert pos(db, GAMMA)["kind"] == "aktie"
    assert db.one("SELECT * FROM positions WHERE name = 'Gold'")["shares"] == 100
    assert db.one("SELECT * FROM positions WHERE name = 'Omega Krypto'")["shares"] == 1

    divs = db.q("SELECT * FROM dividends ORDER BY date")
    assert [(d["position"], d["amount"]) for d in divs] == [("Zeta", 1.0), ("Alpha Motoren", 3.68),
                                                             ("Alpha Motoren", 2.0)]
    assert divs[1]["date"] == "2024-04-20" and divs[1]["tx"] == "t12"

    deposits = {d["month"]: (d["gold"], d["depot"]) for d in db.q("SELECT * FROM deposits")}
    assert deposits == {"2023-12": (10, 50), "2024-01": (20, 402), "2024-02": (0, 171), "2024-03": (0, -67),
                        "2024-04": (0, 0), "2024-05": (0, -349)}

    trades = db.q("SELECT * FROM trades ORDER BY date, id")
    assert all(t["tx"] for t in trades) and len(trades) == 8
    sale = [t for t in trades if t["kind"] == "verkauf" and t["position"] == "Alpha Motoren"][0]
    assert sale["amount"] == pytest.approx(67) and sale["gain"] == pytest.approx(11.5)
    assert sale["position_id"] == alpha["id"]
    assert all(t["position_id"] for t in trades)
    assert db.scalar("SELECT COUNT(*) FROM tr_transactions") == 13
    assert db.get_setting("tr_import")["until"] == "2024-06-20"

    change = db.one("SELECT * FROM changes ORDER BY id DESC LIMIT 1")
    assert change["text"].startswith("Trade-Republic-Import")
    db.undo(change["id"])
    after_undo = counts(db)
    assert {k: v for k, v in after_undo.items() if k != "changes"} == {k: v for k, v in before.items() if k != "changes"}
    assert pos(db, ALPHA)["shares"] == 20
    assert db.one("SELECT * FROM deposits WHERE month = '2024-01'")["depot"] == 400
    assert db.get_setting("tr_import") is None


def test_second_import_is_idempotent_and_merges(db):
    trimport.apply(db, tr_csv())
    again = trimport.apply(db, tr_csv())
    assert again["changes"] == 0
    # A later export that only covers the last weeks: the stored history stays the base
    newer = [r for r in ROWS if r[0] >= "2024-06-01"] + [
        ("2024-07-02T10:00:00Z", "TRADING", "BUY", "STOCK", "Alpha Motoren", ALPHA, "5", "16.00", "-80.00", "-1.00",
         "", "t17"),
    ]
    trimport.apply(db, tr_csv(newer))
    alpha = pos(db, ALPHA)
    assert alpha["shares"] == pytest.approx(20)
    assert alpha["cost"] == pytest.approx((166.5 + 81) / 20)
    assert alpha["realized"] == pytest.approx(11.5)
    assert db.one("SELECT * FROM deposits WHERE month = '2024-07'")["depot"] == pytest.approx(81)
    assert db.one("SELECT * FROM deposits WHERE month = '2024-01'")["depot"] == pytest.approx(402)


def test_manual_trade_after_export_is_flagged(db):
    alpha = pos(db, ALPHA)
    with db.transaction():
        db.insert("trades", {"date": "2024-07-01", "kind": "kauf", "position_id": alpha["id"],
                             "position": "Alpha Motoren", "shares": 1, "amount": 15.0})
    report = trimport.preview(db, tr_csv())
    flagged = [t for t in report["trades_replaced"] if t["after_export"]]
    assert len(flagged) == 1
    assert any("neuer als der Export" in w for w in report["warnings"])


def test_sell_without_buys_is_reported(db):
    rows = [r for r in ROWS if r[11] not in ("t03", "t09")]
    report = trimport.preview(db, tr_csv(rows))
    assert any("mehr verkauft als vorher gekauft" in w for w in report["warnings"])
