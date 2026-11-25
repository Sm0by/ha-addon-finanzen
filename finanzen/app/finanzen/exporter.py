"""Export of all data as .xlsx, so nothing is locked into the app."""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Font

from .db import Database

SHEETS = [
    ("Positionen", "SELECT name AS Name, kind AS Art, shares AS Anteile, cost AS \"Einstand je Stück\", "
                   "price AS Kurs, source AS Quelle, symbol AS Symbol, url AS Adresse, xpath AS Auswahlpfad, "
                   "factor AS Faktor, fallback_url AS \"Ersatz-Adresse\", fallback_xpath AS \"Ersatz-Auswahlpfad\", "
                   "sold_at AS \"Verkauft am\", realized AS \"Realisierter Gewinn\" FROM positions ORDER BY sort, id"),
    ("Buchungen", "SELECT date AS Datum, kind AS Art, position AS Position, shares AS Anteile, amount AS Betrag, "
                  "fee AS Gebühr, gain AS Gewinn FROM trades ORDER BY date, id"),
    ("Einzahlungen", "SELECT month AS Monat, gold AS Gold, depot AS Depot FROM deposits ORDER BY month"),
    ("Dividenden", "SELECT date AS Datum, position AS Position, amount AS Betrag, tax AS Steuer "
                   "FROM dividends ORDER BY date, id"),
    ("Fixkosten", "SELECT name AS Name, amount AS Betrag, interval AS Intervall, account AS Konto, due AS Fällig, "
                  "grp AS Gruppe, installments AS Raten, note AS Anmerkung FROM fixed_costs ORDER BY sort, id"),
    ("Konten", "SELECT name AS Name, balance AS Stand FROM accounts ORDER BY sort, id"),
    ("Zahlungen", "SELECT month AS Monat, name AS Name, amount AS Betrag, due AS Fällig, account AS Konto, "
                  "paid AS Bezahlt FROM payments ORDER BY month, sort, id"),
    ("Trade Republic", "SELECT at AS Zeitpunkt, date AS Datum, type AS Art, isin AS ISIN, name AS Name, "
                       "shares AS Anteile, price AS Kurs, amount AS Betrag, fee AS Gebühr, tax AS Steuer, "
                       "tx AS \"Transaktions-ID\" FROM tr_transactions ORDER BY at, tx"),
    ("Tageswerte", "SELECT date AS Datum, total AS Gesamt, investments AS Investments, stocks_etfs AS \"Aktien und ETFs\", "
                   "stocks AS Aktien, etfs AS ETFs, crypto AS Krypto, gold AS Gold FROM daily ORDER BY date"),
]


def export_workbook(db: Database) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, sql in SHEETS:
        ws = wb.create_sheet(title)
        cursor = db.execute(sql)
        ws.append([col[0] for col in cursor.description])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in cursor.fetchall():
            ws.append(list(row))
    # Spending as a month x category table, like the sheet
    categories = db.q("SELECT * FROM categories ORDER BY sort, id")
    ws = wb.create_sheet("Alltag")
    ws.append(["Monat"] + [c["name"] for c in categories])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    months = [r["month"] for r in db.q("SELECT DISTINCT month FROM spending ORDER BY month")]
    values = {(r["month"], r["category_id"]): r["amount"] for r in db.q("SELECT * FROM spending")}
    for month in months:
        ws.append([month] + [values.get((month, c["id"])) for c in categories])
    ws = wb.create_sheet("Einstellungen")
    ws.append(["Schlüssel", "Wert"])
    for key in ("salary_net", "salary_gross"):
        ws.append([key, db.get_setting(key, 0)])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
