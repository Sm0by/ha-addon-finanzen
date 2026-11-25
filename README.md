# Finanzen app for Home Assistant

Eine Home-Assistant-App für Depot, Fixkosten und die monatliche Zahlungsliste. Sie ersetzt ein Google
Sheet mit Apps-Script-Triggern und läuft vollständig lokal.

## Installation

1. In Home Assistant: **Einstellungen › Apps › App-Store › ⋮ › Repositories**
2. Diese Adresse hinzufügen: `https://github.com/Sm0by/ha-addon-finanzen`
3. **Finanzen** installieren, starten und „In Seitenleiste anzeigen“ einschalten

Alternativ als lokale App: den Ordner `finanzen` nach `/addons/finanzen` kopieren (z. B. über die
Samba-Freigabe), dann im App-Store **⋮ › Nach Updates suchen**.

## Entwicklung

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install aiohttp lxml openpyxl paho-mqtt pytest
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe scripts\seed_dev.py .devdata
```

Lokal starten (ohne Home Assistant, mit erfundenen Testdaten):

```powershell
cd finanzen\app; ..\..\.venv\Scripts\python.exe -m finanzen --dev --host 127.0.0.1 --data ..\..\.devdata
```

Die Testdaten in `tests/sample_sheet.py` bilden nur den Aufbau des Sheets nach, die Werte sind erfunden.
