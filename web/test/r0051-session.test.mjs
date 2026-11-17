// R-0051 W1 / W2: the reconnect ladder survives a failing resume; a silent socket that never got to `ready` is replaced when the
// page comes back to the front or the network comes back. Driven through the real js/session.js with a stand-in WebSocket.
import {test} from 'node:test';
import assert from 'node:assert/strict';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function env() {
  const old = {doc: globalThis.document, ws: globalThis.WebSocket, add: globalThis.addEventListener};
  const on = {};
  globalThis.document = {visibilityState: 'visible', addEventListener: (n, fn) => { on[n] = fn; }};
  globalThis.addEventListener = (n, fn) => { on[n] = fn; };
  const sockets = [];
  class WS { static OPEN = 1; constructor(url) { this.url = url; this.readyState = 1; this.closed = null; sockets.push(this); } close(c) { this.closed = c; this.readyState = 3; } send() {} }
  globalThis.WebSocket = WS;
  return {on, sockets, restore() { globalThis.document = old.doc; globalThis.WebSocket = old.ws; globalThis.addEventListener = old.add; }};
}
const ctx = {relay: 'ws://127.0.0.1:1', channel: 'c'.repeat(22), hostPub: new Uint8Array(32)};
const hooks = (S, resume, log = []) => ({setStatus: (s, k) => log.push(k), resume, onHostDown() { log.push('down'); }, onReady() {}, helloExtra: () => ({})});

test('W1: a resume that rejects (IndexedDB lost) keeps the reconnect ladder going', async () => {
  const E = env();
  const S = await import('../public/js/session.js?w1a');
  let calls = 0;
  S.configure(hooks(S, async () => { calls++; if (calls < 3) throw new Error('connection to IndexedDB server lost'); S.openSession('resume', ctx); }));
  try {
    S.scheduleReconnect();
    for (let i = 0; i < 60 && !S.current(); i++) await sleep(50);
    assert.equal(calls, 3, 'two failed resumes, then the third opens');
    assert.ok(S.current(), 'a session is open after the failures');
    assert.equal(S.retryInfo().attempts, 3, 'each failure is a real scheduled rung');
  } finally { S.closeSession(); E.restore(); }
});

test('W1: a resume that throws synchronously, and one from reconnectNow, reschedule too', async () => {
  const E = env();
  const S = await import('../public/js/session.js?w1b');
  let calls = 0;
  S.configure(hooks(S, () => { calls++; if (calls === 1) throw new Error('sync'); if (calls === 2) return Promise.reject(new Error('async')); S.openSession('resume', ctx); }));
  try {
    S.scheduleReconnect();
    S.reconnectNow('manual');                              // fires the pending rung at once → throws synchronously
    assert.equal(calls, 1);
    await sleep(0);
    assert.ok(S.retryInfo().nextAt, 'a new rung is pending after the synchronous throw');
    for (let i = 0; i < 60 && !S.current(); i++) await sleep(50);
    assert.equal(calls, 3);
    assert.ok(S.current());
  } finally { S.closeSession(); E.restore(); }
});

test('W1 control: a resume that succeeds without a session (unpaired → idle) does not loop', async () => {
  const E = env();
  const S = await import('../public/js/session.js?w1c');
  let calls = 0;
  S.configure(hooks(S, async () => { calls++; }));
  try {
    S.scheduleReconnect();
    await sleep(700);
    assert.equal(calls, 1, 'no retry when resume settled normally');
    assert.equal(S.retryInfo().nextAt, null);
  } finally { S.closeSession(); E.restore(); }
});

async function relaySays(s, m) { s.ws.onmessage({data: JSON.stringify(m)}); await s.chain; }
test('W2: a silent wait-host socket is replaced on foreground and on online, without a status flicker', async () => {
  const E = env();
  const S = await import('../public/js/session.js?w2a');
  const log = []; let resumed = 0;
  S.configure(hooks(S, async () => { resumed++; S.openSession('resume', ctx); }, log));
  try {
    let s = S.openSession('resume', ctx);
    await relaySays(s, {t: 'host', up: false});
    assert.equal(s.phase, 'wait-host');
    log.length = 0;
    s.heardAt = Date.now() - 16000;                         // a zombie: nothing heard since the relay's last word
    E.on.visibilitychange();
    for (let i = 0; i < 20 && S.current() === s; i++) await sleep(5);
    assert.equal(resumed, 1, 'foreground reopens it');
    assert.equal(s.ws.closed, 1000, 'the zombie socket is closed');
    assert.notEqual(S.current(), s);
    assert.equal(E.sockets.length, 2);
    S.current().ws.onopen();
    assert.deepEqual(log.filter((k) => k !== 'down'), [], 'no 「正在连接」/「等待电脑」 repaint on the quiet reopen');
    s = S.current();
    await relaySays(s, {t: 'host', up: false});
    assert.deepEqual(log, ['down', 'st.hostDown'], 'the relay answers the new socket with the same status');
    s.heardAt = Date.now() - 16000;
    E.on.online();
    for (let i = 0; i < 20 && S.current() === s; i++) await sleep(5);
    assert.equal(resumed, 2, 'network back reopens it');
    log.length = 0;
    S.closeSession(); S.openSession('resume', ctx);         // a later ordinary open paints as before
    assert.deepEqual(log, ['st.connecting']);
  } finally { S.closeSession(); E.restore(); }
});

test('W2 controls: a socket heard recently and a pairing are left alone', async () => {
  const E = env();
  const S = await import('../public/js/session.js?w2b');
  let resumed = 0;
  S.configure(hooks(S, async () => { resumed++; S.openSession('resume', ctx); }));
  try {
    let s = S.openSession('resume', ctx);
    await relaySays(s, {t: 'host', up: false});
    s.heardAt = Date.now() - 5000;
    E.on.visibilitychange(); await sleep(20);
    assert.equal(resumed, 0); assert.equal(S.current(), s); assert.equal(s.ws.closed, null);
    s = S.openSession('pair', {...ctx, expires: 2 ** 31});
    s.heardAt = Date.now() - 60000;
    E.on.visibilitychange(); await sleep(20);
    assert.equal(resumed, 0, 'a pairing is never replaced (its code is on screen)'); assert.equal(S.current(), s);
  } finally { S.closeSession(); E.restore(); }
});
