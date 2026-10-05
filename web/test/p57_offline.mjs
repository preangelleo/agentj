// 0.15.2 (P57 lane "offline"): the offline queue in the real page (public/js/outbox.js + relay.js), end to end against
// fakehost.mjs. Ready → the network goes (CDP offline + the socket dropped) → 3 sends → the one line 「网络恢复后自动发送」
// → the network is back but the computer is away → reload → still 3 pending → the computer comes back → the host gets
// exactly 3 messages in order although the first say_res is lost on the way (the page resends that sid, the host answers
// dup) → the pending lines are gone, the pages are normal. Then: /stop offline is not queued; a message with a file
// queued offline loses its file in a reload, says so, and on reconnect its words come back to the field.
// Screenshots → /var/tmp/p57-logs/offline-shots/. Run under flock /tmp/p57-heavy.lock.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';
import { launch, newPage, navigate, evaluate, waitFor, waitState, shoot, sleep } from './browser.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const SHOTS = process.env.P57_SHOTS || '/var/tmp/p57-logs/offline-shots';
mkdirSync(SHOTS, { recursive: true });
const server = await startWebServer();
const fake = await startFakeHost();
const B = await launch();
const st = fake.st;
const ok = [];
const prec = parityRecorder('web/test/p57_offline.mjs');   // the parity runner reads these case names
const step = (s) => { ok.push(s); prec.record(s, true); console.log('  ✓ ' + s); };

// The host's §10.2 rule the fake does not model: a sid this device already used is answered `dup` (compose.Sends).
// onSay reads st.sayWhy right after recording the say, so a getter answers dup exactly for a repeated sid.
// st.dropNext: the host takes the next say (its page is written) but the socket drops before say_res goes out.
Object.defineProperty(st, 'sayWhy', { configurable: true, set() {},
  get() {
    const m = st.says[st.says.length - 1];
    if (m && st.says.slice(0, -1).some((x) => x.sid === m.sid)) return 'dup';
    if (m && st.dropNext) { st.dropNext = false; fake.kick(); }
    return null;
  } });

const pending = (p) => evaluate(p, `[...document.querySelectorAll('#outbox .obi')].map(d => ({t: d.querySelector('.obt').textContent, lost: d.dataset.lost === '1', note: (d.querySelector('.obn') || {}).textContent || ''}))`);
const line = (p) => evaluate(p, `(() => { const b = document.getElementById('outbox'), l = b.querySelector('.obl'); return b.hidden ? null : l ? l.textContent : ''; })()`);
const rec = (p) => evaluate(p, `new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const g = r.result.transaction('kv').objectStore('kv').get('outbox'); g.onsuccess = () => { const v = g.result; r.result.close(); res(v ? { keys: Object.keys(v).sort().join(','), ct: v.ct instanceof Uint8Array, plain: new TextDecoder('latin1').decode(v.ct).includes('m-') } : null); }; }; })`);
async function send(p, text) {
  await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ${JSON.stringify(text)}; i.dispatchEvent(new Event('input')); })()`);
  assert.equal(await evaluate(p, `document.getElementById('send').disabled`), false, 'Send stays usable while not connected');
  await evaluate(p, `document.getElementById('send').click()`);
}
const phoneTurns = () => st.turns.filter((x) => x.src && x.src.k === 'phone').map((x) => x.src.text);

let page;
try {
  page = await newPage(B, 390, 844, 'light', { allow: [server.url, fake.relay] });
  await navigate(page, fake.newPairing(server.url));
  await waitState(page, 'awaiting-approval');
  await fake.approve();
  await waitState(page, 'ready');
  assert.equal(await line(page), null, 'online: no line');
  step('paired, ready, no pending line');

  // ---- the network goes: navigator.onLine false + the socket dropped (a reconnect cannot reach anything)
  await page.send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  fake.kick();
  await waitFor(page, `window.__ajState !== 'ready'`);
  await waitFor(page, `navigator.onLine === false`);
  await waitFor(page, `!document.getElementById('outbox').hidden`);
  assert.equal(await line(page), '网络恢复后自动发送', 'navigator.onLine false → the line shows at once');
  step('offline: the one line shows before anything is sent');

  for (const m of ['m-1 第一条', 'm-2 第二条', 'm-3 第三条']) await send(page, m);
  await waitFor(page, `document.querySelectorAll('#outbox .obi').length === 3`);
  assert.deepEqual((await pending(page)).map((x) => x.t), ['m-1 第一条', 'm-2 第二条', 'm-3 第三条']);
  assert.equal(await line(page), '网络恢复后自动发送');
  assert.equal(await evaluate(page, `document.getElementById('input').value`), '', 'the field is cleared like a normal send');
  assert.equal(await evaluate(page, `document.querySelectorAll('#outbox button').length`), 0, 'no new buttons');
  await waitFor(page, `new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const g = r.result.transaction('kv').objectStore('kv').get('outbox'); g.onsuccess = () => { r.result.close(); res(!!g.result); }; }; })`);
  const sealed = await rec(page);
  assert.deepEqual(sealed, { keys: 'ct,iv,v', ct: true, plain: false }, 'IndexedDB holds only {v, iv, ct}');
  assert.equal(st.says.length, 0, 'nothing reached the host');
  writeFileSync(`${SHOTS}/offline-pending-390.png`, await shoot(page));
  step('3 sends queued, sealed in IndexedDB, nothing sent');

  // ---- /stop offline is not queued: it says 「没连上电脑…」 and leaves the queue as it is
  await evaluate(page, `(() => { const i = document.getElementById('input'); i.value = '/stop'; i.dispatchEvent(new Event('input')); document.getElementById('send').click(); })()`);
  await waitFor(page, `document.getElementById('toast').textContent.includes('停下和清空要连上')`);
  assert.equal((await pending(page)).length, 3);
  assert.equal(await evaluate(page, `document.getElementById('input').value`), '/stop', '/stop stays in the field');
  await evaluate(page, `(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); })()`);
  step('/stop offline is refused with one line, not queued');

  // ---- the network is back, the computer is away; the page is reloaded (an app kill) → the same 3 come back
  fake.setUp(false);
  await page.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await navigate(page, server.url);
  await waitFor(page, `document.querySelectorAll('#outbox .obi').length === 3`);
  assert.deepEqual((await pending(page)).map((x) => x.t), ['m-1 第一条', 'm-2 第二条', 'm-3 第三条']);
  await waitFor(page, `window.__ajState === 'waiting-host'`);
  assert.equal(await line(page), '网络恢复后自动发送');
  writeFileSync(`${SHOTS}/offline-after-reload-390.png`, await shoot(page));
  step('reload with the computer away: still 3 pending, in order');

  // ---- the computer is back; the first say_res is lost (socket dropped while the host holds it), the page resends
  st.dropNext = true;                            // say_res for the first message never arrives
  fake.setUp(true);
  await waitFor(page, `window.__ajState === 'ready'`, 15000);
  await waitFor(page, `document.querySelectorAll('#outbox .obi').length === 0`, 20000);
  assert.equal(st.dropNext, false, 'the first say_res was dropped');
  const sids = st.says.map((m) => m.sid);
  assert.equal(new Set(sids).size, 3, 'three distinct messages');
  assert.equal(sids[0], sids[1], 'the resend carried the same sid (the host answered dup)');
  assert.equal(st.says.length, 4);
  assert.deepEqual(st.says.map((m) => m.text), ['m-1 第一条', 'm-1 第一条', 'm-2 第二条', 'm-3 第三条']);
  assert.deepEqual(phoneTurns(), ['m-1 第一条', 'm-2 第二条', 'm-3 第三条'], 'the host received exactly 3, in order, no duplicate');
  assert.equal(await rec(page), null, 'the record is gone once everything went out');
  await waitFor(page, `document.getElementById('outbox').hidden`);
  writeFileSync(`${SHOTS}/offline-sent-390.png`, await shoot(page));
  step('back: exactly 3 delivered in order; lost say_res → resend → dup; pending lines gone');

  // ---- a message with a file, queued offline, then a reload: the file is gone, the line says so; on ready its words
  // come back to the field and nothing is sent without the file
  await page.send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  fake.kick();
  await waitFor(page, `window.__ajState !== 'ready'`);
  await evaluate(page, `(() => { const dt = new DataTransfer(); dt.items.add(new File(['hello'], 'note.txt', {type: 'text/plain'})); const f = document.getElementById('fDoc'); f.files = dt.files; f.dispatchEvent(new Event('change', {bubbles: true})); })()`);
  await waitFor(page, `document.querySelectorAll('#tray .chip').length === 1`);
  await send(page, 'm-4 带附件');
  await waitFor(page, `document.querySelectorAll('#outbox .obi').length === 1`);
  assert.equal(await evaluate(page, `document.querySelectorAll('#tray .chip').length`), 0, 'the file left the tray with its message');
  assert.equal((await pending(page))[0].lost, false);
  fake.setUp(false);
  await page.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await navigate(page, server.url);
  await waitFor(page, `document.querySelectorAll('#outbox .obi').length === 1`);
  const lost = (await pending(page))[0];
  assert.equal(lost.lost, true);
  assert.equal(lost.note, '刷新后附件没了，这条不会自动发出');
  writeFileSync(`${SHOTS}/offline-file-lost-390.png`, await shoot(page));
  const before = st.says.length;
  fake.setUp(true);
  await waitFor(page, `window.__ajState === 'ready'`, 15000);
  await waitFor(page, `document.getElementById('input').value === 'm-4 带附件'`);
  await waitFor(page, `document.getElementById('outbox').hidden`);
  assert.equal(st.says.length, before, 'not sent without its file');
  step('a queued file lost in a reload: said on its line, words back in the field, nothing sent');

  // ---- a message with a file, queued while the page stays open: on ready the file uploads, then the say carries it
  await evaluate(page, `(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); })()`);
  fake.setUp(false);
  await waitFor(page, `window.__ajState !== 'ready'`);
  await evaluate(page, `(() => { const dt = new DataTransfer(); dt.items.add(new File(['hello again'], 'note2.txt', {type: 'text/plain'})); const f = document.getElementById('fDoc'); f.files = dt.files; f.dispatchEvent(new Event('change', {bubbles: true})); })()`);
  await waitFor(page, `document.querySelectorAll('#tray .chip').length === 1`);
  await send(page, 'm-5 带文件');
  await waitFor(page, `document.querySelectorAll('#outbox .obi').length === 1`);
  assert.equal((await pending(page))[0].lost, false);
  fake.setUp(true);
  await waitFor(page, `document.getElementById('outbox').hidden`, 20000);
  const last = st.says[st.says.length - 1];
  assert.equal(last.text, 'm-5 带文件');
  assert.equal((last.att || []).length, 1, 'the file went with its message');
  assert.equal(st.turns[st.turns.length - 1].src.att[0].name, 'note2.txt');
  step('a queued file with the page open: uploaded on ready, sent with its message');

  // a resource the offline emulation itself refused (the whoosh sound loads lazily on the first Send) is not a page error
  page.problems = page.problems.filter((x) => !/ERR_INTERNET_DISCONNECTED/.test(x));
  assert.equal(page.problems.length, 0, page.problems.join('\n'));
  assert.equal(page.offsite.length, 0, page.offsite.join('\n'));
  console.log(`P57 offline browser PASS (${ok.length} steps) · shots in ${SHOTS}`);
} finally {
  console.log('parity results → ' + prec.write());          // steps never reached stay missing = failed
  if (page) await page.dispose().catch(() => {});
  await B.close(); await server.stop(); await fake.stop();
}
