// P150 (PROTOCOL §22): the busy list from the Agent J host is display text — bounded, plain, one valid colour each, neighbours
// never the same colour; a helper without a name is 「临时助理」; a run without a label says how it was started.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
globalThis.window = {};
const { sanitize, who, what, MAX_DOTS } = await import('../public/js/busy.js');

test('P150 busy: sanitize bounds, strips control characters, fixes colours and neighbours', () => {
  assert.deepEqual(sanitize(null), []);
  assert.deepEqual(sanitize({ tasks: 'x' }), []);
  const many = { tasks: Array.from({ length: 40 }, (_, i) => ({ id: 'k' + i, who: 'W' + i, kind: 'ceo', label: 'x', c: 3 })) };
  const s = sanitize(many);
  assert.equal(s.length, 32);   // P154 追加 11: 32 on the wire (was 12)
  for (let i = 1; i < s.length; i++) assert.notEqual(s[i].c, s[i - 1].c);
  const [a, b, c] = sanitize({ tasks: [{ who: '掌\u0007枢'.repeat(20), label: 'L'.repeat(99), c: 99, kind: 'evil' }, { who: '', label: '', lk: 'schedule', c: 2 }, 7] });
  assert.equal(a.who.length, 24); assert.ok(!/\u0007/.test(a.who)); assert.equal(a.label.length, 99);   // P154: whole titles (≤ LABEL_MAX)
  assert.equal(a.kind, 'temp'); assert.ok(a.c >= 0 && a.c < 9);
  assert.equal(b.who, null); assert.equal(b.lk, 'schedule'); assert.equal(c, undefined);
  assert.ok(who(b).length > 0 && who(a) === a.who, 'a nameless helper still has a name');
  assert.ok(what(b).length > 0, 'a run without a label says how it started');
  assert.equal(MAX_DOTS, 6);
});

test('P150 busy: host text only through textContent; the dialog and dots exist once in the page', () => {
  const src = readFileSync(new URL('../public/js/busy.js', import.meta.url), 'utf8');
  assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML/.test(src));
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  for (const id of ['busyDots', 'busyDlg', 'busyTitle', 'busySub', 'busyList', 'busyClose', 'version-notice', 'version-notice-title'])
    assert.equal(html.split(`id="${id}"`).length, 2, id);
  const i18n = readFileSync(new URL('../public/i18n.js', import.meta.url), 'utf8');
  for (const k of ['busy.temp', 'busy.title', 'busy.title1', 'busy.sub', 'busy.close', 'busy.aria', 'ver.title', 'ver.body', 'ver.refresh', 'r.agent.tip'])
    assert.equal(i18n.split(`"${k}":`).length, 3, k + ' in zh and en');
  const css = readFileSync(new URL('../public/app.css', import.meta.url), 'utf8');
  // only Leo's Heavy / Light pairs (p150-ticker-palette*.txt)
  const heavy = ['#71bbde', '#d1a7e8', '#f7827e', '#dbb293', '#f0aad4', '#fcbe7c', '#f7e17c', '#b7e396', '#78dedc'];
  const light = ['#c1e1f0', '#ebd8f5', '#fbc8c6', '#efddcf', '#f8daec', '#fee2c5', '#fbf2c5', '#dff3d1', '#c4f0f0'];
  heavy.forEach((h, i) => { assert.ok(css.includes(`--busy-h${i}:${h};--busy-l${i}:${light[i]}`), 'pair ' + i); });
});
