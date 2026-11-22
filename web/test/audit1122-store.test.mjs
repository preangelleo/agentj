// Audit 2026-11-22, phone web — js/store.js. The rule of the round: a failure may only stop safely and be retried; it never
// turns "stuck" into "irreversible".
//   N-W2  a read of the device / approval key that timed out or failed is "unknown", not "no record": no new key, no write.
//   N-W1  once IndexedDB worked on this page (or this browser was paired) nothing is served from, or written to, the empty
//         in-memory map; the failure is a typed StorageError and the next attempt opens the store again.
//   N-W3  an open that never answers (or is blocked) is bounded; the next attempt opens afresh.
// Driven through the real module on the stand-in IndexedDB of audit1122-idb.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { withStorage, settled, sleep, hex } from './audit1122-idb.mjs';

const isStorage = (r) => r !== 'HUNG' && r.ok === false && r.e && r.e.name === 'StorageError';
const HOST = { relay: 'wss://relay.example.com', channel: 'c'.repeat(22), hostPub: new Uint8Array(32).fill(7), approved: true, name: 'J' };

// ---------------------------------------------------------------- N-W2: the device key
for (const [label, key, probe] of [['direct record (device)', 'device', 'ok'], ['wrapped record (device2, WebKit)', 'device2', 'fail']]) {
  test(`N-W2: a slow read (> READ_MS) of a real ${label} never replaces it`, () => withStorage(async ({ F, fresh }) => {
    F.ctl.probe = probe;
    const first = await (await fresh('seed')).deviceKey();          // the pairing's key, as an earlier page load stored it
    const rec = F.data().get(key), before = hex(rec.pub), ct = rec.ct ? hex(rec.ct) : null;
    assert.deepEqual(F.log.puts, [key]);
    F.log.puts.length = 0;
    F.ctl.slow.set(key, 3200);                                      // READ_MS is 2500
    const S = await fresh('slow');
    const r = await settled(S.deviceKey(), 9000);
    await sleep(900);                                               // the slow read itself has completed by now
    assert.deepEqual(F.log.puts, [], 'nothing was written over the real key');
    assert.equal(F.data().get(key), rec, 'the stored record is the same object');
    assert.equal(hex(F.data().get(key).pub), before, 'same public key bytes');
    if (ct) assert.equal(hex(F.data().get(key).ct), ct, 'same wrapped private key bytes');
    assert.ok(isStorage(r), 'the attempt fails with a StorageError — got ' + (r === 'HUNG' ? 'HUNG' : r.ok ? 'a key ' + hex(r.v.pub).slice(0, 12) : String(r.e)));
    F.ctl.slow.clear();
    const again = await S.deviceKey();                              // the caller's retry
    assert.equal(hex(again.pub), hex(first.pub), 'the retry loads the real key');
    assert.deepEqual(F.log.puts, []);
  }));
}

test('N-W2: a read that never completes is unknown too — rejected, nothing written, the retry loads the real key', () => withStorage(async ({ F, fresh }) => {
  F.ctl.probe = 'fail';
  const first = await (await fresh('seed')).deviceKey();
  const rec = F.data().get('device2');
  F.log.puts.length = 0;
  F.ctl.hang.add('device2');
  const S = await fresh('hang');
  const r = await settled(S.deviceKey(), 9000);
  assert.ok(isStorage(r), 'StorageError, got ' + (r === 'HUNG' ? 'HUNG' : r.ok ? 'a new key' : String(r.e)));
  assert.deepEqual(F.log.puts, []);
  assert.equal(F.data().get('device2'), rec);
  F.ctl.hang.clear();
  assert.equal(hex((await S.deviceKey()).pub), hex(first.pub));
}));

test('N-W2: a failed read (store error) of device2 is unknown — no new key over it', () => withStorage(async ({ F, fresh }) => {
  F.ctl.probe = 'fail';
  const first = await (await fresh('seed')).deviceKey();
  const rec = F.data().get('device2');
  F.log.puts.length = 0;
  F.ctl.failGet.set('device2', 'DataError');
  const S = await fresh('fail');
  const r = await settled(S.deviceKey(), 9000);
  assert.ok(isStorage(r), 'StorageError, got ' + (r === 'HUNG' ? 'HUNG' : r.ok ? 'a new key' : String(r.e)));
  assert.deepEqual(F.log.puts, []);
  assert.equal(F.data().get('device2'), rec);
  F.ctl.failGet.clear();
  assert.equal(hex((await S.deviceKey()).pub), hex(first.pub), 'not sticky: the next attempt reads the real store');
}));

test('N-W2: a present but unusable device2 record (unwrap fails) is never overwritten', () => withStorage(async ({ F, fresh }) => {
  F.ctl.probe = 'fail';
  await (await fresh('seed')).deviceKey();
  const good = F.data().get('device2');
  const bad = { ...good, ct: new Uint8Array(good.ct.length).fill(1) };   // the record is there; its private half does not unwrap
  F.data().set('device2', bad);
  F.log.puts.length = 0;
  const S = await fresh('bad');
  const r = await settled(S.deviceKey(), 9000);
  assert.ok(isStorage(r), 'StorageError, got ' + (r === 'HUNG' ? 'HUNG' : r.ok ? 'a new key' : String(r.e)));
  assert.equal(r.e.why, 'device-unusable');
  assert.deepEqual(F.log.puts, []);
  assert.equal(F.data().get('device2'), bad, 'the unusable record stays as it was');
}));

test('N-W2: an unusable device2 next to a usable device record uses the device record (no new key)', () => withStorage(async ({ F, fresh }) => {
  const first = await (await fresh('seed')).deviceKey();                 // probe ok: stored under "device"
  F.data().set('device2', { v: 2, pub: new Uint8Array(32), iv: new Uint8Array(12), ct: new Uint8Array(48), kek: null });
  F.log.puts.length = 0;
  const k = await (await fresh('mixed')).deviceKey();
  assert.equal(hex(k.pub), hex(first.pub));
  assert.deepEqual(F.log.puts, []);
}));

test('N-W2 control: both reads completed and found nothing → exactly one new key is stored', () => withStorage(async ({ F, fresh }) => {
  const S = await fresh('new');
  const [a, b] = await Promise.all([S.deviceKey(), S.deviceKey()]);
  assert.equal(hex(a.pub), hex(b.pub));
  assert.deepEqual(F.log.puts, ['device']);
  assert.equal(hex((await (await fresh('reload')).deviceKey()).pub), hex(a.pub));
}));

// ---------------------------------------------------------------- N-W2: the approval key
test('N-W2 signKey: a failed read never mints a new approval key; the retry returns the real one', () => withStorage(async ({ F, fresh }) => {
  const real = await (await fresh('seed')).signKey();
  assert.ok(real && real.pub.length === 32);
  const rec = F.data().get('sign');
  F.log.puts.length = 0;
  F.ctl.failGet.set('sign', 'DataError');
  const S = await fresh('fail');
  const got = await S.signKey();
  assert.equal(got, null, 'unknown → no key for this attempt' + (got ? ' (minted ' + hex(got.pub).slice(0, 12) + ', real ' + hex(real.pub).slice(0, 12) + ')' : ''));
  assert.deepEqual(F.log.puts, []);
  F.ctl.failGet.clear();
  const again = await S.signKey();
  assert.equal(hex(again.pub), hex(real.pub), 'the next attempt reads the registered key');
  assert.equal(F.data().get('sign'), rec);
}));

test('N-W2 signKey: a read that never completes is bounded and mints nothing', () => withStorage(async ({ F, fresh }) => {
  const real = await (await fresh('seed')).signKey();
  F.log.puts.length = 0;
  F.ctl.hang.add('sign');
  const S = await fresh('hang');
  const r = await settled(S.signKey(), 12000);
  assert.notEqual(r, 'HUNG', 'signKey() settles');
  assert.equal(r.v, null);
  assert.deepEqual(F.log.puts, []);
  F.ctl.hang.clear();
  assert.equal(hex((await S.signKey()).pub), hex(real.pub));
}));

test('N-W2 signKey: a present but malformed record is not replaced', () => withStorage(async ({ F, fresh }) => {
  await (await fresh('open')).dbGet('x');
  const odd = { pub: null };
  F.data().set('sign', odd);
  const S = await fresh('odd');
  assert.equal(await S.signKey(), null);
  assert.deepEqual(F.log.puts, []);
  assert.equal(F.data().get('sign'), odd);
}));

// ---------------------------------------------------------------- N-W1: no silent memory store on a page that has a real one
test('N-W1: a store error on one write is surfaced; the paired record is still read from the database', () => withStorage(async ({ F, fresh }) => {
  const S = await fresh('puterr');
  await S.dbPut('host', HOST);
  F.ctl.failPut.set('draft', 'QuotaExceededError');
  const w = await settled(S.dbPut('draft', { v: 1 }), 9000);
  assert.ok(isStorage(w), 'the failed write rejects with a StorageError — got ' + (w === 'HUNG' ? 'HUNG' : w.ok ? 'resolved (silently kept in memory)' : String(w.e)));
  assert.equal(F.data().has('draft'), false);
  assert.deepEqual(await S.dbGet('host'), HOST, 'the paired record is read from the database, not from an empty memory map');
  F.ctl.failPut.clear();
  await S.dbPut('draft', { v: 1 });
  assert.deepEqual(F.data().get('draft'), { v: 1 }, 'a later write reaches the database');
}));

test('N-W1: lost connection + failed re-open → typed error, nothing in memory; the next attempt re-opens and reads the real record', () => withStorage(async ({ F, fresh }) => {
  const S = await fresh('lost');
  await S.dbPut('host', HOST);
  S.markPaired(true);
  const [db] = F.log.live; db.loseConnection();
  F.ctl.open = 'fail';
  const r = await settled(S.dbGet('host'), 9000);
  assert.ok(isStorage(r), 'the read rejects with a StorageError — got ' + (r === 'HUNG' ? 'HUNG' : r.ok ? 'resolved ' + JSON.stringify(r.v) + ' (reads as "not paired")' : String(r.e)));
  const w = await settled(S.dbPut('outbox', { v: 1 }), 9000);
  assert.ok(isStorage(w), 'a write in that state rejects too');
  const sealed = await settled(S.putSealed('draft', { t: 'words' }), 9000);
  assert.ok(sealed !== 'HUNG' && sealed.ok === false, 'a draft is not silently kept in memory');
  assert.equal(await S.signKey(), null, 'and no approval key is minted');
  F.ctl.open = 'ok';
  assert.deepEqual(await S.dbGet('host'), HOST, 'recoverable: the next attempt opens the store again');
  assert.equal(F.data().has('outbox'), false);
  await S.dbPut('outbox', { v: 2 });
  assert.deepEqual(F.data().get('outbox'), { v: 2 });
}));

test('N-W1: a browser that was paired (marker) and cannot open its store gets an error, not an empty memory store', () => withStorage(async ({ F, fresh }) => {
  F.ctl.open = 'fail';
  const S = await fresh('marker');
  const r = await settled(S.dbGet('host'), 9000);
  assert.ok(isStorage(r), 'StorageError — got ' + (r === 'HUNG' ? 'HUNG' : r.ok ? 'resolved ' + r.v : String(r.e)));
  const k = await settled(S.deviceKey(), 9000);
  assert.ok(isStorage(k), 'and no device key is generated');
  F.ctl.open = 'ok';
  F.data().set('host', HOST);
  assert.deepEqual(await S.dbGet('host'), HOST);
}, { paired: true }));

test('N-W1 control: IndexedDB unavailable from the start on a browser never paired here keeps the in-memory page', () => withStorage(async ({ F, ls, fresh }) => {
  for (const how of ['fail', 'throw']) {
    F.ctl.open = how;
    const S = await fresh('private-' + how);
    await S.dbPut('host', HOST);
    assert.deepEqual(await S.dbGet('host'), HOST);
    assert.ok((await S.deviceKey()).pub.length === 32);
    assert.equal(F.data().size, 0);
    S.markPaired(true);
    assert.equal(ls.getItem('aj.paired'), null, 'a pairing held only in memory leaves no paired marker (the next load is a first visit again)');
  }
}));

// Security review r1, W-1: memory-only needs proof that this browser was never used here, not just a missing marker
test('W-1: an unreadable paired marker (localStorage refused) + a refused open is "may have been paired" — an error, never the memory store', () => withStorage(async ({ F, fresh }) => {
  F.ctl.open = 'fail';
  const S = await fresh('blocked-ls');
  const r = await settled(S.dbGet('host'), 9000);
  assert.ok(isStorage(r), 'StorageError — got ' + (r === 'HUNG' ? 'HUNG' : r.ok ? 'resolved ' + r.v + ' (memory store)' : String(r.e)));
  assert.ok(isStorage(await settled(S.deviceKey(), 9000)), 'no device key is made in memory');
  assert.ok(isStorage(await settled(S.dbPut('host', HOST), 9000)), 'so a pairing cannot be stored on this page');
  F.ctl.open = 'ok';
  await S.dbPut('k', 1);
  assert.equal(F.data().get('k'), 1, 'the next attempt uses the real store');
}, { blocked: true }));

test('W-1: a missing marker next to a stored install id is "used here before" — an error, never the memory store', () => withStorage(async ({ F, fresh }) => {
  F.ctl.open = 'fail';
  const S = await fresh('iid-only');
  const r = await settled(S.dbGet('host'), 9000);
  assert.ok(isStorage(r), 'StorageError — got ' + (r === 'HUNG' ? 'HUNG' : r.ok ? 'resolved ' + r.v + ' (memory store)' : String(r.e)));
  assert.ok(isStorage(await settled(S.deviceKey(), 9000)));
}, { items: { 'aj.iid': 'A'.repeat(22) } }));

test('W-1 control: the memory-only page (first visit, IndexedDB refused) leaves no install id and no marker behind, and its install id is fresh', () => withStorage(async ({ F, ls, fresh }) => {
  F.ctl.open = 'fail';
  const S = await fresh('memory-iid');
  await S.deviceKey();
  const a = await S.installId('c'.repeat(22));
  assert.equal(a, await S.installId('c'.repeat(22)), 'stable within the page');
  S.markPaired(true);
  assert.deepEqual([...ls.map.keys()], [], 'nothing persisted: a reload is a first visit again, and no stored install id can make a computer replace an older record');
  assert.equal(F.data().size, 0);
}));

test('N-W1: readSealed tells "nothing stored" from "could not read"; getSealed keeps answering null', () => withStorage(async ({ F, fresh }) => {
  const S = await fresh('sealed');
  assert.equal(await S.readSealed('outbox'), null);
  await S.putSealed('outbox', { v: 1, items: [1] });
  assert.deepEqual(await S.readSealed('outbox'), { v: 1, items: [1] });
  F.ctl.failGet.set('outbox', 'DataError');
  const r = await settled(S.readSealed('outbox'), 9000);
  assert.ok(isStorage(r));
  assert.equal(await S.getSealed('outbox'), null);
  F.ctl.failGet.clear();
  assert.deepEqual(await S.readSealed('outbox'), { v: 1, items: [1] });
}));

// ---------------------------------------------------------------- N-W3: a bounded open, a bounded transaction
test('N-W3: an open that never answers rejects in bounded time; the next attempt opens afresh', () => withStorage(async ({ F, fresh }) => {
  F.data().set('host', HOST);
  F.ctl.open = 'hang';
  const S = await fresh('hungopen');
  const [a, b] = await Promise.all([settled(S.dbGet('host'), 9000), settled(S.dbGet('host'), 9000)]);
  assert.ok(isStorage(a) && isStorage(b), 'both waiters get a StorageError — got ' + (a === 'HUNG' ? 'HUNG (the open promise is pinned)' : a.ok ? 'resolved ' + a.v : String(a.e)));
  assert.equal(F.log.opens, 1, 'they shared the one hung open');
  F.ctl.open = 'ok';
  assert.deepEqual(await S.dbGet('host'), HOST);
  assert.equal(F.log.opens, 2, 'a fresh open, not the pinned one');
}, { paired: true }));

test('N-W3: a hung open on a browser never paired here is unknown too (no memory store from a timeout)', () => withStorage(async ({ F, fresh }) => {
  F.ctl.open = 'hang';
  const S = await fresh('hungopen-new');
  assert.ok(isStorage(await settled(S.dbPut('k', 1), 9000)));
  F.ctl.open = 'ok';
  await S.dbPut('k', 2);
  assert.equal(F.data().get('k'), 2);
}));

test('N-W3: a blocked open rejects; an open that answers after the bound is closed again', () => withStorage(async ({ F, fresh }) => {
  F.ctl.open = 'blocked';
  const S = await fresh('blocked');
  const r = await settled(S.dbGet('host'), 9000);
  assert.ok(isStorage(r), 'StorageError — got ' + (r === 'HUNG' ? 'HUNG' : String(r.ok)));
  F.ctl.open = 'ok'; F.ctl.openDelay = 4600;
  assert.ok(isStorage(await settled(S.dbGet('host'), 9000)));
  await sleep(900);
  assert.equal(F.log.all.length, 1);
  assert.equal(F.log.all[0].closed, true, 'the late connection is closed, not leaked');
  F.ctl.openDelay = 0;
  assert.equal(await S.dbGet('host'), undefined);
  assert.equal(F.log.live.size, 1);
}, { paired: true }));

test('N-W3: a transaction that never completes rejects in bounded time and drops its connection', () => withStorage(async ({ F, fresh }) => {
  const S = await fresh('hungtx');
  await S.dbPut('host', HOST);
  F.ctl.hang.add('host');
  const r = await settled(S.dbGet('host'), 12000);
  assert.ok(isStorage(r), 'StorageError — got ' + (r === 'HUNG' ? 'HUNG' : String(r.ok)));
  F.ctl.hang.clear();
  assert.deepEqual(await S.dbGet('host'), HOST);
  assert.equal(F.log.opens, 2, 'read again on a fresh connection');
}));
