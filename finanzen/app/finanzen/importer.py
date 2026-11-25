"""Import of the Google Sheet "Finanzen" exported as .xlsx (File > Download > .xlsx)."""

from __future__ import annotations

import datetime as dt
import io
import re
from collections import Counter

from openpyxl import load_workbook

from .calc import month_label
from .db import Database, now_iso
from .util import due_from_note, isin_valid, month_key, parse_number, to_date

EXCHANGES = {
    "ETR": ".DE", "FRA": ".F", "AMS": ".AS", "VIE": ".VI", "SWX": ".SW", "EPA": ".PA", "LON": ".L",
    "BIT": ".MI", "BME": ".MC", "STO": ".ST", "CPH": ".CO", "HEL": ".HE", "TSE": ".TO", "TSX": ".TO",
    "CVE": ".V", "NASDAQ": "", "NYSE": "", "NYSEARCA": "", "NYSEAMERICAN": "", "BATS": "", "OTCMKTS": "",
}
OUNCE_GRAMS = 31.1035
# Xetra-Gold: one unit is backed by one gram of gold, traded in EUR on Tradegate
GOLD_PER_GRAM_ISIN = "DE000A0S9GB0"
ISIN_IN_URL = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2}[A-Za-z0-9]{9}\d)(?![A-Za-z0-9])")


class ImportError_(Exception):
    pass


def googlefinance_to_yahoo(ticker: str) -> str:
    """Map a GOOGLEFINANCE ticker (e.g. "ETR:VOW") to a Yahoo symbol ("VOW.DE")."""
    parts = [p.strip() for p in ticker.split(":")]
    if len(parts) == 2:
        first, second = parts
        if first.upper() in EXCHANGES:
            return second + EXCHANGES[first.upper()]
        if second.upper() in EXCHANGES:
            return first + EXCHANGES[second.upper()]
        return second
    return ticker.strip()


def parse_price_formula(formula) -> dict:
    """Read the price sources out of a GOOGLEFINANCE / IMPORTXML formula."""
    result = {"source": "manual", "isin": "", "symbol": "", "url": "", "xpath": "", "factor": 1.0,
              "fallback_url": "", "fallback_xpath": ""}
    if not isinstance(formula, str) or not formula.startswith("="):
        return result
    text = formula.replace('""', '"')
    tickers = [t for t in re.findall(r'GOOGLEFINANCE\(\s*"([^"]+)"', text, re.IGNORECASE)
               if not t.upper().startswith("CURRENCY:")]
    pages = []
    for url, xpath in re.findall(r'IMPORTXML\(\s*"([^"]+)"\s*[,;]\s*"(.*?)"\s*\)', text, re.IGNORECASE):
        if (url, xpath) not in pages:
            pages.append((url, xpath))
    if re.search(r"/\s*31[,.]1035", text):
        result["factor"] = 1 / OUNCE_GRAMS
    if tickers:
        result.update(source="yahoo", symbol=googlefinance_to_yahoo(tickers[0]))
        if pages:
            result.update(fallback_url=pages[0][0], fallback_xpath=pages[0][1])
    elif pages:
        result.update(source="web", url=pages[0][0], xpath=pages[0][1])
        if len(pages) > 1:
            result.update(fallback_url=pages[1][0], fallback_xpath=pages[1][1])
    # An ISIN in one of the page addresses (e.g. ".../ishares-...-etf-ie00b5l8k969"): use Tradegate,
    # which answers reliably, and keep the page as fallback
    for url, _xpath in pages:
        found = [m.upper() for m in ISIN_IN_URL.findall(url) if isin_valid(m)]
        if found:
            result["isin"] = found[0]
            if result["source"] == "web":
                result.update(fallback_url=result["url"], fallback_xpath=result["xpath"], url="", xpath="")
            result["source"] = "tradegate"
            break
    return result


def upgrade_sources(db: Database) -> list[str]:
    """One-time switch of data imported by 1.0.0 to Tradegate: ISIN from the page address, Xetra-Gold for gold.

    Recorded as one change, so it can be undone in the change history.
    """
    if db.get_setting("sources_upgraded"):
        return []
    changed = []
    positions = db.q("SELECT * FROM positions WHERE isin = '' AND source != 'manual'")
    with db.change("Kursquellen auf Tradegate umgestellt (Update 1.1.0)"):
        for pos in positions:
            isin = ""
            for url in (pos["url"], pos["fallback_url"]):
                found = [m.upper() for m in ISIN_IN_URL.findall(url or "") if isin_valid(m)]
                if found:
                    isin = found[0]
                    break
            if not isin and pos["kind"] == "gold":
                isin = GOLD_PER_GRAM_ISIN
            if not isin:
                continue
            data = {"isin": isin, "source": "tradegate", "status": "umgestellt auf Tradegate", "error_since": None}
            if pos["source"] == "web" and pos["url"] and not pos["fallback_url"]:
                data.update(fallback_url=pos["url"], fallback_xpath=pos["xpath"])
            db.update("positions", {"id": pos["id"]}, data)
            changed.append(pos["name"])
    db.set_setting("sources_upgraded", True)
    if changed:
        db.log("Kursquellen auf Tradegate umgestellt: " + ", ".join(changed))
    return changed


def _interval(value) -> str:
    text = str(value or "").lower()
    if "viertel" in text or "quartal" in text:
        return "vierteljaehrlich"
    if "halb" in text:
        return "halbjaehrlich"
    if "jähr" in text or "jaehr" in text or "jahr" in text:
        return "jaehrlich"
    return "monatlich"


def _header_map(ws, row: int = 1) -> dict[str, int]:
    headers = {}
    for cell in ws[row]:
        if isinstance(cell.value, str) and cell.value.strip():
            headers.setdefault(cell.value.strip(), cell.column)
    return headers


def _find(headers: dict[str, int], *names: str) -> int | None:
    for name in names:
        if name in headers:
            return headers[name]
    lowered = {k.lower(): v for k, v in headers.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _cell(ws, row: int, col: int | None):
    if not col:
        return None
    return ws.cell(row=row, column=col).value


def _rows_from_formula(formula) -> set[int]:
    rows: set[int] = set()
    if not isinstance(formula, str):
        return rows
    for start, end in re.findall(r"U(\d+)(?::U(\d+))?", formula):
        first = int(start)
        last = int(end) if end else first
        rows.update(range(first, last + 1))
    return rows


def import_workbook(db: Database, data: bytes, today: dt.date | None = None,
                    formula_data: bytes | None = None) -> dict:
    """Import the sheet. `formula_data` is only for tests (openpyxl cannot write formulas with cached values)."""
    today = today or dt.date.today()
    try:
        values_wb = load_workbook(io.BytesIO(data), data_only=True)
        formula_wb = load_workbook(io.BytesIO(formula_data or data), data_only=False)
    except Exception as err:
        raise ImportError_(f"Die Datei ist keine lesbare .xlsx-Datei ({err})") from err
    names = set(values_wb.sheetnames)
    if not names & {"Investments", "Abonnements", "Sonstiges"}:
        raise ImportError_("In der Datei fehlen die Blätter „Investments“, „Abonnements“ und „Sonstiges“.")

    report = Counter()
    notes: list[str] = []
    with db.transaction():
        db.clear_data()

        # ------------------------------------------------------ fixed costs
        fixed_by_name: dict[str, dict] = {}
        sort = 0
        for sheet, group in (("Abonnements", "abo"), ("Sonstiges", "sonstiges")):
            if sheet not in names:
                continue
            ws = values_wb[sheet]
            headers = _header_map(ws)
            col_interval = _find(headers, "Interval", "Intervall")
            col_account = _find(headers, "Konto")
            col_note = _find(headers, "Anmerkung")
            for row in range(2, ws.max_row + 1):
                name = _cell(ws, row, 1)
                if not isinstance(name, str) or not name.strip():
                    continue
                amount = _cell(ws, row, 2)
                amount = parse_number(amount) if amount not in (None, "") else None
                due, note = due_from_note(_cell(ws, row, col_note))
                sort += 1
                item = {
                    "name": name.strip(), "amount": amount, "interval": _interval(_cell(ws, row, col_interval)),
                    "account": str(_cell(ws, row, col_account) or "").strip(), "due": due, "grp": group,
                    "note": note, "installments": 1, "sort": sort,
                }
                item["id"] = db.insert("fixed_costs", item)
                fixed_by_name.setdefault(item["name"], item)
                report["Fixkosten"] += 1

        # ------------------------------------------- spending (DaylisAVG)
        if "DaylisAVG" in names:
            ws = values_wb["DaylisAVG"]
            headers = _header_map(ws)
            col_month = None
            for title, col in headers.items():
                if title == "Monat":
                    col_month = col
            accounts_by_name = {}
            col_konto = _find(headers, "Konto")
            for row in range(2, ws.max_row + 1):
                label = _cell(ws, row, 1)
                if isinstance(label, str) and label.strip():
                    accounts_by_name[label.strip()] = str(_cell(ws, row, col_konto) or "").strip()
            if col_month:
                categories = []
                col = col_month + 1
                while isinstance(_cell(ws, 1, col), str) and _cell(ws, 1, col).strip():
                    title = _cell(ws, 1, col).strip()
                    cat_id = db.insert("categories", {"name": title, "account": accounts_by_name.get(title, ""),
                                                      "sort": len(categories)})
                    categories.append((col, cat_id))
                    col += 1
                report["Kategorien"] = len(categories)
                months = set()
                for row in range(2, ws.max_row + 1):
                    day = to_date(_cell(ws, row, col_month))
                    if not day:
                        continue
                    key = month_key(day)
                    for col, cat_id in categories:
                        amount = parse_number(_cell(ws, row, col))
                        if amount is None:
                            continue
                        db.execute("INSERT OR REPLACE INTO spending (month, category_id, amount) VALUES (?, ?, ?)",
                                   (key, cat_id, amount))
                        months.add(key)
                report["Monate Alltag"] = len(months)

        # ------------------------------------------------------ investments
        if "Investments" in names:
            ws = values_wb["Investments"]
            wf = formula_wb["Investments"]
            headers = _header_map(ws)
            col_month = _find(headers, "Monat")
            col_gold = _find(headers, "Gold")
            col_depot = _find(headers, "ETF's, Aktien, Crypto")
            if col_month and (col_gold or col_depot):
                for row in range(2, ws.max_row + 1):
                    day = to_date(_cell(ws, row, col_month))
                    if not day:
                        continue
                    gold = parse_number(_cell(ws, row, col_gold)) or 0.0
                    depot = parse_number(_cell(ws, row, col_depot)) or 0.0
                    if day > today and not gold and not depot:
                        continue
                    db.execute(
                        "INSERT INTO deposits (month, gold, depot) VALUES (?, ?, ?) ON CONFLICT(month) DO UPDATE "
                        "SET gold = gold + excluded.gold, depot = depot + excluded.depot",
                        (month_key(day), gold, depot),
                    )
                    report["Monatswerte"] += 1

            col_date = _find(headers, "Datum")
            col_firm = _find(headers, "Firma")
            col_div = _find(headers, "Dividende")
            if col_date and col_firm and col_div:
                for row in range(2, ws.max_row + 1):
                    day = to_date(_cell(ws, row, col_date))
                    firm = _cell(ws, row, col_firm)
                    amount = parse_number(_cell(ws, row, col_div))
                    if day and isinstance(firm, str) and firm.strip() and amount is not None:
                        db.insert("dividends", {"date": day.isoformat(), "position": firm.strip(), "amount": amount})
                        report["Dividenden"] += 1

            # Which rows are ETFs or crypto: read from the summary formulas in column I
            etf_rows: set[int] = set()
            crypto_rows: set[int] = set()
            for row in range(1, min(ws.max_row, 150) + 1):
                label = _cell(ws, row, 8)
                if not isinstance(label, str):
                    continue
                low = label.lower()
                # Only the single-type rows, not "Wert ETF's, Aktien, Crypto Aktuell" (sum of everything)
                mixed = sum(word in low for word in ("etf", "aktien", "crypto", "krypto")) > 1
                if "aktuell" in low and "etf" in low and not mixed:
                    etf_rows |= _rows_from_formula(_cell(wf, row, 9))
                if "aktuell" in low and ("crypto" in low or "krypto" in low) and not mixed:
                    crypto_rows |= _rows_from_formula(_cell(wf, row, 9))

            col_name = _find(headers, "Aktie, ETF, Coin")
            col_price = _find(headers, "Kurs")
            col_shares = _find(headers, "Anteile")
            col_cost = _find(headers, "Buy In")
            position_rows = []
            if col_name:
                for row in range(2, ws.max_row + 1):
                    name = _cell(ws, row, col_name)
                    if not isinstance(name, str) or not name.strip():
                        continue
                    shares = parse_number(_cell(ws, row, col_shares)) or 0.0
                    cost = parse_number(_cell(ws, row, col_cost)) or 0.0
                    price = parse_number(_cell(ws, row, col_price))
                    kind = "etf" if row in etf_rows else "crypto" if row in crypto_rows else "aktie"
                    source = parse_price_formula(_cell(wf, row, col_price))
                    position_rows.append(name.strip())
                    db.insert("positions", dict(
                        source, name=name.strip(), kind=kind, shares=shares, cost=cost, price=price,
                        price_day=today.isoformat() if price is not None else None,
                        status="aus dem Sheet" if source["source"] != "manual" else "keine Kursquelle",
                        sold_at=None if shares > 0 else "", sort=row,
                    ))
                    report["Positionen"] += 1

            col_gold_price = _find(headers, "Goldpreis Pro Gramm aktuell")
            col_grams = _find(headers, "Gold in Gramm")
            if col_grams:
                grams = parse_number(_cell(ws, 2, col_grams)) or 0.0
                invested_gold = db.scalar("SELECT COALESCE(SUM(gold), 0) FROM deposits") or 0.0
                gold_price = parse_number(_cell(ws, 2, col_gold_price)) if col_gold_price else None
                source = parse_price_formula(_cell(wf, 2, col_gold_price)) if col_gold_price else {}
                if not source.get("isin"):
                    # Price per gram from Xetra-Gold on Tradegate; the sheet's page stays as fallback
                    source = dict(source)
                    if source.get("source") == "web":
                        source.update(fallback_url=source["url"], fallback_xpath=source["xpath"], url="", xpath="")
                    source.update(source="tradegate", isin=GOLD_PER_GRAM_ISIN)
                if grams:
                    db.insert("positions", dict(
                        source, name="Gold", kind="gold", shares=grams,
                        cost=(invested_gold / grams) if grams else 0.0, price=gold_price,
                        price_day=today.isoformat() if gold_price is not None else None,
                        status="aus dem Sheet", sort=0,
                    ))
                    report["Positionen"] += 1

            # Accounts and pots: label in column H, plain number in column I (no formula)
            for row in range(50, ws.max_row + 1):
                label = _cell(ws, row, 8)
                raw = _cell(wf, row, 9)
                if isinstance(label, str) and label.strip() and isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    db.insert("accounts", {"name": label.strip().replace('"', "„", 1).replace('"', "“", 1),
                                           "balance": float(raw), "sort": row})
                    report["Konten"] += 1

        # --------------------------------------------- daily values (Berechnungen)
        if "Berechnungen" in names:
            ws = values_wb["Berechnungen"]
            headers = _header_map(ws)
            col_date = _find(headers, "Datum")
            if col_date:
                mapping = {}
                for title, col in headers.items():
                    low = title.lower().replace("’", "'")
                    if col <= col_date:
                        continue
                    if low in ("gesammt", "gesamt"):
                        mapping["total"] = col
                    elif low == "investments":
                        mapping["investments"] = col
                    elif low in ("aktien etf's", "aktien etfs", "aktien und etfs"):
                        mapping["stocks_etfs"] = col
                    elif low == "aktien":
                        mapping["stocks"] = col
                    elif low in ("etf's", "etfs"):
                        mapping["etfs"] = col
                    elif low in ("crypto", "krypto"):
                        mapping["crypto"] = col
                    elif low == "gold":
                        mapping["gold"] = col
                for row in range(2, ws.max_row + 1):
                    day = to_date(_cell(ws, row, col_date))
                    if not day:
                        continue
                    values = {key: parse_number(_cell(ws, row, col)) for key, col in mapping.items()}
                    if all(v is None for v in values.values()):
                        continue
                    values["date"] = day.isoformat()
                    cols = ", ".join(values)
                    marks = ", ".join(f":{c}" for c in values)
                    db.execute(f"INSERT OR REPLACE INTO daily ({cols}) VALUES ({marks})", values)
                    report["Tageswerte"] += 1

        # ------------------------------------------------------------ salary
        if "Summen" in names:
            ws = values_wb["Summen"]
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value == "Netto Monatsgehalt":
                        db.set_setting("salary_net", parse_number(ws.cell(row=cell.row + 1, column=cell.column).value) or 0)
                    elif cell.value == "Brutto Monatsgehalt":
                        db.set_setting("salary_gross", parse_number(ws.cell(row=cell.row + 1, column=cell.column).value) or 0)

        # ------------------------------------------- current month payments
        if "Aktueller Monat" in names:
            ws = values_wb["Aktueller Monat"]
            key = month_key(today)
            rows = []
            for row in range(2, ws.max_row + 1):
                name = _cell(ws, row, 1)
                amount = parse_number(_cell(ws, row, 2))
                if not isinstance(name, str) or not name.strip() or amount is None:
                    continue
                due, _note = due_from_note(_cell(ws, row, 4))
                rows.append((name.strip(), amount, bool(_cell(ws, row, 3)), due))
            counts = Counter(name for name, *_ in rows)
            seen = Counter()
            for index, (name, amount, paid, due) in enumerate(rows):
                fixed = fixed_by_name.get(name)
                label = name
                if counts[name] > 1:
                    seen[name] += 1
                    label = f"{name} (Rate {seen[name]} von {counts[name]})"
                    if fixed and fixed["amount"] and abs(fixed["amount"] - amount * counts[name]) < 0.01:
                        db.execute("UPDATE fixed_costs SET installments = ? WHERE id = ?", (counts[name], fixed["id"]))
                db.insert("payments", {
                    "month": key, "name": label, "amount": amount, "due": due or (fixed or {}).get("due", ""),
                    "account": (fixed or {}).get("account", ""), "paid": 1 if paid else 0,
                    "fixed_cost_id": (fixed or {}).get("id"), "sort": index,
                })
                report["Zahlungen " + month_label(key)] += 1

        summary = ", ".join(f"{count} {name}" for name, count in report.items())
        db.set_setting("last_import", {"at": now_iso(), "summary": summary, "notes": notes})
        db.set_setting("sources_upgraded", True)
        db.log(f"Import aus Google Sheet: {summary}")
    return {"summary": summary, "counts": dict(report), "notes": notes}
