// 0.16 (P71 F27): files alone are a message — the phone's offline queue (public/js/outbox.js) keeps and sends one with
// text "", the same rule relay.js's Send now follows (words OR at least one attachment). Runs the shipped module in Node on
// the tiny in-memory IndexedDB of p57_offline.test.mjs.
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

const { Outbox, pairedHost } = await import('../public/js/outbox.js');
const { dbPut } = await import('../public/js/store.js');
const { b64u } = await import('../public/proto/wire.js');

const sid = (i) => ('f' + String(i).padStart(3, '0')).padEnd(22, 'x');
const HOST = { approved: true, relay: 'wss://relay.agentj.app', channel: 'chanA', hostPub: new Uint8Array(32).fill(7) };
const keyOf = (h) => h.channel + ':' + b64u(h.hostPub);
const tick = () => new Promise((r) => setTimeout(r, 0));

test('F27 offline: a message of files alone (text "") is kept across a reload; spaces alone without files are not', async () => {
  DATA.clear();
  await dbPut('host', HOST);
  assert.equal(await pairedHost(), keyOf(HOST));
  const ob = new Outbox();
  await ob.open();
  const base = { ts: 1700000000000, rt: null };
  ob.add({ ...base, sid: sid(1), text: '', att: [{ name: '语音-1.wav', kind: 'audio' }] });
  ob.add({ ...base, sid: sid(2), text: '   ', att: null });
  ob.add({ ...base, sid: sid(3), text: ' ', att: [{ name: 'photo.png', kind: 'image' }] });
  await ob.wq;
  const again = new Outbox();
  await again.open();
  assert.deepEqual(again.items.map((e) => e.sid), [sid(1), sid(3)], 'files alone stay; spaces alone are dropped on load');
});

test('F27 offline: the drain sends a files-alone message as it is (text "")', async () => {
  DATA.clear();
  await dbPut('host', HOST);
  const said = [];
  let live = false;
  const ob = new Outbox({}, { say: async (e) => { said.push(e); return { ok: true, state: 'queued' }; }, live: () => live, hostNow: () => keyOf(HOST) });
  await ob.open();
  ob.add({ sid: sid(4), text: '', ts: 1700000000001, rt: null, att: [{ name: 'note.txt', kind: 'file' }] });
  await ob.wq;
  live = true;
  await ob.drain();
  for (let i = 0; i < 20 && ob.items.length; i++) await tick();
  assert.equal(said.length, 1);
  assert.equal(said[0].text, '');
  assert.deepEqual(said[0].att, [{ name: 'note.txt', kind: 'file' }]);
  assert.equal(ob.items.length, 0);
});
