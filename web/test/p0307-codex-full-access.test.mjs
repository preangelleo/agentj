// 0.30.7 (chairman 11-10): 「改回全放行并重启」 on a Codex sandbox refusal caused by the owner's config.toml.
// The button exists only for the host's typed card.fix on a system page (never inferred from the text, which also shows the
// terminal commands); a tap is the signed cmd_run by NAME + Face ID; the same name is a Settings command row; an Agent's code
// block never gets this row's 「执行」.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

// a minimal DOM for ui.mk / button events (repair-actions.js paints with document.createElement only)
class Node_ {
  constructor(tag) { this.tagName = String(tag).toUpperCase(); this.children = []; this.dataset = {}; this.attrs = {}; this.listeners = {}; this.textContent = ''; this.className = ''; this.disabled = false; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  append(...xs) { for (const x of xs) this.children.push(x); }
  appendChild(x) { this.children.push(x); return x; }
  addEventListener(e, f) { (this.listeners[e] ||= []).push(f); }
  async click() { for (const f of this.listeners.click || []) await f({ preventDefault() {}, stopPropagation() {} }); }
  get classList() { return { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false }; }
}
globalThis.window = globalThis.window || {};
globalThis.document = globalThis.document || { createElement: (t) => new Node_(t), getElementById: () => null, documentElement: new Node_('html'),
  querySelectorAll: () => [], addEventListener() {}, body: new Node_('body') };
globalThis.addEventListener = globalThis.addEventListener || (() => {});
globalThis.localStorage = globalThis.localStorage || { getItem: () => null, setItem() {}, removeItem() {} };

const { fixOf, paintFix, fixResultText, FIX, repairOf } = await import('../public/js/repair-actions.js');

const page = (src, card, extra = {}) => ({ id: 7, end: 'done', source: src, card, reply: 'agentj codex-sandbox default', ...extra });

test('only the host-typed card.fix on a finished system page offers the button', () => {
  assert.equal(FIX, 'codex_full_access');
  assert.equal(fixOf(page({ k: 'sys' }, { fix: FIX })), true);
  for (const p of [
    page({ k: 'sys' }, null),                                   // desktop notice / any notice without the typed field
    page({ k: 'sys' }, { fix: 'reconnect' }),
    page({ k: 'sys' }, { fix: 'rm -rf ~' }),
    page({ k: 'agent' }, { fix: FIX }),                         // the Agent's own reply cannot carry it
    page({ k: 'phone' }, { fix: FIX }),
    page({ k: 'friend_report' }, { fix: FIX }),
    page({ k: 'sys', quote: { id: 1 } }, { fix: FIX }),
    page({ k: 'sys', via: 'mail' }, { fix: FIX }),
    page({ k: 'sys' }, { fix: FIX }, { end: 'open' }),
    page({ k: 'sys' }, { fix: FIX }, { assist: true }),
    page({ k: 'sys' }, '{"fix":"codex_full_access"}'),
  ]) assert.equal(fixOf(p), false, JSON.stringify(p));
  assert.equal(repairOf(page({ k: 'sys' }, { fix: FIX })), false, 'not the reconnect repair');
});

test('the button: one signed run per tap, disabled offline / on an older host, result words', async () => {
  let runs = 0; const said = [];
  const box = new Node_('div');
  const ctx = { ready: () => true, supported: () => true, run: async () => { runs++; return { ok: true, restart: 'requested' }; }, say: (x) => said.push(x) };
  assert.equal(paintFix(box, page({ k: 'sys' }, { fix: FIX }), ctx), true);
  const b = box.children[0];
  assert.equal(b.dataset.fix, FIX);
  assert.equal(b.disabled, false);
  await b.click();
  assert.equal(runs, 1);
  assert.equal(said.length, 1);
  for (const [ready, supported] of [[false, true], [true, false]]) {
    const box2 = new Node_('div');
    paintFix(box2, page({ k: 'sys' }, { fix: FIX }), { ...ctx, ready: () => ready, supported: () => supported });
    assert.equal(box2.children[0].disabled, true);
    await box2.children[0].click();
    assert.equal(runs, 1, 'a disabled button never sends');
  }
  assert.equal(paintFix(new Node_('div'), page({ k: 'sys' }, null), ctx), false);
  // result texts are distinct keys
  const keys = new Set([fixResultText({ ok: true, unchanged: true }), fixResultText({ ok: true, restart: 'requested' }),
    fixResultText({ ok: true, restart: 'manual' }), fixResultText({ ok: false, why: 'passkey_failed' })]);
  assert.equal(keys.size, 4);
});

test('cmdrun.js: a Settings row, never matched from text, never the reply 「执行」; Face ID wording of its own', () => {
  const js = read('../public/js/cmdrun.js');
  assert.match(js, /codex_full_access: 'codex-sandbox default'/);
  assert.match(js, /export const OWNER_ONLY = new Set\(\['codex_full_access'\]\)/);
  assert.match(js, /!SWITCH_NAME\.test\(n\) && !OWNER_ONLY\.has\(n\) && s === line\(n\)/, 'nameOf skips it');
  assert.match(js, /SWITCH_NAME\.test\(name\) \|\| OWNER_ONLY\.has\(name\)\) return \{ ok: false, why: 'shape' \}/, 'exec refuses it');
  assert.match(js, /export async function fullAccess\(\) \{\n\s+const name = 'codex_full_access';\n\s+if \(!names\.includes\(name\)\) return \{ ok: false, why: 'unsupported' \};/);
  assert.equal([...js.matchAll(/signedWriteFa\(/g)].length, 1, 'still one place sends');
  const tc = read('../public/js/termcommands.js');
  assert.doesNotMatch(tc, /codex_full_access/, 'no plain-words 「执行」 from text');
  const relay = read('../public/js/relay.js');
  assert.match(relay, /paintFix\(box,p,\{ready:\(\)=>isReady\(\)&&C\.paired\(\),supported:\(\)=>cmdSupports\('codex_full_access'\),run:\(\)=>fullAccess\(\)/);
});

test('strings exist in zh and en (dictionaries and the bundled i18n.js)', () => {
  const keys = ['p0307.fix.button', 'p0307.fix.faText', 'p0307.fix.restarting', 'p0307.fix.already', 'p0307.fix.manual',
    'p0307.fix.failed', 'p0307.fix.unsupported', 'p361.cmd.codex_full_access'];
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json')), src = JSON.parse(read('../i18n/web.zh.src.json'));
  for (const k of keys) for (const d of [zh, en, src]) assert.ok(typeof d[k] === 'string' && d[k].length > 3, k);
  assert.equal(zh['p0307.fix.button'], '改回全放行并重启');
  assert.equal(en['p0307.fix.button'], 'Back to full access & restart');
  const bundle = read('../public/i18n.js');
  for (const k of keys) assert.equal(bundle.split(JSON.stringify(k) + ':').length, 3, k + ' in both languages of i18n.js');
});
