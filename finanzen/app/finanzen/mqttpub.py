"""Publish the results to Home Assistant as an MQTT device with discovery."""

from __future__ import annotations

import json
import logging
import threading

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

PREFIX = "homeassistant"
BASE = "finanzen"
STATE_TOPIC = f"{BASE}/state"
STATUS_TOPIC = f"{BASE}/status"

SENSORS = [
    # key, name, unit, icon, extra
    ("gesamtvermoegen", "Gesamtvermögen", "€", "mdi:bank", {}),
    ("investments", "Investments", "€", "mdi:finance", {}),
    ("gold", "Gold", "€", "mdi:gold", {}),
    ("depot", "Depot", "€", "mdi:chart-line", {}),
    ("rendite", "Rendite seit Kauf", "%", "mdi:percent", {}),
    ("dividenden_jahr", "Dividenden dieses Jahr", "€", "mdi:cash-plus", {}),
    ("fixkosten_monat", "Fixkosten pro Monat", "€", "mdi:repeat", {}),
    ("rest_nach_fixkosten", "Übrig nach Fixkosten", "€", "mdi:wallet", {}),
    ("zahlungen_offen", "Offene Zahlungen", "€", "mdi:calendar-clock", {}),
    ("alltag_monat", "Alltag diesen Monat", "€", "mdi:cart", {}),
    ("konten", "Konten und Töpfe", "€", "mdi:piggy-bank", {}),
    ("naechste_abbuchung", "Nächste Abbuchung", None, "mdi:calendar-arrow-right", {"state_class": None}),
    ("letzter_kursabruf", "Letzter Kursabruf", None, "mdi:clock-check",
     {"state_class": None, "device_class": "timestamp"}),
    ("kursquellen_fehler", "Kursquellen mit Fehler", None, "mdi:alert-circle", {}),
]
BINARY = [("zahlung_heute_faellig", "Zahlung heute fällig", "mdi:calendar-alert")]


class MqttPublisher:
    def __init__(self, version: str):
        self.version = version
        self.client: mqtt.Client | None = None
        self.connected = False
        self._last_state: dict | None = None
        self._lock = threading.Lock()

    def start(self, config: dict) -> None:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="finanzen-app")
        except AttributeError:  # paho-mqtt < 2
            client = mqtt.Client(client_id="finanzen-app")
        if config.get("username"):
            client.username_pw_set(config["username"], config.get("password") or None)
        if config.get("ssl"):
            client.tls_set()
        client.will_set(STATUS_TOPIC, "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.reconnect_delay_set(5, 300)
        client.connect_async(config.get("host", "core-mosquitto"), int(config.get("port", 1883)), keepalive=60)
        client.loop_start()
        self.client = client

    def stop(self) -> None:
        if self.client:
            try:
                self.client.publish(STATUS_TOPIC, "offline", retain=True)
                self.client.disconnect()
                self.client.loop_stop()
            except Exception:  # shutting down anyway
                pass

    def _device(self) -> dict:
        return {
            "identifiers": ["finanzen_app"],
            "name": "Finanzen",
            "manufacturer": "Finanzen-App",
            "model": "Home Assistant App",
            "sw_version": self.version,
        }

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False) or (isinstance(reason_code, int) and reason_code != 0):
            _LOGGER.warning("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        self.connected = True
        _LOGGER.info("MQTT verbunden, sende Discovery")
        for key, name, unit, icon, extra in SENSORS:
            payload = {
                "name": name,
                "unique_id": f"finanzen_{key}",
                "state_topic": STATE_TOPIC,
                "value_template": f"{{{{ value_json.{key} }}}}",
                "availability_topic": STATUS_TOPIC,
                "icon": icon,
                "device": self._device(),
            }
            if unit:
                payload["unit_of_measurement"] = unit
            state_class = extra.get("state_class", "measurement")
            if state_class:
                payload["state_class"] = state_class
            if extra.get("device_class"):
                payload["device_class"] = extra["device_class"]
            if key == "naechste_abbuchung":
                payload["json_attributes_topic"] = f"{BASE}/naechste_abbuchung"
            client.publish(f"{PREFIX}/sensor/finanzen/{key}/config", json.dumps(payload), retain=True)
        for key, name, icon in BINARY:
            payload = {
                "name": name,
                "unique_id": f"finanzen_{key}",
                "state_topic": STATE_TOPIC,
                "value_template": f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}",
                "availability_topic": STATUS_TOPIC,
                "icon": icon,
                "device": self._device(),
            }
            client.publish(f"{PREFIX}/binary_sensor/finanzen/{key}/config", json.dumps(payload), retain=True)
        client.publish(STATUS_TOPIC, "online", retain=True)
        with self._lock:
            if self._last_state:
                self._send(self._last_state)

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False

    def _send(self, state: dict) -> None:
        if not self.client:
            return
        attrs = state.pop("_naechste_abbuchung_attrs", None)
        self.client.publish(STATE_TOPIC, json.dumps(state), retain=True)
        if attrs is not None:
            self.client.publish(f"{BASE}/naechste_abbuchung", json.dumps(attrs), retain=True)

    def publish(self, state: dict) -> None:
        with self._lock:
            self._last_state = dict(state)
            if self.connected:
                self._send(dict(state))


def sensor_values(state: dict) -> dict:
    """Turn the UI state into the MQTT state payload."""
    ov, dep, fixed, month, meta = state["overview"], state["depot"], state["fixed"], state["month"], state["meta"]
    upcoming = fixed["upcoming"][0] if fixed["upcoming"] else None
    today = meta["today"]
    due_today = False
    day = int(today[8:10])
    for payment in month["payments"]:
        if payment["paid"]:
            continue
        due = payment.get("due") or ""
        if due[:2].isdigit() and int(due[:2]) == day:
            due_today = True
    last = (meta.get("last_prices") or {}).get("at")
    return {
        "gesamtvermoegen": round(ov["total"], 2),
        "investments": round(ov["investments"], 2),
        "gold": round(dep["gold_value"], 2),
        "depot": round(dep["depot_value"], 2),
        "rendite": round(ov["result_pct"], 2),
        "dividenden_jahr": round(dep["div_year"], 2),
        "fixkosten_monat": round(fixed["total"], 2),
        "rest_nach_fixkosten": round(fixed["rest"], 2),
        "zahlungen_offen": round(month["open_sum"], 2),
        "alltag_monat": round(month["spent"], 2),
        "konten": round(month["accounts_total"], 2),
        "naechste_abbuchung": (f"{upcoming['label']} {upcoming['name']} "
                               f"{upcoming['amount']:.2f} €".replace(f"{upcoming['amount']:.2f}",
                                                                   f"{upcoming['amount']:.2f}".replace(".", ","))
                               if upcoming else "–"),
        "letzter_kursabruf": (last.replace(" ", "T") + _tz_suffix()) if last else None,
        "kursquellen_fehler": meta["sources_errors"],
        "zahlung_heute_faellig": due_today,
        "_naechste_abbuchung_attrs": upcoming or {},
    }


def _tz_suffix() -> str:
    import time

    offset = -time.altzone if time.daylight and time.localtime().tm_isdst else -time.timezone
    sign = "+" if offset >= 0 else "-"
    offset = abs(offset)
    return f"{sign}{offset // 3600:02d}:{offset % 3600 // 60:02d}"
