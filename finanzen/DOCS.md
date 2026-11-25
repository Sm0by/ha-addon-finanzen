# Finanzen

Die App ersetzt das Google Sheet „Finanzen“ samt Apps Script. Sie läuft in Home Assistant, hat eine eigene
Oberfläche in der Seitenleiste und meldet die wichtigsten Werte als Sensoren an Home Assistant.

## Einrichtung

1. App installieren und starten, „In Seitenleiste anzeigen“ einschalten.
2. In Google Sheets: **Datei › Herunterladen › Microsoft Excel (.xlsx)**.
3. In der App unter **Einstellungen › Daten** die Datei hochladen und **Importieren** wählen.
4. Unter **Einstellungen › Benachrichtigungen** das Handy wählen, an das Erinnerungen gehen sollen.

Der Import übernimmt alle Blätter: Fixkosten aus „Abonnements“ und „Sonstiges“, Alltagsausgaben aus
„DaylisAVG“, Positionen, Gold, Dividenden, Monatssummen und Konten aus „Investments“, die Tageswerte aus
„Berechnungen“, das Gehalt aus „Summen“ und die Zahlungsliste aus „Aktueller Monat“. Die Kursquellen
werden aus den Formeln gelesen: `GOOGLEFINANCE("ETR:VOW")` wird zum Yahoo-Symbol `VOW.DE`,
`IMPORTXML(Adresse; Pfad)` zur Webseite mit Auswahlpfad. Steht in der Adresse eine ISIN, nimmt die App
Tradegate.

## Kursquellen

| Quelle | Eingabe | Hinweis |
| --- | --- | --- |
| Tradegate | ISIN | Empfohlen. Kurse in Euro, auch ohne Umsatz am Tag (Mitte aus Geld und Brief). |
| Yahoo | Symbol, z. B. `SAP.DE` | Yahoo blockiert Abrufe ohne Browser oft mit HTTP 429. |
| Webseite | Adresse und XPath | Wie IMPORTXML. Viele Seiten blockieren Programme oder ändern ihren Aufbau. |
| von Hand | Kurs | Für Werte ohne Börsenkurs. |

Unter **Einstellungen › Kursquellen** findet **ISIN suchen** die ISIN zum Namen. **Übernehmen** stellt
die Position auf Tradegate um und ruft den Kurs gleich ab. Ist eine ISIN eingetragen, springt Tradegate
auch ein, wenn die gewählte Quelle ausfällt. Gold läuft über Xetra-Gold (ISIN `DE000A0S9GB0`, ein Anteil
entspricht einem Gramm Gold).

Ein neuer Import ersetzt alle Daten. **Stand vor dem Import wiederherstellen** nimmt ihn zurück.

## Optionen

| Option | Bedeutung |
| --- | --- |
| `kursintervall` | Minuten zwischen zwei Kursabrufen, montags bis freitags von 8 bis 22 Uhr. Sonst einmal am Tag. |
| `log_level` | Ausführlichkeit des Protokolls. |

Alles andere stellst du in der App unter **Einstellungen** ein.

## Was automatisch passiert

| Wann | Was |
| --- | --- |
| alle `kursintervall` Minuten | Kurse von Yahoo oder der Webseite abrufen. Fällt eine Quelle aus, gilt für diese Position der letzte Kurs, die Ersatzquelle wird versucht. |
| täglich 23:55 | Tagesabschluss für den Verlauf speichern. Ein verpasster Tag wird nachgeholt. |
| am 1. um 06:00 | Zahlungsliste des Monats aus den Fixkosten erstellen. Offene Zahlungen des Vormonats werden übernommen. |
| am Fälligkeitstag ab 09:00 | Erinnerung aufs Handy mit Knopf „Bezahlt“. |
| optional | Warnung, wenn das Depot an einem Tag mehr als die eingestellte Grenze verliert. |

## Fixkosten

- **Fällig**: `08.` heißt monatlich am 8., `21.07.` jährlich am 21. Juli, `27.–30.` zwischen dem 27. und 30.
- **Raten**: Teilt einen Posten in der Monatsliste auf, zum Beispiel 2 Raten zu je der Hälfte.
- Posten ohne Betrag zählen nicht mit und erscheinen unter „Noch offen“.

## Käufe und Verkäufe

Unter **Depot › Kauf oder Verkauf buchen**:

- **Kauf**: Betrag und Kurs je Anteil. Anteile und durchschnittlicher Einstand werden neu berechnet, der
  eingezahlte Betrag steigt.
- **Verkauf**: Anteile und die Gutschrift laut Abrechnung (nach Gebühren und Steuern). Gewinn = Gutschrift
  minus Einstand der verkauften Anteile. Der eingezahlte Betrag sinkt um die Gutschrift. Bei einem
  Komplettverkauf wandert die Position zu „Verkaufte Positionen“.

Jede Änderung lässt sich unter **Einstellungen › Änderungsverlauf** zurücknehmen.

## Import aus Trade Republic

Statt Käufe und Verkäufe einzeln zu buchen, kann die App den Transaktionsexport von Trade Republic lesen.
In der Trade-Republic-App: **Profil › Kontoauszüge › Transaktionsexport**, die .csv-Datei dann unter
**Einstellungen › Trade Republic** hochladen.

- Zuerst kommt eine **Vorschau**, dabei ändert sich nichts: Depotwert, Einzahlungen, Ergebnis, realisierte
  Gewinne und Dividenden vorher und nachher, geänderte Positionen, Verkäufe, neue Dividenden und die
  Einzahlungen je Jahr. Erst **Übernehmen** schreibt, und der ganze Import lässt sich im Änderungsverlauf
  zurücknehmen.
- Gelesen werden nur Wertpapier-Buchungen (Käufe, Verkäufe, Dividenden, Splits, Saveback, Aktiengeschenke).
  Kartenzahlungen, Überweisungen und IBANs werden übersprungen und nicht gespeichert.
- Positionen werden über die ISIN zugeordnet, verkaufte Positionen ohne ISIN über den Namen. Fehlende
  Positionen legt der Import an, auch längst verkaufte mit ihrem Gewinn.
- **Anteile und Einstand** kommen aus dem Export: Einstand mit Kaufgebühren, Gewinne nach dem
  Durchschnittskurs, Gutschrift nach Gebühren und Steuern. Splits erhöhen die Anteile.
- **Dividenden** zählen nach Steuern. Schon vorhandene (gleicher Betrag, Datum bis 45 Tage daneben) werden
  erkannt, die übrigen ergänzt.
- **Einzahlungen ins Depot** werden vom ersten bis zum letzten Monat des Exports ersetzt: Käufe mit Gebühren
  minus Verkaufserlöse. Saveback und Aktiengeschenke zahlt Trade Republic, sie zählen nicht als eigene
  Einzahlung und landen damit im Ergebnis. Gold bleibt unverändert.
- Von Hand gebuchte Käufe und Verkäufe derselben Positionen ersetzt der Export.
- Die App merkt sich alle gelesenen Buchungen. Ein späterer Export muss deshalb nur die neuen Wochen
  enthalten, doppelte Buchungen erkennt sie an der Transaktions-ID.

## Sensoren in Home Assistant

Über den MQTT-Broker (Mosquitto) entsteht das Gerät **Finanzen** mit diesen Entitäten:

`Gesamtvermögen`, `Investments`, `Gold`, `Depot`, `Rendite seit Kauf`, `Dividenden dieses Jahr`,
`Fixkosten pro Monat`, `Übrig nach Fixkosten`, `Offene Zahlungen`, `Alltag diesen Monat`,
`Konten und Töpfe`, `Nächste Abbuchung`, `Letzter Kursabruf`, `Kursquellen mit Fehler` und
`Zahlung heute fällig`.

Die Zahlenwerte haben `state_class: measurement`. Home Assistant speichert damit den Verlauf als
Langzeitstatistik und schreibt ihn in eine angebundene InfluxDB.

## Daten und Sicherung

Alle Daten liegen in `/data/finanzen.db` im Ordner der App. Sie sind in jedem Home-Assistant-Backup
enthalten. Während eines Backups wird die App kurz angehalten, damit die Datenbank vollständig gesichert
wird. Unter **Einstellungen › Daten** lässt sich jederzeit alles als .xlsx exportieren.
