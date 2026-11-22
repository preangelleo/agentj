// Audit 2026-11-22 (N-W1), the offline queue (js/outbox.js): a queue record that could not be READ is unknown, not empty.
// Until it has been read, nothing is written or deleted over it; once the store answers, the stored messages and the ones
// typed meanwhile are both there.
import { test } from 'node:test';
import assert from 'node:assert/strict';

if (!globalThis.indexedDB) globalThis.indexedDB = { open() { throw new Error('unused'); } };
const { Outbox } = await import('../public/js/outbox.js');

const sid = (i) => ('s' + String(i).padStart(3, '0')).padEnd(22, 'x');
const item = (i) => ({ sid: sid(i), text: 'queued ' + i, ts: 1700000000000 + i, rt: null, att: null });
const storageError = () => Object.assign(new Error('storage: tx'), { name: 'StorageError', why: 'tx' });
const settle = () => new Promise((r) => setTimeout(r, 20));

test('N-W1 outbox: an unreadable queue is not overwritten or deleted; after the store recovers both old and new messages are kept', async () => {
  let down = true, saved = { v: 1, host: 'H', items: [item(1), item(2)] };
  const writes = [];
  const io = {
    hostKey: async () => { if (down) throw storageError(); return 'H'; },
    load: async () => { if (down) throw storageError(); return structuredClone(saved); },
    save: async (v) => { writes.push('save'); saved = structuredClone(v); },
    del: async () => { writes.push('del'); saved = null; },
  };
  const ob = new Outbox(io, { live: () => false });
  await ob.open();
  assert.equal(ob.add(item(3)), 'ok');                             // typed while the store is down
  await ob.wq; await settle();
  assert.deepEqual(writes, [], 'nothing written or deleted while the stored queue is unknown — got ' + JSON.stringify(writes));
  assert.deepEqual(saved.items.map((e) => e.sid), [sid(1), sid(2)], 'the stored queue is intact');
  assert.equal(ob.drain !== undefined && (await ob.drain()), 'later', 'a drain in that state waits and retries');
  clearTimeout(ob.timer);
  down = false;                                                    // the store is back
  await ob.persist(); await settle();
  assert.deepEqual(ob.items.map((e) => e.sid), [sid(1), sid(2), sid(3)], 'stored messages first, then the one typed meanwhile');
  assert.deepEqual(saved.items.map((e) => e.sid), [sid(1), sid(2), sid(3)]);
  assert.equal(ob.host, 'H');
  assert.deepEqual(writes, ['save']);
});

test('N-W1 outbox control: an ordinary unreadable record (not a storage failure) still reads as an empty queue', async () => {
  let saved = null;
  const ob = new Outbox({ hostKey: async () => 'H', load: async () => { throw new Error('garbled'); }, save: async (v) => { saved = structuredClone(v); }, del: async () => { saved = null; } }, { live: () => false });
  await ob.open();
  ob.add(item(1));
  await ob.wq;
  assert.deepEqual(saved.items.map((e) => e.sid), [sid(1)]);
});
