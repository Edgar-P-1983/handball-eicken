#!/usr/bin/env python3
"""Holt Tabellen, Spielpläne und Spielberichte von nuLiga und schreibt herren.json, wjc.json, mje.json.

Aufruf:
  update.py --mode auto    alle 15 Minuten: nur abrufen, wenn ein Spiel gerade vorbei ist und
                           Ergebnis oder Spielbericht noch fehlen (höchstens 6 Stunden lang)
  update.py --mode daily   täglicher Komplettabgleich
  update.py --mode force   sofortiger Komplettabgleich (manuell aus dem Chat angestoßen)

Spielernamen werden nie gespeichert: Aus den Spielberichten werden nur Spielstände, Zeiten,
Ereignisse, 7-Meter-Zahlen und die Anzahl der eingesetzten Spieler übernommen.
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, time, urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BERLIN = ZoneInfo('Europe/Berlin')
HOST = 'https://hvnb-handball.liga.nu'
BASE = HOST + '/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/'
TEAMS = [
    {'id': 'herren', 'file': 'herren.json', 'ch': 'HVNB+26%2F27', 'group': '489364', 'reports': 'own'},
    {'id': 'wjc', 'file': 'wjc.json', 'ch': 'HRWN+26%2F27', 'group': '505361', 'reports': 'all'},
    {'id': 'mje', 'file': 'mje.json', 'ch': 'HRWN+26%2F27', 'group': '506735', 'reports': 'all'},
]
WINDOW_H = 6            # so lange nach Spielende wird alle 15 Minuten nachgesehen
REPORT_DAYS = 14        # fehlende Spielberichte werden so lange nach dem Spiel noch gesucht
MAX_REPORTS = 40        # höchstens so viele Spielberichte pro Lauf abrufen

LOG = []
def log(msg):
    LOG.append(msg)
    print(msg, flush=True)


# ---------------------------------------------------------------- Abruf
def soup_of(html):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # erst bei Bedarf installieren, damit die kurzen 15-Minuten-Läufe schnell bleiben
        import importlib, site
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--user', '--break-system-packages',
                        'beautifulsoup4'], check=True)
        for d in [site.getusersitepackages()]:
            if d not in sys.path:
                sys.path.append(d)
        importlib.invalidate_caches()
        from bs4 import BeautifulSoup
    return BeautifulSoup(html, 'html.parser')


def fetch(url):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Handball-Eicken-Seite; github.com/Edgar-P-1983/handball-eicken)'})
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read().decode('utf-8', 'replace')
            time.sleep(1.0)
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 + 5 * attempt)
    raise RuntimeError(f'Abruf fehlgeschlagen: {url}: {last!r}')


def txt(el):
    return re.sub(r'\s+', ' ', el.get_text(' ', strip=True)).strip() if el is not None else ''


def cells(tr):
    """Zellen einer Zeile, colspan aufgelöst."""
    out = []
    for td in tr.find_all(['td', 'th'], recursive=False):
        out.append(td)
        for _ in range(int(td.get('colspan', 1) or 1) - 1):
            out.append(None)
    return out


def own_rows(table):
    return [tr for tr in table.find_all('tr') if tr.find_parent('table') is table]


# ---------------------------------------------------------------- Spielplan
def parse_meetings(html):
    s = soup_of(html)
    table = None
    for t in s.find_all('table', class_='result-set'):
        head = [txt(c) for c in t.find_all('th')]
        if 'Heimmannschaft' in head and 'Nr.' in head:
            table = t
            break
    if table is None:
        raise ValueError('Spielplan-Tabelle nicht gefunden')
    games, datum = [], ''
    for tr in own_rows(table):
        c = cells(tr)
        if not c or tr.find('th') or len(c) < 8:
            continue
        tag, dat, zeit, ort, nr, heim, gast, erg = (c[i] for i in range(8))
        if tag is not None and 'Termin offen' in txt(tag):
            d = ''
        else:
            d = txt(dat)
            if re.fullmatch(r'\d\d\.\d\d\.\d{4}', d):
                datum = f'{d[6:10]}-{d[3:5]}-{d[0:2]}'
            d = datum
        z = re.search(r'\d\d:\d\d', txt(zeit)) if zeit is not None else None
        if not d:
            z = None
        title = (zeit.get('title') or '') if zeit is not None else ''
        g = {'nr': int(txt(nr)) if txt(nr).isdigit() else txt(nr), 'datum': d, 'zeit': z.group(0) if z else '',
             'halle': txt(ort), 'heim': txt(heim), 'gast': txt(gast), 'ergebnis': ''}
        a = erg.find('a', href=re.compile('groupMeetingReport')) if erg is not None else None
        if a:
            m = re.search(r'meeting=(\d+)', a['href'])
            sc = re.search(r'\d+:\d+', txt(a))
            if m and sc:
                g['ergebnis'] = sc.group(0)
                g['meeting'] = int(m.group(1))
        if 'ursprünglicher Termin' in title:
            g['verlegt'] = True
        court = ort.find('a', href=re.compile('courtInfo')) if ort is not None else None
        if court:
            g['_court'] = court['href']
        games.append(g)
    if not games:
        raise ValueError('Spielplan leer')
    return games


# ---------------------------------------------------------------- Tabelle
def parse_table(html):
    s = soup_of(html)
    for t in s.find_all('table', class_='result-set'):
        head = [txt(c) for c in own_rows(t)[0].find_all(['th', 'td'])] if own_rows(t) else []
        if 'Rang' in head and 'Mannschaft' in head and 'Punkte' in head:
            break
    else:
        raise ValueError('Tabelle nicht gefunden')
    idx = {h: i for i, h in enumerate(head)}
    rows = []
    for tr in own_rows(t)[1:]:
        c = [txt(x) if x is not None else '' for x in cells(tr)]
        if len(c) < len(head) or not c[idx['Begegnungen']].isdigit():
            continue
        r = {'platz': int(c[idx['Rang']]) if c[idx['Rang']].isdigit() else len(rows) + 1, 'team': c[idx['Mannschaft']], 'spiele': int(c[idx['Begegnungen']]),
             's': int(c[idx['S']]), 'u': int(c[idx['U']]), 'n': int(c[idx['N']])}
        if 'Tore' in idx:
            r['tore'] = c[idx['Tore']]
            r['diff'] = int(c[idx['+/-']].replace('+', '')) if c[idx['+/-']] else 0
        r['punkte'] = c[idx['Punkte']]
        rows.append(r)
    if not rows:
        raise ValueError('Tabelle leer')
    return rows


# ---------------------------------------------------------------- Halle
def parse_court(html):
    s = soup_of(html)
    h1 = s.find('h1')
    name = re.sub(r'\s*\(\d+\)\s*', ' ', txt(h1).replace('Hallenspielplan', '')).strip(' ,')
    adresse = ''
    for h2 in s.find_all('h2'):
        if 'Hallenadresse' in txt(h2):
            p = h2.find_next('p')
            parts = [re.sub(r'\s+', ' ', x) for x in p.stripped_strings] if p else []
            parts = [x for x in parts if not x.startswith('Tel') and 'Routenplaner' not in x]
            adresse = ', '.join(re.sub(r'\s+', ' ', x) for x in parts[:2])
            break
    return {'name': name or 'Halle', 'adresse': adresse}


# ---------------------------------------------------------------- Spielbericht
def event_type(text):
    t = text.lower()
    if '7m' in t or '7-m' in t:
        return None if 'mit tor' in t else '7m verworfen'
    if 'minute' in t or '2-min' in t or '2 min' in t:
        return '2 Minuten'
    if 'auszeit' in t or 'timeout' in t:
        return 'Auszeit'
    if 'verwarn' in t or 'gelb' in t:
        return 'Verwarnung'
    if 'disq' in t or 'rot' in t or 'blau' in t:
        return 'Disqualifikation'
    return None


def parse_report(html, halbzeit_min):
    s = soup_of(html)
    # Endstand und Halbzeit
    end = hz = None
    for td in s.find_all(['td', 'th']):
        if txt(td) == 'Ergebnis':
            nxt = td.find_next_sibling(['td', 'th'])
            m = re.search(r'(\d+:\d+)\s*\((\d+:\d+)\)', txt(nxt))
            if m:
                end, hz = m.group(1), m.group(2)
                break
    if not end:
        raise ValueError('Ergebnis im Spielbericht nicht gefunden')
    # Spielverlauf
    tl = None
    for t in s.find_all('table'):
        head = [txt(c) for c in t.find_all('th')]
        if {'Team', 'Zeit', 'Stand', 'Ereignis'} <= set(head):
            tl, hidx = t, {h: i for i, h in enumerate(head)}
            break
    if tl is None:
        raise ValueError('Spielverlauf fehlt')
    h = a = 0
    last = -1
    verlauf, ev, hzs = [], [], (0, 0)
    for tr in own_rows(tl):
        c = [txt(x) if x is not None else '' for x in cells(tr)]
        if tr.find('th') or len(c) < 4:
            continue
        team, zeit, stand, ereignis = c[hidx['Team']], c[hidx['Zeit']], c[hidx['Stand']], c[hidx['Ereignis']]
        m = re.fullmatch(r'(\d+):(\d\d)', zeit)
        if not m:
            continue
        sec = int(m.group(1)) * 60 + int(m.group(2))
        if sec < last:
            raise ValueError(f'Zeit läuft rückwärts bei {zeit}')
        last = sec
        t = f'{int(m.group(1)):02d}:{m.group(2)}'
        side = 'H' if team == 'Heim' else 'G' if team == 'Gast' else ('H' if 'heim' in ereignis.lower() else 'G' if 'gast' in ereignis.lower() else '')
        sm = re.fullmatch(r'(\d+):(\d+)', stand)
        if sm and (int(sm.group(1)), int(sm.group(2))) != (h, a):
            nh, na = int(sm.group(1)), int(sm.group(2))
            if (nh, na) == (h + 1, a):
                verlauf.append(t + 'H')
            elif (nh, na) == (h, a + 1):
                verlauf.append(t + 'G')
            else:
                raise ValueError(f'Spielstand springt von {h}:{a} auf {nh}:{na} bei {t}')
            h, a = nh, na
            if sec <= halbzeit_min * 60:
                hzs = (h, a)
            continue
        typ = event_type(ereignis)
        if typ and side:
            ev.append([t, typ, side])
    if f'{h}:{a}' != end:
        raise ValueError(f'Spielverlauf endet {h}:{a}, Ergebnis ist {end}')
    if not verlauf:
        raise ValueError('Spielverlauf ohne Tore')
    if f'{hzs[0]}:{hzs[1]}' != hz:
        log(f'    Hinweis: Halbzeit laut Verlauf {hzs[0]}:{hzs[1]}, laut Bericht {hz} – Bericht übernommen')
    # 7-Meter: Würfe und Tore je Mannschaft (in Seitenreihenfolge Heim, Gast)
    siebenm = []
    for tr in s.find_all('tr'):
        c = [txt(x) for x in tr.find_all(['td', 'th'], recursive=False)]
        if c and c[0] == '7-Meter' and len(c) > 1:
            nxt = tr.find_next_sibling('tr')
            c2 = [txt(x) for x in nxt.find_all(['td', 'th'], recursive=False)] if nxt else []
            w = int(c[1]) if c[1].isdigit() else 0
            g = int(c2[1]) if len(c2) > 1 and c2[1].isdigit() else 0
            siebenm.append([w, g])
    siebenm = (siebenm + [[0, 0], [0, 0]])[:2]
    # Kader: nur Anzahl der Spieler mit Rückennummer (keine Namen!)
    kader = []
    for t in s.find_all('table'):
        head = [txt(c) for c in t.find_all('th')]
        if 'Trikot' in head and 'Tore' in head:
            n = sum(1 for tr in own_rows(t) if (cells(tr) and cells(tr)[0] is not None and txt(cells(tr)[0]).isdigit()))
            kader.append(n)
    kader = (kader + [0, 0])[:2]
    return {'tore': end, 'halbzeit': hz, 'siebenm': siebenm, 'kader': kader, 'verlauf': verlauf, 'ereignisse': ev}


# ---------------------------------------------------------------- Ablauf
def now_berlin():
    return dt.datetime.now(BERLIN)


def kickoff(g):
    if not g.get('datum') or not g.get('zeit'):
        return None
    return dt.datetime.strptime(g['datum'] + ' ' + g['zeit'], '%Y-%m-%d %H:%M').replace(tzinfo=BERLIN)


def wants_report(team, D, g):
    return team['reports'] == 'all' or D['team'] in (g['heim'], g['gast'])


def needs_check(team, D, now):
    """Gibt es ein Spiel, das gerade vorbei ist und bei dem Ergebnis oder Bericht noch fehlen?"""
    hz = D.get('halbzeitMin', 30)
    for g in D.get('spiele', []):
        k = kickoff(g)
        if not k:
            continue
        start = k + dt.timedelta(minutes=2 * hz + 10)
        if start <= now <= start + dt.timedelta(hours=WINDOW_H):
            if not g.get('ergebnis') or (wants_report(team, D, g) and not g.get('bericht')):
                return f'Spiel {g["nr"]} {g["heim"]} – {g["gast"]}'
    return None


def update_team(team, D, now):
    url_q = f'championship={team["ch"]}&group={team["group"]}'
    games = parse_meetings(fetch(f'{BASE}groupPage?displayTyp=vorrunde&displayDetail=meetings&{url_q}'))
    tabelle = parse_table(fetch(f'{BASE}groupPage?{url_q}'))
    old = {str(g['nr']): g for g in D.get('spiele', [])}
    if len(games) < 0.8 * len(old):
        raise ValueError(f'nur {len(games)} statt {len(old)} Spiele gefunden – Abbruch')
    names = {r['team'] for r in tabelle}
    hallen = dict(D.get('hallen', {}))
    fetched = 0
    neu_erg, neu_ber = [], []
    for g in games:
        court = g.pop('_court', None)
        if g['halle'] and g['halle'] not in hallen and court:
            try:
                hallen[g['halle']] = parse_court(fetch(court if court.startswith('http') else HOST + court))
                log(f'  neue Halle {g["halle"]}: {hallen[g["halle"]]["name"]}')
            except Exception as e:  # noqa: BLE001
                log(f'  Halle {g["halle"]} nicht lesbar: {e}')
        o = old.get(str(g['nr']), {})
        if g['ergebnis'] and g['ergebnis'] != o.get('ergebnis'):
            neu_erg.append(g)
        if not g['ergebnis']:
            continue
        ob = o.get('bericht')
        if ob and o.get('ergebnis') == g['ergebnis'] and ob.get('meeting') == g.get('meeting'):
            g['bericht'] = ob
            continue
        if not wants_report(team, D, g) or not g.get('meeting'):
            continue
        k = kickoff(g)
        if k and now - k > dt.timedelta(days=REPORT_DAYS) and not ob and o.get('ergebnis') == g['ergebnis']:
            continue  # alter Bericht ohne Verlauf: nicht jedes Mal neu versuchen
        if fetched >= MAX_REPORTS:
            continue
        fetched += 1
        try:
            b = parse_report(fetch(f'{BASE}groupMeetingReport?meeting={g["meeting"]}&{url_q}'), D.get('halbzeitMin', 30))
            if team['id'] != 'mje' and b['tore'] != g['ergebnis']:
                raise ValueError(f'Bericht {b["tore"]} passt nicht zum Ergebnis {g["ergebnis"]}')
            b['meeting'] = g['meeting']
            g['bericht'] = b
            neu_ber.append(g)
        except Exception as e:  # noqa: BLE001
            log(f'  Spielbericht {g["nr"]} (meeting {g["meeting"]}) übersprungen: {e}')
            if ob:
                g['bericht'] = ob
    unknown = {g['heim'] for g in games} | {g['gast'] for g in games}
    if not names & unknown:
        raise ValueError('Tabelle und Spielplan passen nicht zusammen')
    N = dict(D)
    N['tabelle'] = tabelle
    N['spiele'] = games
    N['hallen'] = hallen
    return N, neu_erg, neu_ber


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['auto', 'daily', 'force'], default='auto')
    args = ap.parse_args()
    now = now_berlin()
    stamp = now.replace(second=0, microsecond=0).isoformat()
    changed_files, summary, errors = [], [], []
    for team in TEAMS:
        path = os.path.join(ROOT, team['file'])
        D = json.load(open(path, encoding='utf-8'))
        if args.mode == 'auto':
            why = needs_check(team, D, now)
            if not why:
                continue
            log(f'{team["id"]}: Abruf wegen {why}')
        else:
            log(f'{team["id"]}: Komplettabgleich ({args.mode})')
        try:
            N, neu_erg, neu_ber = update_team(team, D, now)
        except Exception as e:  # noqa: BLE001
            errors.append(f'{team["id"]}: {e}')
            log(f'  FEHLER: {e}')
            continue
        strip = lambda x: {k: v for k, v in x.items() if k != 'stand'}
        if strip(N) != strip(D) or args.mode != 'auto':
            N['stand'] = stamp
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(N, f, ensure_ascii=False, indent=1)
                f.write('\n')
            changed_files.append(team['file'])
        for g in neu_erg:
            summary.append(f'{team["id"]}: Ergebnis {g["heim"]} – {g["gast"]} {g["ergebnis"]}')
        for g in neu_ber:
            summary.append(f'{team["id"]}: Spielbericht {g["heim"]} – {g["gast"]}')
    if not changed_files and not errors:
        log('Nichts zu tun.' if args.mode == 'auto' else 'Keine Änderungen.')
    msg = 'Daten aktualisiert' if not summary else ('Neu: ' + summary[0] if len(summary) == 1 else f'{len(summary)} Neuigkeiten')
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a') as f:
            f.write(f'changed={"true" if changed_files else "false"}\n')
            f.write(f'message={msg}\n')
            f.write('details<<ENDE\n' + '\n'.join(summary + errors) + '\nENDE\n')
    if errors and args.mode != 'auto':
        sys.exit(1)


if __name__ == '__main__':
    main()
