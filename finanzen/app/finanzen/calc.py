"""Business logic: values, bookings, monthly payment list and the state sent to the UI."""

from __future__ import annotations

import datetime as dt

from .db import Database, now_iso
from .util import (
    add_months,
    de_date,
    due_in_month,
    month_key,
    month_start,
    next_due_date,
    period_months,
)

EPS = 1e-9
KINDS = ("aktie", "etf", "crypto", "gold")
INTERVALS = ("monatlich", "vierteljaehrlich", "halbjaehrlich", "jaehrlich")
GROUPS = ("abo", "sonstiges")
MONTHS_DE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
             "September", "Oktober", "November", "Dezember")
MONTHS_SHORT = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")

DEFAULT_JOBS = {
    "prices": True,
    "daily": True,
    "payments": True,
    "reminders": True,
    "depot_warning": False,
}
DEFAULT_NOTIFY = {
    "due": True,
    "new_list": True,
    "depot_warning": False,
    "source_errors": True,
}


def de_num(value: float, digits: int = 2) -> str:
    text = f"{value:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def de_shares(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def month_label(key: str) -> str:
    start = month_start(key)
    return f"{MONTHS_DE[start.month - 1]} {start.year}"


def settings(db: Database) -> dict:
    jobs = dict(DEFAULT_JOBS)
    jobs.update(db.get_setting("jobs", {}) or {})
    notify = dict(DEFAULT_NOTIFY)
    notify.update(db.get_setting("notify", {}) or {})
    return {
        "salary_net": db.get_setting("salary_net", 0) or 0,
        "salary_gross": db.get_setting("salary_gross", 0) or 0,
        "notify_service": db.get_setting("notify_service", "") or "",
        "depot_warning_pct": db.get_setting("depot_warning_pct", 3) or 3,
        "jobs": jobs,
        "notify": notify,
    }


# --------------------------------------------------------------------- depot
def position_value(pos: dict) -> float:
    price = pos["price"] if pos["price"] is not None else pos["cost"]
    return (price or 0) * (pos["shares"] or 0)


def dividends_by_position(db: Database) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in db.q("SELECT position, SUM(amount) AS s FROM dividends GROUP BY position"):
        result[row["position"]] = row["s"] or 0
    return result


def portfolio(db: Database) -> dict:
    positions = db.q("SELECT * FROM positions ORDER BY sort, id")
    divs = dividends_by_position(db)
    sums = {"aktie": 0.0, "etf": 0.0, "crypto": 0.0, "gold": 0.0}
    day_change = 0.0
    prev_total = 0.0
    open_rows, sold_rows, gold_rows = [], [], []
    for pos in positions:
        value = position_value(pos)
        if pos["shares"] > EPS:
            sums[pos["kind"] if pos["kind"] in sums else "aktie"] += value
            if pos["price"] is not None and pos["prev_close"]:
                prev_value = pos["prev_close"] * pos["shares"]
                day_change += value - prev_value
                prev_total += prev_value
            elif pos["price"] is not None:
                prev_total += value
        gain = ((pos["price"] if pos["price"] is not None else pos["cost"]) - pos["cost"]) * pos["shares"]
        row = dict(pos)
        row.update(value=value, gain=gain, dividends=divs.get(pos["name"], 0.0),
                   gain_div=gain + divs.get(pos["name"], 0.0))
        if pos["kind"] == "gold":
            gold_rows.append(row)
        elif pos["shares"] > EPS:
            open_rows.append(row)
        else:
            sold_rows.append(row)
    invested = db.scalar("SELECT COALESCE(SUM(gold + depot), 0) FROM deposits") or 0.0
    invested_gold = db.scalar("SELECT COALESCE(SUM(gold), 0) FROM deposits") or 0.0
    invested_depot = db.scalar("SELECT COALESCE(SUM(depot), 0) FROM deposits") or 0.0
    investments = sum(sums.values())
    div_total = db.scalar("SELECT COALESCE(SUM(amount), 0) FROM dividends") or 0.0
    result = investments - invested
    return {
        "open": sorted(open_rows, key=lambda r: -r["value"]),
        "sold": sold_rows,
        "gold": gold_rows,
        "stocks": sums["aktie"],
        "etfs": sums["etf"],
        "crypto": sums["crypto"],
        "gold_value": sums["gold"],
        "depot_value": sums["aktie"] + sums["etf"] + sums["crypto"],
        "investments": investments,
        "invested": invested,
        "invested_gold": invested_gold,
        "invested_depot": invested_depot,
        "result": result,
        "result_pct": (result / invested * 100) if invested else 0.0,
        "div_total": div_total,
        "result_div": result + div_total,
        "result_div_pct": ((result + div_total) / invested * 100) if invested else 0.0,
        "day_change": day_change,
        "day_change_pct": (day_change / prev_total * 100) if prev_total else 0.0,
    }


def accounts_total(db: Database) -> float:
    return db.scalar("SELECT COALESCE(SUM(balance), 0) FROM accounts") or 0.0


def snapshot_values(db: Database) -> dict:
    pf = portfolio(db)
    acc = accounts_total(db)
    return {
        "total": pf["investments"] + acc,
        "investments": pf["investments"],
        "stocks_etfs": pf["stocks"] + pf["etfs"] + pf["crypto"],
        "stocks": pf["stocks"],
        "etfs": pf["etfs"],
        "crypto": pf["crypto"],
        "gold": pf["gold_value"],
    }


def write_daily(db: Database, day: dt.date) -> None:
    values = snapshot_values(db)
    values["date"] = day.isoformat()
    db.execute(
        "INSERT INTO daily (date, total, investments, stocks_etfs, stocks, etfs, crypto, gold) "
        "VALUES (:date, :total, :investments, :stocks_etfs, :stocks, :etfs, :crypto, :gold) "
        "ON CONFLICT(date) DO UPDATE SET total = excluded.total, investments = excluded.investments, "
        "stocks_etfs = excluded.stocks_etfs, stocks = excluded.stocks, etfs = excluded.etfs, "
        "crypto = excluded.crypto, gold = excluded.gold",
        values,
    )


def book_trade(db: Database, kind: str, position_id: int, date: str, *, amount: float | None = None,
               price: float | None = None, shares: float | None = None, credit: float | None = None) -> str:
    pos = db.one("SELECT * FROM positions WHERE id = ?", (position_id,))
    if not pos:
        raise ValueError("Position nicht gefunden")
    day = dt.date.fromisoformat(date)
    key = month_key(day)
    column = "gold" if pos["kind"] == "gold" else "depot"
    deposit = db.one("SELECT * FROM deposits WHERE month = ?", (key,)) or {"month": key, "gold": 0.0, "depot": 0.0}
    unit = "g" if pos["kind"] == "gold" else "Anteile"
    if kind == "kauf":
        if not amount or amount <= 0:
            raise ValueError("Betrag fehlt")
        price = price or pos["price"]
        if not price or price <= 0:
            raise ValueError("Kurs fehlt")
        added = amount / price
        new_shares = pos["shares"] + added
        new_cost = (pos["shares"] * pos["cost"] + amount) / new_shares
        text = f"Kauf {pos['name']}: {de_shares(added)} {unit} für {de_num(amount)} €"
        with db.change(text):
            db.update("positions", {"id": pos["id"]}, {"shares": new_shares, "cost": new_cost, "sold_at": None})
            deposit = dict(deposit)
            deposit[column] = (deposit[column] or 0) + amount
            db.upsert("deposits", deposit)
            db.insert("trades", {"date": date, "kind": "kauf", "position_id": pos["id"], "position": pos["name"],
                                 "shares": added, "amount": amount, "gain": None})
        return text
    if kind == "verkauf":
        if not shares or shares <= 0:
            raise ValueError("Anteile fehlen")
        if shares > pos["shares"] + EPS:
            raise ValueError(f"Mehr als im Bestand ({de_shares(pos['shares'])} {unit})")
        if credit is None or credit < 0:
            raise ValueError("Gutschrift fehlt")
        gain = credit - shares * pos["cost"]
        rest = pos["shares"] - shares
        data = {"shares": 0.0 if rest < EPS else rest, "realized": (pos["realized"] or 0) + gain}
        if rest < EPS:
            data["sold_at"] = date
        text = (f"Verkauf {pos['name']}: {de_shares(shares)} {unit}, Gutschrift {de_num(credit)} €, "
                f"Gewinn {'+' if gain >= 0 else '−'}{de_num(abs(gain))} €")
        with db.change(text):
            db.update("positions", {"id": pos["id"]}, data)
            deposit = dict(deposit)
            deposit[column] = (deposit[column] or 0) - credit
            db.upsert("deposits", deposit)
            db.insert("trades", {"date": date, "kind": "verkauf", "position_id": pos["id"], "position": pos["name"],
                                 "shares": shares, "amount": credit, "gain": gain})
        return text
    raise ValueError("Unbekannte Buchungsart")


# ---------------------------------------------------------------- fixed costs
def monthly_amount(item: dict) -> float:
    if item["amount"] is None:
        return 0.0
    return item["amount"] / period_months(item["interval"])


def spending_summary(db: Database, today: dt.date) -> dict:
    current = month_key(today)
    categories = db.q("SELECT * FROM categories ORDER BY sort, id")
    rows = db.q("SELECT * FROM spending")
    by_month: dict[str, dict[int, float]] = {}
    for row in rows:
        by_month.setdefault(row["month"], {})[row["category_id"]] = row["amount"]
    past = sorted(m for m in by_month if m < current)[-6:]
    avg = {}
    for cat in categories:
        values = [by_month[m].get(cat["id"], 0.0) for m in past]
        avg[cat["id"]] = sum(values) / len(values) if values else 0.0
    avg_total = sum(avg.values())
    last_key = add_months(current, -1)
    last_total = sum(by_month.get(last_key, {}).values())
    this_total = sum(by_month.get(current, {}).values())
    history = []
    for key in sorted(by_month)[-12:]:
        history.append({
            "month": key,
            "label": MONTHS_SHORT[month_start(key).month - 1],
            "values": [by_month[key].get(cat["id"], 0.0) for cat in categories],
            "total": sum(by_month[key].values()),
        })
    return {
        "categories": categories,
        "avg": avg,
        "avg_total": avg_total,
        "avg_months": len(past),
        "last_total": last_total,
        "this_total": this_total,
        "this_month": by_month.get(current, {}),
        "expected": (avg_total + last_total) / 2 if last_total else avg_total,
        "history": history,
    }


def fixed_summary(db: Database, today: dt.date, sett: dict, spend: dict) -> dict:
    items = db.q("SELECT * FROM fixed_costs ORDER BY sort, id")
    groups = {"abo": 0.0, "sonstiges": 0.0}
    by_account: dict[str, float] = {}
    yearly = 0.0
    for item in items:
        monthly = monthly_amount(item)
        groups[item["grp"] if item["grp"] in groups else "sonstiges"] += monthly
        account = item["account"] or "ohne Konto"
        by_account[account] = by_account.get(account, 0.0) + monthly
        if period_months(item["interval"]) > 1 and item["amount"]:
            yearly += item["amount"] * 12 / period_months(item["interval"])
    for cat in spend["categories"]:
        account = cat["account"] or "ohne Konto"
        by_account[account] = by_account.get(account, 0.0) + spend["avg"].get(cat["id"], 0.0)
    fixed = sum(groups.values())
    total = fixed + spend["avg_total"]
    net = float(sett["salary_net"] or 0)
    upcoming = []
    for item in items:
        if not item["amount"]:
            continue
        when = next_due_date(item["due"], item["interval"], today)
        if when:
            upcoming.append({"date": when.isoformat(), "label": de_date(when)[:6], "name": item["name"],
                             "amount": item["amount"], "account": item["account"]})
    upcoming.sort(key=lambda u: u["date"])
    return {
        "items": [dict(item, monthly=monthly_amount(item)) for item in items],
        "abo": groups["abo"],
        "sonstiges": groups["sonstiges"],
        "fixed": fixed,
        "alltag": spend["avg_total"],
        "total": total,
        "net": net,
        "rest": net - total,
        "quote": (total / net * 100) if net else 0.0,
        "yearly": yearly,
        "yearly_monthly": yearly / 12,
        "by_account": sorted(({"name": k, "amount": v} for k, v in by_account.items()), key=lambda a: -a["amount"]),
        "missing": [item["name"] for item in items if item["amount"] is None],
        "upcoming": upcoming[:8],
    }


# ------------------------------------------------------------ monthly list
def payment_items(db: Database, key: str) -> list[dict]:
    items = []
    sort = 0
    for fc in db.q("SELECT * FROM fixed_costs ORDER BY sort, id"):
        if not fc["amount"] or fc["amount"] <= 0:
            continue
        if not due_in_month(fc["due"], fc["interval"], key):
            continue
        count = max(1, int(fc["installments"] or 1))
        for index in range(count):
            name = fc["name"] if count == 1 else f"{fc['name']} (Rate {index + 1} von {count})"
            sort += 1
            items.append({
                "month": key, "name": name, "amount": round(fc["amount"] / count, 2), "due": fc["due"],
                "account": fc["account"], "paid": 0, "fixed_cost_id": fc["id"], "sort": sort,
            })
    return items


def ensure_month(db: Database, key: str) -> int:
    """Create the payment list of a month if it does not exist yet. Returns the number of new items."""
    if db.scalar("SELECT COUNT(*) FROM payments WHERE month = ?", (key,)):
        return 0
    items = payment_items(db, key)
    previous = add_months(key, -1)
    carried = db.q("SELECT * FROM payments WHERE month = ? AND paid = 0 ORDER BY sort, id", (previous,))
    with db.transaction():
        for index, item in enumerate(items):
            db.insert("payments", item)
        for index, old in enumerate(carried):
            db.insert("payments", {
                "month": key, "name": old["name"], "amount": old["amount"], "due": old["due"],
                "account": old["account"], "paid": 0, "carried": 1, "fixed_cost_id": old["fixed_cost_id"],
                "sort": 1000 + index,
            })
    total = sum(i["amount"] for i in items) + sum(c["amount"] for c in carried)
    text = f"Zahlungsliste {month_label(key)} erstellt: {len(items)} Posten, {de_num(total)} €"
    if carried:
        text += f", {len(carried)} offene aus dem Vormonat übernommen"
    db.log(text)
    return len(items) + len(carried)


# -------------------------------------------------------------- full state
def build_state(db: Database, now: dt.datetime, meta: dict) -> dict:
    today = now.date()
    sett = settings(db)
    pf = portfolio(db)
    acc_rows = db.q("SELECT * FROM accounts ORDER BY sort, id")
    acc_total = sum(a["balance"] for a in acc_rows)
    total = pf["investments"] + acc_total
    yesterday = db.one("SELECT * FROM daily WHERE date < ? ORDER BY date DESC LIMIT 1", (today.isoformat(),))
    spend = spending_summary(db, today)
    fixed = fixed_summary(db, today, sett, spend)
    key = month_key(today)
    payments = db.q("SELECT * FROM payments WHERE month = ? ORDER BY paid, sort, id", (key,))
    open_sum = sum(p["amount"] for p in payments if not p["paid"])
    history = db.q("SELECT date, total, investments, stocks_etfs, gold FROM daily ORDER BY date")
    div_rows = db.q("SELECT * FROM dividends ORDER BY date DESC, id DESC")
    year = str(today.year)
    sources = db.q("SELECT * FROM positions WHERE shares > ? OR kind = 'gold' ORDER BY kind = 'gold' DESC, sort, id",
                   (EPS,))
    errors = [s for s in sources if s["status"].startswith("Fehler")]
    alloc = [
        {"label": "Gold", "value": pf["gold_value"], "color": "#e5b740"},
        {"label": "ETFs", "value": pf["etfs"], "color": "#4aa8ff"},
        {"label": "Aktien", "value": pf["stocks"], "color": "#9b8cff"},
    ]
    if pf["crypto"] > EPS:
        alloc.append({"label": "Krypto", "value": pf["crypto"], "color": "#ff7a59"})
    alloc.append({"label": "Konten und Töpfe", "value": acc_total, "color": "#4cc38a"})

    return {
        "meta": dict(meta, today=today.isoformat(), now=now.replace(microsecond=0).isoformat(),
                     month=key, month_label=month_label(key),
                     sources_total=len(sources), sources_errors=len(errors),
                     last_prices=db.get_setting("last_price_run", None),
                     last_import=db.get_setting("last_import", None),
                     last_tr_import=db.get_setting("tr_import", None),
                     empty=not db.scalar("SELECT COUNT(*) FROM positions") and not db.scalar(
                         "SELECT COUNT(*) FROM fixed_costs")),
        "overview": {
            "total": total,
            "total_delta": (total - yesterday["total"]) if yesterday and yesterday["total"] is not None else None,
            "investments": pf["investments"],
            "day_change": pf["day_change"],
            "day_change_pct": pf["day_change_pct"],
            "result": pf["result"],
            "result_pct": pf["result_pct"],
            "result_div_pct": pf["result_div_pct"],
            "open_sum": open_sum,
            "open_count": sum(1 for p in payments if not p["paid"]),
            "alloc": alloc,
            "history": history,
            "upcoming": fixed["upcoming"][:5],
            "activity": db.q("SELECT * FROM activity ORDER BY id DESC LIMIT 6"),
        },
        "depot": {
            "positions": pf["open"],
            "sold": [s for s in pf["sold"]],
            "gold": pf["gold"],
            "depot_value": pf["depot_value"],
            "gold_value": pf["gold_value"],
            "invested": pf["invested"],
            "invested_gold": pf["invested_gold"],
            "result": pf["result"],
            "div_year": sum(d["amount"] for d in div_rows if d["date"].startswith(year)),
            "div_total": pf["div_total"],
            "dividends": div_rows,
            "trades": db.q("SELECT * FROM trades ORDER BY date DESC, id DESC LIMIT 10"),
            "names": [p["name"] for p in db.q("SELECT name FROM positions ORDER BY sort, id")],
        },
        "fixed": fixed,
        "month": {
            "key": key,
            "label": month_label(key),
            "payments": payments,
            "open_sum": open_sum,
            "all_sum": sum(p["amount"] for p in payments),
            "categories": [
                {"id": c["id"], "name": c["name"], "amount": spend["this_month"].get(c["id"]),
                 "avg": spend["avg"].get(c["id"], 0.0)}
                for c in spend["categories"]
            ],
            "spent": spend["this_total"],
            "expected": spend["expected"],
            "avg_total": spend["avg_total"],
            "last_total": spend["last_total"],
            "history": spend["history"][-6:],
            "history12": spend["history"],
            "accounts": acc_rows,
            "accounts_total": acc_total,
        },
        "settings": sett,
        "sources": sources,
        "activity": db.q("SELECT * FROM activity ORDER BY id DESC LIMIT 40"),
        "changes": db.q("SELECT id, at, text, undone FROM changes ORDER BY id DESC LIMIT 15"),
    }


def touch_price_run(db: Database, ok: int, total: int) -> None:
    db.set_setting("last_price_run", {"at": now_iso(), "ok": ok, "total": total})
