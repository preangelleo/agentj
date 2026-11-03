// #427 · Settings → AI 账号 (js/accounts.js) — node unit: name rules (1–24 characters, unique per harness, case-insensitive,
// a rename may keep its own name), the quota line (「—」 when there is no reading, never estimated; used % clamped; window
// names), both dictionaries carry every acct.* key with the same {placeholders}, no trial wording, the zh source = override =
// polished copy, and the page wiring (the sub-page, its nav row, the flow card drawn into both boxes, the wire names).
// The real page in Chromium against the fake host: test/harness-accounts.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
class El {
  constructor(tag) { this.tagName = String(tag).toUpperCase(); this.kids = []; this.attrs = {}; this.dataset = {}; this.className = ''; this.hidden = false; this.style = {}; this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false }; }
  append(...ns) { this.kids.push(...ns); } replaceChildren(...ns) { this.kids = ns; } setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; } addEventListener() {} removeAttribute(k) { delete this.attrs[k]; }
}
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document ??= { createElement: (t) => new El(t), createElementNS: (_, t) => new El(t), createTextNode: (s) => ({ textContent: s }), addEventListener() {},
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], documentElement: { dataset: {}, style: { setProperty() {} } }, body: { dataset: {} }, visibilityState: 'visible' };

const A = await import('../public/js/accounts.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const dict = (p) => JSON.parse(read(p));

test('name rules: trimmed, 1–24 characters, unique per harness (case-insensitive), a rename keeps its own name', () => {
  const items = [{ id: 'a1', name: '主号' }, { id: 'b2', name: 'Backup' }];
  assert.equal(A.nameProblem('', items), 'acct.nameEmpty');
  assert.equal(A.nameProblem('   ', items), 'acct.nameEmpty');
  assert.equal(A.nameProblem('备用', items), '');
  assert.equal(A.nameProblem(' 主号 ', items), 'acct.nameDup');
  assert.equal(A.nameProblem('backup', items), 'acct.nameDup');
  assert.equal(A.nameProblem('backup', items, 'b2'), '', 'renaming b2 to its own name (another case) is fine');
  assert.equal(A.nameProblem('字'.repeat(24), items), '');
  assert.equal(A.nameProblem('字'.repeat(25), items), 'acct.nameLong');
  assert.equal(A.nameProblem('😀'.repeat(24), items), '', 'counted in characters, not UTF-16 units');
  assert.equal(A.NAME_MAX, 24); assert.equal(A.ITEMS_MAX, 8);
  assert.deepEqual(A.KINDS.map((k) => k[0]), ['claude', 'codex']);
  assert.deepEqual(A.KINDS.map((k) => k[2]), ['setup_token', 'device'], 'Claude = setup-token route, Codex = device code');
});

test('quota line: 「—」 without a reading, used % clamped and rounded, window + time when known', () => {
  for (const q of [null, undefined, {}, { pct: null }, { pct: 'x' }, { pct: NaN }]) assert.match(A.quotaText(q, true), / —$/);
  const at = Date.UTC(2026, 10, 3, 5, 20) / 1000;
  const s = A.quotaText({ pct: 41.6, window: 'week', at }, true);
  assert.match(s, /42/); assert.doesNotMatch(s, /—/);
  assert.match(A.quotaText({ pct: 130, window: '5h' }, false), /100/);
  assert.match(A.quotaText({ pct: -3 }, false), /\b0\b/);
  assert.notEqual(A.quotaText({ pct: 10 }, true).split(' ')[0], A.quotaText({ pct: 10 }, false).split(' ')[0], 'current vs last reading lead');
});

test('dictionaries: every acct.* key in both languages, same placeholders, zh source = override = polished, no trial wording', () => {
  const zh = dict('../i18n/web.zh.json'), src = dict('../i18n/web.zh.src.json'), ov = dict('../i18n/web.zh.override.json'), en = dict('../i18n/web.en.json');
  const keys = Object.keys(zh).filter((k) => k.startsWith('acct.'));
  assert.ok(keys.length >= 40, String(keys.length));
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');
  for (const k of keys) {
    assert.ok(typeof en[k] === 'string' && en[k], 'en ' + k);
    assert.equal(src[k], zh[k], 'src ' + k); assert.equal(ov[k], zh[k], 'override ' + k);
    assert.equal(ph(zh[k]), ph(en[k]), 'placeholders ' + k);
    assert.doesNotMatch(zh[k] + en[k], /试用|trial/i, k);
    assert.doesNotMatch(zh[k], /[^J] 主机|电脑/, 'Agent J 主机 only: ' + k);
  }
  // every key the module and page use exists
  const used = new Set([...read('../public/js/accounts.js').matchAll(/'(acct\.[\w.]+)'/g)].map((m) => m[1]));
  for (const m of read('../public/index.html').matchAll(/data-i18n="(acct\.[\w.]+)"/g)) used.add(m[1]);
  for (const k of used) if (!k.endsWith('.')) assert.ok(k in zh && k in en, 'missing ' + k);
});

test('page wiring: sub-page + nav row, wire names, flow card into both boxes (parity needle kept)', () => {
  const html = read('../public/index.html'), set = read('../public/js/settings.js'), js = read('../public/js/accounts.js'), ar = read('../public/js/authrescue.js');
  for (const id of ['set-accts', 'set-accts-nav', 'acct-msg', 'acct-flow', 'acct-list']) assert.ok(html.includes(`id="${id}"`), id);
  assert.match(set, /\['accts', 'set-accts-nav'\]/); assert.match(set, /accounts\.load\(\)/); assert.match(set, /accounts\.close\(\)/);
  for (const w of ["t: 'acct_get'", "t: 'acct_use'", "t: 'acct_rename'", "t: 'acct_del'", "'acct_res'", "t: 'auth_start', kind: harness, route, account: { name }"]) assert.ok(js.includes(w), w);
  assert.ok(ar.includes('function flowCard(f)'), 'parity needle');
  assert.match(ar, /FLOW_BOXES = \['ar-flow', 'acct-flow'\]/);
  assert.doesNotMatch(js, /innerHTML/);
  assert.doesNotMatch(js, /confirmSheet/, 'delete: the host confirmation card is the only confirmation (CEO 11-03)');
  assert.match(js, /t: 'acct_del', harness, id: it\.id \}, LONG\)/);
});
