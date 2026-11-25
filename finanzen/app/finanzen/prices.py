"""Price sources: Yahoo Finance chart API and web pages read with XPath."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re

import aiohttp
from lxml import html as lxml_html

from .db import Database, now_iso
from .util import parse_number

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
TRADEGATE_URL = "https://www.tradegate.de/refresh.php"
ONVISTA_SEARCH = "https://api.onvista.de/api/v1/instruments/search/facet"
TIMEOUT = aiohttp.ClientTimeout(total=20)
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


class PriceError(Exception):
    pass


def parse_tradegate(data: dict) -> dict:
    """Price from Tradegate's refresh.php answer: last trade, else middle of bid and ask, else last close."""
    last = parse_number(data.get("last"))
    bid = parse_number(data.get("bid"))
    ask = parse_number(data.get("ask"))
    close = parse_number(data.get("close"))
    price = last
    if price is None and bid and ask:
        price = (bid + ask) / 2
    if price is None:
        price = bid or ask or close
    if not price:
        raise PriceError("Tradegate liefert für diese ISIN keinen Kurs")
    return {"price": price, "prev_close": close or None, "currency": "EUR"}


async def tradegate_quote(session: aiohttp.ClientSession, isin: str) -> dict:
    isin = isin.strip().upper()
    if not ISIN_RE.match(isin):
        raise PriceError(f"„{isin}“ ist keine gültige ISIN")
    async with session.get(TRADEGATE_URL, params={"isin": isin}, headers={"User-Agent": USER_AGENT},
                           timeout=TIMEOUT) as resp:
        if resp.status != 200:
            raise PriceError(f"Tradegate antwortet mit HTTP {resp.status}")
        text = await resp.text(errors="replace")
    try:
        data = json.loads(text)
    except ValueError as err:
        raise PriceError(f"Tradegate kennt die ISIN {isin} nicht") from err
    if not isinstance(data, dict):
        raise PriceError(f"Tradegate kennt die ISIN {isin} nicht")
    return parse_tradegate(data)


async def search_instruments(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Find ISINs by name, ticker or ISIN (onvista search)."""
    async with session.get(ONVISTA_SEARCH, params={"searchValue": query}, headers={"User-Agent": USER_AGENT},
                           timeout=TIMEOUT) as resp:
        if resp.status != 200:
            raise PriceError(f"Suche antwortet mit HTTP {resp.status}")
        data = await resp.json(content_type=None)
    results = []
    for facet in data.get("facets") or []:
        for item in facet.get("results") or []:
            if item.get("isin"):
                results.append({"name": item.get("name", ""), "isin": item["isin"],
                                "type": facet.get("label") or facet.get("type", ""), "symbol": item.get("symbol", "")})
    return results[:12]


async def yahoo_quote(session: aiohttp.ClientSession, symbol: str) -> dict:
    url = YAHOO_URL.format(symbol=symbol.strip())
    params = {"range": "1d", "interval": "1d"}
    async with session.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT) as resp:
        if resp.status == 404:
            raise PriceError(f"Symbol {symbol} bei Yahoo unbekannt")
        if resp.status == 429:
            raise PriceError("Yahoo blockiert den Abruf (HTTP 429), Quelle auf Tradegate mit ISIN umstellen")
        if resp.status != 200:
            raise PriceError(f"Yahoo antwortet mit HTTP {resp.status}")
        data = await resp.json(content_type=None)
    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
    except (KeyError, IndexError, TypeError) as err:
        message = (data or {}).get("chart", {}).get("error", {}) or {}
        raise PriceError(message.get("description") or f"Keine Daten für {symbol}") from err
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    currency = meta.get("currency") or "EUR"
    if price is None:
        raise PriceError(f"Kein Kurs für {symbol}")
    if currency == "GBp":
        price, prev, currency = price / 100, (prev / 100 if prev else None), "GBP"
    return {"price": float(price), "prev_close": float(prev) if prev else None, "currency": currency}


async def fx_to_eur(session: aiohttp.ClientSession, currency: str, cache: dict) -> float:
    currency = (currency or "EUR").upper()
    if currency == "EUR":
        return 1.0
    if currency not in cache:
        quote = await yahoo_quote(session, f"{currency}EUR=X")
        cache[currency] = quote["price"]
    return cache[currency]


async def web_price(session: aiohttp.ClientSession, url: str, xpath: str) -> float:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}
    async with session.get(url, headers=headers, timeout=TIMEOUT) as resp:
        if resp.status != 200:
            raise PriceError(f"Webseite antwortet mit HTTP {resp.status}")
        text = await resp.text(errors="replace")
    try:
        document = lxml_html.fromstring(text)
        found = document.xpath(xpath)
    except Exception as err:  # lxml raises several error types
        raise PriceError(f"Auswahlpfad ungültig: {err}") from err
    if not isinstance(found, list):
        found = [found]
    for item in found:
        value = item if isinstance(item, (str, float, int)) else item.text_content()
        number = parse_number(value)
        if number is not None:
            return number
    raise PriceError("Auswahlpfad findet keinen Kurs auf der Seite")


async def quote_position(session: aiohttp.ClientSession, pos: dict, fx_cache: dict) -> dict:
    """Fetch the current price of one position in EUR.

    Order: the chosen source, then Tradegate if an ISIN is known, then the fallback page.
    The factor (e.g. ounce to gram) applies to Yahoo and web pages, Tradegate quotes are used as they are.
    """
    factor = pos.get("factor") or 1.0
    isin = (pos.get("isin") or "").strip()
    errors = []
    attempts = []
    if pos["source"] == "tradegate" and isin:
        attempts.append(("tradegate", isin, ""))
    elif pos["source"] == "yahoo" and pos.get("symbol"):
        attempts.append(("yahoo", pos["symbol"], ""))
    elif pos["source"] == "web" and pos.get("url"):
        attempts.append(("web", pos["url"], pos.get("xpath", "")))
    if isin and pos["source"] != "tradegate":
        attempts.append(("tradegate", isin, ""))
    if pos.get("fallback_url") and pos.get("fallback_xpath"):
        attempts.append(("web", pos["fallback_url"], pos["fallback_xpath"]))
    if not attempts:
        raise PriceError("Keine Kursquelle eingetragen")
    for kind, target, xpath in attempts:
        try:
            if kind == "tradegate":
                quote = await tradegate_quote(session, target)
                return dict(quote, via="Tradegate")
            if kind == "yahoo":
                quote = await yahoo_quote(session, target)
                rate = await fx_to_eur(session, quote["currency"], fx_cache)
                prev = quote["prev_close"] * rate * factor if quote["prev_close"] else None
                return {"price": quote["price"] * rate * factor, "prev_close": prev,
                        "currency": quote["currency"], "via": f"Yahoo {target}"}
            value = await web_price(session, target, xpath)
            return {"price": value * factor, "prev_close": None, "currency": "EUR", "via": "Webseite"}
        except (PriceError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            errors.append(str(err) or err.__class__.__name__)
    raise PriceError("; ".join(errors))


def _source_of(via: str) -> str:
    return {"Tradegate": "tradegate", "Webseite": "web"}.get(via, "yahoo")


def _apply_quote(db: Database, pos: dict, quote: dict, today: str) -> None:
    data = {
        "price": quote["price"],
        "currency": quote["currency"],
        "price_at": now_iso(),
        "price_day": today,
        "status": "ok" if _source_of(quote["via"]) == pos["source"] else f"ok über {quote['via'].split(' ')[0]}",
        "error_since": None,
    }
    prev = quote["prev_close"]
    if prev is None:
        if pos["price_day"] and pos["price_day"] != today and pos["price"] is not None:
            data["prev_close"] = pos["price"]
            data["prev_close_date"] = pos["price_day"]
        else:
            prev = pos["prev_close"]
    else:
        data["prev_close"] = prev
        data["prev_close_date"] = today
    prev = data.get("prev_close", prev)
    data["change_pct"] = (quote["price"] / prev - 1) * 100 if prev else None
    db.update("positions", {"id": pos["id"]}, data)


def _apply_error(db: Database, pos: dict, message: str) -> None:
    data = {"status": f"Fehler: {message}"[:300]}
    if not pos["error_since"]:
        data["error_since"] = now_iso()
    db.update("positions", {"id": pos["id"]}, data)


async def refresh(db: Database, session: aiohttp.ClientSession, ids: list[int] | None = None) -> dict:
    """Fetch prices for all active positions (or the given ids) and store them."""
    if ids:
        marks = ",".join("?" for _ in ids)
        positions = db.q(f"SELECT * FROM positions WHERE id IN ({marks})", tuple(ids))
    else:
        positions = db.q("SELECT * FROM positions WHERE (shares > 0 OR kind = 'gold') AND source != 'manual'")
    today = dt.date.today().isoformat()
    fx_cache: dict = {}
    semaphore = asyncio.Semaphore(4)

    async def one(pos):
        async with semaphore:
            try:
                return pos, await quote_position(session, pos, fx_cache), None
            except PriceError as err:
                return pos, None, str(err)
            except Exception as err:  # never let one source break the whole run
                _LOGGER.exception("Kursabruf für %s fehlgeschlagen", pos["name"])
                return pos, None, f"{err.__class__.__name__}: {err}"

    results = await asyncio.gather(*(one(p) for p in positions))
    ok, failed = 0, []
    for pos, quote, error in results:
        if quote:
            _apply_quote(db, pos, quote, today)
            ok += 1
        else:
            _apply_error(db, pos, error or "unbekannter Fehler")
            failed.append((pos["name"], error))
            _LOGGER.warning("Kein Kurs für %s: %s", pos["name"], error)
    _LOGGER.info("Kurse abgerufen: %s von %s", ok, len(positions))
    return {"ok": ok, "total": len(positions), "failed": failed}
