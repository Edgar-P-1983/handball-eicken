#!/usr/bin/env python3
"""Holt Tabellen, Spielpläne und Spielberichte aller Handball-Mannschaften der Eickener SpVg
und der JSG Eicken-Buer-Oldendorf von nuLiga und schreibt data/teams.json und data/<id>.json.

Aufruf:
  update.py --mode auto    alle 15 Minuten: nur abrufen, wenn ein Spiel gerade vorbei ist und
                           Ergebnis oder Spielbericht noch fehlen (höchstens 6 Stunden lang)
  update.py --mode daily   täglicher Komplettabgleich inkl. Suche nach neuen Mannschaften
  update.py --mode force   sofortiger Komplettabgleich (manuell aus dem Claude-Chat angestoßen)

Spielernamen werden nie gespeichert: Aus den Spielberichten werden nur Spielstände, Zeiten,
Ereignisse, 7-Meter-Zahlen und die Anzahl der eingesetzten Spieler übernommen.
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, time, urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
INDEX = os.path.join(DATA, 'teams.json')
BERLIN = ZoneInfo('Europe/Berlin')
HOST = 'https://hvnb-handball.liga.nu'
BASE = HOST + '/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/'
PDF = HOST + '/cgi-bin/WebObjects/nuLigaDokumentHBDE.woa/wa/nuDokument?dokument=meetingReportHB&meeting='
WEB = 'https://www.eickener-sv.de/index.php/abteilungen/handball'
CLUBS = [
    {'club': '32392', 'name': 'Eickener SpVg', 'kurz': 'ESV'},
    {'club': '32655', 'name': 'JSG Eicken-Buer-Oldendorf', 'kurz': 'JSG'},
]
# Farben der Mannschaften (Punkt vor dem Namen)
DOTS = {'herren1': '#343a40', 'herren2': '#5c6670', 'herren3': '#8b95a1', 'damen1': '#e8590c', 'damen2': '#f08c00',
        'mA': '#1864ab', 'mB': '#1971c2', 'mC': '#1098ad', 'mD': '#7048e8', 'mE': '#6741d9', 'mF': '#4263eb',
        'wA': '#a61e4d', 'wB': '#d6336c', 'wC': '#c2255c', 'wD': '#e64980', 'wE': '#f06595', 'wF': '#f783ac',
        'gE': '#2b8a3e', 'gF': '#37b24d'}
HZ_DEFAULT = {'A': 30, 'B': 30, 'C': 25, 'D': 20, 'E': 20, 'F': 15}
WINDOW_H = 6            # so lange nach Spielende wird alle 15 Minuten nachgesehen
REPORT_DAYS = 14        # fehlende Spielberichte werden so lange nach dem Spiel noch gesucht
MAX_REPORTS = 80        # höchstens so viele Spielberichte pro Lauf abrufen

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


_CACHE = {}


def fetch(url):
    if url in _CACHE:
        return _CACHE[url]
    _CACHE[url] = _fetch(url)
    return _CACHE[url]


def _fetch(url):
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


# ---------------------------------------------------------------- Mannschaften finden
ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5}


def classify(label):
    """'Männer II' -> herren2, 'weibliche C-Jugend' -> wC usw."""
    t = label.strip()
    m = re.fullmatch(r'(Männer|Frauen)(?:\s+([IVX]+|\d+))?', t, re.I)
    if m:
        n = ROMAN.get((m.group(2) or 'I').upper(), int(m.group(2)) if (m.group(2) or '').isdigit() else 1)
        herren = m.group(1).lower() == 'männer'
        return {'id': ('herren' if herren else 'damen') + str(n), 'label': f'{n}. ' + ('Herren' if herren else 'Damen'),
                'bar': 'Herren' if herren else 'Damen', 'barSub': f'{n}. Mannschaft', 'gruppe': 'Erwachsene',
                'klasse': 'Männer' if herren else 'Frauen', 'alter': 'A', 'nr': n, 'sort': (0, 0 if herren else 1, n)}
    m = re.fullmatch(r'(männliche|weibliche|gemischte)\s+([A-F])-Jugend(?:\s+([IVX]+|\d+))?', t, re.I)
    if m:
        g, a = m.group(1).lower(), m.group(2).upper()
        n = ROMAN.get((m.group(3) or 'I').upper(), int(m.group(3)) if (m.group(3) or '').isdigit() else 1)
        gk = {'männliche': 'm', 'weibliche': 'w', 'gemischte': 'g'}[g]
        gw = {'m': 'männlich', 'w': 'weiblich', 'g': 'gemischt'}[gk]
        suf = '' if n == 1 else f' {n}'
        return {'id': gk + a + ('' if n == 1 else str(n)), 'label': f'{g.capitalize()} {a}-Jugend' + ('' if n == 1 else f' {n}'),
                'bar': f'{a}-Jugend', 'barSub': gw + suf, 'gruppe': {'m': 'Männliche Jugend', 'w': 'Weibliche Jugend', 'g': 'Gemischte Jugend'}[gk],
                'klasse': f'{g.capitalize()} {a}-Jugend', 'alter': a, 'nr': n, 'sort': ({'w': 1, 'm': 2, 'g': 3}[gk], 'ABCDEF'.index(a), n)}
    return None


def discover():
    """Liest die Mannschaftslisten beider Vereine auf nuLiga."""
    found = {}
    for club in CLUBS:
        s = soup_of(fetch(f'{BASE}clubTeams?club={club["club"]}'))
        region = saison = ''
        for t in s.find_all('table', class_='result-set'):
            for tr in own_rows(t):
                h2 = tr.find('h2')
                if h2:
                    m = re.match(r'(.+?)\s+(\d{4}/\d\d)$', txt(h2))
                    region, saison = (m.group(1), m.group(2)) if m else (txt(h2), '')
                    continue
                tds = tr.find_all('td', recursive=False)
                a = tr.find('a', href=re.compile('groupPage'))
                if len(tds) < 2 or not a:
                    continue
                info = classify(txt(tds[0]))
                if not info:
                    log(f'  unbekannte Mannschaft übersprungen: {txt(tds[0])}')
                    continue
                m = re.search(r'championship=([^&]+)&group=(\d+)', a['href'])
                if not m:
                    continue
                liga = txt(a)
                st = {'liga': liga, 'ch': m.group(1), 'group': m.group(2)}
                if info['id'] in found:
                    # mehrere Staffeln (z. B. Vorrunde und folgende Runde): alle merken, Auswahl in main()
                    if 'pokal' not in liga.lower():
                        f = found[info['id']]
                        f['alts'] = f.get('alts', []) + [st]
                        if 'pokal' in f['liga'].lower():
                            f.update(st)
                    continue
                found[info['id']] = dict(info, club=club['club'], verein=club['name'], kurz=club['kurz'],
                                         region=region, saison=saison, **st)
                found[info['id']]['alts'] = [st] if 'pokal' not in liga.lower() else []
    if not found:
        raise ValueError('keine Mannschaften gefunden')
    return sorted(found.values(), key=lambda c: c['sort'])


def staffel_info(st, now):
    """Spielplan einer Staffel kurz auswerten: nächstes offenes Spiel, letztes gespieltes."""
    try:
        games = parse_meetings(fetch(f'{BASE}groupPage?displayTyp=vorrunde&displayDetail=meetings&championship={st["ch"]}&group={st["group"]}'))
    except Exception:  # noqa: BLE001  (z. B. noch kein Spielplan veröffentlicht)
        return None
    today = now.date()
    nxt, last = [], []
    for g in games:
        try:
            d = dt.date.fromisoformat(g.get('datum') or '')
        except ValueError:
            d = None
        if g.get('ergebnis'):
            if d:
                last.append(d)
        elif d is None:
            nxt.append(dt.date.max)          # Termin offen
        elif d >= today - dt.timedelta(days=3):
            nxt.append(d)
    return {'spiele': len(games), 'next': min(nxt) if nxt else None, 'last': max(last) if last else None}


def choose_staffel(alts, known_group, now):
    """Bei mehreren Staffeln die nehmen, in der gerade gespielt wird: die mit dem frühesten
    offenen Spiel; ist überall fertig, die zuletzt bespielte. Staffeln ohne Spielplan zählen nicht."""
    infos = [(st, staffel_info(st, now)) for st in alts]
    for st, i in infos:
        log(f'    {st["liga"]}: ' + ('kein Spielplan' if not i else f'{i["spiele"]} Spiele, nächstes {i["next"]}, letztes {i["last"]}'))
    ok = [(st, i) for st, i in infos if i and i['spiele']]
    if not ok:
        return next((st for st in alts if st['group'] == known_group), alts[0])
    active = [(st, i) for st, i in ok if i['next']]
    if active:
        return min(active, key=lambda x: (x[1]['next'], x[0]['group'] != known_group))[0]
    return max(ok, key=lambda x: (x[1]['last'] or dt.date.min, x[0]['group'] == known_group))[0]


def team_links(c):
    q = f'championship={c["ch"]}&group={c["group"]}'
    return [
        {'titel': 'Tabelle & aktuelle Spiele', 'info': 'nuLiga · ' + c['liga'], 'url': f'{BASE}groupPage?{q}'},
        {'titel': 'Kompletter Spielplan', 'info': 'nuLiga · alle Spiele der Staffel', 'url': f'{BASE}groupPage?displayTyp=vorrunde&displayDetail=meetings&{q}'},
        {'titel': 'Alle Mannschaften: ' + c['verein'], 'info': 'nuLiga', 'url': f'{BASE}clubTeams?club={c["club"]}'},
        {'titel': 'Handball beim ESV', 'info': 'eickener-sv.de · Mannschaften und Training', 'url': WEB},
    ]


def skeleton(c):
    return {'stand': '', 'saison': c['saison'], 'team': '', 'kurz': c['kurz'], 'mannschaft': c['label'], 'verein': c['verein'],
            'altersklasse': c['klasse'], 'liga': c['liga'], 'region': c['region'], 'halbzeitMin': HZ_DEFAULT.get(c['alter'], 30),
            'hinweis': 'Sortiert nach Punkten.', 'links': team_links(c),
            'berichtUrl': f'{BASE}groupMeetingReport?championship={c["ch"]}&group={c["group"]}&meeting=',
            'berichtPdf': PDF, 'tabelle': [], 'spiele': [], 'hallen': {}}


def pick_team(names, c):
    """Name der eigenen Mannschaft in Tabelle/Spielplan, z. B. 'Eickener SpVg II'."""
    base = c['verein']
    roman = {v: k for k, v in ROMAN.items()}.get(c['nr'], str(c['nr']))
    want = [base] if c['nr'] == 1 else [f'{base} {roman}', f'{base} {c["nr"]}']
    for w in want:
        if w in names:
            return w
    cands = sorted(n for n in names if n.startswith(base) or 'Eicken' in n)
    if c['nr'] == 1:
        plain = [n for n in cands if not re.search(r'\s(II|III|IV|\d)$', n)]
        if plain:
            return plain[0]
    else:
        suf = [n for n in cands if n.endswith(' ' + roman) or n.endswith(' ' + str(c['nr']))]
        if suf:
            return suf[0]
    return cands[0] if cands else ''


# ---------------------------------------------------------------- Ablauf
def now_berlin():
    return dt.datetime.now(BERLIN)


def kickoff(g):
    if not g.get('datum') or not g.get('zeit'):
        return None
    return dt.datetime.strptime(g['datum'] + ' ' + g['zeit'], '%Y-%m-%d %H:%M').replace(tzinfo=BERLIN)


def wants_report(D, g):
    return D.get('team') in (g['heim'], g['gast'])


def needs_check(D, now):
    """Gibt es ein Spiel, das gerade vorbei ist und bei dem Ergebnis oder Bericht noch fehlen?"""
    hz = D.get('halbzeitMin', 30)
    for g in D.get('spiele', []):
        k = kickoff(g)
        if not k:
            continue
        start = k + dt.timedelta(minutes=2 * hz + 10)
        if start <= now <= start + dt.timedelta(hours=WINDOW_H):
            if not g.get('ergebnis') or (wants_report(D, g) and not g.get('bericht')):
                return f'Spiel {g["nr"]} {g["heim"]} – {g["gast"]}'
    return None


def infer_hz(D, default):
    """Halbzeitdauer aus den Spielberichten ableiten: Bei welcher Spielzeit passt der Spielstand
    zum offiziellen Halbzeitstand? (Mehrheit über alle Berichte, sonst Altersklassen-Standard)"""
    votes = {}
    for g in D.get('spiele', []):
        b = g.get('bericht')
        if not b or not b.get('verlauf') or not b.get('halbzeit'):
            continue
        times = [(int(v[:2]) * 60 + int(v[3:5]), v[5]) for v in b['verlauf']]
        for hz in (15, 20, 25, 30):
            h = sum(1 for t, side in times if t <= hz * 60 and side == 'H')
            a = sum(1 for t, side in times if t <= hz * 60 and side == 'G')
            ok_end = all(t <= 2 * hz * 60 for t, _ in times)
            if f'{h}:{a}' == b['halbzeit'] and ok_end:
                votes[hz] = votes.get(hz, 0) + 1
                break
    return max(votes, key=lambda k: (votes[k], k)) if votes else default


def update_team(c, D, now, pool):
    url_q = f'championship={c["ch"]}&group={c["group"]}'
    games = parse_meetings(fetch(f'{BASE}groupPage?displayTyp=vorrunde&displayDetail=meetings&{url_q}'))
    tabelle = parse_table(fetch(f'{BASE}groupPage?{url_q}'))
    old = {str(g['nr']): g for g in D.get('spiele', [])}
    if old and len(games) < 0.8 * len(old):
        raise ValueError(f'nur {len(games)} statt {len(old)} Spiele gefunden – Abbruch')
    names = {r['team'] for r in tabelle} | {g['heim'] for g in games} | {g['gast'] for g in games}
    team = pick_team(names, c)
    if not team:
        raise ValueError('eigene Mannschaft in Tabelle/Spielplan nicht gefunden')
    points_only = not any('tore' in r for r in tabelle)
    N = dict(D)
    N['team'] = team
    hallen = dict(D.get('hallen', {}))
    fetched = 0
    neu_erg, neu_ber = [], []
    for g in games:
        court = g.pop('_court', None)
        if g['halle'] and g['halle'] not in hallen:
            if g['halle'] in pool:
                hallen[g['halle']] = pool[g['halle']]
            elif court:
                try:
                    hallen[g['halle']] = parse_court(fetch(court if court.startswith('http') else HOST + court))
                    pool[g['halle']] = hallen[g['halle']]
                    log(f'  neue Halle {g["halle"]}: {hallen[g["halle"]]["name"]}')
                except Exception as e:  # noqa: BLE001
                    log(f'  Halle {g["halle"]} nicht lesbar: {e}')
        o = old.get(str(g['nr']), {})
        if g['ergebnis'] and g['ergebnis'] != o.get('ergebnis') and old:
            neu_erg.append(g)
        if not g['ergebnis']:
            continue
        ob = o.get('bericht')
        if ob and o.get('ergebnis') == g['ergebnis'] and ob.get('meeting') == g.get('meeting'):
            g['bericht'] = ob
            continue
        if not wants_report(N, g) or not g.get('meeting'):
            continue
        k = kickoff(g)
        if k and now - k > dt.timedelta(days=REPORT_DAYS) and not ob and o.get('ergebnis') == g['ergebnis']:
            continue  # alter Bericht ohne Verlauf: nicht jedes Mal neu versuchen
        if fetched >= MAX_REPORTS:
            continue
        fetched += 1
        try:
            b = parse_report(fetch(f'{BASE}groupMeetingReport?meeting={g["meeting"]}&{url_q}'), D.get('halbzeitMin', 30))
            if not points_only and b['tore'] != g['ergebnis']:
                raise ValueError(f'Bericht {b["tore"]} passt nicht zum Ergebnis {g["ergebnis"]}')
            b['meeting'] = g['meeting']
            g['bericht'] = b
            if old:
                neu_ber.append(g)
        except Exception as e:  # noqa: BLE001
            log(f'  Spielbericht {g["nr"]} (meeting {g["meeting"]}) übersprungen: {e}')
            if ob:
                g['bericht'] = ob
    N['tabelle'] = tabelle
    N['spiele'] = games
    N['hallen'] = hallen
    N['halbzeitMin'] = infer_hz(N, HZ_DEFAULT.get(c.get('alter', 'A'), 30))
    if points_only and 'nur Punkte' not in N.get('hinweis', ''):
        N['hinweis'] = (N.get('hinweis', '') + ' In dieser Altersklasse zeigt nuLiga nur Punkte (2:0 = Sieg), keine Tore.').strip()
    return N, neu_erg, neu_ber


def index_entry(c, D):
    me = next((r for r in D.get('tabelle', []) if r['team'] == D.get('team')), None)
    e = {k: c[k] for k in ('id', 'label', 'bar', 'barSub', 'gruppe', 'kurz', 'verein', 'liga', 'ch', 'group', 'club',
                           'klasse', 'alter', 'nr', 'region', 'saison')}
    e.update({'file': f'data/{c["id"]}.json', 'dot': DOTS.get(c['id'], '#868e96'), 'hz': D.get('halbzeitMin', 30),
              'team': D.get('team', ''), 'platz': me['platz'] if me else None, 'von': len(D.get('tabelle', [])),
              'punkte': me['punkte'] if me else ''})
    return e


def load_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write('\n')


GEO = os.path.join(DATA, 'hallen-geo.json')
GEO_UA = 'ESV-Handball-App/1.0 (+https://github.com/Edgar-P-1983/handball-eicken)'
GEO_MAX = 80          # höchstens so viele neue Hallen pro Lauf
GEO_RETRY_DAYS = 7    # nicht gefundene Adressen erst nach so vielen Tagen erneut versuchen


def _geo_get(url):
    req = urllib.request.Request(url, headers={'User-Agent': GEO_UA, 'Accept': 'application/json', 'Accept-Language': 'de'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode('utf-8', 'replace'))
    time.sleep(1.2)  # Nominatim: höchstens eine Anfrage pro Sekunde
    return data


def geocode_hall(h):
    """Koordinaten einer Halle: erst genau (Straße + PLZ + Ort), dann gröber. Liefert dict oder None."""
    from urllib.parse import urlencode
    adr = (h.get('adresse') or '').strip()
    name = (h.get('name') or '').strip()
    m = re.match(r'^(.*?),?\s*(\d{5})\s+(.+)$', adr)
    street, plz, city = (m.group(1).strip(' ,'), m.group(2), m.group(3).strip()) if m else ('', '', '')
    nom = 'https://nominatim.openstreetmap.org/search?'
    tries = []
    if street and plz:
        tries.append(('nominatim', nom + urlencode({'street': street, 'postalcode': plz, 'city': city, 'country': 'de', 'format': 'jsonv2', 'limit': 1}), True))
    if adr:
        tries.append(('nominatim', nom + urlencode({'q': adr, 'countrycodes': 'de', 'format': 'jsonv2', 'limit': 1}), True))
    if name and (city or adr):
        tries.append(('nominatim', nom + urlencode({'q': name + ', ' + (city or adr), 'countrycodes': 'de', 'format': 'jsonv2', 'limit': 1}), True))
    if adr:
        tries.append(('photon', 'https://photon.komoot.io/api/?' + urlencode({'q': adr, 'limit': 1, 'lang': 'de'}), True))
    if plz:
        tries.append(('nominatim', nom + urlencode({'postalcode': plz, 'city': city, 'country': 'de', 'format': 'jsonv2', 'limit': 1}), False))
    for quelle, url, genau in tries:
        try:
            j = _geo_get(url)
        except Exception as e:  # noqa: BLE001
            log(f'  Geocoder {quelle}: {e}')
            continue
        lat = lon = None
        if quelle == 'photon':
            f = (j.get('features') or [None])[0]
            if f and f.get('geometry') and (f.get('properties') or {}).get('countrycode', 'DE') == 'DE':
                lon, lat = f['geometry']['coordinates'][:2]
        elif isinstance(j, list) and j:
            lat, lon = j[0].get('lat'), j[0].get('lon')
        if lat is not None and lon is not None:
            return {'lat': round(float(lat), 5), 'lon': round(float(lon), 5), 'genau': genau}
    return None


def update_geo(cfgs, now):
    """Hallen-Koordinaten für die Entfernungs-/Fahrzeitanzeige der App (data/hallen-geo.json)."""
    geo = load_json(GEO, {})
    hallen = {}
    for c in cfgs:
        hallen.update(load_json(os.path.join(DATA, c['id'] + '.json'), {}).get('hallen', {}))
    today = now.date()
    done = 0
    changed = False
    for hid, h in sorted(hallen.items()):
        adr = h.get('adresse') or ''
        old = geo.get(hid)
        if old and old.get('adresse') == adr:
            if 'lat' in old:
                continue
            try:
                if (today - dt.date.fromisoformat(old.get('versuch', '2000-01-01'))).days < GEO_RETRY_DAYS:
                    continue
            except ValueError:
                pass
        if done >= GEO_MAX:
            break
        done += 1
        pos = geocode_hall(h)
        if pos:
            geo[hid] = {'adresse': adr, **pos}
            log(f'  Halle {hid} ({h.get("name")}): {pos["lat"]}, {pos["lon"]}' + ('' if pos['genau'] else ' (nur Ort)'))
        else:
            geo[hid] = {'adresse': adr, 'versuch': today.isoformat()}
            log(f'  Halle {hid} ({h.get("name")}): keine Koordinaten gefunden')
        changed = True
    if changed:
        write_json(GEO, dict(sorted(geo.items())))
    return changed


APP_URL = 'https://edgar-p-1983.github.io/handball-eicken/'
PUSH_MAX_AGE_DAYS = 7  # ältere Ergebnisse (z. B. spät nachgetragen) nicht mehr per Push melden


def kurzname(c):
    """„w. E-Jugend“, „m. C-Jugend“, „1. Herren“ – passt in die Titelzeile einer Mitteilung."""
    sub = (c.get('barSub') or '').strip()
    for g in ('weiblich', 'männlich'):
        if sub.lower().startswith(g) and c.get('bar'):
            return f'{g[0]}. {c["bar"]}{sub[len(g):]}'
    return c.get('label') or c.get('id', '')


def push_event(c, N, g, now, alter_pruefen=True):
    """Mitteilung für ein Ergebnis der eigenen Mannschaft, sonst None.
    Kurz, damit im iPhone-Banner nichts abgeschnitten wird (Ergebnis aus unserer Sicht):
      🏆 Sieg 24:9
      w. E-Jugend bei HSG Grönegau-Melle"""
    me = N.get('team')
    if not me or me not in (g.get('heim'), g.get('gast')):
        return None
    m = re.match(r'^\s*(\d+)\s*:\s*(\d+)', g.get('ergebnis') or '')
    if not m:
        return None
    try:
        day = dt.date.fromisoformat(g.get('datum') or '')
    except ValueError:
        return None
    if alter_pruefen and ((now.date() - day).days > PUSH_MAX_AGE_DAYS or day > now.date() + dt.timedelta(days=1)):
        return None
    heim = g['heim'] == me
    eigen = lambda sc: ':'.join(sc.split(':') if heim else sc.split(':')[::-1])
    h, a = int(m.group(1)), int(m.group(2))
    mine, other = (h, a) if heim else (a, h)
    erg = 'S' if mine > other else 'N' if mine < other else 'U'
    points_only = not any('tore' in r for r in N.get('tabelle', []))
    b = g.get('bericht') or {}
    tore = b.get('tore') if re.fullmatch(r'\d+:\d+', b.get('tore') or '') else ''
    if not points_only:
        tore = f'{h}:{a}'
    emo, wort = {'S': ('🏆', 'Sieg'), 'N': ('😔', 'Niederlage'), 'U': ('🤝', 'Unentschieden')}[erg]
    stand = f'{wort} {eigen(tore)}' if tore else wort
    gegner = re.sub(r'\s+e\.\s?V\.', '', g['gast'] if heim else g['heim']).strip()
    kurz = kurzname(c)
    # Nur zwei Zeilen: Titel mit Sieg/Niederlage und Ergebnis, darunter Mannschaft und Gegner
    zeile = f'{kurz} {"gegen" if heim else "bei"} {gegner}'
    return {'team': c['id'], 'nr': str(g['nr']), 'title': f'{emo} {stand}', 'body': zeile,
            'kurz': f'{emo} {stand} · {zeile}',
            'tag': f'erg-{c["id"]}-{g["nr"]}', 'url': f'{APP_URL}?t={c["id"]}&g={g["nr"]}'}


def letzte_meldung(c, N, now):
    """Mitteilung zum letzten Ergebnis der Mannschaft – für die Test-Mitteilung in der App."""
    me = N.get('team')
    own = [g for g in N.get('spiele', []) if g.get('ergebnis') and me in (g.get('heim'), g.get('gast'))]
    own.sort(key=lambda g: (g.get('datum') or '', g.get('zeit') or ''))
    for g in reversed(own):
        ev = push_event(c, N, g, now, alter_pruefen=False)
        if ev:
            return {k: ev[k] for k in ('title', 'body', 'url', 'tag')}
    return None


FENSTER = os.path.join(DATA, 'fenster.json')


def fenster(cfgs, now):
    """Zeitfenster nach Spielende der eigenen Mannschaften, solange Ergebnis oder Spielbericht fehlen.
    Der Cloudflare-Dienst liest die Datei alle 5 Minuten und stößt in diesen Fenstern die Automatik an."""
    out = []
    for c in cfgs:
        D = load_json(os.path.join(DATA, c['id'] + '.json'), {}) or {}
        hz, me = D.get('halbzeitMin', 30), D.get('team')
        for g in D.get('spiele', []):
            if not me or me not in (g.get('heim'), g.get('gast')) or (g.get('ergebnis') and g.get('bericht')):
                continue
            k = kickoff(g)
            if not k:
                continue
            von = k + dt.timedelta(minutes=2 * hz + 10)
            bis = von + dt.timedelta(hours=WINDOW_H)
            if bis < now or k > now + dt.timedelta(days=8):
                continue
            out.append({'spiel': f'{c["id"]}:{g["nr"]}', 'von': von.isoformat(), 'bis': bis.isoformat()})
    out.sort(key=lambda w: (w['von'], w['spiel']))
    return {'fenster': out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['auto', 'daily', 'force'], default='auto')
    args = ap.parse_args()
    now = now_berlin()
    stamp = now.replace(second=0, microsecond=0).isoformat()
    os.makedirs(DATA, exist_ok=True)
    index = load_json(INDEX, {'stand': '', 'teams': []})
    cfgs = index['teams']
    if args.mode != 'auto':
        try:
            found = discover()
            known = {c['id']: c for c in cfgs}
            for c in found:
                alts = c.pop('alts', [])
                if len(alts) > 1:
                    log(f'{c["id"]}: {len(alts)} Staffeln')
                    c.update(choose_staffel(alts, (known.get(c['id']) or {}).get('group'), now))
            for c in found:
                k = known.get(c['id'])
                if k and (k['group'] != c['group']):
                    log(f'{c["id"]}: neue Staffel {c["liga"]} (bisher {k["liga"]})')
            if {c['id'] for c in found} != set(known):
                log('Mannschaften: ' + ', '.join(c['id'] for c in found))
            cfgs = found
        except Exception as e:  # noqa: BLE001
            log(f'Mannschaftsliste nicht lesbar, nutze bekannte: {e}')
    pool = {}
    for c in cfgs:
        pool.update(load_json(os.path.join(DATA, c['id'] + '.json'), {}).get('hallen', {}))
    changed_files, summary, errors, entries, push = [], [], [], [], []
    known_cfg = {k['id']: k for k in index['teams']}
    for c in cfgs:
        path = os.path.join(DATA, c['id'] + '.json')
        D = load_json(path)
        D_old = D
        if D is None or D.get('links', [{}])[0].get('url', '').find(c['group']) < 0 and args.mode != 'auto':
            fresh = skeleton(c)
            if D is not None:  # neue Staffel: Hallen behalten
                fresh['hallen'] = D.get('hallen', {})
            D = fresh
        if args.mode == 'auto':
            why = needs_check(D, now)
            if not why:
                entries.append(index_entry(c, D))
                continue
            log(f'{c["id"]}: Abruf wegen {why}')
        else:
            log(f'{c["id"]}: Komplettabgleich ({args.mode})')
        try:
            N, neu_erg, neu_ber = update_team(c, D, now, pool)
        except Exception as e:  # noqa: BLE001
            errors.append(f'{c["id"]}: {e}')
            log(f'  FEHLER: {e}')
            if D_old is not None and D is not D_old and c['id'] in known_cfg:
                log('  Staffelwechsel nicht möglich, alte Daten bleiben')
                entries.append(index_entry(known_cfg[c['id']], D_old))
                continue
            if not os.path.exists(path):  # neue Mannschaft: leere Datei, damit die App sie anzeigen kann
                D['stand'] = stamp
                write_json(path, D)
                changed_files.append(path)
            entries.append(index_entry(c, D))
            continue
        lm = letzte_meldung(c, N, now)
        if lm:
            N['letzteMeldung'] = lm
        else:
            N.pop('letzteMeldung', None)
        strip = lambda x: {k: v for k, v in x.items() if k != 'stand'}
        if strip(N) != strip(D) or args.mode != 'auto':
            N['stand'] = stamp
            write_json(path, N)
            changed_files.append(path)
        entries.append(index_entry(c, N))
        for g in neu_erg:
            summary.append(f'{c["label"]}: Ergebnis {g["heim"]} – {g["gast"]} {g["ergebnis"]}')
            ev = push_event(c, N, g, now)
            if ev:
                push.append(ev)
        for g in neu_ber:
            summary.append(f'{c["label"]}: Spielbericht {g["heim"]} – {g["gast"]}')
    if args.mode != 'auto':
        try:
            if update_geo(cfgs, now):
                changed_files.append(GEO)
        except Exception as e:  # noqa: BLE001
            log(f'Hallen-Koordinaten: {e}')
    fw = fenster(cfgs, now)
    if fw != load_json(FENSTER, None):
        write_json(FENSTER, fw)
        changed_files.append(FENSTER)
    new_index = {'stand': index.get('stand', ''), 'teams': entries}
    if new_index['teams'] != index.get('teams') or changed_files:
        new_index['stand'] = stamp
        write_json(INDEX, new_index)
        changed_files.append(INDEX)
    if not changed_files and not errors:
        log('Nichts zu tun.' if args.mode == 'auto' else 'Keine Änderungen.')
    msg = 'Daten aktualisiert' if not summary else ('Neu: ' + summary[0] if len(summary) == 1 else f'{len(summary)} Neuigkeiten')
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a') as f:
            f.write(f'changed={"true" if changed_files else "false"}\n')
            f.write(f'message={msg}\n')
            f.write('details<<ENDE\n' + '\n'.join(summary + errors) + '\nENDE\n')
            f.write('push=' + json.dumps(push[:30], ensure_ascii=False, separators=(',', ':')) + '\n')
    if push:
        log('Mitteilungen: ' + '; '.join(e['title'] + ' – ' + e['body'] for e in push))
    for e in errors:
        print(f'::warning::{e}')
    # Nur abbrechen, wenn gar nichts ging – sonst werden die übrigen Mannschaften gespeichert
    if errors and args.mode != 'auto' and len(errors) >= len(cfgs):
        sys.exit(1)


if __name__ == '__main__':
    main()
