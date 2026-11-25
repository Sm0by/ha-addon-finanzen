"""Access to Home Assistant through the Supervisor: notifications, MQTT credentials, events."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

SUPERVISOR = "http://supervisor"
ACTION_PREFIX = "FINANZEN_"


class HomeAssistant:
    def __init__(self, session: aiohttp.ClientSession, token: str | None = None):
        self.session = session
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.connected = False

    @property
    def available(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        if not self.available:
            _LOGGER.info("Ohne Home Assistant: %s.%s %s", domain, service, data)
            return
        url = f"{SUPERVISOR}/core/api/services/{domain}/{service}"
        async with self.session.post(url, json=data, headers=self._headers(),
                                     timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"{domain}.{service}: HTTP {resp.status} {await resp.text()}")

    async def notify(self, service: str, title: str, message: str,
                     actions: list[dict] | None = None, tag: str | None = None) -> None:
        if not service:
            return
        data: dict = {"title": title, "message": message}
        extra: dict = {}
        if actions:
            extra["actions"] = actions
        if tag:
            extra["tag"] = tag
        if extra:
            data["data"] = extra
        await self.call_service("notify", service, data)

    async def notify_services(self) -> list[str]:
        if not self.available:
            return []
        async with self.session.get(f"{SUPERVISOR}/core/api/services", headers=self._headers(),
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            domains = await resp.json()
        for domain in domains:
            if domain.get("domain") == "notify":
                skip = {"send_message", "persistent_notification"}
                return sorted(s for s in domain.get("services", {}) if s not in skip)
        return []

    async def mqtt_config(self) -> dict | None:
        if not self.available:
            return None
        try:
            async with self.session.get(f"{SUPERVISOR}/services/mqtt", headers=self._headers(),
                                        timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    _LOGGER.warning("MQTT-Dienst nicht verfügbar (HTTP %s)", resp.status)
                    return None
                body = await resp.json()
                return body.get("data") or None
        except aiohttp.ClientError as err:
            _LOGGER.warning("MQTT-Dienst nicht abrufbar: %s", err)
            return None

    async def listen_actions(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Listen to actionable notification buttons (e.g. "Bezahlt") until cancelled."""
        if not self.available:
            return
        url = f"{SUPERVISOR.replace('http', 'ws')}/core/websocket"
        delay = 5
        while True:
            try:
                async with self.session.ws_connect(url, heartbeat=30) as ws:
                    await ws.receive_json()  # auth_required
                    await ws.send_json({"type": "auth", "access_token": self.token})
                    auth = await ws.receive_json()
                    if auth.get("type") != "auth_ok":
                        raise RuntimeError(f"Anmeldung an Home Assistant fehlgeschlagen: {auth}")
                    await ws.send_json({"id": 1, "type": "subscribe_events",
                                        "event_type": "mobile_app_notification_action"})
                    self.connected = True
                    delay = 5
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = msg.json()
                        if data.get("type") != "event":
                            continue
                        action = (data.get("event", {}).get("data", {}) or {}).get("action", "")
                        if action.startswith(ACTION_PREFIX):
                            try:
                                await callback(action)
                            except Exception:  # keep listening
                                _LOGGER.exception("Aktion %s fehlgeschlagen", action)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("Verbindung zu Home Assistant getrennt: %s", err)
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
