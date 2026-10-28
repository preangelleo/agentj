// P539 (#300 / #299): per-platform passkey words, the shared "no own passkey here" block, ask_done reasons,
// pre-authorization offers and draft_restore — pure logic, in Node (no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window ??= {};   // t.js without brand/lang.js = 中文 (lang() reads window.AJLang)
const ENV = await import('../public/js/phone-env.js');
const T = await import('../public/js/t.js');
const R = await import('../public/js/pkrules.js');
const A = await import('../public/js/pkassist.js');
const DICT = (await import('../public/i18n.js')).default;
const LANG = (l) => { window.AJLang = { get: () => l, t: (d, k, v) => { const s = d[l][k] ?? d.zh[k] ?? k; return v ? s.replace(/\{(\w+)\}/g, (m, x) => (x in v ? String(v[x]) : m)) : s; }, onChange() {} }; };

const UA = {
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  ipadMac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
  oppo: 'Mozilla/5.0 (Linux; Android 14; PJZ110 Build/UKQ1.230924.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36 HeyTapBrowser/45.11',
  huawei: 'Mozilla/5.0 (Linux; Android 12; HarmonyOS; ALN-AL00; HMSCore 6.13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0 HuaweiBrowser/15.0 Mobile Safari/537.36',
  macChrome: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
  winChrome: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
  linux: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
};

test('os detection matrix; browserEnvironment keeps its exact shape; probeEnvironment adds os', async () => {
  const cases = [['iphone', 0, 'ios'], ['ipadMac', 5, 'ios'], ['ipadMac', 0, 'mac'], ['oppo', 5, 'android'], ['huawei', 5, 'android'],
    ['macChrome', 0, 'mac'], ['winChrome', 0, 'windows'], ['linux', 0, 'linux']];
  for (const [k, touch, want] of cases) assert.equal(ENV.osOf(UA[k], touch), want, k);
  assert.equal(ENV.osOf('Node.js/22'), 'other');
  assert.deepEqual(Object.keys(ENV.browserEnvironment(UA.oppo, 5)).sort(), ['inApp', 'platform']);
  assert.equal(ENV.browserEnvironment(UA.ipadMac, 5).platform, 'ios');
  const e = await ENV.probeEnvironment({ ua: UA.winChrome, touch: 0, storage: { persist() {}, persisted: async () => true }, supported: async () => true, probe: async () => true });
  assert.equal(e.os, 'windows'); assert.equal(e.platform, 'desktop');
});

test('bioLabel zh / en', () => {
  const want = { ios: ['Face ID', 'Face ID'], android: ['Touch ID', 'Touch ID'], mac: ['Touch ID', 'Touch ID'],
    windows: ['Windows Hello', 'Windows Hello'], linux: ['Touch ID', 'Touch ID'], other: ['Touch ID', 'Touch ID'] };   // P559: two names only
  for (const [os, [zh, en]] of Object.entries(want)) { assert.equal(ENV.bioLabel(os, 'zh'), zh); assert.equal(ENV.bioLabel(os, 'en'), en); }
  assert.equal(ENV.bioLabel('nope', 'en'), 'Touch ID');
});

test('t(): Face ID becomes the platform label on Android / Mac / Windows / Linux (P559); iOS and Node are unchanged', () => {
  LANG('zh');
  T.setTextOs(undefined);
  assert.equal(T.textOs(), 'ios', 'node (no browser UA) never substitutes');
  assert.equal(T.t('phoneEnv.setup'), '设置 Face ID');
  T.setTextOs('android');
  assert.equal(T.t('phoneEnv.setup'), '设置 Touch ID');
  assert.equal(T.t('pk.askTitle'), '用 Touch ID 记住这台手机？');
  assert.equal(T.t('p352.needFaceId', { device: 'X' }).split('Touch ID').length, 2, 'the "Face ID / Touch ID" pair collapses to one label');
  T.setTextOs('mac');
  assert.equal(T.t('p352.needFaceId', { device: 'X' }).split('Touch ID').length, 2);
  assert.ok(!T.t('elev.fa.cancel').includes('Face ID'));
  T.setTextOs('linux');
  assert.equal(T.t('phoneEnv.setup'), '设置 Touch ID');
  LANG('en');
  T.setTextOs('android');
  assert.equal(T.t('elev.fa.cancel'), "Touch ID didn't go through. Nothing was sent.");
  assert.equal(T.t('phoneEnv.setup'), 'Set up Touch ID');
  T.setTextOs('windows');
  assert.equal(T.t('pk.restore'), 'Reconnect with Windows Hello');
  T.setTextOs('ios');
  assert.equal(T.t('pk.restore'), 'Reconnect with Face ID');
  assert.equal(T.bioWords('{device} uses Face ID', 'android', 'en'), '{device} uses Touch ID', 'vars are filled after the swap');
  T.setTextOs(undefined); LANG('zh');
});

test('pk.notSaved / pk.none per platform: Android never says Apple / iCloud / iPhone / Face ID; Mac says Touch ID; iOS unchanged', () => {
  for (const lng of ['zh', 'en']) {
    LANG(lng);
    for (const base of ['pk.notSaved', 'pk.none']) {
      T.setTextOs('android');
      const and = T.t(A.osKey(base, 'android'));
      assert.equal(A.osKey(base, 'android'), base + '.android');
      for (const bad of ['Apple', 'iCloud', 'iPhone', 'Face ID', '钥匙串']) assert.ok(!and.includes(bad), `${lng} ${base} android: ${bad}`);
      assert.match(and, /Google/);
      T.setTextOs('mac');
      const mac = T.t(A.osKey(base, 'mac'));
      assert.ok(mac.includes('Touch ID') && !mac.includes('Face ID'), `${lng} ${base} mac`);
      T.setTextOs('windows');
      assert.ok(T.t(A.osKey(base, 'windows')).includes('Windows Hello'));
      assert.ok(!/Apple|iCloud|Face ID/.test(T.t(A.osKey(base, 'linux'))), 'linux → other');
      assert.equal(A.osKey(base, 'linux'), base + '.other');
      T.setTextOs('ios');
      assert.equal(T.t(A.osKey(base, 'ios')), DICT[lng][base], 'iOS keeps the iCloud Keychain path word for word');
    }
  }
  T.setTextOs(undefined); LANG('zh');
});

test('fa_s → the quiet hint; WebAuthn errors → the reason', () => {
  assert.equal(R.hintKey('local'), null);
  assert.equal(R.hintKey('borrowed'), 'p539.hint.borrowed');
  assert.equal(R.hintKey('unknown'), 'p539.hint.unknown');
  assert.equal(R.hintKey(null), null, 'older host: nothing extra');
  assert.equal(R.faState('local'), 'local'); assert.equal(R.faState('LOCAL'), null); assert.equal(R.faState(undefined), null);
  assert.equal(R.failKey({ name: 'NotAllowedError' }, 'borrowed'), 'p539.fail.borrowed');
  assert.equal(R.failKey('NotAllowedError', 'local'), 'p539.fail.cancel');
  assert.equal(R.failKey('NotAllowedError', null), 'p539.fail.cancel');
  assert.equal(R.failKey('AbortError'), 'p539.fail.abort');
  assert.equal(R.failKey('SecurityError'), 'p539.fail.other');
  assert.equal(R.errName({ name: 'NotAllowedError', message: 'secret detail' }), 'NotAllowedError');
  assert.equal(R.errName({ name: '<x>' }), 'Error');
  assert.equal(R.errName(null), 'Error');
  LANG('zh');
  assert.equal(T.t('p539.hint.borrowed'), '这台遥控器还没有自己的 {bio} 钥匙，系统会请你用手机扫码确认。');
  assert.equal(T.t('p539.pk.setup'), '在本机设置');
});

test('ask_done why → reason key; absent / unknown keeps r.appr.st.*', () => {
  for (const w of ['timeout', 'denied', 'passkey', 'nobody', 'estop', 'serve_stop']) {
    assert.equal(R.whyKey(w), 'p539.why.' + w);
    assert.ok(DICT.zh['p539.why.' + w] && DICT.en['p539.why.' + w], w);
  }
  assert.equal(R.whyKey(undefined), null); assert.equal(R.whyKey('other'), null);
  assert.match(DICT.zh['p539.why.timeout'], /^等太久没批准，已替你拒绝。/);
});

test('allow_offer preauth filter: strict pattern, ≤ 3, unknown actions not rendered', () => {
  assert.deepEqual(R.preauthOffer(['preauth:computer.control', 'preauth:Computer', 'preauth:', 'preauth:a b', 'x@y.z', 7, 'preauth:' + 'a'.repeat(41),
    'preauth:browser.control', 'preauth:a', 'preauth:b']), ['preauth:computer.control', 'preauth:browser.control', 'preauth:a']);
  assert.deepEqual(R.preauthOffer('preauth:computer.control'), []);
  assert.equal(R.preauthLabelKey('preauth:computer.control'), 'p539.pre.computer.control');
  assert.equal(R.preauthLabelKey('preauth:browser.control'), null);
  assert.deepEqual(R.preauthShown(['preauth:browser.control', 'preauth:computer.control']), ['preauth:computer.control']);
  assert.ok(R.PREAUTH_RE.test('preauth:computer.control') && !R.PREAUTH_RE.test('preauth:computer.control\n'));
  LANG('zh'); assert.equal(T.t('p539.pre.computer.control'), '以后这台遥控器批准的 Computer Use 不再问');
  LANG('en'); assert.equal(T.t('p539.pre.computer.control'), "Don't ask again for Computer Use approved from this remote");
  LANG('zh');
  const items = R.preauthItems({ items: [{ action: 'computer.control', device: 'd1', name: 'Mac\u0007 Chrome', at: 1760000000 }, { action: 'BAD', device: 'x' }, null, { action: 'preauth:computer.control', device: 'd2' }] });
  assert.deepEqual(items, [{ action: 'computer.control', device: 'd1', name: 'Mac Chrome', at: 1760000000 }, { action: 'preauth:computer.control', device: 'd2', name: '', at: null }]);
  assert.deepEqual(R.preauthItems({}), []);
  // two bindings (via mcp_ops / codex_plugin) on one remote → one row, the newest
  const two = R.preauthItems({ items: [{ action: 'computer.control', device: 'd1', name: 'Mac', at: 10, via: 'mcp_ops' },
    { action: 'computer.control', device: 'd1', name: 'Mac', at: 20, via: 'codex_plugin' }] });
  assert.deepEqual(two, [{ action: 'computer.control', device: 'd1', name: 'Mac', at: 20 }]);
});

test('snap.addAsk keeps preauth offers on any tool, mail offers only on mail; fa_s and done_why are carried', async () => {
  const snap = await import('../public/js/snap.js');
  const id = (n) => n.toString(16).padStart(32, '0');
  snap.addAsk({ id: id(1), tool: 'mcp__computer-use__left_click', summary: 'click', allow_offer: ['preauth:computer.control', 'a@b.c'], passkey_required: true, fa: 'cred', fa_ch: 'x', fa_s: 'borrowed' });
  const a = snap.S.asks.get(id(1));
  assert.deepEqual(a.allow_offer, ['preauth:computer.control']);
  assert.equal(a.fa_s, 'borrowed');
  snap.addAsk({ id: id(2), tool: 'agentj mail send', summary: 'mail', allow_offer: ['a@b.c'] });
  assert.deepEqual(snap.S.asks.get(id(2)).allow_offer, ['a@b.c']);
  snap.askDone({ id: id(1), result: 'timeout', why: 'timeout' });
  assert.equal(snap.S.lastAsk.done_why, 'p539.why.timeout');
  snap.askDone({ id: id(2), result: 'deny' });
  assert.equal(snap.S.lastAsk.done_why, null);
});

test('draft_restore: empty composer takes the text, else a new line under it; empty text → the retype toast', () => {
  assert.deepEqual(R.draftRestore({ text: '帮我改一下' }), { text: '帮我改一下', key: 'p539.draft.back' });
  assert.deepEqual(R.draftRestore({ text: '  \n ' }), { text: null, key: 'p539.draft.empty' });
  assert.deepEqual(R.draftRestore({}), { text: null, key: 'p539.draft.empty' });
  assert.equal(R.draftRestore({ text: 'x'.repeat(R.DRAFT_MAX + 5) }).text.length, R.DRAFT_MAX);
  assert.equal(R.mergeDraft('', 'hello'), 'hello');
  assert.equal(R.mergeDraft('   ', 'hello'), 'hello');
  assert.equal(R.mergeDraft('mine  \n', 'hello'), 'mine\nhello');
  LANG('zh'); assert.equal(T.t('p539.draft.back'), '原话已放回输入框，改好再发');
  assert.equal(T.t('p539.draft.empty'), '请重新输入要发的话');
});

test('wire: pk_reg carries aa + plat, pk_restore carries att; one shared block on every card type', () => {
  const src = (f) => readFileSync(new URL('../public/' + f, import.meta.url), 'utf8');
  const fid = src('js/faceid.js');
  assert.match(fid, /if \(a\) result\.aa = a;/);
  assert.match(fid, /result\.plat = platOf\(\);/);
  assert.match(fid, /if \(a\) out\.att = a;/);
  assert.match(src('app.js'), /\.\.\.\(a\.att \? \{ att: a\.att \} : \{\}\)/);
  for (const f of ['js/elevate.js', 'js/relay.js', 'js/secretout.js']) assert.match(src(f), /paintPk\(/, f);
  for (const t of ["case 'pk_state'", "case 'preauth'", "case 'draft_restore'"]) assert.ok(src('app.js').includes(t), t);
  assert.ok(!/console\.|log\(/.test(src('js/pkassist.js') + src('js/pkrules.js')), 'the draft text is never logged');
});

test('the rest of the fa cards share the block: keys · cmd_run · terminal · vault row · controls (approval_set, 恢复)', () => {
  const src = (f) => readFileSync(new URL('../public/' + f, import.meta.url), 'utf8');
  // a WebAuthn rejection keeps its name; our own "no passkey here" errors are not a WebAuthn failure
  assert.equal(R.webauthnErr(new DOMException('x', 'NotAllowedError')), 'NotAllowedError');
  assert.equal(R.webauthnErr(new Error('passkey_failed')), null);
  assert.equal(R.webauthnErr(null), null);
  // the failure → the reason + 再试一次, never a copy of the words: each module goes through pkassist.js
  for (const f of ['js/keys.js', 'js/cmdrun.js', 'js/terminal.js', 'js/vaultrow.js']) assert.match(src(f), /paintPk\(/, f);
  for (const f of ['js/keys.js', 'js/cmdrun.js', 'js/vaultrow.js', 'js/settings.js']) assert.match(src(f), /pkSheet\(/, f);
  assert.match(src('js/controls.js'), /catch \(e\) \{ return \{ ok: false, why: 'passkey_failed', err: webauthnErr\(e\), fa_s: faState\(res\.fa_s\) \}; \}/);
  assert.match(src('js/controls.js'), /if \(!res\.ok && res\.err\) failNotice\(res\.err, res\.fa_s, onResume\);/);
  assert.match(src('js/settings.js'), /if \(m\.err\) failNotice\(m\.err, m\.fa_s, \(\) => approvalSet\(key, value\)\);/);
  for (const f of ['js/keys.js', 'js/cmdrun.js', 'js/terminal.js', 'js/vaultrow.js', 'js/controls.js', 'js/settings.js'])
    assert.doesNotMatch(src(f), /'p539\.(hint|fail)\./, f + ' does not carry its own copy of the words');
  // the shared confirm sheet: one block under its buttons, cleared on every open / close
  const html = src('index.html'), ui = src('js/ui.js');
  assert.ok(html.indexOf('id="confirm-pk"') > html.indexOf('id="confirm-yes"'), 'under the buttons');
  assert.equal((ui.match(/pkOff\(\)/g) || []).length, 2, 'cleared on open and on close');
  assert.match(html, /id="set-vault-pk"/);
  // the terminal sheet: 在本机设置 only on the intro sheet (it steps the sheet aside first)
  assert.match(src('js/terminal.js'), /setup: o\.kind === 'intro'/);
  assert.match(src('js/terminal.js'), /setupStepsAside\(\$\('term-sh-pk'\), \(\) => sheetNo\(\)\)/);
});

test('paintPk: setup:false leaves only 再试一次; the confirm sheet line is the same block', () => {
  LANG('zh');
  const mkBox = () => { const kids = []; return { hidden: true, dataset: {}, replaceChildren(...k) { kids.length = 0; kids.push(...k); }, kids }; };
  // a minimal DOM for ui.js mk(): enough to see which buttons exist
  const prevDoc = globalThis.document;
  globalThis.document = { createElement: (tag) => ({ tag, className: '', textContent: '', dataset: {}, children: [], hidden: false,
    append(...c) { this.children.push(...c); }, addEventListener() {}, setAttribute() {}, get childElementCount() { return this.children.length; } }) };
  try {
    const box = mkBox();
    A.paintPk(box, { fa: 'borrowed', err: 'NotAllowedError', retry: () => {}, setup: false });
    const keys = box.kids.flatMap((k) => [k.dataset.k, ...(k.children || []).map((c) => c.dataset.k)]).filter(Boolean);
    assert.deepEqual(keys, ['p539.fail.borrowed', 'p539.fail.retry']);
    const box2 = mkBox();
    A.paintPk(box2, { fa: 'borrowed', err: 'NotAllowedError', retry: () => {} });
    const keys2 = box2.kids.flatMap((k) => [k.dataset.k, ...(k.children || []).map((c) => c.dataset.k)]).filter(Boolean);
    assert.deepEqual(keys2, ['p539.fail.borrowed', 'p539.fail.retry', 'p539.fail.setup']);
  } finally { globalThis.document = prevDoc; }
});
