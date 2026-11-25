"""The running app: storage, price runs, schedules, notifications and MQTT."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path

import aiohttp

from . import __version__, calc, prices
from .db import Database
from .ha import ACTION_PREFIX, HomeAssistant
from .importer import upgrade_sources
from .mqttpub import MqttPublisher, sensor_values
from .util import due_day, month_key

_LOGGER = logging.getLogger(__name__)

TICK_SECONDS = 30


class FinanzenApp:
    def __init__(self, data_dir: Path, options: dict, dev: bool = False):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.options = options
        self.dev = dev
        self.db = Database(self.data_dir / "finanzen.db")
        self.session: aiohttp.ClientSession | None = None
        self.ha: HomeAssistant | None = None
        self.mqtt: MqttPublisher | None = None
        self._tasks: list[asyncio.Task] = []
        self._publish_handle: asyncio.TimerHandle | None = None
        self._price_lock = asyncio.Lock()

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self.db.scalar("SELECT COUNT(*) FROM positions"):
            upgraded = upgrade_sources(self.db)
            if upgraded:
                _LOGGER.info("Kursquellen auf Tradegate umgestellt: %s", ", ".join(upgraded))
        else:
            self.db.set_setting("sources_upgraded", True)
        self.session = aiohttp.ClientSession()
        self.ha = HomeAssistant(self.session, token=None if not self.dev else "")
        if self.ha.available:
            config = await self.ha.mqtt_config()
            if config:
                self.mqtt = MqttPublisher(__version__)
                self.mqtt.start(config)
            else:
                self.db.log("MQTT nicht verfügbar: Sensoren in Home Assistant fehlen", "warn")
            self._tasks.append(asyncio.create_task(self.ha.listen_actions(self.on_action)))
        self._tasks.append(asyncio.create_task(self._loop()))
        self.changed()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.mqtt:
            self.mqtt.stop()
        if self.session:
            await self.session.close()
        self.db.close()

    # ------------------------------------------------------------------ state
    def meta(self) -> dict:
        return {
            "version": __version__,
            "dev": self.dev,
            "ha": bool(self.ha and self.ha.available),
            "ha_events": bool(self.ha and self.ha.connected),
            "mqtt": bool(self.mqtt and self.mqtt.connected),
            "interval": self.interval_minutes(),
        }

    def state(self) -> dict:
        return calc.build_state(self.db, dt.datetime.now(), self.meta())

    def interval_minutes(self) -> int:
        try:
            return max(5, int(self.options.get("kursintervall", 15)))
        except (TypeError, ValueError):
            return 15

    def changed(self) -> None:
        """Data changed: publish the sensors to Home Assistant shortly after."""
        if not self.mqtt:
            return
        loop = asyncio.get_running_loop()
        if self._publish_handle:
            self._publish_handle.cancel()
        self._publish_handle = loop.call_later(1.5, self._publish)

    def _publish(self) -> None:
        self._publish_handle = None
        try:
            self.mqtt.publish(sensor_values(self.state()))
        except Exception:
            _LOGGER.exception("Senden an MQTT fehlgeschlagen")

    # ----------------------------------------------------------------- prices
    async def refresh_prices(self, ids: list[int] | None = None, manual: bool = False) -> dict:
        async with self._price_lock:
            result = await prices.refresh(self.db, self.session, ids)
            if not ids:
                calc.touch_price_run(self.db, result["ok"], result["total"])
                text = f"Kurse abgerufen: {result['ok']} von {result['total']}"
                if result["failed"]:
                    text += " · Fehler bei " + ", ".join(name for name, _ in result["failed"])
                self.db.log(text, "warn" if result["failed"] else "info")
            elif manual:
                for name, error in result["failed"]:
                    self.db.log(f"Test {name}: {error}", "warn")
        self.changed()
        if not ids:
            await self._after_prices()
        return result

    async def _after_prices(self) -> None:
        sett = calc.settings(self.db)
        today = dt.date.today().isoformat()
        if sett["jobs"].get("depot_warning") and self.db.get_setting("depot_warned") != today:
            pf = calc.portfolio(self.db)
            limit = float(sett.get("depot_warning_pct") or 3)
            if pf["day_change_pct"] <= -limit:
                losers = sorted(pf["open"], key=lambda p: p.get("change_pct") or 0)[:3]
                names = ", ".join(f"{p['name']} {p['change_pct']:+.1f} %" for p in losers if p.get("change_pct"))
                await self.notify("depot_warning", "Depot im Minus",
                                  f"Heute {pf['day_change_pct']:+.1f} % ({pf['day_change']:+.2f} €). {names}")
                self.db.set_setting("depot_warned", today)

    # ------------------------------------------------------------ notifications
    async def notify(self, kind: str, title: str, message: str, actions: list[dict] | None = None,
                     tag: str | None = None) -> bool:
        sett = calc.settings(self.db)
        if not sett["notify"].get(kind, True) or not sett["notify_service"] or not self.ha:
            return False
        try:
            await self.ha.notify(sett["notify_service"], title, message, actions, tag)
            self.db.log(f"Benachrichtigung an {sett['notify_service']}: {title}")
            return True
        except Exception as err:
            self.db.log(f"Benachrichtigung fehlgeschlagen: {err}", "warn")
            return False

    async def on_action(self, action: str) -> None:
        if action.startswith(f"{ACTION_PREFIX}BEZAHLT_"):
            payment_id = int(action.rsplit("_", 1)[1])
            payment = self.db.one("SELECT * FROM payments WHERE id = ?", (payment_id,))
            if payment and not payment["paid"]:
                with self.db.change(f"{payment['name']} als bezahlt markiert (Handy)"):
                    self.db.update("payments", {"id": payment_id}, {"paid": 1})
                self.db.log(f"{payment['name']} per Handy als bezahlt markiert")
                self.changed()

    # -------------------------------------------------------------- schedules
    async def _loop(self) -> None:
        await asyncio.sleep(3)
        while True:
            try:
                await self.tick(dt.datetime.now())
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Fehler im Zeitplan")
            await asyncio.sleep(TICK_SECONDS)

    def _empty(self) -> bool:
        return not self.db.scalar("SELECT COUNT(*) FROM positions") and not self.db.scalar(
            "SELECT COUNT(*) FROM fixed_costs")

    async def tick(self, now: dt.datetime) -> None:
        if self._empty():
            return
        sett = calc.settings(self.db)
        jobs = sett["jobs"]
        today = now.date()

        # Prices: every N minutes on weekdays from 8 to 22 o'clock, otherwise once a day
        if jobs.get("prices"):
            last = self.db.get_setting("last_price_run")
            last_at = dt.datetime.fromisoformat(last["at"]) if last else None
            market = now.weekday() < 5 and 8 <= now.hour < 22
            due = last_at is None or last_at.date() != today or (
                market and now - last_at >= dt.timedelta(minutes=self.interval_minutes()))
            if due:
                await self.refresh_prices()

        # Daily snapshot at 23:55, a missed day is written with the last known prices
        if jobs.get("daily"):
            if now.time() >= dt.time(23, 55) and not self.db.one(
                    "SELECT date FROM daily WHERE date = ?", (today.isoformat(),)):
                calc.write_daily(self.db, today)
                self.db.log(f"Tagesabschluss {today:%d.%m.%Y} gespeichert")
                self.changed()
            yesterday = today - dt.timedelta(days=1)
            last = self.db.get_setting("last_price_run")
            if last and last["at"][:10] == yesterday.isoformat() and not self.db.one(
                    "SELECT date FROM daily WHERE date = ?", (yesterday.isoformat(),)):
                calc.write_daily(self.db, yesterday)
                self.db.log(f"Tagesabschluss {yesterday:%d.%m.%Y} nachgeholt (letzte bekannte Kurse)")

        # Payment list of the month on the 1st from 06:00
        key = month_key(today)
        if jobs.get("payments") and (today.day > 1 or now.hour >= 6):
            created = calc.ensure_month(self.db, key)
            if created:
                payments = self.db.q("SELECT * FROM payments WHERE month = ?", (key,))
                total = sum(p["amount"] for p in payments)
                await self.notify("new_list", f"Zahlungsliste {calc.month_label(key)}",
                                  f"{len(payments)} Posten, zusammen {total:.2f} €".replace(".", ","))
                self.changed()

        # Reminders for payments due today from 09:00
        if jobs.get("reminders") and now.hour >= 9 and self.db.get_setting("reminded_day") != today.isoformat():
            self.db.set_setting("reminded_day", today.isoformat())
            for payment in self.db.q("SELECT * FROM payments WHERE month = ? AND paid = 0", (key,)):
                if due_day(payment["due"]) == today.day and payment["reminded"] != today.isoformat():
                    amount = f"{payment['amount']:.2f}".replace(".", ",")
                    sent = await self.notify(
                        "due", f"{payment['name']} ist fällig",
                        f"{amount} € · {payment['account'] or 'ohne Konto'} · fällig {payment['due']}",
                        actions=[{"action": f"{ACTION_PREFIX}BEZAHLT_{payment['id']}", "title": "Bezahlt"}],
                        tag=f"finanzen-zahlung-{payment['id']}",
                    )
                    if sent:
                        self.db.execute("UPDATE payments SET reminded = ? WHERE id = ?",
                                        (today.isoformat(), payment["id"]))

        # A price source that fails for more than a day
        if self.db.get_setting("source_error_day") != today.isoformat():
            limit = (now - dt.timedelta(days=1)).replace(microsecond=0).isoformat(sep=" ")
            broken = self.db.q("SELECT name FROM positions WHERE error_since IS NOT NULL AND error_since < ? "
                               "AND (shares > 0 OR kind = 'gold')", (limit,))
            if broken:
                self.db.set_setting("source_error_day", today.isoformat())
                await self.notify("source_errors", "Kursquelle gestört",
                                  "Seit über einem Tag kein Kurs für " + ", ".join(b["name"] for b in broken))
