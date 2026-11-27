// Web Push ohne Fremdbibliothek: Verschlüsselung nach RFC 8291 (aes128gcm) und
// Absender-Kennung nach RFC 8292 (VAPID), alles mit WebCrypto.

const te = new TextEncoder();

export function b64uEnc(buf) {
  const b = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64uDec(str) {
  let s = String(str || '').replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

async function hkdf(salt, ikm, info, bytes) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({name: 'HKDF', hash: 'SHA-256', salt, info}, key, bytes * 8));
}

// Nachricht für ein Abo verschlüsseln (RFC 8291). opts.salt / opts.keyPair nur für Tests.
export async function encrypt(text, p256dh, auth, opts = {}) {
  const ua = b64uDec(p256dh), secret = b64uDec(auth);
  if (ua.length !== 65 || ua[0] !== 4 || secret.length !== 16) throw new Error('abo-schluessel');
  const uaKey = await crypto.subtle.importKey('raw', ua, {name: 'ECDH', namedCurve: 'P-256'}, false, []);
  const as = opts.keyPair || await crypto.subtle.generateKey({name: 'ECDH', namedCurve: 'P-256'}, true, ['deriveBits']);
  const asPub = new Uint8Array(await crypto.subtle.exportKey('raw', as.publicKey));
  const shared = new Uint8Array(await crypto.subtle.deriveBits({name: 'ECDH', public: uaKey}, as.privateKey, 256));
  const ikm = await hkdf(secret, shared, concat(te.encode('WebPush: info\0'), ua, asPub), 32);
  const salt = opts.salt || crypto.getRandomValues(new Uint8Array(16));
  const cek = await hkdf(salt, ikm, te.encode('Content-Encoding: aes128gcm\0'), 16);
  const nonce = await hkdf(salt, ikm, te.encode('Content-Encoding: nonce\0'), 12);
  const key = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt']);
  const ct = new Uint8Array(await crypto.subtle.encrypt({name: 'AES-GCM', iv: nonce}, key, concat(te.encode(text), new Uint8Array([2]))));
  const head = new Uint8Array(21 + asPub.length);
  head.set(salt, 0);
  new DataView(head.buffer).setUint32(16, 4096);
  head[20] = asPub.length;
  head.set(asPub, 21);
  return concat(head, ct);
}

// VAPID-Schlüssel aus dem Secret VAPID_JWK (privater Schlüssel als JWK)
let VAPID = null;
export async function vapid(env) {
  if (!env.VAPID_JWK) return null;
  if (VAPID && VAPID.src === env.VAPID_JWK) return VAPID;
  const j = JSON.parse(env.VAPID_JWK);
  const key = await crypto.subtle.importKey('jwk', {kty: 'EC', crv: 'P-256', x: j.x, y: j.y, d: j.d, ext: true},
    {name: 'ECDSA', namedCurve: 'P-256'}, false, ['sign']);
  const pub = b64uEnc(concat(new Uint8Array([4]), b64uDec(j.x), b64uDec(j.y)));
  VAPID = {src: env.VAPID_JWK, key, pub, jwt: {}};
  return VAPID;
}

// Signiertes Token für einen Push-Dienst (aud). Apple möchte es höchstens einmal pro Stunde neu,
// deshalb wird es 12 Stunden genutzt und im KV-Speicher zwischen den Aufrufen gemerkt.
export async function vapidAuth(env, endpoint, subject) {
  const v = await vapid(env);
  if (!v) throw new Error('kein-vapid');
  const aud = new URL(endpoint).origin;
  const now = Math.floor(Date.now() / 1000);
  let c = v.jwt[aud];
  if (!c && env.PUSH) {
    try { c = await env.PUSH.get('j:' + aud, 'json'); } catch (e) { c = null; }
    if (c && c.k !== v.pub) c = null;
  }
  if (!c || c.exp - now < 2 * 3600) {
    const exp = now + 12 * 3600;
    const h = b64uEnc(te.encode(JSON.stringify({typ: 'JWT', alg: 'ES256'})));
    const p = b64uEnc(te.encode(JSON.stringify({aud, exp, sub: subject})));
    const sig = new Uint8Array(await crypto.subtle.sign({name: 'ECDSA', hash: 'SHA-256'}, v.key, te.encode(h + '.' + p)));
    c = {t: h + '.' + p + '.' + b64uEnc(sig), exp, k: v.pub};
    if (env.PUSH) { try { await env.PUSH.put('j:' + aud, JSON.stringify(c), {expirationTtl: 13 * 3600}); } catch (e) {} }
  }
  v.jwt[aud] = c;
  return 'vapid t=' + c.t + ', k=' + v.pub;
}

// Eine Mitteilung an ein Abo schicken. Liefert den HTTP-Status des Push-Dienstes.
export async function sendPush(env, sub, payload, subject, opts = {}) {
  const body = await encrypt(JSON.stringify(payload), sub.p, sub.a);
  const r = await fetch(sub.e, {
    method: 'POST',
    headers: {
      'Authorization': await vapidAuth(env, sub.e, subject),
      'Content-Encoding': 'aes128gcm',
      'Content-Type': 'application/octet-stream',
      'TTL': String(opts.ttl || 6 * 3600),
      'Urgency': opts.urgency || 'normal',
    },
    body,
  });
  try { await r.arrayBuffer(); } catch (e) {}
  return r.status;
}
