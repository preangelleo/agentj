// Audit 2026-11-22, phone web — the reconnect ladder over a failing store (N-W1 / N-W3) and the web terminal's own writer
// (N-W4). The real js/session.js, js/store.js and js/blobs.js with a stand-in WebSocket and the stand-in IndexedDB of
// audit1122-idb.mjs; the terminal's deadline tick and its send are the shipped functions, cut out of js/terminal.js.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { withStorage, sleep } from './audit1122-idb.mjs';
import { padJson, unpadJson, KIND, MAX_JSON_P33 } from '../public/proto/wire.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
function env() {
  const old = { doc: globalThis.document, ws: globalThis.WebSocket, add: globalThis.addEventListener };
  const on = {};
  globalThis.document = { visibilityState: 'visible', addEventListener: (n, fn) => { on[n] = fn; } };
  globalThis.addEventListener = (n, fn) => { on[n] = fn; };
  const sockets = [];
  class WS {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 1; this.closed = null; this.sent = []; sockets.push(this); }
    close(c) { this.closed = c; this.readyState = 3; }
    send(b) { this.sent.push(b); }
  }
  globalThis.WebSocket = WS;
  return { on, sockets, restore() { globalThis.document = old.doc; globalThis.WebSocket = old.ws; globalThis.addEventListener = old.add; } };
}
const HOST = { relay: 'ws://127.0.0.1:1', channel: 'h'.repeat(22), hostPub: new Uint8Array(32).fill(3), approved: true };
/** What app.js resume() does with the record it read, as hooks the test can count (the page's own function is checked below). */
function pageHooks(S, Store, seen) {
  return {
    setStatus: (s, k) => seen.status.push(k),
    onHostDown() { seen.down++; }, onReady() {}, helloExtra: () => ({}),
    onProtocolFail() { seen.protocolFail++; },
    async resume() {
      seen.resumes++;
      const host = await Store.dbGet('host');
      if (!host || !host.approved) { seen.idle++; return; }        // the pairing screen
      S.openSession('resume', { relay: host.relay, channel: host.channel, hostPub: new Uint8Array(host.hostPub) });
    },
  };
}
const fresh = () => ({ status: [], down: 0, idle: 0, resumes: 0, protocolFail: 0 });

test('N-W1: a paired phone whose store fails mid-page is never sent to the pairing screen; the ladder re-arms and recovers', () => withStorage(async ({ F, fresh: store }) => {
  const E = env(), seen = fresh();
  const Store = await store('ladder-a');
  const S = await import('../public/js/session.js?audit1122-a');
  S.configure(pageHooks(S, Store, seen));
  try {
    await Store.dbPut('host', HOST);                               // paired; IndexedDB worked on this page
    Store.markPaired(true);
    const [db] = F.log.live; db.loseConnection();                  // iOS back from the background …
    F.ctl.open = 'fail';                                           // … and the re-open fails
    S.scheduleReconnect();
    for (let i = 0; i < 40 && seen.resumes < 3; i++) await sleep(50);
    assert.equal(seen.idle, 0, 'no pairing screen from a failed read');
    assert.ok(seen.resumes >= 3, 'the ladder kept going: ' + seen.resumes + ' attempts');
    assert.equal(S.current(), null);
    assert.ok(seen.status.filter((k) => k === 'st.retryIn').length >= 3, 'the retry line is on screen');
    F.ctl.open = 'ok';                                             // storage is back
    for (let i = 0; i < 140 && !S.current(); i++) await sleep(50);
    assert.ok(S.current(), 'a later rung reads the real record and opens the session');
    assert.equal(E.sockets.at(-1).url, HOST.relay + '/v1/dev/' + HOST.channel);
    assert.equal(seen.idle, 0);
    assert.deepEqual(F.data().get('host'), HOST, 'the pairing record is untouched');
  } finally { S.closeSession(); E.restore(); }
}));

test('N-W3: a resume whose store open never answers settles, re-arms the ladder, and recovers', () => withStorage(async ({ F, fresh: store }) => {
  const E = env(), seen = fresh();
  F.data().set('host', HOST);
  F.ctl.open = 'hang';
  const Store = await store('ladder-b');
  const S = await import('../public/js/session.js?audit1122-b');
  S.configure(pageHooks(S, Store, seen));
  try {
    S.scheduleReconnect();
    for (let i = 0; i < 180 && seen.resumes < 2; i++) await sleep(50);   // one bounded open (4 s) + the next rung
    assert.ok(seen.resumes >= 2, 'a hung read does not end the ladder — attempts: ' + seen.resumes);
    assert.equal(seen.idle, 0);
    F.ctl.open = 'ok';
    for (let i = 0; i < 240 && !S.current(); i++) await sleep(50);
    assert.ok(S.current(), 'the session opens once the store answers');
    assert.equal(seen.idle, 0);
  } finally { S.closeSession(); E.restore(); }
}, { paired: true }));

test('N-W2: a device key that cannot be read at the handshake retries the resume — no new key, no protocol failure', () => withStorage(async ({ F }) => {
  const E = env(), seen = fresh();
  F.ctl.open = 'fail';                                             // session.js's own store.js: paired marker, store down
  const S = await import('../public/js/session.js?audit1122-c');
  S.configure({ ...pageHooks(S, { dbGet: async () => HOST }, seen) });
  try {
    const s = S.openSession('resume', { relay: HOST.relay, channel: HOST.channel, hostPub: HOST.hostPub });
    s.ws.onmessage({ data: JSON.stringify({ t: 'host', up: true }) });
    await s.chain;
    assert.equal(seen.protocolFail, 0, 'not a protocol failure');
    assert.deepEqual(s.ws.sent, [], 'no handshake was sent under a made-up key');
    assert.deepEqual(F.log.puts, [], 'no key was written');
    assert.equal(seen.down, 1);
    assert.ok(S.retryInfo().nextAt, 'the next rung is pending');
  } finally { S.closeSession(); E.restore(); }
}, { paired: true }));

test('N-W1: app.js — resume() and boot treat a failed read as "try again", never as "not paired"', () => {
  const APP = read('../public/app.js');
  const resume = APP.slice(APP.indexOf('async function resume() {'), APP.indexOf('// ---------------------------------------------------------------- pairing input'));
  assert.match(resume, /try \{ host = await dbGet\('host'\); \} catch \(e\) \{\n    if \(!isStorageError\(e\)\) throw e;\n    if \(!session\.current\(\) && !session\.retryInfo\(\)\.nextAt\) session\.scheduleReconnect\(\);\n    return;\n  \}\n  if \(!host \|\| !host\.approved\) \{ showIdle\(\); return; \}/,
    'showIdle() only after a read that completed');
  const main = APP.slice(APP.indexOf('async function main() {'), APP.indexOf('\nmain();'));
  assert.match(main, /try \{ await bootRead\(deviceKey\); \} catch/);
  assert.match(main, /try \{ host = await bootRead\(\(\) => dbGet\('host'\)\); \} catch \{ return fatal\('error\.noStore'\); \}[^\n]*\n[^\n]*\n  let lost = !host && wasPaired\(\);/,
    '`lost` (「浏览器删了数据」) is decided only from a read that completed');
  assert.doesNotMatch(main, /await dbGet\('host'\)\.catch/);
});

// ---------------------------------------------------------------- N-W4
const TERM = read('../public/js/terminal.js');
function cut(name) {
  const i = TERM.indexOf(`\nfunction ${name}(`);
  assert.ok(i >= 0, name);
  return TERM.slice(i + 1, TERM.indexOf('\n}\n', i) + 3);
}
const decode = (ws) => ws.sent.map((f) => unpadJson(new Uint8Array(f).subarray(1), MAX_JSON_P33));

test('N-W4: the terminal\'s local deadline ends only the terminal\'s writer — a pending ask and an upload in flight finish, no reconnect', async () => {
  const E = env(), seen = fresh();
  const S = await import('../public/js/session.js');                // the instance js/blobs.js sends on
  const Blobs = await import('../public/js/blobs.js');
  S.configure({ setStatus: (s, k) => seen.status.push(k), onHostDown() { seen.down++; }, onReady() {}, helloExtra: () => ({}), resume() { seen.resumes++; },
    onApp: (m) => { Blobs.handle(m); } });
  try {
    const s = S.openSession('resume', { relay: HOST.relay, channel: HOST.channel, hostPub: HOST.hostPub });
    // a completed handshake, without the crypto: frames are the padded JSON itself
    s.gen = { send: { encrypt: async (_ad, pt) => pt }, live: true, chain: Promise.resolve() };
    const fromHost = async (m) => { s.recv = { decrypt: async () => padJson(m, MAX_JSON_P33) }; s.ws.onmessage({ data: new Uint8Array([KIND.DATA]).buffer }); await s.chain; };
    s.phase = 'ready-wait';
    await fromHost({ t: 'ready', caps: ['p33'] });
    assert.equal(S.isReady(), true);
    const G = S.gen();

    // 1. a pending ask and an upload in flight on the chat session
    const ask = S.ask1({ t: 'fs_list', path: '~' }, 'fs_res', 8000);
    const bytes = new Uint8Array(60000).fill(65);
    const up = new Blobs.Upload({ size: bytes.length, type: 'text/plain', name: 'a.txt', arrayBuffer: async () => bytes.buffer }, { purpose: 'att' });
    const upload = up.start();
    for (let i = 0; i < 100 && !decode(s.ws).some((m) => m.t === 'blob_open'); i++) await sleep(10);
    const r = decode(s.ws).find((m) => m.t === 'fs_list').r;

    // 2. the web terminal, ready on the same generation, with a keystroke still waiting behind the pacer
    const T = { phase: 'ready', G, W: S.newWriter ? S.newWriter() : undefined, id: 'ab'.repeat(8), deadline: 1000, pty: true };
    const box = { S: T, gen: S.gen, sendApp: S.sendApp, disconnectGeneration: S.disconnectGeneration, endWriter: S.endWriter,
      performance: { now: () => box.clock }, clock: 0, closedWhy: [], droppedCalls: 0,
      closed(why) { box.closedWhy.push(why); T.phase = 'closed'; }, dropped() { box.droppedCalls++; }, paintLeft() {}, paintChip() {} };
    vm.runInNewContext(cut('send') + cut('onTick') + '\nglobalThis.send = send; globalThis.onTick = onTick;', box);
    let held = true;
    s.pacer = { wait: () => (held ? 20 : 0), mark() {}, boost: false };
    box.send({ t: 'term_in', id: T.id, seq: 1, d: 'x' });           // queued: waits in the pacer
    const chat = S.sendApp({ t: 'say', text: 'still here' }).then(() => true, () => false);   // a chat frame queued behind it

    // 3. the terminal's own deadline passes
    box.clock = 1001;
    box.onTick();
    assert.deepEqual(box.closedWhy, ['timeout'], 'the terminal closed itself');
    held = false;
    await chat;
    await sleep(60);

    assert.equal(S.current(), s, 'the chat session is the same session');
    assert.equal(S.isReady(), true);
    assert.equal(S.gen(), G, 'same Noise generation');
    assert.equal(s.ws.closed, null, 'the socket was not closed');
    assert.equal(seen.down, 0, 'no host-down / reconnect');
    assert.equal(S.retryInfo().nextAt, null);
    assert.equal(seen.resumes, 0);
    const out = decode(s.ws);
    assert.equal(out.some((m) => m.t === 'term_in'), false, 'the terminal frame queued before the deadline never reached the socket');
    assert.equal(out.some((m) => m.t === 'say' && m.text === 'still here'), true, 'the chat frame behind it did');
    await assert.rejects(S.sendApp({ t: 'term_in', id: T.id, seq: 2, d: 'y' }, T.G, T.W), 'nothing more goes out on the ended writer');

    // 4. the ask and the upload settle normally
    await fromHost({ t: 'fs_res', r, ok: true, entries: [] });
    assert.deepEqual(await ask, { t: 'fs_res', r, ok: true, entries: [] }, 'the pending ask got its answer');
    await fromHost({ t: 'blob_ack', bid: up.bid, next: 0 });
    for (let i = 0; i < 100 && !decode(s.ws).some((m) => m.t === 'blob_end'); i++) await sleep(10);
    assert.equal(decode(s.ws).filter((m) => m.t === 'blob_chunk').length, 2, 'the upload went on sending');
    await fromHost({ t: 'blob_done', bid: up.bid, ok: true, kind: 'file', bytes: bytes.length });
    assert.deepEqual(await upload, { ok: true, kind: 'file', bytes: bytes.length }, 'the upload finished');
    assert.equal(box.droppedCalls, 0);
  } finally { S.closeSession(); E.restore(); }
});

test('N-W4: terminal.js gives every terminal session its own writer and sends everything on it', () => {
  const code = TERM.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.match(cut('ready'), /S\.W = newWriter\(\);/, 'a fresh writer per opened terminal');
  assert.match(cut('send'), /sendApp\(obj, writer\.G, writer\.W\)/);
  assert.doesNotMatch(cut('onTick'), /disconnectGeneration/, 'the deadline no longer drops the chat session');
  for (const m of code.matchAll(/sendApp\(([^;]*);/g)) assert.match(m[1], /\.W\)/, 'every terminal frame rides the writer: ' + m[0]);
});
