"""Web interface and JSON API, served through Home Assistant Ingress."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from aiohttp import web

from . import calc, trimport
from .core import FinanzenApp
from .db import now_iso
from .exporter import export_workbook
from .importer import ImportError_, import_workbook
from .prices import ISIN_RE, PriceError, search_instruments
from .util import isin_valid, parse_number

_LOGGER = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
INGRESS_IPS = {"172.30.32.2", "127.0.0.1", "::1"}

POSITION_FIELDS = {
    "name": str, "kind": str, "shares": float, "cost": float, "source": str, "isin": str, "symbol": str, "url": str,
    "xpath": str, "factor": float, "fallback_url": str, "fallback_xpath": str,
}
FIXED_FIELDS = {
    "name": str, "amount": float, "interval": str, "account": str, "due": str, "grp": str, "note": str,
    "installments": int,
}


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, ensure_ascii=False, default=str))


def _clean(fields: dict, body: dict, *, partial: bool = True) -> dict:
    data = {}
    for key, kind in fields.items():
        if key not in body:
            continue
        value = body[key]
        if kind is float:
            if value in (None, ""):
                data[key] = None
                continue
            number = parse_number(value)
            if number is None:
                raise ValueError(f"„{value}“ ist keine Zahl")
            data[key] = number
        elif kind is int:
            number = parse_number(value)
            data[key] = int(number) if number is not None else 1
        else:
            data[key] = str(value or "").strip()
    if "kind" in data and data["kind"] not in calc.KINDS:
        raise ValueError("Unbekannte Art")
    if "interval" in data and data["interval"] not in calc.INTERVALS:
        raise ValueError("Unbekanntes Intervall")
    if "grp" in data and data["grp"] not in calc.GROUPS:
        raise ValueError("Unbekannte Gruppe")
    if "source" in data and data["source"] not in ("tradegate", "yahoo", "web", "manual"):
        raise ValueError("Unbekannte Kursquelle")
    if data.get("isin"):
        data["isin"] = data["isin"].replace(" ", "").upper()
        if not ISIN_RE.match(data["isin"]) or not isin_valid(data["isin"]):
            raise ValueError(f"„{data['isin']}“ ist keine gültige ISIN (Prüfziffer passt nicht)")
    if "installments" in data:
        data["installments"] = max(1, min(12, data["installments"]))
    return data


@web.middleware
async def guard(request: web.Request, handler):
    app: FinanzenApp = request.app["finanzen"]
    if not app.dev and request.remote not in INGRESS_IPS:
        return web.Response(status=403, text="Nur über Home Assistant erreichbar")
    try:
        return await handler(request)
    except (ValueError, KeyError, ImportError_) as err:
        message = err.args[0] if err.args else str(err)
        return _json({"error": str(message)}, status=400)


def create_web_app(core: FinanzenApp) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=30 * 1024 * 1024)
    app["finanzen"] = core
    routes = web.RouteTableDef()
    db = core.db

    def done(text: str | None = None) -> web.Response:
        core.changed()
        return _json({"ok": True, "message": text, "state": core.state()})

    @routes.get("/")
    async def index(_request):
        # Version the static files so a browser never keeps an old app.js after an update
        stamp = max(int((STATIC / name).stat().st_mtime) for name in ("app.js", "app.css"))
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        html = html.replace("static/app.css", f"static/app.css?v={stamp}").replace("static/app.js", f"static/app.js?v={stamp}")
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})

    @routes.get("/api/state")
    async def state(_request):
        return _json(core.state())

    # ---------------------------------------------------------------- prices
    @routes.post("/api/prices/refresh")
    async def refresh(_request):
        result = await core.refresh_prices(manual=True)
        return done(f"Kurse abgerufen: {result['ok']} von {result['total']}")

    @routes.post("/api/positions/{pid}/test")
    async def test_position(request):
        pid = int(request.match_info["pid"])
        result = await core.refresh_prices([pid], manual=True)
        pos = db.one("SELECT name, price, status FROM positions WHERE id = ?", (pid,))
        if result["failed"]:
            return done(f"{pos['name']}: {result['failed'][0][1]}")
        return done(f"{pos['name']}: {pos['price']:.4f} €".replace(".", ","))

    # ------------------------------------------------------------- positions
    @routes.post("/api/positions")
    async def add_position(request):
        body = await request.json()
        data = _clean(POSITION_FIELDS, body)
        if not data.get("name"):
            raise ValueError("Name fehlt")
        data.setdefault("kind", "aktie")
        data.setdefault("source", "manual")
        data["status"] = "neu"
        data["sort"] = (db.scalar("SELECT MAX(sort) FROM positions") or 0) + 1
        with db.change(f"Position {data['name']} angelegt"):
            db.insert("positions", data)
        return done(f"{data['name']} angelegt")

    @routes.patch("/api/positions/{pid}")
    async def edit_position(request):
        pid = int(request.match_info["pid"])
        body = await request.json()
        data = _clean(POSITION_FIELDS, body)
        pos = db.one("SELECT * FROM positions WHERE id = ?", (pid,))
        if not pos:
            raise ValueError("Position nicht gefunden")
        if "price_manual" in body:
            price = parse_number(body["price_manual"])
            if price is None:
                raise ValueError("Kurs ist keine Zahl")
            today = dt.date.today().isoformat()
            data.update(price=price, price_at=now_iso(), price_day=today, status="von Hand", error_since=None,
                        change_pct=None)
        if any(k in data for k in ("source", "isin", "symbol", "url", "xpath", "factor", "fallback_url",
                                   "fallback_xpath")):
            data["status"] = "geändert, bitte testen"
            data["error_since"] = None
        for key in ("shares", "cost", "factor"):
            if key in data and data[key] is None:
                data[key] = 0.0 if key != "factor" else 1.0
        with db.change(f"{pos['name']} geändert"):
            db.update("positions", {"id": pid}, data)
        return done()

    @routes.get("/api/search")
    async def search(request):
        query = request.query.get("q", "").strip()
        if len(query) < 2:
            raise ValueError("Bitte mindestens zwei Zeichen eingeben")
        try:
            results = await search_instruments(core.session, query)
        except PriceError as err:
            raise ValueError(str(err)) from err
        except Exception as err:  # network problems
            raise ValueError(f"Suche fehlgeschlagen: {err}") from err
        return _json({"query": query, "results": results})

    @routes.delete("/api/positions/{pid}")
    async def delete_position(request):
        pid = int(request.match_info["pid"])
        pos = db.one("SELECT * FROM positions WHERE id = ?", (pid,))
        if not pos:
            raise ValueError("Position nicht gefunden")
        with db.change(f"Position {pos['name']} gelöscht"):
            db.delete("positions", {"id": pid})
        return done(f"{pos['name']} gelöscht")

    @routes.post("/api/trades")
    async def trade(request):
        body = await request.json()
        date = str(body.get("date") or dt.date.today().isoformat())[:10]
        text = calc.book_trade(
            db, str(body.get("kind")), int(body.get("position_id")), date,
            amount=parse_number(body.get("amount")), price=parse_number(body.get("price")),
            shares=parse_number(body.get("shares")), credit=parse_number(body.get("credit")),
        )
        db.log(text)
        return done(text)

    # ------------------------------------------------------------- dividends
    @routes.post("/api/dividends")
    async def add_dividend(request):
        body = await request.json()
        amount = parse_number(body.get("amount"))
        position = str(body.get("position") or "").strip()
        date = str(body.get("date") or dt.date.today().isoformat())[:10]
        if amount is None or not position:
            raise ValueError("Position und Betrag angeben")
        with db.change(f"Dividende {position} {amount:.2f} €"):
            db.insert("dividends", {"date": date, "position": position, "amount": amount})
        return done("Dividende gespeichert")

    @routes.delete("/api/dividends/{did}")
    async def delete_dividend(request):
        did = int(request.match_info["did"])
        row = db.one("SELECT * FROM dividends WHERE id = ?", (did,))
        if not row:
            raise ValueError("Dividende nicht gefunden")
        with db.change(f"Dividende {row['position']} vom {row['date']} gelöscht"):
            db.delete("dividends", {"id": did})
        return done()

    # ----------------------------------------------------------- fixed costs
    @routes.post("/api/fixed")
    async def add_fixed(request):
        body = await request.json()
        data = _clean(FIXED_FIELDS, body)
        data.setdefault("name", "Neuer Posten")
        data.setdefault("interval", "monatlich")
        data.setdefault("grp", "sonstiges")
        data["sort"] = (db.scalar("SELECT MAX(sort) FROM fixed_costs") or 0) + 1
        with db.change(f"Fixkosten {data['name']} angelegt"):
            db.insert("fixed_costs", data)
        return done()

    @routes.patch("/api/fixed/{fid}")
    async def edit_fixed(request):
        fid = int(request.match_info["fid"])
        item = db.one("SELECT * FROM fixed_costs WHERE id = ?", (fid,))
        if not item:
            raise ValueError("Posten nicht gefunden")
        data = _clean(FIXED_FIELDS, await request.json())
        with db.change(f"Fixkosten {item['name']} geändert"):
            db.update("fixed_costs", {"id": fid}, data)
        return done()

    @routes.delete("/api/fixed/{fid}")
    async def delete_fixed(request):
        fid = int(request.match_info["fid"])
        item = db.one("SELECT * FROM fixed_costs WHERE id = ?", (fid,))
        if not item:
            raise ValueError("Posten nicht gefunden")
        with db.change(f"Fixkosten {item['name']} gelöscht"):
            db.delete("fixed_costs", {"id": fid})
        return done(f"„{item['name']}“ gelöscht")

    # ----------------------------------------------------------- month view
    @routes.patch("/api/payments/{pid}")
    async def edit_payment(request):
        pid = int(request.match_info["pid"])
        row = db.one("SELECT * FROM payments WHERE id = ?", (pid,))
        if not row:
            raise ValueError("Zahlung nicht gefunden")
        body = await request.json()
        paid = 1 if body.get("paid") else 0
        with db.change(f"{row['name']} als {'bezahlt' if paid else 'offen'} markiert"):
            db.update("payments", {"id": pid}, {"paid": paid})
        return done()

    @routes.post("/api/payments")
    async def add_payment(request):
        body = await request.json()
        name = str(body.get("name") or "").strip()
        amount = parse_number(body.get("amount"))
        if not name or amount is None:
            raise ValueError("Name und Betrag angeben")
        key = str(body.get("month") or core.state()["meta"]["month"])
        with db.change(f"Zahlung {name} hinzugefügt"):
            db.insert("payments", {"month": key, "name": name, "amount": amount, "paid": 0, "sort": 2000})
        return done()

    @routes.delete("/api/payments/{pid}")
    async def delete_payment(request):
        pid = int(request.match_info["pid"])
        row = db.one("SELECT * FROM payments WHERE id = ?", (pid,))
        if not row:
            raise ValueError("Zahlung nicht gefunden")
        with db.change(f"Zahlung {row['name']} entfernt"):
            db.delete("payments", {"id": pid})
        return done()

    @routes.put("/api/spending")
    async def set_spending(request):
        body = await request.json()
        month = str(body.get("month"))
        cat = int(body.get("category_id"))
        amount = parse_number(body.get("amount")) if body.get("amount") not in (None, "") else None
        name = db.scalar("SELECT name FROM categories WHERE id = ?", (cat,)) or "Kategorie"
        with db.change(f"Alltag {name} {month}"):
            if amount is None:
                db.delete("spending", {"month": month, "category_id": cat})
            else:
                db.upsert("spending", {"month": month, "category_id": cat, "amount": amount})
        return done()

    @routes.post("/api/accounts")
    async def add_account(request):
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("Name fehlt")
        with db.change(f"Konto {name} angelegt"):
            db.insert("accounts", {"name": name, "balance": parse_number(body.get("balance")) or 0.0,
                                   "sort": (db.scalar("SELECT MAX(sort) FROM accounts") or 0) + 1})
        return done()

    @routes.patch("/api/accounts/{aid}")
    async def edit_account(request):
        aid = int(request.match_info["aid"])
        row = db.one("SELECT * FROM accounts WHERE id = ?", (aid,))
        if not row:
            raise ValueError("Konto nicht gefunden")
        body = await request.json()
        data = {}
        if "balance" in body:
            value = parse_number(body["balance"])
            if value is None:
                raise ValueError("Stand ist keine Zahl")
            data["balance"] = value
        if "name" in body and str(body["name"]).strip():
            data["name"] = str(body["name"]).strip()
        with db.change(f"{row['name']} geändert"):
            db.update("accounts", {"id": aid}, data)
        return done()

    @routes.delete("/api/accounts/{aid}")
    async def delete_account(request):
        aid = int(request.match_info["aid"])
        row = db.one("SELECT * FROM accounts WHERE id = ?", (aid,))
        if not row:
            raise ValueError("Konto nicht gefunden")
        with db.change(f"Konto {row['name']} gelöscht"):
            db.delete("accounts", {"id": aid})
        return done()

    # -------------------------------------------------------------- settings
    @routes.patch("/api/settings")
    async def edit_settings(request):
        body = await request.json()
        with db.change("Einstellungen geändert"):
            for key in ("salary_net", "salary_gross", "depot_warning_pct"):
                if key in body:
                    value = parse_number(body[key])
                    if value is None:
                        raise ValueError("Bitte eine Zahl eingeben")
                    db.upsert("settings", {"key": key, "value": json.dumps(value)})
            if "notify_service" in body:
                db.upsert("settings", {"key": "notify_service", "value": json.dumps(str(body["notify_service"]))})
            for group in ("jobs", "notify"):
                if group in body and isinstance(body[group], dict):
                    current = calc.settings(db)[group]
                    current.update({k: bool(v) for k, v in body[group].items() if k in current})
                    db.upsert("settings", {"key": group, "value": json.dumps(current)})
        return done("Gespeichert")

    @routes.get("/api/notify-services")
    async def notify_services(_request):
        services = await core.ha.notify_services() if core.ha else []
        return _json({"services": services})

    @routes.post("/api/notify/test")
    async def notify_test(_request):
        sent = await core.notify("due", "Finanzen", "Testnachricht aus der Finanzen-App")
        return done("Testnachricht gesendet" if sent else "Kein Ziel für Benachrichtigungen gewählt")

    # ------------------------------------------------------ import and export
    async def upload(request) -> bytes:
        data = None
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            async for part in reader:
                if part.name == "file":
                    data = await part.read(decode=False)
        else:
            data = await request.read()
        if not data:
            raise ValueError("Keine Datei erhalten")
        return bytes(data)

    @routes.post("/api/import")
    async def do_import(request):
        data = await upload(request)
        backup = core.data_dir / "vor-import.db"
        db.backup_to(backup)
        report = import_workbook(db, data)
        db.set_setting("import_backup", {"at": now_iso(), "file": backup.name})
        core.changed()
        await core.refresh_prices()
        return done("Import fertig: " + report["summary"])

    @routes.post("/api/import/undo")
    async def undo_import(_request):
        info = db.get_setting("import_backup")
        backup = core.data_dir / "vor-import.db"
        if not info or not backup.exists():
            raise ValueError("Kein Stand vor dem Import vorhanden")
        db.restore_from(backup)
        backup.unlink()
        db.log("Import zurückgenommen, Stand vor dem Import wiederhergestellt")
        return done("Stand vor dem Import wiederhergestellt")

    @routes.post("/api/tr/preview")
    async def tr_preview(request):
        data = await upload(request)
        return _json({"ok": True, "preview": trimport.preview(db, data)})

    @routes.post("/api/tr/apply")
    async def tr_apply(request):
        data = await upload(request)
        result = trimport.apply(db, data)
        db.log(result["text"])
        if result["new_open"]:
            core.changed()
            await core.refresh_prices()
        return done(result["text"])

    @routes.get("/api/export.xlsx")
    async def export(_request):
        data = export_workbook(db)
        name = f"finanzen-{dt.date.today().isoformat()}.xlsx"
        return web.Response(body=data, headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": f'attachment; filename="{name}"',
        })

    @routes.post("/api/changes/{cid}/undo")
    async def undo(request):
        text = db.undo(int(request.match_info["cid"]))
        db.log(f"Zurückgenommen: {text}")
        return done(f"Zurückgenommen: {text}")

    app.add_routes(routes)
    app.router.add_static("/static", STATIC, append_version=True)
    return app
