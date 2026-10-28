// P559 (董事长 10-27 17:05): the Huawei Pad (a touch tablet whose browser says plain "X11; Linux" — desktop mode) showed
// 「Face ID」 on the hand-over card and its 「已设好」 key never passed. Page side: a touch Linux browser is the Android family
// (Touch ID, never Face ID); after a failed tap the next tap asks in the plain form (no transports filter, no hints); failing in
// both forms in a row turns Settings' 「已设好」 into 「上次没有通过」 with the set-up button until a tap passes again.
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
  get childElementCount() { return this.children.length; }
}
globalThis.document ??= { getElementById: () => null, addEventListener() {}, body: null, createElement: (t) => new El(t) };
const ENV = await import('../public/js/phone-env.js');
const F = await import('../public/js/faceid.js');
const A = await import('../public/js/pkassist.js');
const T = await import('../public/js/t.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const ID = Buffer.alloc(16, 9).toString('base64url');
const PAD = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36';

test('a touch tablet in desktop mode is the Android family: Touch ID, never Face ID', () => {
  assert.equal(ENV.osOf(PAD, 10), 'android');
  assert.equal(ENV.osOf(PAD, 0), 'linux');                 // a desktop Linux browser stays linux
  T.setTextOs(ENV.osOf(PAD, 10));
  assert.equal(T.t('p336.faYes'), '用 Touch ID 确认');         // the card's button on the Huawei Pad
  assert.doesNotMatch(T.t('p539.fail.setup'), /Face ID/);
  T.setTextOs(undefined);
});

test('get() form: hinted by default, plain after a failure; a pass keeps the form, both forms failing marks the key', () => {
  mem.clear();
  const hinted = F.wantOf(ID, 'local');                    // P561 追加 8: no hints, no hard-coded ['internal'] (nothing kept → [])
  assert.deepEqual(hinted.allowCredentials[0].transports, []);
  assert.equal(hinted.hints, undefined);
  const plain = F.wantOf(ID, 'local', true);
  assert.deepEqual(plain.allowCredentials[0].transports, []);
  assert.equal(plain.hints, undefined);
  assert.equal(F.isPlain(ID), false);
  assert.equal(F.noteResult(ID, false), true);             // hinted failed → next tap plain; P566 (#350): one refusal never marks
  assert.equal(F.isPlain(ID), true); assert.equal(F.failedHere(), false);
  assert.deepEqual(F.wantOf(ID, 'local').allowCredentials[0].transports, []);
  assert.equal(F.noteResult(ID, false), false);            // plain failed too → P566: the second clear miss marks it, back to hinted
  assert.equal(F.failedHere(), true); assert.equal(F.isPlain(ID), false);
  F.noteResult(ID, false); assert.equal(F.noteResult(ID, true), true);   // plain passed: kept, mark cleared
  assert.equal(F.failedHere(), false); assert.equal(F.isPlain(ID), true);
});

test('approve(): the second tap after a failure really asks in the plain form', async () => {
  mem.clear();
  const seen = [];
  let fail = true;
  const resp = { clientDataJSON: new Uint8Array(1).buffer, authenticatorData: new Uint8Array(1).buffer, signature: new Uint8Array(1).buffer };
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { credentials: { get: async ({ publicKey }) => {
    seen.push(publicKey);
    if (fail) { const e = new Error('x'); e.name = 'NotAllowedError'; throw e; }
    return { rawId: new Uint8Array(16).fill(9).buffer, response: resp };   // the key it was asked for (P566: another seat's is refused)
  } } } });
  await assert.rejects(F.approve(new Uint8Array(32), ID, false, 'local'));
  fail = false;
  await F.approve(new Uint8Array(32), ID, false, 'local');
  assert.deepEqual(seen[0].allowCredentials[0].transports, []);   // P561 追加 8
  assert.equal(seen[0].hints, undefined);
  assert.deepEqual(seen[1].allowCredentials[0].transports, []);
  assert.equal(seen[1].hints, undefined);
  assert.equal(F.failedHere(), false);
});

test('Settings: 「已设好」 only while the key passes here', () => {
  const box = () => ({ row: { hidden: true, dataset: {} }, label: {}, note: {}, setupBtn: {}, oldBtn: { dataset: {} } });
  const ok = box();
  A.paintPkRow('local', ok);
  assert.equal(ok.setupBtn.hidden, true);
  const bad = box();
  A.paintPkRow('local', { ...bad, failed: true });
  assert.equal(bad.row.dataset.pk, 'failed');
  assert.equal(bad.label.textContent, A.withBio('p561.pk.st.missing'));   // P561 wording
  assert.equal(bad.setupBtn.hidden, false);
  assert.equal(bad.setupBtn.textContent, T.t('p561.pk.reset'));
  assert.equal(bad.note.hidden, true);   // P561 (10-27 23:1x): no long note
  assert.match(read('../public/app.js'), /failed: passkey\.failedHere\(\)/);
  const D = Object.fromEntries(['web.zh.src', 'web.zh.override', 'web.zh', 'web.en'].map((n) => [n, JSON.parse(read(`../i18n/${n}.json`))]));
  for (const k of ['p561.pk.st.missing', 'p561.pk.reset']) for (const d of Object.values(D)) assert.ok(d[k], k);
});

// P559 追加 (chairman 17:13): only two names in titles / cards / buttons — iPhone / iPad = Face ID; Mac, Android / HarmonyOS
// (Huawei Pad), Linux = Touch ID. 「屏幕锁」 / 「指纹或人脸」 never come back as the name; renaming is a user-visible change
// that goes to Jarvis → the chairman first.
test('the unlock is named Face ID or Touch ID — never 屏幕锁 / 指纹或人脸 (static lock)', () => {
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  for (const os of ['ios', 'android', 'mac', 'linux', 'other']) {
    const want = os === 'ios' ? 'Face ID' : 'Touch ID';
    assert.equal(zh['p539.bio.' + os], want, 'zh ' + os); assert.equal(en['p539.bio.' + os], want, 'en ' + os);
    assert.equal(ENV.bioLabel(os, 'zh'), want);
  }
  for (const [ua, touch, want] of [[PAD, 10, 'Touch ID'], ['Mozilla/5.0 (Linux; Android 14; PJZ110) Chrome/129.0 Mobile', 5, 'Touch ID'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/141.0', 0, 'Touch ID'], ['Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X)', 5, 'Face ID'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605', 5, 'Face ID']]) {   // the last one: an iPad asking for the desktop site
    T.setTextOs(ENV.osOf(ua, touch));
    for (const k of ['p539.pk.st.local', 'p539.pk.st.none']) assert.equal(A.withBio(k), '本机 ' + want + '：' + zh[k].split('：')[1], ua + ' ' + k);
    assert.equal(T.t('p336.faYes'), '用 ' + want + ' 确认');
  }
  T.setTextOs(undefined);
  // the explanation line, verbatim, and it is never swapped (it names all three)
  assert.equal(zh['p559.pk.sub'], '花钱、删数据、解绑或转移席位、密钥外发前的最后确认。按设备不同，可能是 Face ID、Touch ID 或屏幕锁。');
  assert.match(en['p559.pk.sub'], /Face ID, Touch ID or screen lock/);
  T.setTextOs('android');
  assert.equal(T.tRaw('p559.pk.sub'), zh['p559.pk.sub']);
  T.setTextOs(undefined);
});

test('the Settings row has the same shape as 遥控器管理 / 转移席位: icon + title + small explanation (static)', () => {
  const html = read('../public/index.html');
  const row = html.slice(html.indexOf('id="set-pk-row"'), html.indexOf('id="setup-faceid"'));
  assert.match(row, /<svg class="mi" data-icon="face"/);
  assert.match(row, /<svg class="mi" data-icon="finger"/);
  assert.match(row, /<span class="pkrow__txt"><b class="setlab" id="set-pk-state"[^>]*>[^<]*<\/b><small id="set-pk-sub"><\/small>/);
  const js = read('../public/js/pkassist.js');
  assert.match(js, /sub\.textContent = tRaw\('p559\.pk\.sub'\)/);
  assert.match(js, /textOs\(\) === 'ios' \? 'face' : 'finger'/);
});

test('a borrowed key always asks the phone route — the plain fallback is only for this device\'s own key', () => {
  mem.clear();
  F.noteResult(ID, false);                                   // the own-key form failed once → plain next time
  assert.deepEqual(F.wantOf(ID, 'local').allowCredentials[0].transports, []);
  const b = F.wantOf(ID, 'borrowed');
  assert.deepEqual(b.allowCredentials[0].transports, ['hybrid']); assert.deepEqual(b.hints, ['hybrid']);
  mem.clear();
});

test('P560: a pass or a second failure tells Settings to repaint its row in the same session (no reload needed)', () => {
  mem.clear();
  const seen = [];
  const had = globalThis.dispatchEvent;
  globalThis.dispatchEvent = (e) => { seen.push(e.type); return true; };
  try {
    F.noteResult(ID, false); assert.deepEqual(seen, []);                                 // P566 (#350): one refusal never marks
    F.noteResult(ID, false); assert.deepEqual(seen, ['agentj-pk-failed-change']);        // the second clear miss → 找不到了
    F.noteResult(ID, false); assert.deepEqual(seen, ['agentj-pk-failed-change']);        // still marked: no repaint
    F.noteResult(ID, true); assert.deepEqual(seen, ['agentj-pk-failed-change', 'agentj-pk-failed-change']);   // passed → 已设好
    F.noteResult(ID, true); assert.equal(seen.length, 2);                                // no change, no repaint
  } finally { globalThis.dispatchEvent = had; }
  assert.match(read('../public/app.js'), /addEventListener\('agentj-pk-failed-change', paintPkRow\)/);
});
