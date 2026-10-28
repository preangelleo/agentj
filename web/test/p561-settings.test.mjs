// P561 追加 16 / 18 (董事长 10-28), static half; the browser half is test/p561-settings.mjs.
// - The shortcut a Settings button shows comes from the shortcut table (interactions.json → interactions.gen.js ROWS), never a
//   letter in the page; a phone shows none; an entry without a key shows none.
// - The upgrade note names /update, and typed /update is the very path of the 「检查并升级」 button (api.slash('update')).
// - 「本机文件访问」 has the list's line icon and chevron; the rescue cards keep words off their buttons with spacing tokens.
// - The gear's remote-count digit wears --state in every state (no Noir override, no color-mix); P564 (董事长 10-28): the circle
//   is P517's Lotion in every state — P563's Sooty circle withdrawn.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const html = read('../public/index.html'), menuCss = read('../public/menu.css'), appCss = read('../public/app.css');
const ix = JSON.parse(read('../interactions.json'));
const entry = (id) => ix.entries.find((e) => e.id === id);
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));

globalThis.window ??= globalThis;
globalThis.navigator ??= { platform: 'Linux x86_64', userAgent: 'node' };
let coarse = false;
globalThis.matchMedia = () => ({ matches: coarse, addEventListener() {} });
const K = await import('../public/js/setkeys.js');
const G = await import('../public/js/interactions.gen.js');

test('the keys come from the shortcut table, drawn per platform', () => {
  const upg = G.ROWS.find((r) => r[0] === 'app.upgrade_host');
  const combo = upg[1].find(([c]) => c.startsWith('Alt+Shift+'))[0];
  assert.equal(K.comboOf('app.upgrade_host'), combo);
  assert.deepEqual(K.keyTokens('app.upgrade_host', { keyboard: true, mac: false }), G.kbdTokens(combo, false));
  assert.deepEqual(K.keyTokens('app.upgrade_host', { keyboard: true, mac: true }), G.kbdTokens(combo, true));
  assert.equal(K.keyTokens('app.upgrade_host', { keyboard: true, mac: true })[0], '⌥');
  // the table changes → the hint follows (no letter is kept anywhere else)
  const rows = [['app.upgrade_host', [['Alt+Shift+Q', 'press']], 1], ['app.stop_all', [['Alt+Shift+S', 'press']], 1]];
  assert.deepEqual(K.keyTokens('app.upgrade_host', { rows, keyboard: true, mac: false }), ['Alt', 'Shift', 'Q']);
  assert.deepEqual(K.keyTokens('app.stop_all', { rows, keyboard: true, mac: false }), ['Alt', 'Shift', 'S']);
  const src = read('../public/js/setkeys.js');
  assert.doesNotMatch(src.replace(/^\s*\/\/.*$/gm, ''), /Alt\+Shift\+[A-Z]/, 'no combo written in setkeys.js');
  assert.doesNotMatch(html, /data-ix-key="[^"]+"[^>]*>[^<]*Alt\+Shift/, 'no combo written in the page');
});

test('a phone shows no key; an entry without a key shows nothing', () => {
  assert.deepEqual(K.keyTokens('app.upgrade_host', { keyboard: false }), []);
  coarse = true;
  assert.equal(K.hasKeyboard(), false);
  assert.deepEqual(K.keyTokens('app.upgrade_host'), []);
  coarse = false;
  // P563 追加 2: app.stop_all has its key now (Alt+Shift+S) — the button shows the table's key, not a letter of its own
  assert.deepEqual(entry('app.stop_all').keys.map((k) => k.combo), ['Alt+Shift+S']);
  assert.equal(K.comboOf('app.stop_all'), 'Alt+Shift+S');
  assert.equal(K.comboOf('nope.none', [['nope.none', [], 1]]), '', 'an entry without a key shows nothing');
  assert.equal(K.comboOf('app.upgrade_host'), entry('app.upgrade_host').keys[0].combo, 'gen.js = interactions.json');
  assert.match(html, /id="set-estop" class="aj-btn"><span data-i18n="p435\.estop">[^<]+<\/span><kbd class="setkbd" data-ix-key="app\.stop_all" hidden><\/kbd><\/button>/);
  assert.match(html, /<span id="set-hostupg-how" data-ix-key="app\.upgrade_host"><\/span>/);
});

test('/update is named, and it is the button\'s own path', () => {
  for (const d of [zh, en]) {
    assert.match(d['p561.upgHowKey'], /\{key\}.*\/update|\/update.*\{key\}/);
    assert.match(d['p561.upgHow'], /\/update/);
    assert.doesNotMatch(d['p561.upgHow'], /\{key\}/);
  }
  for (const f of ['web.zh.src.json', 'web.zh.override.json']) {
    const d = JSON.parse(read('../i18n/' + f));
    assert.equal(d['p561.upgHowKey'], zh['p561.upgHowKey']); assert.equal(d['p561.upgHow'], zh['p561.upgHow']);
  }
  const app = read('../public/app.js'), api = read('../public/js/api.js'), relay = read('../public/js/relay.js');
  assert.match(app, /upgradeHost: async \(\) => \{[\s\S]{0,200}api\.slash\('update'\)/, 'the button = api.slash(update)');
  assert.match(api, /if \(cmd === 'update'\) return upgradeFlow\(\)/);
  assert.match(relay, /const CMDS = \[[^\]]*"update"/, 'typed /update is a built-in command');
  assert.match(relay, /return C\.api\.slash\(cmd, arg\);/, 'typed built-ins go through api.slash');
});

test('本机文件访问 has the list icon and chevron', () => {
  const row = /<button type="button" id="set-local-files-nav" class="setnav"[^>]*>(<svg class="mi"[^>]*>[\s\S]*?<\/svg>)<span>[\s\S]*?<\/span>(<svg class="chev"[\s\S]*?<\/svg>)<\/button>/.exec(html);
  assert.ok(row, 'icon + chevron');
  const models = /id="open-models" class="setnav">(<svg class="mi"[^>]*>)/.exec(html)[1];
  assert.equal(row[1].slice(0, models.length), models, 'same 24-grid stroke icon as its neighbours');
});

test('rescue cards: spacing tokens between words and buttons', () => {
  assert.match(menuCss, /#settings \.rscard>\*\+\.rsacts\{margin-top:var\(--ui-space-content\)\}/);
  assert.match(menuCss, /#settings #layout-diag-card>\.aj-btn\+\.setnote\{margin-top:var\(--ui-space-control\)\}/);
});

// the design system lives in agentjarvis/brand/; the public export ships web/ alone, with the same bytes under public/brand/
const BRAND_CSS = existsSync(new URL('../../brand/palette.css', import.meta.url)) ? '../../brand/palette.css' : '../public/brand/palette.css';
test('the gear digit follows --state in every state, on the Lotion circle', () => {
  const css = appCss.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.match(css, /#setBtn>\.remote-count-badge\{color:var\(--state\)\}/);
  assert.match(css, /\.remote-count-badge\{[^}]*background:var\(--c-lotion\)/, 'P517 Lotion circle');
  // P564 (董事长 10-28): one Lotion circle in every state — no rule repaints the badge's background, no mixed / darkened digit
  const rules = [...css.matchAll(/([^{}]*remote-count-badge[^{}]*)\{([^}]*)\}/g)];
  for (const [, sel, body] of rules) {
    if (/^\s*\.remote-count-badge\s*$/.test(sel)) continue;
    assert.doesNotMatch(body, /background/, 'no background override: ' + sel.trim());
  }
  assert.doesNotMatch(css, /c-sooty\);color:var\(--state\)/, 'no Sooty circle (P563 withdrawn)');
  assert.doesNotMatch(css.slice(css.indexOf('.remote-count-badge{')), /remote-count-badge[^}]*color-mix/, 'no color-mix on the badge');
  assert.doesNotMatch(css, /#setBtn>\.remote-count-badge\{color:var\(--c-noir\)\}/, 'no flat Noir digit');
  assert.match(read(BRAND_CSS), /--c-lotion: #faf9f5;/);
});

