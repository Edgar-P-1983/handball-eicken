// ESV Handball – kleiner Dienst bei Cloudflare
//
//   GET /health                          → {ok, ors}
//   GET /route?from=lat,lon&to=lat,lon   → {km, min}        Fahrstrecke und Fahrzeit mit dem Auto
//   GET /geocode?q=Text                  → {results:[{label, lat, lon}]}
//
// Die Fahrzeit kommt von openrouteservice (HeiGIT, Heidelberg). Der Schlüssel dafür liegt nur hier
// als Secret und ist in der App nicht sichtbar. Antworten gibt es nur für die App selbst (Origin).
// Es wird nichts gespeichert und nichts protokolliert.

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

async function geocode(url, env) {
  const q = (url.searchParams.get('q') || '').trim().slice(0, 120);
  if (q.length < 3) throw new Fehler(400, 'zu-kurz');
  // OpenStreetMap (Nominatim) kennt Ortsteile wie Eicken-Bruche gut, Pelias (HeiGIT) ergänzt.
  // Beide parallel fragen, Nominatim-Treffer zuerst.
  const nominatim = async () => {
    const u = NOMINATIM + '?' + new URLSearchParams({'q': q, 'format': 'jsonv2', 'countrycodes': 'de', 'limit': '4', 'accept-language': 'de'});
    const r = await fetch(u, {headers: {'User-Agent': UA, 'Referer': ORIGINS[0] + '/handball-eicken/', 'Accept': 'application/json'}});
    if (!r.ok) throw new Fehler(502, 'nominatim-' + r.status);
    const j = await r.json();
    return (Array.isArray(j) ? j : []).map(x => ({label: kurz(x.display_name), lat: +x.lat, lon: +x.lon}));
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

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const origin = req.headers.get('Origin') || '';
    const extra = String(env.EXTRA_ORIGINS || '').split(',').map(s => s.trim()).filter(Boolean);
    const allowed = ORIGINS.includes(origin) || extra.includes(origin);
    const cors = allowed ? {'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'} : {'Vary': 'Origin'};
    if (req.method === 'OPTIONS') {
      return new Response(null, {status: allowed ? 204 : 403, headers: {...cors, 'Access-Control-Allow-Methods': 'GET', 'Access-Control-Max-Age': '86400'}});
    }
    if (req.method !== 'GET') return json({error: 'methode'}, 405, cors);
    if (url.pathname === '/' || url.pathname === '/health') return json({ok: true, ors: !!env.ORS_API_KEY}, 200, cors);
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
