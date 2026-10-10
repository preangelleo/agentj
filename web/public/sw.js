// agentj web client service worker — generic Web Push + local-only share intake. No cache: every page load still
// comes from the network, so the version hash in the page stays the whole story. A push carries no content, only its
// kind ("reply" | "ask" | "security"), encrypted by the host to this browser's subscription keys.
// SW_VERSION changes with every redesign of the shell: a changed sw.js is what makes phones install the new worker
// (skipWaiting + clients.claim take over at once, so notification text / icons follow the new shell without a second visit).
const SW_VERSION = 'aj-web-2026-10-10-p110-share';
// The notification text follows the page language: the page registers sw.js?lang=en when English is chosen (the worker
// cannot read the page's localStorage). Still only these three fixed sentences per language — never any content.
const BODY = {
  zh: { reply: '有新回复', ask: '有一个请求等你批准', security: 'Agent J 有紧急安全更新' },
  en: { reply: 'New reply', ask: 'A request is waiting for your approval', security: 'Agent J has an urgent security update' },
};
const LANG = /[?&]lang=en\b/.test(self.location.search) ? 'en' : 'zh';

self.addEventListener('install', () => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', (e) => {
  let k = 'reply';
  try { const d = e.data && e.data.json(); if (d && BODY.zh[d.k]) k = d.k; } catch { /* malformed → generic */ }
  e.waitUntil(self.registration.showNotification('Agent J', { body: BODY[LANG][k], tag: 'aj-' + k, renotify: true,
    icon: 'brand/img/icon-192.png', badge: 'brand/img/favicon-192-round.png', data: { v: SW_VERSION } }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((cs) => {
    for (const c of cs) if ('focus' in c) return c.focus();
    return self.clients.openWindow('./');
  }));
});

// PR1: this is a browser-local navigation, never a network upload. No content storage or logs.
// Worker termination / five minutes loses the intake: ask the owner to share again, never fall back to a server.
const SHARE_MAX = 26214400; // total images, 25 MiB (each existing attachment has this same bound)
const SHARE_FORM_MAX = SHARE_MAX + 1048576;
const SHARE_COUNT = 10;
const SHARE_TTL = 300000;
const shared = new Map();
function pruneShares() {
  for (const [id, v] of shared) if (Date.now() - v.at >= SHARE_TTL) shared.delete(id);
}
async function receiveShare(req) {
  pruneShares();
  try {
    if (!/^multipart\/form-data\s*;/i.test(req.headers.get('content-type') || '')) throw 0;
    if (Number(req.headers.get('content-length')) > SHARE_FORM_MAX) throw 0;
    const reader = req.body.getReader(), chunks = []; let size = 0;
    for (;;) {
      const {done, value} = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > SHARE_FORM_MAX) { await reader.cancel(); throw 0; }
      chunks.push(value);
    }
    const form = await new Response(new Blob(chunks), {headers: {'content-type': req.headers.get('content-type')}}).formData();
    const images = form.getAll('images');
    if (images.length > SHARE_COUNT || images.some(f => !(f instanceof File) || !/^image\//.test(f.type) || !f.size) || images.reduce((n, f) => n + f.size, 0) > SHARE_MAX) throw 0;
    const text = ['title', 'text', 'url'].map(k => form.get(k) || '');
    if (text.some(v => typeof v !== 'string') || text.join('\n').length > 16000 || (!images.length && !text.some(Boolean))) throw 0;
    // Bound concurrent intakes as well as each form; reject rather than evict another pending screenshot.
    if (shared.size >= 2) throw 0;
    const id = crypto.randomUUID();
    shared.set(id, {at: Date.now(), images, text: text.filter(Boolean).join('\n')});
    return Response.redirect(new URL('./?share=' + id, self.location.href).href, 303);
  } catch {
    return Response.redirect(new URL('./?share=failed', self.location.href).href, 303);
  }
}
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (u.origin === self.location.origin && u.pathname === '/share-target' && e.request.method === 'POST') {
    // Even a malformed intake is consumed here; it must never reach ASSETS / Worker / access logs.
    e.respondWith(receiveShare(e.request));
  }
});
self.addEventListener('message', e => {
  if (e.data?.t !== 'share-take' || !e.ports[0] || !e.source?.url) return;
  pruneShares();
  const u = new URL(e.source.url), id = e.data.id;
  // The one-time handle is bound to the landing URL, never a filename / image / shared text in a URL.
  if (u.origin !== self.location.origin || u.pathname !== '/' || u.searchParams.get('share') !== id) return;
  const v = shared.get(id); shared.delete(id);
  e.ports[0].postMessage(v ? {images: v.images, text: v.text} : {error: 'expired'});
});
