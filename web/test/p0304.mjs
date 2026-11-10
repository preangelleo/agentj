#!/usr/bin/env node
// P0304: real page + fake Noise host; browser-local PushManager stand-in (no external push service).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, waitFor, shoot } from './browser.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const out = root + 'reports/qa/p0304/';
mkdirSync(out, { recursive: true });
const web = await startWebServer();
const fake = await startFakeHost();
const B = await launch();
const k = Buffer.from(Uint8Array.of(4, ...new Uint8Array(64).fill(7))).toString('base64url');
const js = `(() => {
  window.__pushTest = { subs: 0, permissionRequests: 0, sub: null, pause: false, entered: false };
  Object.defineProperty(Notification, 'permission', { configurable: true, get: () => 'granted' });
  Notification.requestPermission = async () => { window.__pushTest.permissionRequests++; return 'granted'; };
  const options = { applicationServerKey: Uint8Array.of(4, ...new Uint8Array(64).fill(7)) };
  const make = () => ({ options, toJSON: () => ({ endpoint: 'https://example.com/push', keys: { p256dh: 'test', auth: 'test' } }),
    unsubscribe: async () => { window.__pushTest.sub = null; return true; } });
  PushManager.prototype.getSubscription = async function() { return window.__pushTest.sub; };
  PushManager.prototype.subscribe = async function() {
    const test = window.__pushTest;
    test.entered = true;
    if (test.pause) await new Promise(resolve => { test.release = resolve; });
    test.sub = make(); test.subs++; return test.sub;
  };
})()`;
const ev = (p, s) => evaluate(p, s);
const states = async (p) => ev(p, `import('./js/push.js').then(m => m.state())`);
let page;
try {
  for (const w of [390, 1440]) for (const theme of ['light', 'dark']) {
    fake.reset();
    page = await newPage(B, w, 844, theme, { allow: [web.url, fake.relay + '/'] });
    await page.send('Page.addScriptToEvaluateOnNewDocument', { source: js });
    await navigate(page, fake.newPairing(web.url));
    await waitState(page, 'awaiting-approval');
    await fake.approve();
    await waitState(page, 'ready');
    await fake.send({ t: 'push_key', k });
    await waitFor(page, `document.getElementById('setBtn') !== null`);
    await ev(page, `document.getElementById('setBtn').click()`);
    await waitFor(page, `!document.getElementById('settings').hidden`);
    await waitFor(page, `import('./js/push.js').then(m => m.state() === 'offer')`);
    assert.equal(await states(page), 'offer', 'permission without host record must not silently opt in');
    await ev(page, `document.getElementById('set-push').scrollIntoView({block:'center'})`);
    writeFileSync(`${out}before-${w}-${theme}.png`, await shoot(page));
    await fake.send({ t: 'push_state', on: true });
    await waitFor(page, `window.__pushTest.subs === 1`);
    assert.equal(await states(page), 'on');
    assert.equal(await ev(page, `window.__pushTest.permissionRequests`), 0, 'recovery must not prompt');
    assert.ok(fake.log.some(m => m.t === 'push_sub'));
    const firstCount = fake.log.filter(m => m.t === 'push_sub').length;
    await fake.send({ t: 'push_state', on: true }); // delayed host acknowledgement must not restart a subscribe
    await waitFor(page, `document.getElementById('set-push').dataset.act === 'off'`);
    assert.equal(fake.log.filter(m => m.t === 'push_sub').length, firstCount, 'ack does not loop');
    await ev(page, `document.getElementById('set-push').scrollIntoView({block:'center'})`);
    writeFileSync(`${out}after-${w}-${theme}.png`, await shoot(page));
    // Browser loses its subscription: host-on launches recovery, but host-off arrives while subscribe is pending.
    await ev(page, `window.__pushTest.sub = null; window.__pushTest.pause = true; window.__pushTest.entered = false`);
    await fake.send({ t: 'push_state', on: false });
    await fake.send({ t: 'push_state', on: true });
    await waitFor(page, `window.__pushTest.entered`);
    await fake.send({ t: 'push_state', on: false });
    await waitFor(page, `import('./js/push.js').then(m => m.state() === 'offer')`);
    await ev(page, `window.__pushTest.release(); window.__pushTest.pause = false`);
    await waitFor(page, `window.__pushTest.subs === 2`);
    await waitFor(page, `window.__pushTest.sub === null`);
    assert.equal(await states(page), 'offer', 'host off during subscribe cannot paint on');
    assert.equal(fake.log.filter(m => m.t === 'push_sub').length, firstCount, 'stale subscription never sent');
    await waitFor(page, `document.getElementById('set-push').textContent.length > 0`);
    assert.equal(await states(page), 'offer', 'host disabled overrides stale browser subscription');
    await ev(page, `document.getElementById('set-push').click()`);
    await waitFor(page, `document.getElementById('set-push').dataset.act === 'off'`);
    await ev(page, `document.getElementById('set-push').click()`);
    await waitFor(page, `localStorage.getItem('aj.pushExplicitOff') === '1'`);
    for (let i = 0; i < 80 && !fake.log.some(m => m.t === 'push_off'); i++) await new Promise(r => setTimeout(r, 50));
    const offMsg = fake.log.filter(m => m.t === 'push_off').at(-1);
    assert.match(offMsg.r, /^[0-9a-f-]{36}$/);
    // Socket delivery alone and a stale uncorrelated off state are NOT success.
    await fake.send({ t: 'push_state', on: false });
    assert.equal(await ev(page, `document.getElementById('set-push').dataset.act`), 'on');
    await fake.send({ t: 'push_state', on: false, r: offMsg.r });
    await fake.send({ t: 'push_state', on: true });
    for (let i = 0; i < 80 && fake.log.filter(m => m.t === 'push_off').length < 2; i++) await new Promise(r => setTimeout(r, 50));
    const secondOff = fake.log.filter(m => m.t === 'push_off').at(-1);
    assert.notEqual(secondOff.r, offMsg.r, 'newer host-on after matching off ack needs a fresh revocation');
    await fake.send({ t: 'push_state', on: false, r: secondOff.r });
    await fake.send({ t: 'push_key', k });
    assert.equal(await ev(page, `window.__pushTest.subs`), 3, 'explicit off must survive host stale-on');
    assert.equal(await states(page), 'offer');
    // A failed unsubscribe must not revoke the owner's local off decision.
    await ev(page, `window.__pushTest.sub = { unsubscribe: async () => { throw Error('blocked'); } }`);
    const failedOff = ev(page, `import('./js/push.js').then(m => m.disablePush())`);
    await waitFor(page, `localStorage.getItem('aj.pushExplicitOff') === '1'`);
    await waitFor(page, `document.getElementById('set-push').dataset.act !== 'off'`);
    const failedR = fake.log.filter(m => m.t === 'push_off').at(-1)?.r;
    await fake.send({ t: 'push_state', on: false, r: failedR });
    assert.equal(await failedOff, false);
    assert.equal(await ev(page, `localStorage.getItem('aj.pushExplicitOff')`), '1');
    const beforeRetry = fake.log.filter(m => m.t === 'push_off').length;
    await fake.send({ t: 'push_state', on: true });
    await waitFor(page, `window.__pushTest.subs === 3`); // no new subscription; existing count unchanged
    await waitFor(page, `document.getElementById('set-push').dataset.act !== 'off'`);
    assert.equal(await states(page), 'offer');
    assert.ok(fake.log.filter(m => m.t === 'push_off').length > beforeRetry, 'host on retries pending off after unsubscribe failure');
    const afterRetry = fake.log.filter(m => m.t === 'push_off').length;
    fake.kick();
    await waitFor(page, `window.__ajState !== 'ready'`);
    await waitState(page, 'ready', 20000);
    await fake.send({ t: 'push_key', k });
    await new Promise(r => setTimeout(r, 3100)); // old in-flight off must time out before the next key retries
    await fake.send({ t: 'push_key', k });
    for (let i = 0; i < 80 && fake.log.filter(m => m.t === 'push_off').length <= afterRetry; i++) await new Promise(r => setTimeout(r, 50));
    assert.ok(fake.log.filter(m => m.t === 'push_off').length > afterRetry, 'reconnect key retries durable off');
    assert.equal(await ev(page, `window.__pushTest.subs`), 3, 'reconnect must not subscribe');
    // localStorage write fails while the host is unreachable: IndexedDB must retain off through a reload.
    await ev(page, `localStorage.setItem('aj.pushExplicitOff','0'); Storage.prototype.setItem = function() { throw Error('quota'); }; window.__pushTest.sub = null`);
    fake.kick();
    await waitFor(page, `window.__ajState !== 'ready'`);
    assert.equal(await ev(page, `import('./js/push.js').then(m => m.disablePush())`), false, 'offline off cannot claim success');
    assert.equal(await ev(page, `localStorage.getItem('aj.pushExplicitOff')`), '0', 'localStorage write was refused');
    const load = page.waitFor('Page.loadEventFired'); await page.send('Page.reload'); await load;
    await waitState(page, 'ready', 20000);
    await fake.send({ t: 'push_state', on: true });
    await fake.send({ t: 'push_key', k });
    await waitFor(page, `import('./js/push.js').then(m => m.state() === 'offer')`);
    assert.equal(await ev(page, `window.__pushTest.subs`), 0, 'reload must not re-subscribe on stale host-on');
    // Simulate a corrupted DB with no flags store and a blocked IDB open: each must fail closed,
    // yet the off request must still reach the host instead of waiting forever for IndexedDB.
    await ev(page, `new Promise((resolve, reject) => { const q = indexedDB.deleteDatabase(AJSeat.db('aj-push-intent')); q.onsuccess = resolve; q.onerror = () => reject(q.error); })`);
    await ev(page, `new Promise((resolve, reject) => { const q = indexedDB.open(AJSeat.db('aj-push-intent'), 1); q.onupgradeneeded = () => q.result.createObjectStore('wrong'); q.onsuccess = () => { q.result.close(); resolve(); }; q.onerror = () => reject(q.error); })`);
    await ev(page, `window.__pushTest.sub = null; Storage.prototype.setItem = function() { throw Error('quota'); }`);
    const beforeCorrupt = fake.log.filter(m => m.t === 'push_off').length;
    await new Promise(r => setTimeout(r, 3100)); // previous unacknowledged off must expire
    const corruptOff = ev(page, `import('./js/push.js').then(m => m.disablePush())`);
    for (let i = 0; i < 80 && fake.log.filter(m => m.t === 'push_off').length <= beforeCorrupt; i++) await new Promise(r => setTimeout(r, 50));
    assert.ok(fake.log.filter(m => m.t === 'push_off').length > beforeCorrupt, 'corruption cannot skip host off');
    const corruptR = fake.log.filter(m => m.t === 'push_off').at(-1).r;
    await fake.send({ t: 'push_state', on: false, r: corruptR });
    assert.equal(await corruptOff, false, 'corrupted flags store must fail closed');
    await ev(page, `indexedDB.open = () => ({ set onblocked(fn) { setTimeout(fn, 0); }, set onerror(fn) {}, set onsuccess(fn) {} })`);
    const failed = await ev(page, `import('./js/push.js').then(m => m.disablePush())`);
    assert.equal(failed, false, 'blocked store cannot claim durable off');
    await fake.send({ t: 'push_state', on: true });
    assert.equal(await ev(page, `window.__pushTest.subs`), 0, 'blocked store never auto-enables');
    assert.equal(await ev(page, `navigator.serviceWorker.getRegistration('./').then(r => r.scope)`), web.url, 'worker scope stays seat-local');
    assert.match(readFileSync(root + 'web/public/sw.js', 'utf8'), /aj-web-2026-11-09-0304-push/);
    assert.equal(page.offsite.length, 0);
    await page.dispose(); page = null;
  }
  // Two seat routes on the same origin share one browser context: B's enable marker cannot overwrite A's off.
  page = await newPage(B, 390, 844, 'dark', { allow: [web.url, fake.relay + '/'] });
  await navigate(page, web.url + 'abcdefgh/');
  assert.equal(await ev(page, `AJSeat.db('aj-push-intent')`), 'aj-push-intent@abcdefgh');
  await ev(page, `new Promise((resolve, reject) => { const q = indexedDB.open(AJSeat.db('aj-push-intent'), 1); q.onupgradeneeded = () => q.result.createObjectStore('flags'); q.onerror = () => reject(q.error); q.onsuccess = () => { const db = q.result; const tx = db.transaction('flags', 'readwrite'); tx.objectStore('flags').put(true, 'off'); tx.oncomplete = () => { db.close(); resolve(); }; }; })`);
  await navigate(page, web.url + 'ijklmnop/');
  assert.equal(await ev(page, `AJSeat.db('aj-push-intent')`), 'aj-push-intent@ijklmnop');
  await ev(page, `new Promise((resolve, reject) => { const q = indexedDB.open(AJSeat.db('aj-push-intent'), 1); q.onupgradeneeded = () => q.result.createObjectStore('flags'); q.onerror = () => reject(q.error); q.onsuccess = () => { const db = q.result; const tx = db.transaction('flags', 'readwrite'); tx.objectStore('flags').put(false, 'off'); tx.oncomplete = () => { db.close(); resolve(); }; }; })`);
  await navigate(page, web.url + 'abcdefgh/');
  assert.equal(await ev(page, `new Promise((resolve, reject) => { const q = indexedDB.open(AJSeat.db('aj-push-intent')); q.onerror = () => reject(q.error); q.onsuccess = () => { const db = q.result; const r = db.transaction('flags').objectStore('flags').get('off'); r.onsuccess = () => { db.close(); resolve(r.result); }; }; })`), true, 'B cannot overwrite A off marker');
  await page.dispose(); page = null;
  console.log('p0304-phone PASS');
} finally { if (page) await page.dispose(); await B.close(); await fake.stop(); await web.stop(); }
