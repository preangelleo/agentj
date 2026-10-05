// 0.15.2 (P57 lane "offline"): the phone's offline queue (public/js/outbox.js, PROTOCOL §14). Runs the shipped module in
// Node: sealing goes through the real store.js (AES-GCM under a non-extractable WebCrypto key) on a tiny in-memory
// IndexedDB; the drain state machine runs against fake say_res answers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
// ---- a minimal IndexedDB (one db, one store "kv"): what store.js uses, structured values kept as they are
const DATA = new Map();
globalThis.indexedDB = {
  open() {
    const r = {};
    setTimeout(() => {
      r.result = {
        createObjectStore() {}, close() {},
        transaction() {
          const tx = {};
          const store = {
            get: (k) => ({ result: DATA.get(k) }),
            put: (v, k) => { DATA.set(k, v); return { result: k }; },
            delete: (k) => { DATA.delete(k); return { result: undefined }; },
          };
          tx.objectStore = () => store;
          setTimeout(() => tx.oncomplete && tx.oncomplete());
          return tx;
        },
      };
      r.onsuccess();
    });
    return r;
  },
};

const { Outbox, verdict, MAX, pairedHost } = await import('../public/js/outbox.js');
const { dbPut, getSealed, wipeLocal } = await import('../public/js/store.js');
const { b64u } = await import('../public/proto/wire.js');

const sid = (i) => ('s' + String(i).padStart(3, '0')).padEnd(22, 'x');
const item = (i, extra = {}) => ({ sid: sid(i), text: '离线消息 ' + i, ts: 1700000000000 + i, rt: null, att: null, ...extra });
const HOST_A = { approved: true, relay: 'wss://relay.agentj.app', channel: 'chanA', hostPub: new Uint8Array(32).fill(7) };
const HOST_B = { approved: true, relay: 'wss://relay.agentj.app', channel: 'chanA', hostPub: new Uint8Array(32).fill(9) };
const keyOf = (h) => h.channel + ':' + b64u(h.hostPub);
const tick = () => new Promise((r) => setTimeout(r, 0));

test('P57 offline: verdict — ok / dup leave the queue, too_many / timeout wait, a drop stops, the rest are refused for good', () => {
  assert.equal(verdict({ ok: true, state: 'delivered' }), 'sent');
  assert.equal(verdict({ ok: true, state: 'queued' }), 'sent');
  assert.equal(verdict({ ok: false, why: 'dup' }), 'sent', 'dup = it had already arrived');
  for (const w of ['too_many', 'timeout', 'att_open']) assert.equal(verdict({ ok: false, why: w }), 'later', w);
  for (const w of ['offline', 'lost']) assert.equal(verdict({ ok: false, why: w }), 'stop', w);
  for (const w of ['stopped', 'shape', 'too_long', 'reply_unknown', 'no_agent', 'too_many_att', 'att_lost', 'att_failed', 'other']) assert.equal(verdict({ ok: false, why: w }), 'drop', w);
});

test('P57 offline: the queue is sealed in IndexedDB ({v, iv, ct}, no plaintext), survives a reload in order, and is bound to the computer', async () => {
  DATA.clear();
  await dbPut('host', HOST_A);
  assert.equal(await pairedHost(), keyOf(HOST_A));
  const ob = new Outbox();
  await ob.open();
  assert.equal(ob.host, keyOf(HOST_A));
  for (const i of [1, 2, 3]) assert.equal(ob.add(item(i, i === 2 ? { rt: { id: 41, excerpt: '摘一句' } } : {})), 'ok');
  await ob.wq;
  const raw = DATA.get('outbox');
  assert.deepEqual(Object.keys(raw).sort(), ['ct', 'iv', 'v']);
  assert.ok(raw.ct instanceof Uint8Array && raw.iv instanceof Uint8Array && raw.v === 1);
  const bytes = Buffer.from(raw.ct).toString('latin1');
  for (const needle of ['离线', Buffer.from('离线消息', 'utf8').toString('latin1'), sid(1), 'chanA', '摘一句']) assert.ok(!bytes.includes(needle), 'no plaintext: ' + needle);
  const rec = await getSealed('outbox');
  assert.equal(rec.host, keyOf(HOST_A));
  assert.deepEqual(rec.items.map((e) => e.sid), [sid(1), sid(2), sid(3)]);
  // a reload (a fresh module state) reads the same three, oldest first, quote kept
  const again = new Outbox();
  await again.open();
  assert.deepEqual(again.items.map((e) => e.text), ['离线消息 1', '离线消息 2', '离线消息 3']);
  assert.deepEqual(again.items[1].rt, { id: 41, excerpt: '摘一句' });
  // a message sent in the moment before the record was read comes after it
  const early = new Outbox();
  const opening = early.open();
  early.add(item(4));
  await opening; await early.wq;
  assert.deepEqual(early.items.map((e) => e.sid), [sid(1), sid(2), sid(3), sid(4)]);
  // another computer (same channel, other host key): the record is deleted unread, nothing goes to it
  await dbPut('host', HOST_B);
  const other = new Outbox();
  await other.open(); await other.wq;
  assert.deepEqual(other.items, []);
  assert.equal(DATA.has('outbox'), false, 'a queue for another computer is deleted');
});

test('P57 offline: unpair / revoke — wipeLocal deletes the record, wipe() empties memory and stops a late write', async () => {
  DATA.clear();
  await dbPut('host', HOST_A);
  const ob = new Outbox();
  await ob.open();
  ob.add(item(1)); await ob.wq;
  assert.ok(DATA.has('outbox'));
  ob.add(item(2));                     // a write still on its way …
  ob.wipe();                           // … when the phone is unpaired
  await wipeLocal();
  await ob.wq; await tick();
  assert.equal(DATA.has('outbox'), false, 'outbox deleted with the drafts');
  assert.equal(DATA.has('local'), false, 'and the key that sealed it');
  assert.deepEqual(ob.items, []);
});

test('P57 offline: at most 50 wait; the 51st is refused', async () => {
  const ob = new Outbox({ load: async () => null, save: async () => {}, del: async () => {}, hostKey: async () => 'H' });
  await ob.open();
  for (let i = 0; i < MAX; i++) assert.equal(ob.add(item(i)), 'ok');
  assert.equal(ob.add(item(99)), 'full');
  assert.equal(ob.items.length, 50);
  await ob.wq;
});

/** An outbox on an in-memory record + a scripted host: answers[i] for the i-th say (a function gets the item). */
async function rig(answers, { host = 'H', now = 'H' } = {}) {
  let saved = null;
  const says = [], done = [];
  const ctl = { live: true, now };
  const ob = new Outbox(
    { load: async () => saved, save: async (v) => { saved = structuredClone(v); }, del: async () => { saved = null; }, hostKey: async () => host },
    { say: async (e) => { says.push(e.sid); const a = answers.shift(); return typeof a === 'function' ? a(e) : a ?? { ok: true, state: 'delivered' }; },
      live: () => ctl.live, hostNow: () => ctl.now, done: (e, r, k) => done.push([e.sid, k, r.why || r.state]) });
  await ob.open();
  return { ob, says, done, ctl, saved: () => saved };
}

test('P57 offline: drain sends strictly in order, one say at a time; ok and dup leave, a refusal leaves with its reason', async () => {
  let open = 0, most = 0;
  const slow = (r) => async () => { open++; most = Math.max(most, open); await tick(); open--; return r; };
  const { ob, says, done, saved } = await rig([slow({ ok: true, state: 'delivered' }), slow({ ok: false, why: 'dup' }), slow({ ok: false, why: 'stopped' }), slow({ ok: true, state: 'queued' })]);
  for (const i of [1, 2, 3, 4]) ob.add(item(i));
  const [a, b] = [ob.drain(), ob.drain()];
  assert.equal(a, b, 'single flight');
  assert.equal(await a, 'empty');
  assert.equal(most, 1, 'never two says in flight');
  assert.deepEqual(says, [sid(1), sid(2), sid(3), sid(4)]);
  assert.deepEqual(done, [[sid(1), 'sent', 'delivered'], [sid(2), 'sent', 'dup'], [sid(3), 'drop', 'stopped'], [sid(4), 'sent', 'queued']]);
  await ob.wq;
  assert.equal(saved(), null, 'an empty queue leaves no record');
});

test('P57 offline: too_many keeps the message first and retries later; a drop mid-drain stops and keeps the rest; the resend uses the same sid and dup ends it', async () => {
  const { ob, says, done, ctl, saved } = await rig([{ ok: false, why: 'too_many' }]);
  for (const i of [1, 2]) ob.add(item(i));
  assert.equal(await ob.drain(), 'later');
  assert.ok(ob.timer, 'a retry is scheduled');
  clearTimeout(ob.timer);
  assert.deepEqual(ob.items.map((e) => e.sid), [sid(1), sid(2)]);
  // the connection drops while the host has taken say 1 but its say_res never comes back
  let release;
  const { ob: o2, says: s2, done: d2, ctl: c2 } = await rig([() => new Promise((r) => { release = r; }), { ok: false, why: 'dup' }, { ok: true, state: 'delivered' }]);
  for (const i of [1, 2, 3]) o2.add(item(i));
  const run = o2.drain();
  await tick();
  assert.deepEqual(s2, [sid(1)]);
  c2.live = false;
  o2.lost();                           // relay.setConn(false)
  assert.equal(await run, 'stop');
  assert.deepEqual(o2.items.map((e) => e.sid), [sid(1), sid(2), sid(3)], 'nothing lost, nothing reordered');
  assert.equal(await o2.drain(), 'stop', 'not connected: nothing is sent');
  assert.deepEqual(s2, [sid(1)]);
  c2.live = true;                      // ready again
  assert.equal(await o2.drain(), 'empty');
  assert.deepEqual(s2, [sid(1), sid(1), sid(2), sid(3)], 'the resend carries the sid chosen at queue time');
  assert.deepEqual(d2, [[sid(1), 'sent', 'dup'], [sid(2), 'sent', 'delivered'], [sid(3), 'sent', 'delivered']]);
  // the late answer to the abandoned first say changes nothing
  release({ ok: true, state: 'delivered' });
  await tick();
  assert.deepEqual(o2.items, []);
  assert.equal(d2.length, 3);
  void says; void done; void ctl; void saved;
});

test('P57 offline: never to another computer — a ready session to a different host sends nothing', async () => {
  const { ob, says, ctl } = await rig([], { host: 'H', now: 'OTHER' });
  ob.add(item(1));
  assert.equal(await ob.drain(), 'stop');
  assert.deepEqual(says, []);
  ctl.now = null;
  assert.equal(await ob.drain(), 'stop');
  ctl.now = 'H';
  assert.equal(await ob.drain(), 'empty');
  assert.deepEqual(says, [sid(1)]);
  const np = await rig([], { host: null, now: null });
  np.ob.add(item(1));
  assert.equal(await np.ob.drain(), 'stop', 'not paired: never sent');
});

test('P57 offline: a wipe during a say ends the drain — no result handled, nothing written back', async () => {
  let release;
  const { ob, done, saved } = await rig([() => new Promise((r) => { release = r; })]);
  ob.add(item(1)); ob.add(item(2));
  await ob.wq;
  const run = ob.drain();
  await tick();
  ob.wipe();
  release({ ok: true, state: 'delivered' });
  assert.equal(await run, 'wiped');
  assert.deepEqual(done, []);
  assert.deepEqual(ob.items, []);
  assert.ok(saved() && saved().items.length === 2, 'the record itself is store.wipeLocal\'s to delete (same tick in forgetLocal)');
});
