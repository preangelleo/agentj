// P340 E (#45): the composer buttons' order (js/toolorder.js) — pure logic: what counts as an order, the default, moves,
// 「恢复默认」, this device's copy, and how a `preferences` broadcast decides. The page is driven in test/p340_e.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const mem = new Map();
globalThis.localStorage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
const o = await import('../public/js/toolorder.js');
const DEF = ['file', 'image', 'camera', 'mic'];

test('isOrder: the four ids, each exactly once', () => {
  for (const v of [DEF, ['mic', 'file', 'image', 'camera'], ['camera', 'mic', 'image', 'file']]) assert.equal(o.isOrder(v), true, String(v));
  for (const v of [['mic', 'mic', 'image', 'camera'], ['file', 'image', 'camera'], [...DEF, 'mic'], ['file', 'image', 'camera', 'voice'],
    ['file', 'image', 'camera', 1], [], 'mic,file,image,camera', null, undefined, 5, { 0: 'mic' }, ['File', 'image', 'camera', 'mic']])
    assert.equal(o.isOrder(v), false, JSON.stringify(v));
});

test('default = today\'s order; anything invalid falls back to it', () => {
  assert.deepEqual([...o.TOOL_IDS], DEF);
  assert.deepEqual(o.normalize(undefined), DEF);
  assert.deepEqual(o.normalize(['mic', 'mic', 'image', 'camera']), DEF);
  assert.deepEqual(o.normalize(['mic', 'file', 'image', 'camera']), ['mic', 'file', 'image', 'camera']);
  assert.equal(o.isDefault(DEF), true);
  assert.equal(o.isDefault(['mic', 'file', 'image', 'camera']), false);
  assert.equal(o.isDefault('junk'), true, 'junk reads as the default');
  const a = o.normalize(DEF); a.push('x');
  assert.deepEqual(o.normalize(DEF), DEF, 'a fresh copy every time');
});

test('moved: take one out, put it back at a place (clamped)', () => {
  assert.deepEqual(o.moved(DEF, 3, 0), ['mic', 'file', 'image', 'camera'], 'mic to the far left (Leo\'s left hand)');
  assert.deepEqual(o.moved(DEF, 0, 3), ['image', 'camera', 'mic', 'file']);
  assert.deepEqual(o.moved(DEF, 1, 2), ['file', 'camera', 'image', 'mic']);
  assert.deepEqual(o.moved(DEF, 2, 2), DEF);
  assert.deepEqual(o.moved(DEF, 3, -5), ['mic', 'file', 'image', 'camera']);
  assert.deepEqual(o.moved(DEF, 0, 99), ['image', 'camera', 'mic', 'file']);
  for (let f = 0; f < 4; f++) for (let t = 0; t < 4; t++) assert.equal(o.isOrder(o.moved(DEF, f, t)), true);
});

test('set → this device keeps it; restore default writes the default back; junk in storage reads as default', () => {
  mem.clear();
  assert.deepEqual(o._reload(), DEF, 'nothing stored = default');
  const seen = [];
  o.onChange((x) => seen.push(x));
  o.set(['mic', 'file', 'image', 'camera']);
  assert.equal(mem.get('aj.toolOrder'), JSON.stringify(['mic', 'file', 'image', 'camera']));
  assert.deepEqual(o._reload(), ['mic', 'file', 'image', 'camera'], 'survives a reload');
  o.set(DEF);
  assert.deepEqual(o.order(), DEF);
  assert.equal(mem.get('aj.toolOrder'), JSON.stringify(DEF));
  assert.equal(seen.length, 2);
  o.set(DEF);
  assert.equal(seen.length, 2, 'no change, no event');
  mem.set('aj.toolOrder', '["mic","mic"');
  assert.deepEqual(o._reload(), DEF, 'unreadable storage = default');
  mem.set('aj.toolOrder', '["mic","mic","image","camera"]');
  assert.deepEqual(o._reload(), DEF, 'an invalid stored order = default');
});

test('fromHost: the host decides when it has the key; an older host leaves this device\'s order alone', () => {
  mem.clear(); o._reload();
  o.set(['camera', 'mic', 'image', 'file']);
  assert.equal(o.fromHost({ appearance: { language: 'zh' } }), false, 'no ui block = an older host');
  assert.equal(o.fromHost({ ui: { card_hot_action: 'status' } }), false, 'ui without the key = an older host');
  assert.equal(o.fromHost(null), false);
  assert.deepEqual(o.order(), ['camera', 'mic', 'image', 'file'], 'kept');
  assert.equal(o.fromHost({ ui: { composer_buttons: ['mic', 'file', 'image', 'camera'] } }), true);
  assert.deepEqual(o.order(), ['mic', 'file', 'image', 'camera'], 'another remote\'s change arrives');
  assert.equal(mem.get('aj.toolOrder'), JSON.stringify(['mic', 'file', 'image', 'camera']), 'and is kept for the next start');
  o.fromHost({ ui: { composer_buttons: ['mic', 'mic', 'image', 'camera'] } });
  assert.deepEqual(o.order(), DEF, 'an invalid host value = the default');
});

test('hold: while this device saves, a broadcast from an earlier save waits; the last one wins on release', () => {
  mem.clear(); o._reload();
  o.set(['camera', 'image', 'file', 'mic']);
  o.hold(true);
  o.fromHost({ ui: { composer_buttons: ['mic', 'file', 'image', 'camera'] } });
  assert.deepEqual(o.order(), ['camera', 'image', 'file', 'mic'], 'not flicked back');
  o.fromHost({ ui: { composer_buttons: ['camera', 'image', 'file', 'mic'] } });
  o.hold(false);
  assert.deepEqual(o.order(), ['camera', 'image', 'file', 'mic']);
});

test('the DOM ids are the composer buttons in index.html, in the default order', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  const tools = html.slice(html.indexOf('<div class="tools" id="tools">'));
  const at = DEF.map((id) => tools.indexOf(`id="${o.TOOL_DOM[id]}"`));
  assert.ok(at.every((x) => x > 0) && at.every((x, i) => !i || x > at[i - 1]), 'tDoc · tPhoto · tCamera · mic, in this order: ' + at);
  assert.match(html, /id="set-tools"/);
  assert.match(html, /id="set-tools-reset"/);
});

test('texts exist in both languages with the same placeholders', () => {
  const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
  const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));
  const keys = ['label', 'reset', 'note', 'local', 'file', 'image', 'camera', 'mic', 'chip', 'moved', 'picked', 'dropped', 'resetDone'].map((k) => 'p340e.' + k);
  const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
  for (const k of keys) {
    assert.ok(zh[k] && en[k], k);
    assert.equal(ph(zh[k]), ph(en[k]), k);
    assert.doesNotMatch(en[k], /[一-鿿]/, 'en has no Chinese: ' + k);
  }
});
