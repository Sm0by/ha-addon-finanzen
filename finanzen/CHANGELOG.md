# Changelog

## 1.1.1

- Import: Aktien wurden als Krypto eingestuft, weil die Summenzeile „Wert ETF's, Aktien, Crypto Aktuell“ mitgezählt wurde
- Import: Fälligkeiten wie „21.01.“, die der Excel-Export in ein Datum verwandelt, werden wieder als Fälligkeit erkannt

## 1.1.0

- Neue Kursquelle Tradegate (nach ISIN, Kurse in Euro). Yahoo und finanzen.net blockieren Abrufe ohne Browser
- ISIN-Suche nach Name unter Einstellungen › Kursquellen, „Übernehmen“ stellt auf Tradegate um und testet
- Ist eine ISIN eingetragen, springt Tradegate auch bei anderen Quellen ein
- Gold über Xetra-Gold (ein Anteil entspricht einem Gramm), gold.de bleibt Ersatzquelle
- Beim ersten Start einmalige Umstellung: ISIN aus der Webadresse und Gold auf Tradegate, im Änderungsverlauf zurücknehmbar
- Import erkennt ISINs in den IMPORTXML-Adressen
- Klare Meldung bei Yahoo-Sperre (HTTP 429), Kursfehler stehen auch im Protokoll der App
- Kopfzeile auf dem Handy kompakter

## 1.0.0

- Erste Version: Übersicht, Depot, Fixkosten, Monat und Einstellungen
- Import des Google Sheets „Finanzen“ (.xlsx) inklusive Kursquellen aus den Formeln
- Kursabruf über Yahoo Finance und Webseiten mit XPath, mit Ersatzquelle
- Tagesabschluss, Zahlungsliste am 1., Erinnerungen mit Knopf „Bezahlt“, Depot-Warnung
- Kauf und Verkauf mit Einstand und Gewinn, Dividenden, Änderungsverlauf mit Rücknahme
- Sensoren per MQTT-Discovery, Export als .xlsx
