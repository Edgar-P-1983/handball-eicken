"""Test abgeschlossen: schreibt nur noch eine leere Notiz (keine nuLiga-Daten mehr)."""
import os, sys
OUT = sys.argv[1] if len(sys.argv) > 1 else 'debug'
os.makedirs(OUT, exist_ok=True)
open(f'{OUT}/log.txt', 'w').write('Test abgeschlossen – keine Daten.\n')
