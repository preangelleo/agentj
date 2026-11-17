// R-0051 W12: js/store.js keeps one IndexedDB connection for its key-value operations; a connection the browser closed is
// replaced once; a store that cannot be opened at all still falls back to memory (W1). A small stand-in IndexedDB counts opens.
import {test} from 'node:test';
import assert from 'node:assert/strict';

function fakeIDB({failOpen = false, reqDelay = 0, failPut = null} = {}) {
  const data = new Map(), stats = {opens: 0, closes: 0, live: new Set()};
  const later = (fn) => setTimeout(fn, 0);
  class DB {
    constructor() { this.closed = false; this.lost = false; this.pending = new Set(); stats.live.add(this); }
    createObjectStore() {}
    close() { if (!this.closed) { this.closed = true; stats.closes++; stats.live.delete(this); } }
    transaction(name, mode) {
      if (this.closed || this.lost) { const e = new Error('The database connection is closing.'); e.name = this.lostName || 'InvalidStateError'; throw e; }
      const tx = {error: null, aborted: false};
      this.pending.add(tx);
      const req = (fnv) => {
        const r = {result: undefined};
        setTimeout(() => {
          if (tx.aborted) return;
          try { r.result = fnv(); } catch (e) { tx.error = e; this.pending.delete(tx); tx.onerror && tx.onerror(); return; }
          later(() => { if (tx.aborted) return; this.pending.delete(tx); tx.oncomplete && tx.oncomplete(); });
        }, reqDelay);
        return r;
      };
      tx.objectStore = () => ({get: (k) => req(() => data.get(k)), put: (v, k) => req(() => {
        if (mode !== 'readwrite') throw new Error('ro');
        if (failPut && k === failPut) { const e = new Error('Key already exists'); e.name = 'ConstraintError'; throw e; }
        data.set(k, v); return k; }), delete: (k) => req(() => { data.delete(k); })});
      return tx;
    }
    // the browser drops the connection (iOS back from the background)
    loseConnection() { this.lost = true; stats.live.delete(this); this.onclose && this.onclose(); }
    // r1b F3 order: a forced close aborts the running transactions first; onclose comes later (or never)
    forceLoss(errorName = 'AbortError', closeAfter = 30) {
      this.lost = true; stats.live.delete(this);
      for (const tx of this.pending) {
        tx.aborted = true; tx.error = errorName ? Object.assign(new Error('aborted'), {name: errorName}) : null;
        tx.onabort && tx.onabort();
      }
      this.pending.clear();
      if (closeAfter !== null) setTimeout(() => this.onclose && this.onclose(), closeAfter);
    }
  }
  const indexedDB = {
    open() {
      stats.opens++;
      const r = {};
      later(() => {
        if (failOpen) { r.error = new Error('UnknownError'); r.onerror && r.onerror(); return; }
        r.result = new DB(); if (stats.opens === 1) r.onupgradeneeded && r.onupgradeneeded(); r.onsuccess && r.onsuccess();
      });
      return r;
    },
  };
  return {indexedDB, data, stats};
}
async function withStore(tag, opts, body) {
  const old = globalThis.indexedDB, F = fakeIDB(opts);
  globalThis.indexedDB = F.indexedDB;
  try { const S = await import('../public/js/store.js?' + tag); await body(S, F); } finally { globalThis.indexedDB = old; }
}

test('W12: many key-value operations share one connection', () => withStore('w12a', {}, async (S, F) => {
  for (let i = 0; i < 40; i++) { await S.dbPut('k' + i, i); assert.equal(await S.dbGet('k' + i), i); }
  await S.dbDel('k0');
  assert.equal(await S.dbGet('k0'), undefined);
  assert.equal(F.stats.opens, 1, 'one open for 82 operations');
  assert.equal(F.data.get('k39'), 39, 'written to the database, not to the memory fallback');
}));

test('W12: concurrent first operations wait for the same open', () => withStore('w12b', {}, async (S, F) => {
  await Promise.all(Array.from({length: 10}, (_, i) => S.dbPut('c' + i, i)));
  assert.equal(F.stats.opens, 1);
  assert.equal(F.data.size, 10);
}));

test('W12: a connection the browser lost is replaced, and storage stays persistent', () => withStore('w12c', {}, async (S, F) => {
  await S.dbPut('a', 1);
  const [db] = F.stats.live; db.loseConnection();
  await S.dbPut('b', 2);
  assert.equal(F.stats.opens, 2, 'reopened once');
  assert.equal(F.data.get('b'), 2, 'the write reached the database');
  const [db2] = F.stats.live; db2.lost = true;              // lost without a close event: the transaction itself throws
  assert.equal(await S.dbGet('a'), 1);
  assert.equal(F.stats.opens, 3);
  await S.dbPut('c', 3);
  assert.equal(F.data.get('c'), 3, 'still persistent after a silent loss');
  assert.equal(F.stats.opens, 3);
}));

test('W12: another tab upgrading closes this connection; the next operation opens a new one', () => withStore('w12d', {}, async (S, F) => {
  await S.dbPut('a', 1);
  const [db] = F.stats.live; db.onversionchange();
  assert.equal(db.closed, true);
  assert.equal(await S.dbGet('a'), 1);
  assert.equal(F.stats.opens, 2);
}));

test('W1 regression: a store that never opens falls back to memory and keeps working', () => withStore('w12e', {failOpen: true}, async (S, F) => {
  await S.dbPut('host', {approved: true});
  assert.deepEqual(await S.dbGet('host'), {approved: true});
  assert.equal(F.data.size, 0);
  const opens = F.stats.opens;
  await S.dbPut('x', 1);
  assert.equal(F.stats.opens, opens, 'no further opens once in memory mode');
}));

const tick = () => new Promise((r) => setImmediate(r));
async function pendingOn(F, n) { for (let i = 0; i < 200; i++) { const [db] = F.stats.live; if (db && db.pending.size >= n) return db; await tick(); } throw new Error('no pending transactions'); }

for (const [name, closeAfter] of [['AbortError', 30], ['UnknownError', 30], [null, null]]) {
  test(`r1b F3: abort (${name ?? 'no error'}) before close — pending read and write finish on a fresh connection`, () =>
    withStore('f3-' + (name || 'null'), {reqDelay: 15}, async (S, F) => {
      await S.dbPut('host', {approved: true});                 // persisted pairing state
      const read = S.dbGet('host'), write = S.dbPut('draft', 'x');
      const db = await pendingOn(F, 2);
      db.forceLoss(name, closeAfter);                         // aborts land before onclose (or with no onclose at all)
      assert.deepEqual(await read, {approved: true}, 'the read is served from the database, not empty memory');
      assert.equal(await write, 'draft');
      assert.equal(F.data.get('draft'), 'x', 'the write reached the database');
      assert.equal(F.stats.opens, 2, 'one fresh connection for both');
      await new Promise((r) => setTimeout(r, 40));
      assert.deepEqual(await S.dbGet('host'), {approved: true}, 'later operations stay on the database');
      assert.equal(F.stats.opens, 2);
    }));
}

test('r1b F3: InvalidStateError / TransactionInactiveError from a dying connection retry once', () => withStore('f3-ise', {}, async (S, F) => {
  await S.dbPut('host', 1);
  const [db] = F.stats.live; db.lost = true;               // transaction() throws InvalidStateError, no close event
  assert.equal(await S.dbGet('host'), 1);
  assert.equal(F.stats.opens, 2);
  const [db2] = F.stats.live; db2.lost = true; db2.lostName = 'TransactionInactiveError';
  await S.dbPut('k', 3);
  assert.equal(F.data.get('k'), 3);
  assert.equal(F.stats.opens, 3);
}));

test('r1b F3 control: a store error (ConstraintError) is not retried and falls back to memory as before', () =>
  withStore('f3-store', {failPut: 'bad'}, async (S, F) => {
    await S.dbPut('host', 1);
    await S.dbPut('bad', 2);
    assert.equal(F.stats.opens, 1, 'no reconnect for a store error');
    assert.equal(F.data.has('bad'), false);
    assert.equal(await S.dbGet('bad'), 2, 'memory fallback (W1)');
  }));

test('r1b F3 control: a fresh connection that fails again falls back to memory — only one retry', () => withStore('f3-twice', {reqDelay: 15}, async (S, F) => {
  await S.dbPut('host', 1);
  const read = S.dbGet('host');
  (await pendingOn(F, 1)).forceLoss('AbortError', null);
  const second = await pendingOn(F, 1);                       // the retry's transaction on connection 2
  second.forceLoss('AbortError', null);
  assert.equal(await read, undefined, 'memory after the second loss');
  assert.equal(F.stats.opens, 2, 'exactly one retry');
}));
