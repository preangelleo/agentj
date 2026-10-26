#!/usr/bin/env node
// P550 (P545 × P547) phone gate (p44 `p550-seatmove`): a seat's page is named by its channel (m.agentj.app/<seat id>/), and a
// P547 seat transfer gives the remote a new host with a new channel = another seat id. REAL web client in headless Chromium,
// two synthetic Noise hosts (fakehost.mjs). Walks:
//   paired on /<A>/ → host_moved → 「切过去」 → the page opens /<B>/ (B = the new channel's seat id) and is ready on the new host
//   with the same device key; /<A>/'s record says movedTo B; the browser's seat list has B, not A; reopening /<A>/ (its Home
//   Screen icon) goes to /<B>/ · the root page keeps P547's in-place move (p547.mjs)
// Usage: node web/test/p550_seatmove.mjs
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, sleep } from './browser.mjs';

const checks = [];
const ok = (cond, msg) => { assert.ok(cond, msg); checks.push(msg); };
// js/seats.js seatId: the first 40 bits of SHA-256("agentj/seat-url/v1\n" ‖ channel) as 8 lower-case base32 letters
function seatId(channel) {
  const d = createHash('sha256').update('agentj/seat-url/v1\n' + channel).digest();
  let n = 0n;
  for (let i = 0; i < 5; i++) n = (n << 8n) | BigInt(d[i]);
  let out = '';
  for (let i = 0; i < 8; i++) out += 'abcdefghijklmnopqrstuvwxyz234567'[Number((n >> BigInt(35 - 5 * i)) & 31n)];
  return out;
}

const web = await startWebServer();
const fake = await startFakeHost(), fake2 = await startFakeHost(), B = await launch();
const A = seatId(fake.channel), Bs = seatId(fake2.channel);
const rec = (p) => evaluate(p, `import('./js/store.js').then(async (s) => { const h = await s.dbGet('host'); return h && { channel: h.channel, relay: h.relay, approved: h.approved, dk: h.dk, movedTo: h.movedTo || null, db: s.DB_NAME }; })`);
const other = (p, seat) => evaluate(p, `new Promise((res) => { const r = indexedDB.open('agentjarvis@${seat}'); r.onsuccess = () => {
  const db = r.result; if (!db.objectStoreNames.contains('kv')) { db.close(); res(null); return; }
  const q = db.transaction('kv').objectStore('kv').get('host'); q.onsuccess = () => { db.close(); const h = q.result; res(h && { channel: h.channel, movedTo: h.movedTo || null, approved: h.approved }); }; }; })`);
const path = (p) => evaluate(p, 'location.pathname');

try {
  ok(A !== Bs, 'two hosts, two seat ids');
  const p = await newPage(B, 390, 844, 'dark', { touch: true, allow: [web.url, fake.relay + '/', fake2.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + A + '/?lang=zh'));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready'); await sleep(400);
  const before = await rec(p);
  ok((await path(p)) === `/${A}/` && before.db === 'agentjarvis@' + A && before.channel === fake.channel, `paired on /${A}/ (its own database)`);
  fake2.trust(fake.allowList());                       // the old host handed this remote over (handover list)
  await fake.send({ t: 'host_moved', relay: fake2.relay, channel: fake2.channel, hostPub: fake2.hostPub, name: 'Mac mini' });
  await waitFor(p, `!document.getElementById('confirm').hidden`, 5000);
  await evaluate(p, `document.getElementById('confirm-yes').click()`);
  for (let i = 0; i < 200 && (await path(p).catch(() => '')) !== `/${Bs}/`; i++) await sleep(50);
  ok((await path(p)) === `/${Bs}/`, `「切过去」 → the page opens the new seat's own address /${Bs}/`);
  await waitState(p, 'ready', 15000);
  const r = await rec(p);
  ok(r.db === 'agentjarvis@' + Bs && r.channel === fake2.channel && r.relay === fake2.relay && r.approved && r.dk === before.dk,
    'there: the moved pairing in that seat\'s database, same device key');
  ok(fake2.conns > 0, 'and ready on the new host');
  const old = await other(p, A);
  ok(old && old.movedTo === Bs && old.channel === fake.channel, `/${A}/'s record says movedTo ${Bs}`);
  const list = await evaluate(p, `(() => { const raw = window.AJSeat.root; return JSON.parse(raw.getItem('aj.seats') || '[]').map((e) => e.s); })()`);
  ok(list.includes(Bs) && !list.includes(A), 'the browser\'s seat list has the new seat, not the old one');
  await navigate(p, web.url + A + '/');
  for (let i = 0; i < 200 && (await path(p).catch(() => '')) !== `/${Bs}/`; i++) await sleep(50);
  ok((await path(p)) === `/${Bs}/`, `reopening /${A}/ (its Home Screen icon) goes to /${Bs}/`);
  await waitState(p, 'ready', 15000);
  ok(true, 'ready there again');
  const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION_REFUSED|favicon/i.test(x));
  assert.deepEqual(problems, [], 'console / CSP clean');
  await p.dispose();
  console.log(`p550-seatmove: ${checks.length} checks passed`);
  for (const c of checks) console.log('  ✓ ' + c);
} finally { await B.close(); await fake.stop(); await fake2.stop(); await web.stop(); }
process.exit(0);
