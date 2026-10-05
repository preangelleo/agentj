// agentj web client service worker — Web Push only (PROTOCOL §9). No fetch handler, no cache: every page load still
// comes from the network, so the version hash in the page stays the whole story. A push carries no content, only its
// kind ("reply" | "ask" | "security"), encrypted by the host to this browser's subscription keys.
// SW_VERSION changes with every redesign of the shell: a changed sw.js is what makes phones install the new worker
// (skipWaiting + clients.claim take over at once, so notification text / icons follow the new shell without a second visit).
const SW_VERSION = 'aj-web-2026-10-05-agentj015-f19';
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
