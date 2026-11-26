# Changelog

## 1.2.4

- Monat: Auch in den Kontoständen unter „Konten und Töpfe“ lässt sich rechnen, etwa „1.200 − 45,90“
- Am Handy schiebt sich das Eingabefeld über die Tastatur, auch in der Home-Assistant-App, wo die
  Tastatur das Feld bisher oft verdeckt hat

## 1.2.3

- Monat: In den Feldern der Alltagsausgaben lässt sich rechnen, „50,00 + 25“ wird zu 75,00 (auch −, *, /
  und Klammern). Auf dem Handy öffnet sich dafür die normale Tastatur

## 1.2.2

- Depot: „Wert Depot“ zeigt Gewinn oder Verlust ohne und mit Dividenden

## 1.2.1

- Depot: Die Kacheln „Wert Depot“ und „Wert Gold“ zeigen Gewinn oder Verlust der Kategorie (Wert minus
  eingezahlt, in Euro und Prozent). Zusammen ergeben sie das „Ergebnis seit Beginn“

## 1.2.0

- Import des Transaktionsexports von Trade Republic (.csv) unter Einstellungen › Trade Republic, mit
  Vorschau vor dem Übernehmen und Rücknahme im Änderungsverlauf
- Anteile, Einstand mit Gebühren, realisierte Gewinne, Splits, Dividenden nach Steuern und die monatlichen
  Einzahlungen ins Depot kommen aus dem Export; verkaufte Positionen werden mit ihrem Gewinn angelegt
- Saveback und Aktiengeschenke zählen nicht als eigene Einzahlung
- Nur Wertpapier-Buchungen werden gelesen und gespeichert, Kartenzahlungen und IBANs nicht
- Spätere Exporte ergänzen die gespeicherten Buchungen, doppelte erkennt die App an der Transaktions-ID
- Export als .xlsx enthält Gebühren, Steuern und die Buchungen aus Trade Republic

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
