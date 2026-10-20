// P393 (#124 「很多页面的搜索没用」) · the pure parts of the page search: the match rule, where each page's search looks, the
// strings in both languages, and that every search field in the app is in reports/qa/p393/search-inventory.md (a new search
// UI must be listed there with its end-to-end check in p393_search.mjs).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

globalThis.window = globalThis.window || {};          // t.js reads window.AJLang at import time
const PF = await import('../public/js/pagefind.js');
const WEB = fileURLToPath(new URL('../', import.meta.url));
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const read = (p) => readFileSync(WEB + p, 'utf8');

test('match rule: every word, case-insensitive, CJK substrings', () => {
  assert.deepEqual(PF.terms('  Free   CODE '), ['free', 'code']);
  assert.ok(PF.matches('永久免费码：AJ-FREE-0000', PF.terms('免费')));
  assert.ok(PF.matches('Lifetime free code', PF.terms('FREE code')));
  assert.ok(!PF.matches('Lifetime free code', PF.terms('free tax')));
  assert.ok(!PF.matches('预约地点：花园餐厅', PF.terms('免费')));
  assert.deepEqual(PF.terms(''), []);
});

test('a card page searches its own records, in place', () => {
  const jc = read('public/js/jcards.js'), fp = read('public/js/five-pages.js');
  assert.match(jc, /export function filter\(body, q\)/);
  assert.match(jc, /querySelectorAll\('\.jc-entry, \.jc-item'\)/, 'notes, automations and checklist rows are the units');
  assert.match(jc, /RAW\.set\(entry,String\(it\.text\)\)/, 'a note is matched on its own text too');
  assert.match(jc, /if \(hooks\.drawn\) hooks\.drawn\(\)/, 'a re-drawn card is searched again');
  assert.match(fp, /if\(current==='card'\)\{page\.scrollTop=0;cardFind\(\);return;\}/, 'typing never fetches the card again');
  assert.match(fp, /if\(current==='card'\)return;/, 'render() no longer says 没找到 before the records arrive');
  assert.match(read('public/cards.css'), /\.jc-body \[data-find-out\]\{display:none!important\}/);
});

test('Settings filters its own page and reaches the sub-pages', () => {
  const s = read('public/js/settings.js');
  assert.match(s, /function filterMain\(q\)/);
  assert.match(s, /export function subItems\(query\)/);
  assert.match(s, /filterMain\(q\);\n\}/, 'every keystroke filters the page');
  assert.match(s, /closeSearch\(\) \{[^\n]*filterMain\(''\)/, 'closing brings the whole page back');
  assert.match(s, /closeSearch\(\); showSub\(h\.sub\);/, 'a sub-page hit opens that sub-page');
  assert.match(s, /sub === 'contacts' && el\('ctm-q'\)/, '通讯录 uses its own whole-book search');
  const idx = s.slice(s.indexOf('function filterMain'), s.indexOf('/** Scroll a row to the middle'));
  assert.doesNotMatch(idx, /\.value\b/, 'the index reads labels only, never input values');
  const css = read('public/menu.css');
  assert.match(css, /#set-main\[data-find\] \[data-find-out\]\{display:none!important\}/);
  assert.match(css, /prefers-reduced-motion:reduce\)\{\.set-found\{animation:none\}/);
});

test('new strings exist in both languages', () => {
  for (const f of ['web.en.json', 'web.zh.json', 'web.zh.src.json', 'web.zh.override.json']) {
    const d = JSON.parse(read('i18n/' + f));
    for (const k of ['p393.entries', 'p393.findCard']) assert.ok(typeof d[k] === 'string' && d[k], `${f}: ${k}`);
  }
  const en = JSON.parse(read('i18n/web.en.json')), zh = JSON.parse(read('i18n/web.zh.json'));
  assert.ok(!/[一-鿿]/.test(en['p393.entries'] + en['p393.findCard']));
  assert.ok(/[一-鿿]/.test(zh['p393.entries'] + zh['p393.findCard']));
});

// the inventory is private QA evidence (reports/ is not in the public export): checked where it exists
test('every search field in the app is in the inventory', {skip: !existsSync(ROOT + 'reports/qa/p393/search-inventory.md')}, () => {
  const inv = readFileSync(ROOT + 'reports/qa/p393/search-inventory.md', 'utf8');
  const html = read('public/index.html');
  const ids = [...html.matchAll(/<input[^>]*type="search"[^>]*>/g)].map((m) => (m[0].match(/id="([^"]+)"/) || [])[1]).filter(Boolean);
  const js = readdirSync(WEB + 'public/js').filter((f) => f.endsWith('.js')).map((f) => [f, read('public/js/' + f)]);
  for (const [f, src] of js) if (/\.type = 'search'|PF\.field\(|pagefind\.openIn\(|PF\.openIn\(/.test(src)) ids.push(f);
  if (/pagefind\.openIn\(/.test(read('public/app.js'))) ids.push('app.js');
  assert.ok(ids.length >= 8, ids.join(','));
  for (const id of ids) assert.ok(inv.includes(id), `search-inventory.md lists ${id}`);
});
