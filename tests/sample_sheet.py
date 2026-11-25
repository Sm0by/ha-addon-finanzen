"""Builds a workbook with the layout of the original Google Sheet, filled with made-up values."""

from __future__ import annotations

import datetime as dt
import io

from openpyxl import Workbook

TODAY = dt.date(2026, 11, 24)


def _dummy(formula: str, value) -> str:
    """How Google Sheets exports functions Excel does not know."""
    inner = formula.replace('"', '""')
    return f'=IFERROR(__xludf.DUMMYFUNCTION("{inner}"),{value})'


def build() -> tuple[bytes, bytes]:
    """Returns (values workbook, formulas workbook)."""
    values = Workbook()
    formulas = Workbook()
    values.remove(values.active)
    formulas.remove(formulas.active)

    def both(title):
        return values.create_sheet(title), formulas.create_sheet(title)

    # Abonnements / Sonstiges
    abo_v, abo_f = both("Abonnements")
    head = ["Ausgabe", "Kosten", "Interval", "Kosten Monat", "Kosten Jahr", "Konto", "Anmerkung"]
    rows = [
        ["Musikdienst", 9.99, "Monatlich", None, None, "Bank A", "Immer am 05.XX."],
        ["Cloudspeicher", 30.0, "Jährlich", None, None, "Bank A", "Immer am 14.03."],
        ["Server", 12.5, "Monatlich", None, None, "Bank B", "27-30.XX."],
    ]
    for ws in (abo_v, abo_f):
        ws.append(head)
        for row in rows:
            ws.append(row)
    son_v, son_f = both("Sonstiges")
    rows = [
        ["Miete", 800.0, "Monatlich", None, None, "Bank B", "immer am 01.XX."],
        ["Versicherung", 120.0, "Jährlich", None, None, "Bank B", "Immer am 22.11"],
        ["Verein", 60.0, "Monatlich", None, None, "Bank B", None],
        ["Zahnarzt", 50.0, "Jährlich", None, None, "Bank B", "70€ - 20€ Erstattung"],
        ["Hausrat", None, None, None, None, None, None],
    ]
    for ws in (son_v, son_f):
        ws.append(head)
        for row in rows:
            ws.append(row)

    # DaylisAVG with a month table in columns R..T
    day_v, day_f = both("DaylisAVG")
    for ws in (day_v, day_f):
        ws["A1"], ws["F1"], ws["R1"], ws["S1"], ws["T1"] = "Ausgabe", "Konto", "Monat", "Essen", "Tanken"
        ws["A2"], ws["F2"] = "Essen", "Bank A"
        ws["A3"], ws["F3"] = "Tanken", "Bank A"
        for index, (month, food, fuel) in enumerate([(5, 300, 100), (6, 310, 90), (7, 290, 110), (8, 305, 95),
                                                     (9, 295, 105), (10, 300, 100), (11, 150, 40)]):
            ws.cell(row=2 + index, column=18, value=dt.datetime(2026, month, 1))
            ws.cell(row=2 + index, column=19, value=food)
            ws.cell(row=2 + index, column=20, value=fuel)

    # Investments
    inv_v, inv_f = both("Investments")
    headers = {1: "Monat", 2: "Gold", 3: "ETF's, Aktien, Crypto", 4: "ETF's, Aktien, Crypto (- Sparplan)",
               12: "Datum", 13: "Firma", 14: "Dividende", 18: "Aktie, ETF, Coin", 19: "Kurs", 20: "Anteile",
               23: "Buy In", 27: "Goldpreis Pro Gramm aktuell", 28: "Gold in Gramm"}
    for ws in (inv_v, inv_f):
        for col, text in headers.items():
            ws.cell(row=1, column=col, value=text)
        for index, (gold, depot) in enumerate([(1000, 0), (0, 500), (0, -100), (0, 0)]):
            ws.cell(row=2 + index, column=1, value=dt.datetime(2026, 8 + index, 1))
            ws.cell(row=2 + index, column=2, value=gold)
            ws.cell(row=2 + index, column=3, value=depot)
        ws.cell(row=2, column=12, value=dt.datetime(2026, 5, 8))
        ws.cell(row=2, column=13, value="Beispiel AG")
        ws.cell(row=2, column=14, value=12.5)
        ws.cell(row=3, column=12, value=dt.datetime(2025, 5, 8))
        ws.cell(row=3, column=13, value="Welt ETF")
        ws.cell(row=3, column=14, value=3.0)
        ws.cell(row=42, column=8, value="Wert Gold Aktuell")
        ws.cell(row=43, column=8, value="Wert ETF's Aktuell")
        ws.cell(row=44, column=8, value="Wert Aktien Aktuell")
        ws.cell(row=45, column=8, value="Wert Crypto Aktuell")
        ws.cell(row=70, column=8, value='Topf "Rücklagen"')
        ws.cell(row=71, column=8, value="Girokonto")
    positions = [
        ("Beispiel AG", 50.0, 10, 40.0, 'LET(googleAbsent; ISTNV(GOOGLEFINANCE("ETR:BSP")); '
                                         'WENN(googleAbsent; IMPORTXML("https://example.com/bsp"; '
                                         '"/html/body/main/span[1]"); GOOGLEFINANCE("ETR:BSP")))'),
        ("Welt ETF", 100.0, 5, 90.0, 'GOOGLEFINANCE("AMS:WELT")'),
        ("Nischen ETF", 20.0, 3, 25.0, 'IMPORTXML("https://example.com/etf/nischen-etf-ie00b5l8k969"; '
                                        '"//div[@class=\'kurs\']/text()")'),
        ("US Tech", 150.0, 1, 100.0, 'GOOGLEFINANCE("TECH") * GOOGLEFINANCE("CURRENCY:USDEUR")'),
        ("Alte Aktie", 10.0, 0, 8.0, 'GOOGLEFINANCE("FRA:ALT")'),
    ]
    for index, (name, price, shares, cost, formula) in enumerate(positions):
        row = 2 + index
        for ws in (inv_v, inv_f):
            ws.cell(row=row, column=18, value=name)
            ws.cell(row=row, column=20, value=shares)
            ws.cell(row=row, column=23, value=cost)
        inv_v.cell(row=row, column=19, value=price)
        inv_f.cell(row=row, column=19, value=_dummy("=" + formula, price) if "GOOGLEFINANCE" in formula
                   else "=" + formula)
    inv_v["AA2"], inv_v["AB2"] = 100.0, 20.0
    inv_f["AA2"] = _dummy('=LINKS(IMPORTXML("https://example.com/gold"; "//div[@id=\'gold\']/text()"); 8) / 31,1035',
                          100.0)
    inv_f["AB2"] = "=20"
    inv_f["I42"] = "=AA2*AB2"
    inv_f["I43"] = "=U3+U4"
    inv_f["I44"] = "=SUMME(U2:U2)+U5+U6"
    inv_f["I45"] = 0
    inv_v["I70"], inv_v["I71"] = 1000.0, 250.0
    inv_f["I70"], inv_f["I71"] = 1000, 250

    # Berechnungen (daily values)
    ber_v, ber_f = both("Berechnungen")
    for ws in (ber_v, ber_f):
        for col, text in zip(range(11, 19), ["Datum", "Gesammt", "Investments", "Aktien ETF's", "Aktien", "ETF's",
                                             "Crypto", "Gold"]):
            ws.cell(row=1, column=col, value=text)
        ws.cell(row=2, column=11, value="22.11.2026")
        ws.cell(row=3, column=11, value=dt.datetime(2026, 11, 23))
        for row, base in ((2, 4000.0), (3, 4100.0)):
            for offset, col in enumerate(range(12, 19)):
                ws.cell(row=row, column=col, value=base - offset * 100)

    # Summen with salary
    sum_v, sum_f = both("Summen")
    for ws in (sum_v, sum_f):
        ws["J10"], ws["K10"] = "Brutto Monatsgehalt", "Netto Monatsgehalt"
        ws["J11"], ws["K11"] = 3000, 2000

    # Aktueller Monat with a payment split into two installments
    akt_v, akt_f = both("Aktueller Monat")
    for ws in (akt_v, akt_f):
        ws.append(["Ausgabe", "Betrag", "Bezahlt", "Anmerkung"])
        ws.append(["Musikdienst", 9.99, True, "Immer am 05.XX."])
        ws.append(["Miete", 400.0, True, "immer am 01.XX."])
        ws.append(["Miete", 400.0, False, "immer am 01.XX."])
        ws.append(["Server", 12.5, False, "27-30.XX."])

    out = []
    for wb in (values, formulas):
        buffer = io.BytesIO()
        wb.save(buffer)
        out.append(buffer.getvalue())
    return out[0], out[1]
