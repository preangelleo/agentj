// P563 追加 2 (董事长 10-28: 「按钮里写出快捷键，用户能记住」): 紧急停止所有 Agent gets a key — Alt+Shift+S (S = Stop).
// Static half; the browser half is p563-stopkey.mjs (button hint, shortcut page row, the key opens the same confirm sheet).
// - the shortcut table has it, once, and no other entry has the same key in any layer
// - it is a global function key (works with a text field focused, like every Alt+Shift key) dispatched through relay.js ACT
// - ACT calls C.estop(true) = controls.onEstop, the button's own handler, whose first step is the confirm sheet
// - the Settings button carries the table key (setkeys.js), never a letter of its own
// P563 追加 3: the full-screen ✓ is Save every time (text-editor.js), see also p563-fsedit.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const ix = JSON.parse(read('../interactions.json'));
const G = await import('../public/js/interactions.gen.js');
const html = read('../public/index.html'), relay = read('../public/js/relay.js'), controls = read('../public/js/controls.js'), app = read('../public/app.js');

globalThis.window ??= globalThis;
globalThis.navigator ??= { platform: 'Linux x86_64', userAgent: 'node' };
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
const K = await import('../public/js/setkeys.js');

test('the shortcut table has app.stop_all = Alt+Shift+S, a global table key', () => {
  const e = ix.entries.find((x) => x.id === 'app.stop_all');
  assert.deepEqual(e.keys, [{ combo: 'Alt+Shift+S', impl: 'table' }]);
  assert.equal(e.scope, 'any');
  assert.ok(!e.nokey, 'no "settings-only" any more');
  assert.match(e.allowReserved, /ChromeOS/, 'the one known clash (Chromebook status tray) is written down');
  assert.deepEqual(G.ROWS.find((r) => r[0] === 'app.stop_all')[1], [['Alt+Shift+S', 'press']]);
  assert.deepEqual(G.KEYMAP['alt+shift:KeyS'], ['app.stop_all', 0], 'no repeat: a held key never stacks confirm sheets');
  assert.ok(G.GLOBAL.includes('app.stop_all'));
  assert.ok(G.TABLE.includes('app.stop_all'));
});

test('no other entry uses Alt+Shift+S (or any key twice)', () => {
  const owners = new Map();
  for (const e of ix.entries) for (const k of e.keys || []) {
    if (!/^Alt\+Shift\+[A-Z]$/.test(k.combo)) continue;
    assert.ok(!owners.has(k.combo), `${k.combo}: ${owners.get(k.combo)} and ${e.id}`);
    owners.set(k.combo, e.id);
  }
  assert.equal(owners.get('Alt+Shift+S'), 'app.stop_all');
  // the generated dispatch map has exactly one owner per physical key
  const ids = Object.entries(G.KEYMAP).filter(([k]) => k === 'alt+shift:KeyS');
  assert.equal(ids.length, 1);
});

test('a card key never takes S now (cardkeys reads the table)', async () => {
  const CK = await import('../public/js/cardkeys.js');
  assert.ok(CK.taken().has('S'));
  const m = CK.assign([{ key: 'skills', en: 'Skills', id: 'skills' }]);
  assert.notEqual(m.get('skills'), 'S');
});

test('the key runs the button\'s own handler, and that handler asks first', () => {
  const act = relay.slice(relay.indexOf('// @ix-actions-begin'), relay.indexOf('// @ix-actions-end'));
  assert.match(act, /"app\.stop_all": \(\) => keyNav\(\(\) => C\.estop && C\.estop\(true\)\),/);
  assert.match(app, /estop: \(stop\) => \(stop \? controls\.onEstop\(\) : controls\.onResume\(\)\),/, 'C.estop(true) = controls.onEstop');
  assert.match(controls, /el\('set-estop'\)\?\.addEventListener\('click', onEstop\);/, 'the button = controls.onEstop');
  const fn = controls.slice(controls.indexOf('export async function onEstop()'));
  assert.match(fn, /^export async function onEstop\(\) \{\n  if \(!await confirmSheet\(t\('estop\.confirmTitle'\), t\('estop\.confirmText'\), t\('estop\.confirmYes'\)\)\) return;/,
    'the confirm sheet comes before the signed estop');
  // the global listener: an open confirm sheet (or any sensitive layer) blocks function keys
  assert.match(relay, /const blocked = !el\('confirm'\)\.hidden \|\| /);
});

test('the Settings button shows the table key, per platform', () => {
  assert.match(html, /id="set-estop" class="aj-btn"><span data-i18n="p435\.estop">[^<]+<\/span><kbd class="setkbd" data-ix-key="app\.stop_all" hidden><\/kbd><\/button>/);
  assert.equal(K.comboOf('app.stop_all'), 'Alt+Shift+S');
  assert.deepEqual(K.keyTokens('app.stop_all', { keyboard: true, mac: false }), ['Alt', 'Shift', 'S']);
  assert.deepEqual(K.keyTokens('app.stop_all', { keyboard: true, mac: true }), ['⌥', 'Shift', 'S']);
  assert.deepEqual(K.keyTokens('app.stop_all', { keyboard: false }), [], 'a phone shows none');
});

test('P563 追加 3: the full-screen ✓ submits the field\'s form, whatever else it holds', () => {
  const ed = read('../public/js/text-editor.js'), fs = read('../public/js/fsedit.js');
  assert.match(ed, /export const saveForm = \(input\) => input\.form \|\| null;/);
  assert.doesNotMatch(ed, /soleForm/);
  // ✓ is labelled Save in every text editor (no caller overrides the bottom-right corner)
  assert.match(fs, /bottomRight:\{label:composer\.t\('p537\.save'\),icon:'✓',action:\(\{save\}\)=>save\(\)\}/);
  const dict = read('../public/i18n.js');   // the built dictionary the page reads (zh block, then en)
  assert.deepEqual([...dict.matchAll(/"p537\.save": "([^"]+)"/g)].map((m) => m[1]), ['保存', 'Save']);
});
