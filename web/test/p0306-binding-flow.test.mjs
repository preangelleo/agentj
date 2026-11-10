// #0306 A5 (董事长 11-09 22:5x · THREAT-MODEL A5): 「同一个主人」 on a first-time remote. The host's
// {ok:false, why:'roster_binding_required', next:'bind', cont} is a first step: no 「没成功」, no raw code; the existing binding
// sheet says 「这台遥控器第一次用，先用 Face ID 确认一次」 (one UV); on the binding receipt the page sends the start again with the
// one-time ticket — once, only after success, only for that friend, only before it expires. Failure / cancel → plain line +
// retry. Old host (no ticket) / expired / refused continuation → the normal button state. Browser leg: test/p0306-binding-flow.mjs.
// 0.30.6 (contract-a5 DEVIATIONS): next:'passkey' (no ticket) → the 「在本机设置」 offer, never the binding sheet; a continuation
// the host turned down is a normal start answer (ok → started; roster_binding_required + fresh ticket → retry line, at most ONE
// automatic bind+continue per owner tap); R-F1 confirm refusals peer_too_old / peer_binding_required; R-F5 grant-card binding.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createSameOwnerStart, CONT_MS } from '../public/js/sameowner-start.js';
const DICT = (await import('../public/i18n.js')).default;
const TICKET = 'tkt_' + 'A'.repeat(40);

function harness({ first = { ok: false, why: 'roster_binding_required', next: 'bind', cont: TICKET }, cont = { ok: true }, sheetOpen = false } = {}) {
  let clock = 1_000_000;
  const log = { writes: [], binds: 0, said: [], retries: [], started: 0, settled: 0, passkey: 0, failed: [] };
  const conts = Array.isArray(cont) ? cont : [cont];
  const flow = createSameOwnerStart({
    write: async (target, extra) => { log.writes.push({ target, extra }); return log.writes.length === 1 ? first : conts[Math.min(log.writes.length - 2, conts.length - 1)]; },
    needPasskey: (f) => { log.passkey++; log.passkeyFor = f; }, fail: (why) => log.failed.push(why),
    bind: () => { log.binds++; }, binding: () => sheetOpen,
    say: (k) => log.said.push(k), sayRetry: (k, l, fn) => log.retries.push({ k, l, fn }),
    started: () => log.started++, settle: () => log.settled++, now: () => clock,
  });
  return { flow, log, tick: (ms) => { clock += ms; } };
}

test('first-time remote: no failure, no raw code, binding sheet opened once, start continues with the ticket exactly once', async () => {
  const { flow, log } = harness();
  const res = await flow.begin('AJ-FATHER');
  assert.deepEqual(res, { ok: false, why: 'binding', quiet: true }, 'the page shows nothing of its own (no 「没成功」, no code)');
  assert.equal(flow.active(), true, 'the binding sheet that opens now speaks the first-time line');
  assert.equal(log.binds, 1, 'asks the host for the binding offer (one sheet → one UV)');
  assert.deepEqual(log.writes[0], { target: { friend: 'AJ-FATHER', op: 'group', value: 'same-owner' }, extra: undefined });
  assert.equal(await flow.bound(true), true);
  assert.equal(log.writes.length, 2);
  assert.deepEqual(log.writes[1], { target: { friend: 'AJ-FATHER', op: 'group', value: 'same-owner' }, extra: { cont: TICKET } },
    'signed target unchanged; ticket beside it; same friend');
  assert.deepEqual(log.said, ['p417.so.started']); assert.equal(log.started, 1);
  // a second receipt (duplicate / replayed roster_bind_res) never sends the ticket again
  assert.equal(await flow.bound(true), false);
  assert.equal(log.writes.length, 2);
  assert.equal(flow.active(), false);
});

test('the host offer already opened the sheet: no second offer request', async () => {
  const { flow, log } = harness({ sheetOpen: true });
  await flow.begin('AJ-FATHER');
  assert.equal(log.binds, 0);
});

test('binding cancelled or failed: plain line + retry, never continues; retry reopens the sheet, success then continues once', async () => {
  const { flow, log } = harness();
  await flow.begin('AJ-FATHER');
  assert.equal(await flow.bound(false), true);
  assert.equal(log.writes.length, 1, 'no continuation after a failed / cancelled binding');
  assert.equal(log.retries.length, 1); assert.equal(log.retries[0].k, 'p0306.so.bindFailed'); assert.equal(log.retries[0].l, 'p0306.so.retry');
  assert.equal(log.settled, 1, 'back to the normal button state');
  assert.equal(flow.active(), false, 'an unrelated binding sheet is not ours while failed');
  log.retries[0].fn();
  assert.equal(log.binds, 2); assert.equal(flow.active(), true);
  await flow.bound(true);
  assert.equal(log.writes.length, 2); assert.equal(log.writes[1].extra.cont, TICKET);
});

test('expired ticket: no continuation, normal state with a plain line', async () => {
  const { flow, log, tick } = harness();
  await flow.begin('AJ-FATHER');
  tick(CONT_MS + 1);
  await flow.bound(true);
  assert.equal(log.writes.length, 1);
  assert.deepEqual(log.said, ['p0306.so.again']); assert.equal(log.settled, 1);
});

test('old host (no ticket) or a malformed ticket: binding still works, nothing is sent on the owner\'s behalf', async () => {
  for (const cont of [undefined, '', 'short', 'bad ticket with spaces and more than enough', { x: 1 }]) {
    const { flow, log } = harness({ first: { ok: false, why: 'roster_binding_required', ...(cont === undefined ? {} : { cont }) } });
    await flow.begin('AJ-FATHER');
    await flow.bound(true);
    assert.equal(log.writes.length, 1, JSON.stringify(cont));
    assert.deepEqual(log.said, ['p0306.so.again']);
  }
});

test('continuation turned down (spent / other device / other friend / not bound): a normal start answer, never a loop', async () => {
  // contract-a5 DEVIATION 2: no special refusal code — the host answers as for any start
  for (const [why, said, failed] of [['offline', ['p0306.so.again'], []], ['not_friend', [], ['not_friend']], ['phone_required', [], ['phone_required']]]) {
    const { flow, log } = harness({ cont: { ok: false, why } });
    await flow.begin('AJ-FATHER');
    await flow.bound(true);
    assert.equal(log.writes.length, 2, why);
    assert.deepEqual(log.said, said, why); assert.deepEqual(log.failed, failed, why);
    assert.equal(log.settled, 1); assert.equal(log.started, 0); assert.equal(log.binds, 1, 'no second automatic bind');
    assert.equal(await flow.bound(true), false, 'no third write');
  }
});

test('continuation answered roster_binding_required + fresh ticket: retry line (owner tap), no automatic second bind; the tap uses the fresh ticket once', async () => {
  const FRESH = 'tkt_' + 'B'.repeat(40);
  const { flow, log } = harness({ cont: [{ ok: false, why: 'roster_binding_required', next: 'bind', cont: FRESH }, { ok: true }] });
  await flow.begin('AJ-FATHER');
  await flow.bound(true);
  assert.equal(log.writes.length, 2); assert.equal(log.binds, 1, 'ONE automatic bind+continue per tap');
  assert.equal(log.retries.length, 1); assert.equal(log.retries[0].k, 'p0306.so.bindFailed');
  assert.equal(flow.active(), false, 'waiting for the owner, not looping');
  assert.equal(await flow.bound(true), false, 'a stray receipt does not continue without the owner\'s tap');
  assert.equal(log.writes.length, 2);
  log.retries[0].fn();                                  // the owner's tap
  assert.equal(log.binds, 2);
  await flow.bound(true);
  assert.equal(log.writes.length, 3); assert.equal(log.writes[2].extra.cont, FRESH, 'the fresh ticket, not the spent one');
  assert.equal(log.started, 1); assert.deepEqual(log.said, ['p417.so.started']);
  assert.equal(await flow.bound(true), false);
});

test('continuation refused while the host\'s re-offered sheet is already up: no retry line; that sheet\'s tap continues once with the fresh ticket', async () => {
  const FRESH = 'tkt_' + 'C'.repeat(40);
  const { flow, log } = harness({ sheetOpen: true, cont: [{ ok: false, why: 'roster_binding_required', next: 'bind', cont: FRESH }, { ok: true }] });
  await flow.begin('AJ-FATHER');                        // the host's offer already opened the first sheet (binding() true)
  await flow.bound(true);
  assert.equal(log.writes.length, 2); assert.equal(log.retries.length, 0, 'the re-offered sheet is the owner\'s next tap');
  assert.equal(log.binds, 0, 'the page never asks for a sheet by itself here');
  assert.equal(flow.active(), true);
  await flow.bound(true);                                // the owner confirmed that sheet
  assert.equal(log.writes.length, 3); assert.equal(log.writes[2].extra.cont, FRESH);
  assert.equal(log.started, 1);
  assert.equal(await flow.bound(true), false, 'and never again');
});

test('next:passkey (no passkey on this remote, no ticket): the setup offer, no binding sheet, no raw code, nothing continues', async () => {
  const { flow, log } = harness({ first: { ok: false, why: 'roster_binding_required', next: 'passkey' } });
  const res = await flow.begin('AJ-FATHER');
  assert.equal(res.quiet, true, 'friends.js shows no 「没成功」 toast of its own');
  assert.equal(log.passkey, 1); assert.equal(log.binds, 0, 'not the binding prompt');
  assert.equal(log.passkeyFor, 'AJ-FATHER', '0304 r2: the friend rides along, so the start can go on once the passkey is saved');
  assert.equal(flow.active(), false);
  assert.equal(await flow.bound(true), false); assert.equal(log.writes.length, 1);
});

test('the host offer opened the sheet and the owner cancelled it before the start answer: no second sheet, retry still works', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const log = { writes: 0, binds: 0, retries: [] };
  const flow = createSameOwnerStart({
    write: async (_t, extra) => { log.writes++; if (log.writes === 1) { await gate; return { ok: false, why: 'roster_binding_required', next: 'bind', cont: TICKET }; } return { ok: !!extra?.cont }; },
    bind: () => log.binds++, binding: () => false, say: () => {}, sayRetry: (k, l, fn) => log.retries.push(fn),
    needPasskey: () => {}, fail: () => {}, started: () => {}, settle: () => {},
  });
  const p = flow.begin('AJ-FATHER');
  assert.equal(flow.active(), true, 'the offer sheet that opens before the answer is ours');
  await flow.bound(false);                               // cancelled
  release(); await p;
  assert.equal(log.binds, 0, 'the cancelled sheet does not reopen by itself');
  log.retries[0]();
  assert.equal(log.binds, 1);
  await flow.bound(true);
  assert.equal(log.writes, 2);
});

test('other answers pass through untouched (ok start, ordinary refusals); flow ends', async () => {
  for (const first of [{ ok: true }, { ok: false, why: 'not_friend' }, { ok: false, why: 'phone_required' }]) {
    const { flow, log } = harness({ first });
    assert.deepEqual(await flow.begin('AJ-FATHER'), first);
    assert.equal(log.binds, 0); assert.equal(flow.active(), false);
    assert.equal(await flow.bound(true), false, 'a later binding receipt is not ours');
    assert.equal(log.writes.length, 1);
  }
});

test('page wiring: same-owner select uses the flow; binding sheet / receipt route to it; no 「请重新选择」 left', () => {
  const src = (f) => readFileSync(new URL('../public/js/' + f, import.meta.url), 'utf8');
  const fr = src('friends.js'), rb = src('rosterbind.js');
  assert.match(fr, /sel\.value === 'same-owner' \? await sameOwnerStart\.begin\(f\.id\)/);
  assert.match(fr, /if \(!res\.quiet\) toast\(/);
  assert.match(fr, /signedWrite\('fr_set', target, \{ \.\.\.WRITE_MSG\.fr_set\(target\), \.\.\.extra \}\)/, 'ticket beside the signed target');
  assert.match(rb, /t\(starting \? 'p0306\.so\.firstBind' : 'p574\.bindWhy'\)/);
  assert.match(rb, /if \(flow\?\.active\(\)\) \{ flow\.bound\(m\.ok === true\)/);
  assert.match(rb, /const starting = !!flowNow\(\);\n    if \(!await confirmSheet/, 'the wording is decided when the sheet opens');
  // one UV: the sheet tap → approve() with nothing asynchronous in between (iOS gesture), unchanged
  assert.match(rb, /'grow'\)\) \{[\s\S]{0,120}return false;\n    \}\n    \/\/ No asynchronous work between confirmation and get\(\): preserve the iOS tap gesture\.\n    const fa = await approve\(challenge, e\.id\);/);
  for (const l of ['zh', 'en']) {
    assert.ok(!/重新选择|Choose Same owner again/.test(DICT[l]['p574.bound']), l + ' p574.bound no longer asks to redo the step');
  }
  assert.match(fr, /needPasskey: \(friend\) => \{ soAfterKey = \{ friend, until: Date\.now\(\) \+ SO_AFTER_KEY_MS \}; offerSetupHere\('p0306\.so\.needPasskey'\); \}/, 'next:passkey → the existing 「在本机设置」 entry');
  // 0304 r2 (QA scene 6): once this page saved that passkey, the same start runs once by itself (same friend, ≤ 10 min)
  assert.match(fr, /export async function passkeySaved\(\) \{\n  const p = soAfterKey; soAfterKey = null;\n  if \(!p \|\| !p\.friend \|\| Date\.now\(\) > p\.until \|\| !isReady\(\)\) return;\n  const res = await sameOwnerStart\.begin\(p\.friend\);/);
  assert.match(readFileSync(new URL('../public/app.js', import.meta.url), 'utf8'), /friends\.passkeySaved\(\)\.catch/);
  for (const l of ['zh', 'en']) assert.ok(!/再选一次|once more/.test(DICT[l]['p0306.so.needPasskey']), l + ' needPasskey says it goes on by itself');
  // the continuation ticket rides beside the signed object: serve.on_friends runs controls.check over {friend, op, value} first
  assert.match(src('sameowner-start.js'), /deps\.write\(\{ friend: f\.friend, op: 'group', value: 'same-owner' \}, \{ cont: f\.cont \}\)/);
});

test('R-F1: peer_too_old / peer_binding_required on the confirm (or the friends row) → plain line, card not confirmable', () => {
  const fr = readFileSync(new URL('../public/js/friends.js', import.meta.url), 'utf8');
  assert.match(fr, /peer_too_old: 'p0306\.so\.peerTooOld', peer_binding_required: 'p0306\.so\.peerBinding'/);
  assert.match(fr, /if \(SO_PEER_WHY\.includes\(code\)\) p\.pairing_peer_why = code;/);
  assert.match(fr, /soWhy: f\.same_owner === 'pending' && SO_PEER_WHY\.includes\(f\.same_owner_why\)/);
  assert.match(fr, /\|\| blocked\) acts\.querySelector\('#frOwnerAllow'\)\.disabled = true;/);
  assert.match(fr, /if \(on && \(p\.pairing_scope_ok === false \|\| peerWhy\(p, x\.friend\)\)\) return;/);
});

test('R-F5: grant card roster_bind_offer → answer_refused roster_binding_required → binding prompt, card kept, no popup over the sheet', () => {
  const app = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8'), rb = readFileSync(new URL('../public/js/rosterbind.js', import.meta.url), 'utf8');
  assert.match(app, /m\.why==='roster_binding_required'&&\(snap\.S\.asks\.get\(m\.id\)\?\.tool\|\|m\.ask\?\.tool\)==='agentj secret grant'\?rosterbind\.grantRefused\(m\):null/);
  assert.match(app, /relay\.answerRefused\(m,bind\?\.text\)/);
  assert.match(rb, /if \(busy \|\| Date\.now\(\) - offeredAt < 5000\) \{ grantCard = m\.id; return \{ text: t\('p0306\.grant\.bind'\), quiet: true \}; \}/);
  assert.match(rb, /offerSetupHere\('p0306\.grant\.needPasskey'\)/);
});

test('dictionaries: both languages, first-time line as asked, no trial wording, no raw code', () => {
  assert.equal(DICT.zh['p0306.so.firstBind'].split('。')[0], '这台遥控器第一次用，先用 Face ID 确认一次');
  assert.match(DICT.en['p0306.so.firstBind'], /^First time on this remote — confirm once with Face ID/);
  for (const k of ['p0306.so.firstBind', 'p0306.so.bindFailed', 'p0306.so.retry', 'p0306.so.again', 'fr.why.roster_binding_required', 'p574.bound',
    'p0306.so.needPasskey', 'p0306.so.peerTooOld', 'p0306.so.peerBinding', 'p0306.grant.bind', 'p0306.grant.needPasskey', 'p0306.grant.bound']) {
    assert.ok(DICT.zh[k] && DICT.en[k], k);
    assert.ok(!/试用|trial|roster_binding_required|没成功/i.test(DICT.zh[k] + DICT.en[k]), k);
    assert.ok(!/[㐀-鿿]/.test(DICT.en[k]), 'en has no Chinese: ' + k);
  }
});

test('R-F1 texts: as the contract asks, in the glossary\'s words (the host is 「Agent J 主机」 / "Agent J host", never 「电脑」 / "computer")', () => {
  assert.equal(DICT.en['p0306.so.peerTooOld'], 'The other Agent J host is too old for Same owner — update it, then start again.');
  assert.equal(DICT.en['p0306.so.peerBinding'], 'On the other Agent J host, open Same owner once from one of its remotes (confirm with Face ID) first.');
  for (const k of ['p0306.so.peerTooOld', 'p0306.so.peerBinding']) assert.ok(!/电脑|computer/i.test(DICT.zh[k] + DICT.en[k]), k);
  for (const k of ['p0306.so.needPasskey', 'p0306.grant.needPasskey']) for (const l of ['zh', 'en']) assert.match(DICT[l][k], /\{bio\}/, l + ' ' + k + ' names this system\'s own unlock');
});

test('while the continuation is in flight a re-offered sheet still speaks the first-time line; a receipt then is not taken', async () => {
  let during = null, receipt = null;
  const flow = createSameOwnerStart({
    write: async (_t, extra) => {
      if (!extra) return { ok: false, why: 'roster_binding_required', next: 'bind', cont: TICKET };
      during = flow.active(); receipt = await flow.bound(true);
      return { ok: true };
    },
    bind: () => {}, binding: () => false, say: () => {}, sayRetry: () => {}, needPasskey: () => {}, fail: () => {}, started: () => {}, settle: () => {},
  });
  await flow.begin('AJ-FATHER');
  await flow.bound(true);
  assert.equal(during, true, 'the host\'s re-offer during the continuation belongs to this start');
  assert.equal(receipt, false, 'no second continuation from inside the first');
  assert.equal(flow.active(), false);
});
