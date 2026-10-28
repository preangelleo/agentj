// P561 (董事长 10-27 22:47, Mac M1 Chrome): Settings said 「本机 Touch ID：已设好」 while Chrome said 「No passkeys available」 —
// the row only knew the Agent J host has a key on record. Now the first refusal of this remote's own key marks it
// 「这台遥控器的钥匙找不到了」 (row + card) with 「重新设置」 (the P547 「另一台批准」 flow); a pass or a new key clears it; an
// interruption marks nothing. The full scenario (real host, CDP virtual authenticators, two remotes and one) ran as a separate
// acceptance (REPORT-561).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
const mem = new Map();
globalThis.localStorage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
globalThis.location ??= { hostname: 'm.agentj.app' };
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.className = ''; this.textContent = ''; this.dataset = {}; this.hidden = false; }
  append(...k) { this.children.push(...k); }
  replaceChildren(...k) { this.children = k; }
  addEventListener() {}
  setAttribute() {}
  get childElementCount() { return this.children.length; }
}
globalThis.document ??= { getElementById: () => null, addEventListener() {}, body: null, createElement: (t) => new El(t) };
mem.set('aj.pk.failed', 'old');                         // a 0.25.15 mark (never cleared by a reset) — dropped on load
const F = await import('../public/js/faceid.js');
const MIGRATED = !mem.has('aj.pk.failed');
const A = await import('../public/js/pkassist.js');
const T = await import('../public/js/t.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const ID = Buffer.alloc(16, 7).toString('base64url');

// P566 (#350, iPhone Air 10-28 16:03): one refusal no longer marks it (a cancel / timeout / page in the background say the same
// NotAllowedError) — the second clear miss in a row does (the first switched the request form)
test('the second clear refusal of the own key marks it; an interruption does not; a pass or a new key clears it', () => {
  mem.clear();
  const seen = [];
  const had = globalThis.dispatchEvent;
  globalThis.dispatchEvent = (e) => { seen.push(e.type); return true; };
  try {
    F.noteResult(ID, false, false, 'AbortError');
    assert.equal(F.failedHere(), false);                  // switched away: says nothing about the key
    F.noteResult(ID, false, false, 'NotAllowedError');
    assert.equal(F.failedHere(), false);                  // P566: one refusal — maybe a cancel — never marks
    F.noteResult(ID, false, false, 'NotAllowedError', { hidden: true, ms: 900 });
    assert.equal(F.failedHere(), false);                  // the page went to the background meanwhile: not a miss
    F.noteResult(ID, false, false, 'NotAllowedError', { ms: 59000 });
    assert.equal(F.failedHere(), false);                  // ran into the timeout: not a miss
    F.noteResult(ID, false, true, 'NotAllowedError', { ms: 800 });
    assert.equal(F.failedHere(), true);                   // the second clear miss in a row (the other form) → 找不到了
    assert.deepEqual(seen, ['agentj-pk-failed-change']);
    F.clearFailed();                                      // pk_reg_res ok: a new key of this device's own
    assert.equal(F.failedHere(), false);
    assert.equal(seen.length, 2);
    F.clearFailed(); assert.equal(seen.length, 2);        // nothing changed, no repaint
  } finally { globalThis.dispatchEvent = had; }
  assert.match(read('../public/app.js'), /passkey\.clearFailed\(pkRegId\);[^\n]*\n\s*toast\((?:\(handed \? pkassist\.withBio\('p565\.pk\.handedIn'\) : )?t\('pk\.saved'\)/);
});

test('approve(): the rejection name reaches noteResult (AbortError leaves Settings alone)', async () => {
  mem.clear();
  let name = 'AbortError';
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { credentials: { get: async () => { const e = new Error('x'); e.name = name; throw e; } } } });
  await assert.rejects(F.approve(new Uint8Array(32), ID, false, 'local'));
  assert.equal(F.failedHere(), false);
  name = 'NotAllowedError';
  await assert.rejects(F.approve(new Uint8Array(32), ID, false, 'local'));
  assert.equal(F.failedHere(), false);                    // P566: the first one never marks
  await assert.rejects(F.approve(new Uint8Array(32), ID, false, 'local'));
  assert.equal(F.failedHere(), true);
  mem.clear();
  await assert.rejects(F.approve(new Uint8Array(32), ID, false, 'borrowed'));   // a borrowed key lives elsewhere: never marks here
  assert.equal(F.failedHere(), false);
});

test('card: own key refused → one line 「{bio} 记录找不到了（可以重新设置）」 + 「再试一次」 + 「重新设置」; other states unchanged', () => {
  assert.equal(A.failKey('NotAllowedError', 'local'), 'p561.fail.missing');
  assert.equal(A.failKey('NotAllowedError', 'borrowed'), 'p539.fail.borrowed');
  assert.equal(A.failKey('NotAllowedError', null), 'p539.fail.cancel');
  assert.equal(A.failKey('AbortError', 'local'), 'p539.fail.abort');
  const box = { hidden: true, dataset: {}, replaceChildren(...k) { this.kids = k; } };
  A.paintPk(box, { fa: 'local', err: 'NotAllowedError', retry: () => {} });
  const [line, acts] = box.kids;
  assert.equal(line.dataset.k, 'p561.fail.missing');
  assert.equal(line.textContent, '这台遥控器的 Face ID 记录找不到了（可以重新设置）');   // chairman 10-27 23:1x: one line, no cases
  assert.deepEqual(acts.children.map((b) => b.dataset.k), ['p539.fail.retry', 'p561.pk.reset']);
  assert.deepEqual(acts.children.map((b) => b.textContent), ['再试一次', '重新设置']);
  const b2 = { hidden: true, dataset: {}, replaceChildren(...k) { this.kids = k; } };
  A.paintPk(b2, { fa: 'local' });                         // no failure on a local key: nothing to say
  assert.equal(b2.hidden, true);
});

test('Settings row: 「这台遥控器的 {bio} 记录找不到了（可以重新设置）」 + 「重新设置」, no long note', () => {
  const box = () => ({ row: { hidden: true, dataset: {} }, label: {}, note: {}, setupBtn: {}, oldBtn: { dataset: {} } });
  const b = box();
  A.paintPkRow('local', { ...b, failed: true });
  assert.equal(b.row.dataset.pk, 'failed');
  assert.equal(b.label.textContent, '这台遥控器的 Face ID 记录找不到了（可以重新设置）');
  assert.equal(b.setupBtn.hidden, false);
  assert.equal(b.setupBtn.textContent, '重新设置');
  assert.equal(b.note.textContent, ''); assert.equal(b.note.hidden, true);   // the one-remote way lives on the wait page (pkp.waitHost*)
  const ok = box();
  A.paintPkRow('local', ok);                               // a key that passes here: unchanged
  assert.equal(ok.setupBtn.hidden, true);
  assert.match(ok.label.textContent, /已设好$/);
  for (const s of ['none', 'borrowed', 'unknown']) {       // the mark is only about this device's own key
    const x = box(); A.paintPkRow(s, { ...x, failed: true }); assert.equal(x.row.dataset.pk, s);
  }
});

test('dictionaries: zh src / override / built and en carry the P561 words; P559 「上次没有通过」 is gone', () => {
  const D = Object.fromEntries(['web.zh.src', 'web.zh.override', 'web.zh', 'web.en'].map((n) => [n, JSON.parse(read(`../i18n/${n}.json`))]));
  for (const k of ['p561.pk.st.missing', 'p561.pk.reset', 'p561.fail.missing']) {
    for (const [n, d] of Object.entries(D)) assert.ok(typeof d[k] === 'string' && d[k], n + ' ' + k);
    assert.equal(D['web.zh'][k], D['web.zh.src'][k]); assert.equal(D['web.zh'][k], D['web.zh.override'][k]);
  }
  for (const d of Object.values(D)) for (const k of ['p559.pk.st.failed', 'p559.pk.failedNote', 'p561.pk.missingNote', 'p561.fail.reset']) assert.equal(d[k], undefined, k);
  assert.equal(D['web.en']['p561.pk.st.missing'], "This remote's {bio} record can't be found (you can set it up again)");
  assert.equal(D['web.zh']['p561.fail.missing'], D['web.zh']['p561.pk.st.missing']);
  T.setTextOs('mac');
  assert.equal(T.t('p561.pk.reset'), '重新设置');
  T.setTextOs(undefined);
});

// P561 追加 3/4 (董事长 10-28 00:1x: Mac M1 reset twice — 「设置成功」 shown, the row still said 「重新设置」 after a reload, cards
// still failed). Two defects: the 0.25.15 mark was never cleared by a saved key; a card that arrived before the reset carries
// the OLD credential id, so its 「再试一次」 asked the browser for the replaced key and marked the row again.
const NEW = Buffer.alloc(16, 9).toString('base64url');
const idsOf = (pk) => pk.allowCredentials.map((c) => Buffer.from(c.id).toString('base64url'));

test('migration: the 0.25.15 mark is dropped once on load', () => {
  assert.equal(MIGRATED, true);
});

test('a saved key is asked for first, even from a card that still names the old one; a refusal marks the new key', async () => {
  mem.clear();
  F.setChannel('CH1');                                    // P566: OWN is kept per computer
  F.noteResult(ID, false, false, 'NotAllowedError'); F.noteResult(ID, false, true, 'NotAllowedError');
  assert.equal(F.failedHere(), true);
  F.clearFailed(NEW);                                     // pk_reg_res ok after 「重新设置」
  assert.equal(F.failedHere(), false);
  assert.deepEqual(F.ownIds(ID), [NEW, ID]);
  assert.deepEqual(F.ownIds(NEW), [NEW]);
  F.noteResult(ID, false, false, 'NotAllowedError'); F.noteResult(ID, false, true, 'NotAllowedError');   // an old-key refusal (an old page) no longer marks the row
  assert.equal(F.failedHere(), false);
  let asked = null, ok = true;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { credentials: { get: async (o) => {
    asked = o.publicKey;
    if (!ok) { const e = new Error('x'); e.name = 'NotAllowedError'; throw e; }
    const b = (n) => new Uint8Array(n).buffer;
    return { rawId: Buffer.from(idsOf(o.publicKey)[0], 'base64url'), response: { clientDataJSON: b(4), authenticatorData: b(37), signature: b(8) } };   // the first key it was asked for
  } } } });
  const fa = await F.approve(new Uint8Array(32), ID, false, 'local');   // the card still says ID
  assert.deepEqual(idsOf(asked), [NEW, ID]);
  assert.equal(asked.hints, undefined);                  // 追加 8: no hints for this device's own key
  assert.equal(fa.id, NEW);
  ok = false;
  await assert.rejects(F.approve(new Uint8Array(32), ID, false, 'local'));
  await assert.rejects(F.approve(new Uint8Array(32), ID, false, 'local'));
  assert.equal(F.failedHere(), true);                    // the newest key itself was refused twice → 「找不到了」
  F.setChannel('CH2');                                    // P566 (#350): another computer — this seat's newest key is not asked for there
  assert.deepEqual(F.ownIds(ID), [ID]);
  F.setChannel('CH1');
  mem.clear();
  ok = true;
  await F.approve(new Uint8Array(32), ID, false, 'borrowed');
  assert.deepEqual(idsOf(asked), [ID]);                   // a borrowed key: only the card's id, the phone route
});

test('get() asks with the transports create() reported (Google Password Manager / iCloud Keychain), as reported, else no filter; no hints', async () => {
  mem.clear();
  const b = (n) => new Uint8Array(n).buffer;
  let opts = null;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/141', maxTouchPoints: 0,
    credentials: { create: async (o) => { opts = o.publicKey; return { rawId: Buffer.from(NEW, 'base64url'), authenticatorAttachment: 'platform',
      response: { getPublicKey: () => b(91), getPublicKeyAlgorithm: () => -7, getTransports: () => ['hybrid', 'internal', 'bogus'], clientDataJSON: b(4), attestationObject: b(8) } }; } } } });
  const reg = await F.register(new Uint8Array(32), { relayIndex: 0, channel: ID, hostPub: new Uint8Array(32), peer: true });
  assert.equal(reg.id, NEW);
  assert.equal(opts.authenticatorSelection.authenticatorAttachment, 'platform');
  assert.deepEqual(F.transportsOf(NEW), ['hybrid', 'internal']);
  assert.deepEqual(F.wantOf(NEW, 'local', false).allowCredentials[0].transports, ['hybrid', 'internal']);
  assert.equal(F.wantOf(NEW, 'local', false).hints, undefined);
  assert.equal(F.wantOf(NEW, 'unknown', false).hints, undefined);
  assert.deepEqual(F.wantOf(ID, 'local', false).allowCredentials[0].transports, []);           // older key: nothing kept → no filter
  assert.deepEqual(F.wantOf(ID, 'unknown', false).allowCredentials[0].transports, []);
  assert.deepEqual(F.wantOf(NEW, 'borrowed', false).hints, ['hybrid']);
  assert.deepEqual(F.wantOf(NEW, 'local', true).allowCredentials[0].transports, []);              // P559 plain form unchanged
  assert.deepEqual(F.wantOf(NEW, 'borrowed', false).allowCredentials[0].transports, ['hybrid']);
});

test('Settings layout: words on top, the button under them, left-aligned (static)', () => {
  const css = read('../public/app.css');
  assert.match(css, /#settings \.pkrow\{display:grid;grid-template-columns:auto minmax\(0,1fr\)/);
  assert.match(css, /#settings \.pkrow>\.aj-btn\{grid-column:2;justify-self:start\}/);
  assert.match(css, /#settings \.setrow>\.aj-btn--sm\{grid-column:1 \/ -1;justify-self:start;order:1\}/);
  assert.match(css, /#settings \.preauth__item\{flex-direction:column;align-items:flex-start\}/);
});

// P561 追加 6 (#335, 华为 Pad 10-28 00:02: dev_gone twice while the passkey sheet was up, the approval never left the page)
test('a passkey answer waits for the reconnected session instead of 「没发出去」 (static + whenReady)', async () => {
  const api = read('../public/js/api.js');
  assert.match(api, /if \(!fa\) await sendApp\(out, g\);/);                        // no passkey: as before, pinned to its session
  // P566 (#364): handed in through pkhandin — the first ready connection, and again after a reconnect that swallowed it
  assert.match(api, /const sent = await handIn\('ask:' \+ p\.id, \{ send: \(g2\) => sendApp\(out, g2\), until: Date\.now\(\) \+ APPROVE_HANDIN_MS, ch,/);
  const S = await import('../public/js/session.js');
  const t0 = Date.now();
  assert.equal(await S.whenReady(300), null);                                        // never connected → null after the wait
  assert.ok(Date.now() - t0 >= 250);
});
