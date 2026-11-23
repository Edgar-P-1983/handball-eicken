"""Einmaliger Test: ruft nuLiga-Seiten ab und speichert den Seitenaufbau.

Spielberichte werden anonymisiert gespeichert (Namen werden durch X ersetzt),
damit keine Spielernamen im öffentlichen Repository landen.
"""
import os, re, sys, time, urllib.request
from bs4 import BeautifulSoup, Comment, NavigableString

BASE = 'https://hvnb-handball.liga.nu/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/'
TEAMS = {
    'herren': ('HVNB+26%2F27', '489364', ['8280361', '8280439']),
    'wjc': ('HRWN+26%2F27', '505361', ['8267627']),
    'mje': ('HRWN+26%2F27', '506735', ['8266118']),
}
OUT = sys.argv[1] if len(sys.argv) > 1 else 'debug'
os.makedirs(OUT, exist_ok=True)

KEEP = set('''spielbericht spielverlauf spielstand heim gast heimmannschaft gastmannschaft tor tore 7m 7m-tor
7m-tore siebenmeter zeitstrafe zeitstrafen 2-min 2min 2-minuten minuten minute min verwarnung verwarnungen
disqualifikation disqualifikationen auszeit auszeiten team-timeout timeout halbzeit halbzeitstand endstand
ergebnis spielzeit zeit nr name spieler spielerin mannschaft mannschaften offizielle offizieller schiedsrichter
zeitnehmer sekretär datum uhrzeit halle staffel liga spiel spielnummer bemerkung bemerkungen gelb rot blau
aktion ereignis strafe strafen versuche würfe geworfen verworfen gehalten gesamt summe und der die das im in von
bis mit am um für gegen nicht kein keine ja nein hz erste zweite hälfte statistik aufstellung kader torwart
info details pdf download zurück seite drucken genehmigt freigegeben vorläufig unbestätigt bestätigt
staffelleiter spieltag saison punkte sieg niederlage unentschieden stand protokoll technischer delegierter
gehaltener verworfener siebenmeterwurf 7-meter 7m-wurf 7-m spielort anwurf heimspiel auswärts
schiedsrichterin mannschaftsverantwortlicher mannschaftsverantwortliche vorname nachname trikot rückennummer
mo di mi do fr sa so montag dienstag mittwoch donnerstag freitag samstag sonntag uhr tabelle spielplan
nichtangetreten wertung gewertet ersatz verlegt'''.split())


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (handball-eicken update; GitHub Actions)'})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read()
        ct = r.headers.get('Content-Type', '')
    time.sleep(1.5)
    return data, ct


def strip(soup):
    for t in soup(['script', 'style', 'noscript', 'iframe', 'svg', 'img']):
        t.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for a in soup.find_all(href=True):
        if re.search(r'person|player|portrait|mailto', a['href'], re.I):
            a['href'] = '#entfernt'
    for t in soup.find_all(string=re.compile('@')):
        t.replace_with('[E-Mail entfernt]')


def mask_token(tok):
    core = tok.strip('.:,;()[]"\'')
    if not core or re.fullmatch(r'[\d:.,()\-–/+%#*]+', core) or len(core) <= 2 or core.lower() in KEEP:
        return tok
    return 'X' * 3


def anonymise(soup):
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString) or not node.strip():
            continue
        if node.find_parent(['th']):
            continue
        new = re.sub(r'\S+', lambda m: mask_token(m.group(0)), str(node))
        node.replace_with(new)


def text_dump(soup):
    lines = []
    for i, tab in enumerate(soup.find_all('table')):
        lines.append(f'=== TABLE {i} class={tab.get("class")} id={tab.get("id")}')
        for tr in tab.find_all('tr'):
            cells = [re.sub(r'\s+', ' ', c.get_text(' ', strip=True)) for c in tr.find_all(['th', 'td'])]
            lines.append(' | '.join(cells))
    return '\n'.join(lines)


def save(name, data, ct, anon=False):
    soup = BeautifulSoup(data, 'html.parser')
    strip(soup)
    if anon:
        anonymise(soup)
    body = soup.body or soup
    open(f'{OUT}/{name}.html', 'w', encoding='utf-8').write(f'<!-- Content-Type: {ct} -->\n' + body.prettify())
    open(f'{OUT}/{name}.txt', 'w', encoding='utf-8').write(text_dump(soup))
    return soup


log = []
clubs = {'esv': '32392', 'jsg': '32655'}
for key, club in clubs.items():
    for kind, url in {
        'clubteams': f'{BASE}clubTeams?club={club}',
        'clubinfo': f'{BASE}clubInfoDisplay?club={club}',
    }.items():
        try:
            data, ct = get(url)
            soup = save(f'{key}_{kind}', data, ct)
            log.append(f'OK {key}_{kind} {len(data)} {ct}')
            if kind == 'clubteams':
                links = sorted({a['href'] for a in soup.find_all('a', href=True) if 'teamPortrait' in a['href'] or 'groupPage' in a['href']})
                open(f'{OUT}/{key}_links.txt', 'w').write('\n'.join(links))
                tp = [l for l in links if 'teamPortrait' in l][:1]
                for i, l in enumerate(tp):
                    u = l if l.startswith('http') else 'https://hvnb-handball.liga.nu' + l
                    d2, c2 = get(u)
                    save(f'{key}_teamportrait{i}', d2, c2, anon=True)
                    log.append(f'OK {key}_teamportrait{i} {u}')
        except Exception as e:
            log.append(f'FEHLER {key}_{kind}: {e!r}')
open(f'{OUT}/log.txt', 'w').write('\n'.join(log) + '\n')
print('\n'.join(log))
