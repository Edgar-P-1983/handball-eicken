# ESV Handball

Spielplan, Ergebnisse und Tabellen aller Handball-Mannschaften der Eickener SpVg (Melle) und der JSG Eicken-Buer-Oldendorf, mit Favoriten.

Die Daten stammen von nuLiga (HVNB/HRWN) und stehen im Ordner `data/`: `teams.json` ist das Verzeichnis aller Mannschaften (wird täglich aus den Vereinsseiten auf nuLiga ermittelt), dazu eine Datei je Mannschaft.

## Automatische Aktualisierung

Der Workflow `.github/workflows/update.yml` ruft `scripts/update.py` auf:

- alle 15 Minuten ein kurzer Blick: Nur wenn ein Spiel gerade vorbei ist und Ergebnis oder Spielbericht noch fehlen, wird nuLiga abgefragt (höchstens 6 Stunden nach Spielende),
- täglich früh morgens ein Komplettabgleich,
- bei jeder Änderung im Repository ein sofortiger Komplettabgleich.

Aus den Spielberichten werden nur Spielstände, Zeiten, Ereignisse, 7-Meter-Zahlen und die Zahl der eingesetzten Spieler übernommen, keine Namen.
