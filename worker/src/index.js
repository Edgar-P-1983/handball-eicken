// ESV Handball – kleiner Dienst bei Cloudflare
//
//   GET /health                          → {ok, ors}
//   GET /route?from=lat,lon&to=lat,lon   → {km, min}        Fahrstrecke und Fahrzeit mit dem Auto
//   GET /geocode?q=Text                  → {results:[{label, lat, lon}]}
//   GET  /push/key                       → {key}            öffentlicher Schlüssel für Mitteilungen
//   POST /push/subscribe {sub, teams}    → Mitteilungen für diese Mannschaften (leere Liste = abmelden)
//   POST /push/unsubscribe {endpoint}    → abmelden
//   POST /push/test {endpoint, title?, body?, url?} → Test-Mitteilung an genau dieses Gerät
//                                          (die App schickt das letzte Ergebnis als Beispiel mit)
//   POST /notify {events, after}         → nur für die GitHub-Automatik (Bearer NOTIFY_KEY)
//   Zeitplan alle 5 Minuten              → stößt die GitHub-Automatik an, wenn gerade ein Spiel der
//                                          eigenen Mannschaften zu Ende ist (data/fenster.json)
//
// Die Fahrzeit kommt von openrouteservice (HeiGIT, Heidelberg). Der Schlüssel dafür liegt nur hier
// als Secret und ist in der App nicht sichtbar. Antworten gibt es nur für die App selbst (Origin).
// Für die Fahrzeit wird nichts gespeichert und nichts protokolliert. Für Mitteilungen speichert der
// Dienst je Gerät nur die anonyme Empfangsadresse des Push-Dienstes (Apple, Google, Mozilla …),
// deren Schlüssel und die gewählten Mannschaften – beim Abschalten wird alles gelöscht.

import {vapid, sendPush} from './push.js';

const ORIGINS = ['https://edgar-p-1983.github.io'];
const ORS = 'https://api.heigit.org/openrouteservice/v2/directions/driving-car/json';
const PELIAS = 'https://api.heigit.org/pelias/v1/search';
const NOMINATIM = 'https://nominatim.openstreetmap.org/search';
const UA = 'ESV-Handball-App/1.0 (+https://edgar-p-1983.github.io/handball-eicken/)';

class Fehler extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}

function json(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {'Content-Type': 'application/json; charset=utf-8', 'X-Content-Type-Options': 'nosniff', ...headers},
  });
}

// "52.2,8.3" → [52.2, 8.3]; nur Punkte in Mitteleuropa
function punkt(s) {
  const m = /^\s*(-?\d{1,2}(?:\.\d{1,8})?)\s*,\s*(-?\d{1,3}(?:\.\d{1,8})?)\s*$/.exec(s || '');
  if (!m) return null;
  const lat = +m[1], lon = +m[2];
  if (!(lat > 44 && lat < 58 && lon > 1 && lon < 20)) return null;
  return [lat, lon];
}

async function route(url, env) {
  const a = punkt(url.searchParams.get('from')), b = punkt(url.searchParams.get('to'));
  if (!a || !b) throw new Fehler(400, 'koordinaten');
  if (!env.ORS_API_KEY) throw new Fehler(503, 'kein-schluessel');
  const r = await fetch(ORS, {
    method: 'POST',
    headers: {'Authorization': env.ORS_API_KEY, 'Content-Type': 'application/json', 'Accept': 'application/json'},
    body: JSON.stringify({
      coordinates: [[a[1], a[0]], [b[1], b[0]]],
      radiuses: [5000, 5000],      // Punkt darf bis 5 km neben der nächsten Straße liegen
      instructions: false,
      geometry: false,
    }),
  });
  if (!r.ok) {
    let code = '';
    try { const j = await r.json(); code = j && j.error && (j.error.code || j.error) || ''; } catch (e) {}
    throw new Fehler(r.status === 429 ? 429 : 502, 'ors-' + r.status + (code ? '-' + code : ''));
  }
  const j = await r.json();
  const s = j && j.routes && j.routes[0] && j.routes[0].summary;
  if (!s) throw new Fehler(502, 'ors-leer');
  return {km: Math.round((s.distance || 0) / 100) / 10, min: Math.max(1, Math.round((s.duration || 0) / 60))};
}

function kurz(label) {
  return String(label || '').replace(/,\s*(Deutschland|Germany)$/i, '').trim();
}

// Kurze Anzeige aus den Adressteilen von Nominatim: „Name, Straße 1, 49328 Melle (Eicken-Bruche)“
function nomLabel(x) {
  const a = x.address || {};
  const ort = a.city || a.town || a.village || a.municipality || '';
  const teil = [a.suburb, a.city_district, a.quarter, a.hamlet, a.village].find(t => t && t !== ort) || '';
  const str = [a.road || a.pedestrian || a.footway || a.path || '', a.house_number || ''].join(' ').trim();
  const name = x.name && x.name !== str && x.name !== ort && x.name !== teil ? x.name : '';
  const plzOrt = [a.postcode || '', ort].join(' ').trim();
  const parts = [name, str, plzOrt].filter(Boolean);
  if (!parts.length) return kurz(x.display_name);
  if (!str && !name && teil) return teil + ', ' + plzOrt;
  return parts.join(', ') + (teil ? ' (' + teil + ')' : '');
}

async function geocode(url, env) {
  const q = (url.searchParams.get('q') || '').trim().slice(0, 120);
  if (q.length < 3) throw new Fehler(400, 'zu-kurz');
  // OpenStreetMap (Nominatim) kennt Ortsteile wie Eicken-Bruche gut, Pelias (HeiGIT) ergänzt.
  // Beide parallel fragen, Nominatim-Treffer zuerst.
  const nominatim = async () => {
    const u = NOMINATIM + '?' + new URLSearchParams({'q': q, 'format': 'jsonv2', 'addressdetails': '1', 'countrycodes': 'de', 'limit': '4', 'accept-language': 'de'});
    const r = await fetch(u, {headers: {'User-Agent': UA, 'Referer': ORIGINS[0] + '/handball-eicken/', 'Accept': 'application/json'}});
    if (!r.ok) throw new Fehler(502, 'nominatim-' + r.status);
    const j = await r.json();
    return (Array.isArray(j) ? j : []).map(x => ({label: nomLabel(x), lat: +x.lat, lon: +x.lon}));
  };
  const pelias = async () => {
    if (!env.ORS_API_KEY) return [];
    const u = PELIAS + '?' + new URLSearchParams({'text': q, 'boundary.country': 'DEU', 'size': '4', 'lang': 'de'});
    const r = await fetch(u, {headers: {'Authorization': env.ORS_API_KEY, 'Accept': 'application/json'}});
    if (!r.ok) throw new Fehler(502, 'pelias-' + r.status);
    const j = await r.json();
    return (j.features || []).filter(f => f && f.geometry && f.geometry.coordinates).map(f => ({
      label: kurz(f.properties && (f.properties.label || f.properties.name)),
      lat: +f.geometry.coordinates[1], lon: +f.geometry.coordinates[0],
    }));
  };
  const [a, b] = await Promise.allSettled([nominatim(), pelias()]);
  if (a.status === 'rejected' && b.status === 'rejected') throw new Fehler(502, 'geocoder');
  const all = [...(a.status === 'fulfilled' ? a.value : []), ...(b.status === 'fulfilled' ? b.value : [])];
  const seen = new Set(), near = (x, y) => Math.abs(x.lat - y.lat) < 0.002 && Math.abs(x.lon - y.lon) < 0.003;
  const out = [];
  for (const x of all) {
    if (!isFinite(x.lat) || !isFinite(x.lon) || !x.label || seen.has(x.label) || out.some(y => near(x, y))) continue;
    seen.add(x.label); out.push(x);
    if (out.length >= 5) break;
  }
  return {results: out};
}

// ---------- Mitteilungen (Web Push)
const APP = 'https://edgar-p-1983.github.io/handball-eicken/';
const SUBJECT = APP;  // Kontakt-Angabe für die Push-Dienste (VAPID „sub“)
const PUSH_HOSTS = /(^|\.)(fcm\.googleapis\.com|push\.services\.mozilla\.com|push\.apple\.com|notify\.windows\.com)$/;
const BATCH = 6;      // Geräte pro Aufruf (Cloudflare Free: 50 Unteranfragen, 10 ms Rechenzeit)

async function readJson(req, max) {
  const n = +(req.headers.get('Content-Length') || 0);
  if (n > max) throw new Fehler(413, 'zu-gross');
  const t = await req.text();
  if (t.length > max) throw new Fehler(413, 'zu-gross');
  try { return JSON.parse(t || '{}') || {}; } catch (e) { throw new Fehler(400, 'json'); }
}

function b64ok(s, len) {
  if (typeof s !== 'string' || !/^[A-Za-z0-9_-]+=*$/.test(s)) return false;
  return Math.floor(s.replace(/=+$/, '').length * 3 / 4) === len;
}

function cleanSub(sub) {
  if (!sub || typeof sub.endpoint !== 'string' || sub.endpoint.length > 600) return null;
  let u;
  try { u = new URL(sub.endpoint); } catch (e) { return null; }
  if (u.protocol !== 'https:' || !PUSH_HOSTS.test(u.hostname)) return null;
  const k = sub.keys || {};
  if (!b64ok(k.p256dh, 65) || !b64ok(k.auth, 16)) return null;
  return {e: sub.endpoint, p: k.p256dh, a: k.auth};
}

function cleanTeams(t) {
  if (!Array.isArray(t)) return [];
  return [...new Set(t.filter(x => typeof x === 'string' && /^[A-Za-z0-9]{1,16}$/.test(x)))].slice(0, 30);
}

async function subKey(endpoint) {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint)));
  return 's:' + [...h.slice(0, 16)].map(b => b.toString(16).padStart(2, '0')).join('');
}

function needKv(env) { if (!env.PUSH) throw new Fehler(503, 'kein-speicher'); }

async function pushKey(env) {
  const v = await vapid(env);
  if (!v) throw new Fehler(503, 'kein-vapid');
  return {key: v.pub};
}

async function subscribe(req, env) {
  needKv(env);
  const b = await readJson(req, 4000);
  const s = cleanSub(b.sub);
  if (!s) throw new Fehler(400, 'abo');
  const k = await subKey(s.e);
  let teams = cleanTeams(b.teams);
  if (typeof b.old === 'string' && b.old && b.old !== s.e) {
    const ok = await subKey(b.old);
    if (!Array.isArray(b.teams)) { const o = await env.PUSH.get(ok, 'json'); teams = cleanTeams(o && o.t); }
    await env.PUSH.delete(ok);
  }
  if (!teams.length) { await env.PUSH.delete(k); return {ok: true, teams: []}; }
  const rec = {e: s.e, p: s.p, a: s.a, t: teams, d: new Date().toISOString().slice(0, 10)};
  const js = JSON.stringify(rec);
  await env.PUSH.put(k, js, {metadata: js.length <= 1000 ? rec : {gross: 1}});
  return {ok: true, teams};
}

async function unsubscribe(req, env) {
  needKv(env);
  const b = await readJson(req, 2000);
  if (typeof b.endpoint !== 'string' || !b.endpoint) throw new Fehler(400, 'abo');
  await env.PUSH.delete(await subKey(b.endpoint));
  return {ok: true};
}

const LAST_TEST = new Map();
async function testPush(req, env) {
  needKv(env);
  const b = await readJson(req, 2000);
  if (typeof b.endpoint !== 'string' || !b.endpoint) throw new Fehler(400, 'abo');
  const k = await subKey(b.endpoint);
  const now = Date.now();
  if (now - (LAST_TEST.get(k) || 0) < 15000) throw new Fehler(429, 'zu-oft');
  LAST_TEST.set(k, now);
  const rec = await env.PUSH.get(k, 'json');
  if (!rec) throw new Fehler(404, 'nicht-angemeldet');
  const eigen = typeof b.title === 'string' && b.title.trim() && typeof b.body === 'string';
  const st = await sendPush(env, rec, eigen ? {
    title: b.title.slice(0, 90), body: b.body.slice(0, 240), tag: 'test',
    url: typeof b.url === 'string' && b.url.startsWith(APP) ? b.url.slice(0, 300) : APP,
  } : {
    title: '🔔 Test geklappt!', body: 'So kommen Mitteilungen zu neuen Ergebnissen an.', tag: 'test', url: APP,
  }, SUBJECT, {ttl: 600, urgency: 'high'});
  if (st === 404 || st === 410) { await env.PUSH.delete(k); throw new Fehler(410, 'abgelaufen'); }
  if (st < 200 || st >= 300) throw new Fehler(502, 'push-' + st);
  return {ok: true};
}

function cleanEvent(e) {
  if (!e || typeof e !== 'object') return null;
  const team = String(e.team || ''), nr = String(e.nr || '');
  if (!/^[A-Za-z0-9]{1,16}$/.test(team) || !/^[A-Za-z0-9-]{1,24}$/.test(nr)) return null;
  const url = typeof e.url === 'string' && e.url.startsWith(APP) ? e.url.slice(0, 300) : APP;
  return {
    id: team + ':' + nr, team, nr,
    title: String(e.title || 'ESV Handball').slice(0, 90),
    body: String(e.body || '').slice(0, 240),
    kurz: String(e.kurz || e.title || '').slice(0, 160),
    tag: String(e.tag || ('erg-' + team + '-' + nr)).slice(0, 64),
    url,
  };
}

async function listSubs(env) {
  const out = [];
  let cursor;
  for (let i = 0; i < 20; i++) {
    const r = await env.PUSH.list({prefix: 's:', cursor});
    for (const k of r.keys) {
      let m = k.metadata;
      if (!m || !m.e) m = await env.PUSH.get(k.name, 'json');
      if (m && m.e && Array.isArray(m.t)) out.push({k: k.name, e: m.e, p: m.p, a: m.a, t: m.t});
    }
    if (r.list_complete) break;
    cursor = r.cursor;
  }
  return out.sort((x, y) => (x.k < y.k ? -1 : 1));
}

function gleich(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function notify(req, env) {
  if (!env.NOTIFY_KEY || !gleich(req.headers.get('Authorization') || '', 'Bearer ' + env.NOTIFY_KEY)) throw new Fehler(401, 'zugang');
  needKv(env);
  const b = await readJson(req, 30000);
  let events = (Array.isArray(b.events) ? b.events : []).map(cleanEvent).filter(Boolean).slice(0, 40);
  // Weitergeblättert wird über den Namen des letzten Geräts, nicht über eine Position:
  // so verschiebt das Löschen abgelaufener Abos nichts.
  const after = typeof b.after === 'string' ? b.after : '';
  // Beim ersten Aufruf: schon gemeldete Ergebnisse aussortieren (nie doppelt melden)
  if (!after) {
    const recent = (await env.PUSH.get('n:recent', 'json')) || [];
    const seen = new Set(recent);
    events = events.filter(e => !seen.has(e.id) && seen.add(e.id));
    if (events.length) await env.PUSH.put('n:recent', JSON.stringify(recent.concat(events.map(e => e.id)).slice(-1000)));
  }
  if (!events.length) return {events: [], total: 0, sent: 0, failed: 0, removed: 0, next: null};
  const teams = new Set(events.map(e => e.team));
  const subs = (await listSubs(env)).filter(s => s.t.some(t => teams.has(t)) && s.k > after);
  const page = subs.slice(0, BATCH);
  const res = {sent: 0, failed: 0, removed: 0, codes: {}};
  for (let i = 0; i < page.length; i += 6) {
    await Promise.all(page.slice(i, i + 6).map(async s => {
      const mine = events.filter(e => s.t.includes(e.team));
      const payload = mine.length === 1
        ? {title: mine[0].title, body: mine[0].body, tag: mine[0].tag, url: mine[0].url}
        : {title: '🤾 ' + mine.length + ' neue Ergebnisse', body: mine.map(e => e.kurz).join('\n'), tag: 'erg-mehrere', url: APP};
      let st = 0;
      try { st = await sendPush(env, s, payload, SUBJECT); } catch (e) { st = 0; }
      res.codes[st] = (res.codes[st] || 0) + 1;
      if (st >= 200 && st < 300) res.sent++;
      else {
        res.failed++;
        if (st === 404 || st === 410) { try { await env.PUSH.delete(s.k); res.removed++; } catch (e) {} }
      }
    }));
  }
  return {events, rest: subs.length, ...res, next: subs.length > BATCH ? page[page.length - 1].k : null};
}

// ---------- Anstoß der GitHub-Automatik (GitHub startet Zeitpläne oft nur stündlich)
const GH_DISPATCH = 'https://api.github.com/repos/Edgar-P-1983/handball-eicken/actions/workflows/update.yml/dispatches';

async function anstoss(env, now = Date.now()) {
  if (!env.GH_DISPATCH_TOKEN || !env.PUSH) return {aktiv: false, grund: 'nicht-eingerichtet'};
  const r = await fetch(APP + 'data/fenster.json?t=' + Math.floor(now / 60000), {headers: {'Accept': 'application/json'}});
  if (!r.ok) return {aktiv: false, grund: 'fenster-' + r.status};
  const f = await r.json();
  const offen = (Array.isArray(f.fenster) ? f.fenster : [])
    .map(w => ({von: Date.parse(w.von), bis: Date.parse(w.bis), spiel: String(w.spiel || '')}))
    .filter(w => now >= w.von && now <= w.bis);
  if (!offen.length) return {aktiv: false, grund: 'kein-spiel'};
  // In den ersten 90 Minuten nach Spielende alle 10 Minuten, danach alle 30 Minuten
  const seit = Math.min(...offen.map(w => now - w.von));
  const abstand = seit < 90 * 60000 ? 9 * 60000 : 29 * 60000;
  const last = +((await env.PUSH.get('a:last')) || 0);
  if (now - last < abstand) return {aktiv: true, grund: 'gewartet', spiele: offen.map(w => w.spiel)};
  const d = await fetch(GH_DISPATCH, {
    method: 'POST',
    headers: {
      'Authorization': 'Bearer ' + env.GH_DISPATCH_TOKEN, 'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'esv-handball-dienst', 'Content-Type': 'application/json',
    },
    body: JSON.stringify({ref: 'main', inputs: {mode: 'auto'}}),
  });
  const status = {zeit: new Date(now).toISOString(), status: d.status, spiele: offen.map(w => w.spiel)};
  if (d.status !== 204) { try { status.antwort = (await d.text()).slice(0, 200); } catch (e) {} }
  await env.PUSH.put('a:last', String(now), {expirationTtl: 2 * 86400});
  await env.PUSH.put('a:status', JSON.stringify(status), {expirationTtl: 14 * 86400});
  return {aktiv: true, grund: 'angestossen', ...status};
}

async function anstossStatus(req, env) {
  if (!env.NOTIFY_KEY || !gleich(req.headers.get('Authorization') || '', 'Bearer ' + env.NOTIFY_KEY)) throw new Fehler(401, 'zugang');
  needKv(env);
  return {eingerichtet: !!env.GH_DISPATCH_TOKEN, letzter: await env.PUSH.get('a:status', 'json')};
}

const PUSH_ROUTES = {
  'GET /push/key': (req, env) => pushKey(env),
  'POST /push/subscribe': subscribe,
  'POST /push/unsubscribe': unsubscribe,
  'POST /push/test': testPush,
};

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(anstoss(env, event && event.scheduledTime ? event.scheduledTime : Date.now()).catch(() => {}));
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    const origin = req.headers.get('Origin') || '';
    const extra = String(env.EXTRA_ORIGINS || '').split(',').map(s => s.trim()).filter(Boolean);
    const allowed = ORIGINS.includes(origin) || extra.includes(origin);
    const cors = allowed ? {'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'} : {'Vary': 'Origin'};
    if (req.method === 'OPTIONS') {
      return new Response(null, {status: allowed ? 204 : 403, headers: {...cors, 'Access-Control-Allow-Methods': 'GET, POST', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '86400'}});
    }
    if (req.method === 'GET' && url.pathname === '/anstoss') {
      try { return json(await anstossStatus(req, env), 200, {}); } catch (e) { return json({error: e.code || 'fehler'}, e.status || 500, {}); }
    }
    if (req.method === 'POST' && url.pathname === '/notify') {
      try { return json(await notify(req, env), 200, {}); } catch (e) { return json({error: e.code || 'fehler'}, e.status || 500, {}); }
    }
    const pr = PUSH_ROUTES[req.method + ' ' + url.pathname];
    if (pr) {
      if (!allowed) return json({error: 'nur-fuer-die-app'}, 403, cors);
      try { return json(await pr(req, env), 200, {...cors, 'Cache-Control': 'no-store'}); } catch (e) { return json({error: e.code || 'fehler'}, e.status || 500, cors); }
    }
    if (req.method !== 'GET') return json({error: 'methode'}, 405, cors);
    if (url.pathname === '/' || url.pathname === '/health') return json({ok: true, ors: !!env.ORS_API_KEY, push: !!(env.VAPID_JWK && env.PUSH), notify: !!env.NOTIFY_KEY, anstoss: !!env.GH_DISPATCH_TOKEN}, 200, cors);
    if (url.pathname !== '/route' && url.pathname !== '/geocode') return json({error: 'unbekannt'}, 404, cors);
    if (!allowed) return json({error: 'nur-fuer-die-app'}, 403, cors);
    try {
      if (url.pathname === '/route') return json(await route(url, env), 200, {...cors, 'Cache-Control': 'private, max-age=86400'});
      return json(await geocode(url, env), 200, {...cors, 'Cache-Control': 'private, max-age=3600'});
    } catch (e) {
      return json({error: e.code || 'fehler'}, e.status || 502, cors);
    }
  },
};
