# Handball Eicken

Spielplan, Ergebnisse und Tabellen der Eickener SpVg (Melle): 1. Herren, weibliche C-Jugend und männliche E-Jugend (JSG).

Die Daten stammen von nuLiga (HVNB/HRWN) und stehen in `herren.json`, `wjc.json` und `mje.json`.

## Automatische Aktualisierung

Der Workflow `.github/workflows/update.yml` ruft `scripts/update.py` auf:

- alle 15 Minuten ein kurzer Blick: Nur wenn ein Spiel gerade vorbei ist und Ergebnis oder Spielbericht noch fehlen, wird nuLiga abgefragt (höchstens 6 Stunden nach Spielende),
- täglich früh morgens ein Komplettabgleich,
- bei jeder Änderung im Repository ein sofortiger Komplettabgleich.

Aus den Spielberichten werden nur Spielstände, Zeiten, Ereignisse, 7-Meter-Zahlen und die Zahl der eingesetzten Spieler übernommen, keine Namen.
