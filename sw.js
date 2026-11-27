// ESV Handball: Service Worker
// Immer zuerst das Netz fragen (damit Daten und Seite aktuell sind); die Seite selbst wird dabei
// am Zwischenspeicher des Browsers vorbei geholt. Nur ohne Verbindung wird die zuletzt geladene
// Fassung gezeigt.
const CACHE = 'esv-handball-v2';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())
));
self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  const key = url.origin + url.pathname;
  const net = req.mode === 'navigate' ? fetch(req.url, {cache: 'no-cache', credentials: 'same-origin'}) : fetch(req);
  e.respondWith(
    net.then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(key, copy)); }
      return res;
    }).catch(() => caches.match(key).then(r => r || Response.error()))
  );
});

// ---------- Mitteilungen bei neuen Ergebnissen
const API = 'https://esv-handball.esv-eicken.workers.dev';
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(d.title || 'ESV Handball', {
    body: d.body || '',
    icon: 'icon-192.png',
    badge: 'icon-192.png',
    tag: d.tag || undefined,
    renotify: !!d.tag,
    data: {url: d.url || self.registration.scope},
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || './', self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    const c = list.find(w => w.url.startsWith(self.registration.scope));
    if (c) { c.postMessage({type: 'oeffne', url}); return c.focus ? c.focus() : undefined; }
    return self.clients.openWindow(url);
  }));
});
// Der Browser hat das Abo erneuert: beim Dienst umziehen (Mannschaften bleiben erhalten)
self.addEventListener('pushsubscriptionchange', e => {
  e.waitUntil((async () => {
    const old = e.oldSubscription;
    let sub = e.newSubscription;
    if (!sub) {
      const key = old && old.options && old.options.applicationServerKey;
      if (!key) return;
      sub = await self.registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key});
    }
    await fetch(API + '/push/subscribe', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sub: sub.toJSON(), old: old ? old.endpoint : undefined})});
  })().catch(() => {}));
});
