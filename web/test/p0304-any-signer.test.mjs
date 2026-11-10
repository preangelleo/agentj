// #0304 A1 · same-owner scope v4 (reports/qa/0304-frozen/contract-a1-scope-v4.md): any bound remote of either end may sign.
// The phone hashes the received `pairing_scope` bytes, checks the shape, verifies EVERY roster entry's binding in BOTH ends
// (one bad entry → the whole card is unsignable), and signs only as exactly one entry of the "here" roster equal to its own
// stored descriptor. v3 cards (no pairing_v) keep the designated-signer path unchanged. The confirm card lists all entries of
// both ends under 「确认后，这些遥控器都能签同主人授权」 (R-F4, each name + the first 8 characters of its device id); after the first signer, the host's ask_done folds every remote's card.
// Browser leg (two real remotes on one fake host, screenshots): test/p0304-any-signer.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const { bindRegistration, pairingScope, pairingCode, SCOPE_V4 } = await import('../public/js/ownergrant.js');
const { sha256hex } = await import('../public/proto/wire.js');
const DICT = (await import('../public/i18n.js')).default;

async function entry(name, dev) {
  const ed = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']);
  const key = { priv: ed.privateKey, pub: new Uint8Array(await crypto.subtle.exportKey('raw', ed.publicKey)) };
  const pk = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']);
  const reg = { id: Buffer.from(name + dev).toString('base64url'), alg: -7, spki: Buffer.from(await crypto.subtle.exportKey('spki', pk.publicKey)).toString('base64url') };
  const signed = await bindRegistration(reg, dev, key, 'localhost');
  // host my_roster() key order: id, alg, spki, rp, dev, name, sign_pub (bind before sign_pub when stored)
  return { id: reg.id, alg: reg.alg, spki: reg.spki, rp: 'localhost', dev, name, bind: signed.roster_sig, sign_pub: signed.roster_sign_pub };
}
const descriptor = (e) => { const { name, ...rest } = e; return rest; };   // what rosterbind.remember() stores (no name)
// Python json.dumps(..., separators=(",", ":"), ensure_ascii=True): non-ASCII escaped as \uXXXX
const pyDumps = (v) => JSON.stringify(v).replace(/[\u007f-￿]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
async function card(ends, { friend = 'AJ-PEER', v = 4, raw } = {}) {
  const pairing_scope = raw ?? pyDumps([SCOPE_V4, ...[...ends].sort((a, b) => (a[0] < b[0] ? -1 : 1))]);
  const challenge = (await sha256hex(new TextEncoder().encode(pairing_scope))).slice(0, 32);
  return { pairing: true, friend, name: 'Father@TB', pairing_scope, challenge, code: pairingCode(challenge), ...(v === 4 ? { pairing_v: 4 } : {}) };
}
const H = await Promise.all([entry('工作手机', 'phoneAAAAAAAAAAA'), entry('Laptop', 'laptopBBBBBBBBBB'), entry('Pad', 'padCCCCCCCCCCCCC')]);
const P = await Promise.all([entry('Spare tablet', 'tabletDDDDDDDDDD'), entry('Mac', 'macEEEEEEEEEEEEE')]);
const here = () => ['AJ-HERE', 'host-ed-here', 'a'.repeat(32), structuredClone(H)];
const peer = () => ['AJ-PEER', 'host-ed-peer', 'b'.repeat(32), structuredClone(P)];

test('any bound remote of this host can sign the same v4 round; each signs only as itself', async () => {
  const pq = await card([here(), peer()]);
  for (const me of H) {
    const out = await pairingScope(pq, me.sign_pub, me.dev, descriptor(me));
    assert.equal(out.v, 4); assert.equal(out.code, pq.code);
    // contract-a1 §4.6: every entry of BOTH rosters, name + at least the first 8 characters of its device id
    assert.deepEqual(out.find((e) => e.here).remotes, ['工作手机 · phoneAAA', 'Laptop · laptopBB', 'Pad · padCCCCC']);
    assert.deepEqual(out.find((e) => !e.here).remotes, ['Spare tablet · tabletDD', 'Mac · macEEEEE']);
    assert.ok(out.every((e) => e.pendingRemotes.length === 0), 'no trusted / pending split in v4 (R-F4)');
  }
  // a remote of this host claiming another remote's dev, or someone else's descriptor
  await assert.rejects(pairingScope(pq, H[0].sign_pub, H[1].dev, descriptor(H[1])), /pairing local key|roster changed/);
  await assert.rejects(pairingScope(pq, H[0].sign_pub, H[0].dev, descriptor(H[1])), /pairing local key/);
  // the peer's remotes are on the card but are NOT local signers here
  await assert.rejects(pairingScope(pq, P[0].sign_pub, P[0].dev, descriptor(P[0])), /roster changed/);
});

test('an unbound remote cannot sign; bound after the round started → "start again", never signable', async () => {
  const pq = await card([here(), peer()]);
  const late = await entry('New phone', 'newFFFFFFFFFFFFF');
  await assert.rejects(pairingScope(pq, late.sign_pub, late.dev, null), /pairing bind needed/);
  await assert.rejects(pairingScope(pq, late.sign_pub, late.dev, descriptor(late)), /pairing roster changed/);
  // same device, credential replaced since the snapshot
  const swapped = { ...descriptor(H[0]), id: 'other-credential' };
  await assert.rejects(pairingScope(pq, H[0].sign_pub, H[0].dev, swapped), /pairing local key|roster changed/);
});

test('a tampered roster entry in EITHER end makes the whole card unsignable (A1-6)', async () => {
  for (const side of [0, 1]) for (const field of ['spki', 'id', 'alg', 'rp', 'dev', 'sign_pub', 'bind']) {
    const ends = [here(), peer()];
    const victim = ends[side][3][ends[side][3].length - 1];
    victim[field] = field === 'alg' ? -8 : field === 'sign_pub' ? H[2].sign_pub === victim.sign_pub ? P[0].sign_pub : H[2].sign_pub : field === 'bind' ? H[0].bind : 'substituted';
    const pq = await card(ends);   // digest recomputed: the host would show the same tampered bytes on both sides
    await assert.rejects(pairingScope(pq, H[0].sign_pub, H[0].dev, descriptor(H[0])), /pairing (binding|local key|roster changed)/, `${side}:${field}`);
  }
  const unsigned = [here(), peer()]; delete unsigned[1][3][0].bind;
  await assert.rejects(pairingScope(await card(unsigned), H[0].sign_pub, H[0].dev, descriptor(H[0])), /pairing binding/);
});

test('the six digits cover BOTH full rosters: adding, removing or reordering any entry changes the code', async () => {
  const base = await card([here(), peer()]);
  const extra = await entry('Extra', 'extraGGGGGGGGGGG');
  const variants = [
    (() => { const e = [here(), peer()]; e[1][3].push(extra); return e; })(),
    (() => { const e = [here(), peer()]; e[1][3].pop(); return e; })(),
    (() => { const e = [here(), peer()]; e[0][3].pop(); return e; })(),
    (() => { const e = [here(), peer()]; e[0][3].reverse(); return e; })(),
  ];
  for (const v of variants) {
    const pq = await card(v);
    assert.notEqual(pq.challenge, base.challenge);
    assert.notEqual(pq.code, base.code);
    const out = await pairingScope(pq, H[0].sign_pub, H[0].dev, descriptor(H[0]));
    assert.equal(out.code, pq.code);
  }
  // a code / challenge that does not match the received bytes
  await assert.rejects(pairingScope({ ...base, code: base.code === '000000' ? '000001' : '000000' }, H[0].sign_pub, H[0].dev, descriptor(H[0])), /pairing code/);
  await assert.rejects(pairingScope({ ...base, challenge: 'f'.repeat(32) }, H[0].sign_pub, H[0].dev, descriptor(H[0])), /pairing digest/);
});

test('the received bytes are hashed as-is (host escapes non-ASCII; a JS re-serialization would differ)', async () => {
  const pq = await card([here(), peer()]);
  assert.ok(pq.pairing_scope.includes('\\u5de5'), 'fixture carries escaped non-ASCII like the host');
  assert.notEqual(JSON.stringify(JSON.parse(pq.pairing_scope)), pq.pairing_scope);
  const out = await pairingScope(pq, H[0].sign_pub, H[0].dev, descriptor(H[0]));
  assert.equal(out.code, pq.code);
});

test('v4 shape checks: tag, 3 elements, 4-element ends, exactly one peer end, version consistency', async () => {
  const good = await card([here(), peer()]);
  const bad = async (arr) => card(null, { raw: JSON.stringify(arr) });
  const sorted = [here(), peer()];
  for (const arr of [
    ['agentj-same-owner-v3', ...sorted], [SCOPE_V4, sorted[0]], [SCOPE_V4, ...sorted, sorted[0]],
    [SCOPE_V4, [...sorted[0], 'x'], sorted[1]], [SCOPE_V4, sorted[0], sorted[0]],
    [SCOPE_V4, sorted[0], ['AJ-OTHER', 'e', 'n', P]], [SCOPE_V4, sorted[0], ['AJ-PEER', 'e', 'n', {}]],
  ]) await assert.rejects(pairingScope(await bad(arr), H[0].sign_pub, H[0].dev, descriptor(H[0])), /pairing (scope|friend)/);
  // a v4 scope without pairing_v goes the v3 way and fails its shape; a v3 scope marked v4 fails the v4 shape
  await assert.rejects(pairingScope({ ...good, pairing_v: undefined }, H[0].sign_pub, H[0].dev, descriptor(H[0])), /pairing scope/);
  const v3 = JSON.stringify([['AJ-HERE', 'host-ed', H[0].sign_pub, 'n', H], ['AJ-PEER', 'peer-ed', P[0].sign_pub, 'n', P]]);
  await assert.rejects(pairingScope({ ...(await card(null, { raw: v3 })), pairing_v: 4 }, H[0].sign_pub, H[0].dev, descriptor(H[0])), /pairing scope/);
  await assert.rejects(pairingScope({ ...good, pairing_scope: 'x'.repeat(48001) }, H[0].sign_pub, H[0].dev, descriptor(H[0])), /pairing scope/);
});

test('v3 path unchanged: designated signer only, trusted + pending split, full device id labels', async () => {
  const ends = [['AJ-HERE', 'host-ed', H[0].sign_pub, 'nonce-a', [H[0], H[1]]], ['AJ-PEER', 'peer-ed', P[0].sign_pub, 'nonce-b', [P[0]]]];
  const raw = JSON.stringify(ends);
  const pq = await card(null, { raw, v: 3 });
  const out = await pairingScope(pq, H[0].sign_pub, H[0].dev, descriptor(H[0]));
  assert.equal(out.v, undefined);
  assert.deepEqual(out.map((e) => e.remotes), [['工作手机 · ' + H[0].dev], ['Spare tablet · ' + P[0].dev]]);
  assert.deepEqual(out[0].pendingRemotes, ['Laptop · ' + H[1].dev]);
  // the second remote of this host is NOT the designated signer in v3
  await assert.rejects(pairingScope(pq, H[1].sign_pub, H[1].dev, descriptor(H[1])), /pairing signer/);
});

test('page wiring: v4 roster list, "start again" state, first-confirmer folding and v3 designated-signer line', () => {
  const src = (f) => readFileSync(new URL('../public/js/' + f, import.meta.url), 'utf8');
  const fr = src('friends.js'), snap = src('snap.js');
  assert.match(fr, /p\.pairing_roster\?\.v === 4/);
  assert.match(fr, /t\('p0304\.so\.signers'\)/);
  assert.match(fr, /p\.pairing_roster_changed/);
  // 0304 r2 (QA scene 1): folded at once — no greyed digits left — with the one line that says where it was confirmed
  assert.match(fr, /code === 'already_confirmed'\) \{ H\.foldCard\(p\.id, true\); askDone\(\{ id: p\.id, result: 'gone' \}\); toast\(t\('p0304r2\.so\.elsewhere'\), 4000\); return; \}/);
  assert.match(fr, /if \(m\.result !== 'gone' \|\| !a \|\| !pqOf\(a\)\.pairing\) return;\n  H\.foldCard\(m\.id, !mine\);\n  if \(!mine\) soNotice/);
  assert.match(fr, /if \(!f \|\| !\(f\.sameOwner === 'active' \|\| \(f\.sameOwner === 'pending' && f\.soWait\)\)\) return;/, 'the line only when this side really said yes');
  assert.match(fr, /el\('sheetTitle'\)\.textContent = t\('p0304r2\.so\.title', \{ name: x\.name \}\);/, 'the card front names the friend');
  assert.match(src('../app.js'), /foldCard: \(id, quiet\) => relay\.foldEnded\(id, quiet\)/);
  assert.match(fr, /code === 'wrong_remote' \? p\.pairing_roster\?\.find\(\(r\) => r\.here\)\?\.remotes\?\.\[0\]/);
  assert.match(snap, /a\.pairing_roster_changed = e\?\.message === 'pairing roster changed'/);
  assert.match(snap, /op: 'owner-confirm-v3'/, 'v4 keeps the owner-confirm-v3 op (contract §5)');
});

test('ask_done folds a v4 pairing card on this remote (the host sends it to every remote after the first confirm)', async () => {
  globalThis.window ??= globalThis; globalThis.addEventListener ??= () => {};
  globalThis.document ??= { createElement: () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {} }, append() {}, setAttribute() {} }), getElementById: () => null,
    querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, documentElement: { dataset: {}, style: { setProperty() {} } }, body: { dataset: {} } };
  const snap = await import('../public/js/snap.js');
  const pq = await card([here(), peer()]);
  const id = 'c'.repeat(32);
  snap.S.asks.set(id, { id, tool: 'peer_question', pq, state: 'open', deadline: Date.now() + 60000 }); snap.S.order.push(id);
  snap.askDone({ id, result: 'gone' });
  assert.equal(snap.S.asks.has(id), false); assert.equal(snap.S.order.includes(id), false);
  assert.equal(snap.S.lastAsk.state, 'ended');
});

test('dictionaries: both languages, no trial wording, one language per string', () => {
  for (const k of ['p0304.so.signers', 'p0304.so.signersHere', 'p0304.so.signersPeer', 'p0304.so.rosterChanged', 'p0304.so.wrongRemote']) {
    assert.ok(DICT.zh[k] && DICT.en[k], k);
    assert.ok(!/试用|trial/i.test(DICT.zh[k] + DICT.en[k]), k);
    assert.ok(!/[㐀-鿿]/.test(DICT.en[k]), 'en has no Chinese: ' + k);
  }
  assert.equal(DICT.zh['p0304.so.signers'], '确认后，这些遥控器都能签同主人授权');                       // contract-a1 §4.6 verbatim
  assert.equal(DICT.en['p0304.so.signers'], 'After you confirm, all of these remotes can sign same-owner grants');
});
