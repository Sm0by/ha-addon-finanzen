"""Import of the Trade Republic transaction export (CSV).

Only security transactions are read: buys, sales, dividends, splits and the cash bonuses
(Saveback, stock perk) that paid for some purchases. Card payments, transfers and payment
details such as IBANs are skipped while reading and never stored.

All imported rows are kept in ``tr_transactions``. Every import merges the new rows with the
stored ones and derives holdings, average cost, realized gains, dividends and the monthly
amount paid into the depot from the complete history, so a later export of a shorter period
is enough to bring the app up to date.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re

from . import calc
from .db import Database, now_iso
from .util import month_key, parse_number

SECURITY_TYPES = {"BUY", "SELL", "DIVIDEND", "SPLIT", "BENEFITS_SAVEBACK", "STOCKPERK"}
BONUS_TYPES = {"BENEFITS_SAVEBACK", "STOCKPERK"}
REQUIRED = ("date", "type", "symbol", "shares", "amount")
KIND_BY_CLASS = {"STOCK": "aktie", "FUND": "etf", "CRYPTO": "crypto"}
ZERO = 1e-6
DIVIDEND_DAYS = 45
NAME_NOISE = {
    "ag", "se", "sa", "nv", "plc", "inc", "corp", "co", "ltd", "the", "adr", "ads", "spons", "sponsored",
    "vz", "st", "namens", "aktien", "aktie", "holding", "group", "kgaa", "reg", "registered", "shares", "class",
}


def _num(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        value = parse_number(text)
        if value is None:
            raise ValueError(f"„{text}“ ist keine Zahl") from None
        return value


def _iso_day(text: str) -> str:
    day = (text or "").strip()[:10]
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        raise ValueError(f"Unbekanntes Datum „{text}“ im Export") from None
    return day


def parse_csv(data: bytes) -> tuple[list[dict], dict]:
    """Read the export. Returns the security rows and some facts about the file."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    fields = {(name or "").strip().lower() for name in (reader.fieldnames or [])}
    missing = [name for name in REQUIRED if name not in fields]
    if missing:
        raise ValueError("Das ist kein Transaktionsexport von Trade Republic, es fehlen die Spalten "
                         + ", ".join(missing))
    rows: list[dict] = []
    total = 0
    last_day = ""
    for raw in reader:
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items() if key}
        total += 1
        if row.get("date"):
            last_day = max(last_day, row["date"][:10])
        kind = row.get("type", "").upper()
        isin = row.get("symbol", "").replace(" ", "").upper()
        if kind not in SECURITY_TYPES or not isin:
            continue
        day = _iso_day(row["date"])
        entry = {
            "at": row.get("datetime") or day,
            "date": day,
            "type": kind,
            "isin": isin,
            "name": row.get("name") or isin,
            "asset_class": row.get("asset_class", "").upper(),
            "shares": _num(row.get("shares", "")),
            "price": _num(row["price"]) if row.get("price") else None,
            "amount": _num(row.get("amount", "")),
            "fee": _num(row.get("fee", "")),
            "tax": _num(row.get("tax", "")),
        }
        tx = row.get("transaction_id", "")
        if not tx:
            raw_key = "|".join(str(entry[k]) for k in ("at", "type", "isin", "shares", "amount"))
            tx = "x-" + hashlib.sha1(raw_key.encode()).hexdigest()[:20]
        entry["tx"] = tx
        rows.append(entry)
    if not rows:
        raise ValueError("Im Export stehen keine Wertpapier-Buchungen")
    info = {
        "rows": total,
        "security_rows": len(rows),
        "first": min(r["date"] for r in rows),
        "last": max(r["date"] for r in rows),
        "export_end": last_day or max(r["date"] for r in rows),
    }
    return rows, info


# ------------------------------------------------------------------ history
def compute(rows: list[dict]) -> dict:
    """Derive holdings, trades, dividends and monthly own payments from all rows."""
    securities: dict[str, dict] = {}
    months: dict[str, float] = {}
    bonus = 0.0
    problems: list[str] = []
    for row in sorted(rows, key=lambda r: (r["at"], r["tx"])):
        sec = securities.setdefault(row["isin"], {
            "isin": row["isin"], "name": row["name"], "asset_class": row["asset_class"], "shares": 0.0,
            "cost_total": 0.0, "avg": 0.0, "realized": 0.0, "sells": 0, "sold_at": None, "trades": [],
            "dividends": [], "bonus": 0.0,
        })
        sec["name"] = row["name"] or sec["name"]
        sec["asset_class"] = row["asset_class"] or sec["asset_class"]
        month = month_key(dt.date.fromisoformat(row["date"]))
        kind = row["type"]
        if kind == "BUY":
            shares = abs(row["shares"])
            paid = abs(row["amount"]) + abs(row["fee"])
            sec["shares"] += shares
            sec["cost_total"] += paid
            sec["avg"] = sec["cost_total"] / sec["shares"] if sec["shares"] > ZERO else 0.0
            sec["sold_at"] = None
            months[month] = months.get(month, 0.0) + paid
            sec["trades"].append({"tx": row["tx"], "date": row["date"], "kind": "kauf", "shares": shares,
                                  "amount": round(paid, 2), "fee": abs(row["fee"]), "gain": None})
        elif kind == "SELL":
            sold = abs(row["shares"])
            credit = abs(row["amount"]) - abs(row["fee"]) + row["tax"]
            if sold > sec["shares"] + ZERO:
                problems.append(f"{sec['name']}: am {row['date']} mehr verkauft als vorher gekauft. "
                                "Fehlen ältere Buchungen im Export?")
                basis = sec["cost_total"]
            else:
                basis = sec["avg"] * sold
            gain = credit - basis
            sec["realized"] += gain
            sec["sells"] += 1
            sec["shares"] = max(0.0, sec["shares"] - sold)
            sec["cost_total"] = max(0.0, sec["cost_total"] - basis)
            if sec["shares"] < ZERO:
                sec["shares"] = 0.0
                sec["cost_total"] = 0.0
                sec["sold_at"] = row["date"]
            months[month] = months.get(month, 0.0) - credit
            sec["trades"].append({"tx": row["tx"], "date": row["date"], "kind": "verkauf", "shares": sold,
                                  "amount": round(credit, 2), "fee": abs(row["fee"]), "gain": round(gain, 2)})
        elif kind == "SPLIT":
            sec["shares"] += row["shares"]
            sec["avg"] = sec["cost_total"] / sec["shares"] if sec["shares"] > ZERO else sec["avg"]
        elif kind == "DIVIDEND":
            sec["dividends"].append({"tx": row["tx"], "date": row["date"], "amount": round(row["amount"] + row["tax"], 2),
                                     "tax": row["tax"]})
        elif kind in BONUS_TYPES:
            value = abs(row["amount"])
            bonus += value
            sec["bonus"] += value
            months[month] = months.get(month, 0.0) - value
    return {"securities": securities, "months": months, "bonus": bonus, "problems": problems}


def _words(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9äöüß]+", name.lower())
    return [w for w in words if w not in NAME_NOISE]


def _same_name(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    if wa[0] == wb[0]:
        return True
    ja, jb = "".join(wa), "".join(wb)
    return len(min(ja, jb, key=len)) >= 4 and (ja.startswith(jb) or jb.startswith(ja))


# --------------------------------------------------------------------- plan
def build_plan(db: Database, rows: list[dict], info: dict) -> dict:
    """Compare the export with the app. Reads only, the result says what ``execute`` would write."""
    stored = {r["tx"]: r for r in db.q("SELECT * FROM tr_transactions")}
    new_rows = []
    for row in rows:
        if row["tx"] not in stored:
            stored[row["tx"]] = row
            new_rows.append(row)
    history = compute(list(stored.values()))
    securities = history["securities"]
    warnings = list(history["problems"])

    positions = db.q("SELECT * FROM positions ORDER BY sort, id")
    candidates = [p for p in positions if p["kind"] != "gold"]
    by_isin: dict[str, dict] = {}
    for pos in candidates:
        if pos["isin"] and pos["isin"] in securities and pos["isin"] not in by_isin:
            by_isin[pos["isin"]] = pos
    taken = {p["id"] for p in by_isin.values()}
    matches: dict[str, tuple[dict | None, str]] = {}
    for isin, sec in securities.items():
        if isin in by_isin:
            matches[isin] = (by_isin[isin], "isin")
            continue
        found = [p for p in candidates if p["id"] not in taken and (not p["isin"] or p["isin"] not in securities)
                 and _same_name(p["name"], sec["name"])]
        if len(found) == 1:
            matches[isin] = (found[0], "name")
            taken.add(found[0]["id"])
        else:
            if len(found) > 1:
                warnings.append(f"{sec['name']}: mehrere Positionen kommen in Frage ("
                                + ", ".join(p["name"] for p in found)
                                + "). Trag die ISIN an der richtigen Position ein und lade die Vorschau neu.")
            matches[isin] = (None, "neu")

    plan = {
        "info": dict(info, new_rows=len(new_rows), known_rows=len(rows) - len(new_rows)),
        "tr_inserts": new_rows,
        "pos_updates": [], "pos_inserts": [], "positions": [],
        "div_updates": [], "div_inserts": [], "dividends_matched": 0, "dividends_app_only": [],
        "trade_inserts": [], "trade_updates": [], "trade_deletes": [], "trades_replaced": [],
        "dep_upserts": [], "deposit_years": [], "sales": [],
        "not_in_export": [], "warnings": warnings, "bonus": history["bonus"], "position_ids": {},
    }

    # Positions
    next_sort = max((p["sort"] for p in positions), default=0) + 1
    names: dict[str, str] = {}
    for isin, sec in sorted(securities.items(), key=lambda kv: kv[1]["name"].lower()):
        pos, how = matches[isin]
        cost = sec["cost_total"] / sec["shares"] if sec["shares"] > ZERO else sec["avg"]
        data = {
            "shares": round(sec["shares"], 10),
            "cost": round(cost, 6),
            "realized": round(sec["realized"], 2) if sec["sells"] else None,
            "sold_at": sec["sold_at"] if sec["shares"] <= ZERO else None,
        }
        entry = {"isin": isin, "name": pos["name"] if pos else sec["name"], "tr_name": sec["name"], "match": how,
                 "new": data, "old": None, "changed": True}
        if pos:
            names[isin] = pos["name"]
            plan["position_ids"][isin] = pos["id"]
            old = {k: pos[k] for k in ("shares", "cost", "realized", "sold_at")}
            entry["old"] = old
            changes = {}
            if abs((old["shares"] or 0) - data["shares"]) > ZERO:
                changes["shares"] = data["shares"]
            if abs((old["cost"] or 0) - data["cost"]) > 0.00005:
                changes["cost"] = data["cost"]
            if (old["realized"] is None) != (data["realized"] is None) or (
                    data["realized"] is not None and abs(old["realized"] - data["realized"]) > 0.005):
                changes["realized"] = data["realized"]
            if (old["sold_at"] or None) != data["sold_at"]:
                changes["sold_at"] = data["sold_at"]
            if pos["isin"] != isin:
                changes["isin"] = isin
                entry["isin_old"] = pos["isin"]
            # Shown as a change only if it is visible at two decimals, small cost corrections are still written
            visible = changes.keys() - {"isin", "cost"}
            entry["changed"] = bool(visible) or ("cost" in changes and abs((old["cost"] or 0) - data["cost"]) >= 0.005)
            if changes:
                plan["pos_updates"].append({"id": pos["id"], "data": changes})
        else:
            names[isin] = sec["name"]
            plan["pos_inserts"].append(dict(data, name=sec["name"], kind=KIND_BY_CLASS.get(sec["asset_class"], "aktie"),
                                            source="tradegate", isin=isin, sort=next_sort))
            next_sort += 1
        plan["positions"].append(entry)
        for trade in sec["trades"]:
            if trade["kind"] == "verkauf":
                plan["sales"].append({"date": trade["date"], "name": names[isin], "shares": trade["shares"],
                                      "credit": trade["amount"], "gain": trade["gain"]})
    plan["sales"].sort(key=lambda s: s["date"])
    matched_ids = {pos["id"] for pos, _how in matches.values() if pos}
    for pos in candidates:
        if pos["id"] not in matched_ids and pos["shares"] > ZERO:
            plan["not_in_export"].append(pos["name"])
    if plan["not_in_export"]:
        warnings.append("Nicht im Export: " + ", ".join(plan["not_in_export"])
                        + ". Diese Positionen bleiben, ihre Einzahlungen werden aber durch die aus dem Export ersetzt.")

    # Trades: the export replaces bookings made by hand for the same positions
    existing = db.q("SELECT * FROM trades")
    by_tx = {t["tx"]: t for t in existing if t["tx"]}
    for trade in existing:
        if not trade["tx"] and trade["position_id"] in matched_ids:
            plan["trade_deletes"].append(trade["id"])
            after = trade["date"] > info["export_end"]
            plan["trades_replaced"].append({"date": trade["date"], "kind": trade["kind"], "position": trade["position"],
                                            "amount": trade["amount"], "after_export": after})
            if after:
                warnings.append(f"Die Buchung {trade['position']} vom {trade['date']} ist neuer als der Export "
                                "und ginge verloren. Lade besser einen aktuellen Export.")
    for isin, sec in securities.items():
        for trade in sec["trades"]:
            row = {"date": trade["date"], "kind": trade["kind"], "position": names[isin], "shares": trade["shares"],
                   "amount": trade["amount"], "gain": trade["gain"], "fee": trade["fee"]}
            old = by_tx.get(trade["tx"])
            if old is None:
                plan["trade_inserts"].append(dict(row, tx=trade["tx"], isin=isin))
            else:
                diff = {k: v for k, v in row.items() if old[k] != v and not (
                    isinstance(v, float) and isinstance(old[k], float) and abs(old[k] - v) < 0.005)}
                if diff:
                    plan["trade_updates"].append({"id": old["id"], "data": diff})

    # Dividends: link to existing ones (same amount, close date), add the missing ones
    app_divs = db.q("SELECT * FROM dividends ORDER BY date, id")
    known_tx = {d["tx"] for d in app_divs if d["tx"]}
    free = [d for d in app_divs if not d["tx"]]
    for isin, sec in securities.items():
        for div in sec["dividends"]:
            if div["tx"] in known_tx:
                plan["dividends_matched"] += 1
                continue
            day = dt.date.fromisoformat(div["date"])
            best = None
            for cand in free:
                distance = abs((dt.date.fromisoformat(cand["date"]) - day).days)
                if abs(cand["amount"] - div["amount"]) <= 0.015 and distance <= DIVIDEND_DAYS:
                    score = (cand["position"] != names[isin], distance)
                    if best is None or score < best[0]:
                        best = (score, cand)
            data = {"date": div["date"], "position": names[isin], "amount": div["amount"], "tax": div["tax"],
                    "tx": div["tx"]}
            if best:
                free.remove(best[1])
                plan["dividends_matched"] += 1
                plan["div_updates"].append({"id": best[1]["id"], "data": data})
            else:
                plan["div_inserts"].append(data)
    plan["div_inserts"].sort(key=lambda d: d["date"])
    plan["dividends_app_only"] = [{"date": d["date"], "position": d["position"], "amount": d["amount"]} for d in free]
    new_keys = {(d["position"], d["date"][:7]) for d in plan["div_inserts"]}
    for div in free:
        if (div["position"], div["date"][:7]) in new_keys:
            warnings.append(f"Dividende {div['position']} vom {div['date']} steht schon in der App, im Export mit "
                            "anderem Betrag. Prüf, ob sie doppelt ist.")

    # Deposits: the depot column is replaced from the first to the last month of the export
    all_days = [r["date"] for r in stored.values()]
    first = month_key(dt.date.fromisoformat(min(all_days)))
    last = month_key(dt.date.fromisoformat(max(all_days + [info["export_end"]])))
    deposits = {d["month"]: d for d in db.q("SELECT * FROM deposits")}
    years: dict[str, dict] = {}
    for month in sorted(set(deposits) | set(history["months"])):
        if not first <= month <= last:
            continue
        old = deposits.get(month, {}).get("depot", 0.0) or 0.0
        new = round(history["months"].get(month, 0.0), 2)
        year = years.setdefault(month[:4], {"year": month[:4], "old": 0.0, "new": 0.0})
        year["old"] += old
        year["new"] += new
        if abs(old - new) > 0.005:
            plan["dep_upserts"].append({"month": month, "gold": deposits.get(month, {}).get("gold", 0.0) or 0.0,
                                        "depot": new})
    plan["deposit_years"] = [y for _k, y in sorted(years.items())]
    plan["deposit_range"] = {"first": first, "last": last}
    plan["changes"] = sum(len(plan[k]) for k in (
        "tr_inserts", "pos_updates", "pos_inserts", "div_updates", "div_inserts", "trade_inserts", "trade_updates",
        "trade_deletes", "dep_upserts"))
    plan["new_open"] = sum(1 for p in plan["pos_inserts"] if p["shares"] > ZERO)
    return plan


def execute(db: Database, plan: dict) -> None:
    for row in plan["tr_inserts"]:
        db.insert("tr_transactions", {k: row[k] for k in (
            "tx", "at", "date", "type", "isin", "name", "asset_class", "shares", "price", "amount", "fee", "tax")})
    ids = dict(plan["position_ids"])
    for upd in plan["pos_updates"]:
        db.update("positions", {"id": upd["id"]}, upd["data"])
    for data in plan["pos_inserts"]:
        ids[data["isin"]] = db.insert("positions", data)
    for tid in plan["trade_deletes"]:
        db.delete("trades", {"id": tid})
    for upd in plan["trade_updates"]:
        db.update("trades", {"id": upd["id"]}, upd["data"])
    for row in plan["trade_inserts"]:
        data = {k: v for k, v in row.items() if k != "isin"}
        data["position_id"] = ids.get(row["isin"])
        db.insert("trades", data)
    for upd in plan["div_updates"]:
        db.update("dividends", {"id": upd["id"]}, upd["data"])
    for data in plan["div_inserts"]:
        db.insert("dividends", data)
    for data in plan["dep_upserts"]:
        db.upsert("deposits", data)


def _totals(db: Database) -> dict:
    pf = calc.portfolio(db)
    realized = db.scalar("SELECT COALESCE(SUM(realized), 0) FROM positions WHERE kind != 'gold'") or 0.0
    return {"depot_value": pf["depot_value"], "invested_depot": pf["invested_depot"], "invested": pf["invested"],
            "result": pf["result"], "realized": realized, "div_total": pf["div_total"]}


def preview(db: Database, data: bytes) -> dict:
    rows, info = parse_csv(data)
    plan = build_plan(db, rows, info)
    before = _totals(db)
    db.execute("BEGIN")
    try:
        execute(db, plan)
        after = _totals(db)
    finally:
        db.execute("ROLLBACK")
    report = {k: v for k, v in plan.items() if k not in (
        "tr_inserts", "pos_updates", "pos_inserts", "div_updates", "trade_inserts", "trade_updates", "trade_deletes",
        "dep_upserts", "position_ids", "div_inserts")}
    report["dividends_new"] = plan["div_inserts"]
    report["totals"] = {"before": before, "after": after}
    report["counts"] = {
        "positions_changed": sum(1 for p in plan["positions"] if p["changed"]),
        "positions_new": len(plan["pos_inserts"]),
        "trades_new": len(plan["trade_inserts"]),
        "dividends_new": len(plan["div_inserts"]),
        "months_changed": len(plan["dep_upserts"]),
    }
    return report


def summary(plan: dict) -> str:
    parts = []
    changed = sum(1 for p in plan["positions"] if p["changed"] and p["old"] is not None)
    if changed:
        parts.append(f"{changed} Positionen angepasst")
    if plan["pos_inserts"]:
        parts.append(f"{len(plan['pos_inserts'])} neu angelegt")
    if plan["trade_inserts"]:
        parts.append(f"{len(plan['trade_inserts'])} Buchungen")
    if plan["div_inserts"]:
        parts.append(f"{len(plan['div_inserts'])} Dividenden ergänzt")
    if plan["dep_upserts"]:
        parts.append(f"Einzahlungen in {len(plan['dep_upserts'])} Monaten")
    return ", ".join(parts) if parts else "keine Änderungen"


def apply(db: Database, data: bytes) -> dict:
    rows, info = parse_csv(data)
    plan = build_plan(db, rows, info)
    text = "Trade-Republic-Import: " + summary(plan)
    last = {"at": now_iso(), "summary": summary(plan), "until": info["export_end"], "rows": info["security_rows"]}
    if plan["changes"]:
        with db.change(text):
            execute(db, plan)
            # Written through the change log, so an undo also restores the previous "last import"
            db.upsert("settings", {"key": "tr_import", "value": json.dumps(last)})
    else:
        db.set_setting("tr_import", last)
    return {"text": text, "changes": plan["changes"], "new_open": plan["new_open"]}
