// agentjarvis web client service worker — Web Push only (PROTOCOL §9). No fetch handler, no cache: every page load still
// comes from the network, so the version hash in the page stays the whole story. A push carries no content, only its
// kind ("reply" | "ask"), encrypted by the host to this browser's subscription keys.
const BODY = { reply: '有新回复', ask: '有一个请求等你批准' };

self.addEventListener('push', (e) => {
  let k = 'reply';
  try { const d = e.data && e.data.json(); if (d && BODY[d.k]) k = d.k; } catch { /* malformed → generic */ }
  e.waitUntil(self.registration.showNotification('Agent Jarvis', { body: BODY[k], tag: 'aj-' + k, renotify: true, icon: 'icon-192.png', badge: 'icon-192.png' }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((cs) => {
    for (const c of cs) if ('focus' in c) return c.focus();
    return self.clients.openWindow('./');
  }));
});
