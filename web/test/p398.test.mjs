// P398 · phone message state and buttons (backlog #154 #155 #156 #157) — the parts that are plain logic.
//   #154  replybtn.stateOf: a later message about something else leaves the group on; only an answer closes it
//         (the next message being one of its words, or a later one quoting this page with one of its words)
//   #155  relay.js: the pill never turns a message's 「取消」 into a stop — 已送达 grace, 停止这一轮 worded, say_sync
//   #156  attkeys.unique: the same bytes twice → once (in the tray already, or twice in one take)
//   #157  every menu page ends with --page-end (safe area + the corner arc + a section gap)
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window ??= globalThis;
const { stateOf, older } = await import('../public/js/replybtn.js');
const attkeys = await import('../public/js/attkeys.js');
const src = (f) => readFileSync(new URL('../public/' + f, import.meta.url), 'utf8');

const SAYS = ['派', '先不派'];
const ask = { id: 10, source: { k: 'phone', text: '补货' }, reply: '派不派？', end: 'done' };
const me = (id, text, quote = null) => ({ id, source: { k: 'phone', text, quote: quote === null ? null : { id: quote } }, reply: 'ok', end: 'done' });

test('#154: a different message after the question leaves the buttons on', () => {
  const pages = [ask, me(11, '等我看完再说'), me(12, '另外一件事')];
  assert.deepEqual(stateOf(ask, pages, SAYS), { on: true, said: null });
  assert.equal(older(ask, pages), true, 'a tap here goes as a reply to #10');
  assert.equal(older(pages[2], pages), false, 'the newest page: a plain say');
});
test('#154: the next message being one of the words answers it (typed or another remote)', () => {
  assert.deepEqual(stateOf(ask, [ask, me(11, '先不派')], SAYS), { on: false, said: '先不派' });
  assert.deepEqual(stateOf(ask, [ask, { id: 11, source: { k: 'telegram', text: '派' } }], SAYS), { on: false, said: '派' });
});
test('#154: a later reply quoting this page with one of the words answers it; quoting another page does not', () => {
  assert.deepEqual(stateOf(ask, [ask, me(11, '别的'), me(12, '派', 10)], SAYS), { on: false, said: '派' });
  const other = { id: 11, source: { k: 'phone', text: 'x' }, reply: '派不派这个？', end: 'done' };
  assert.deepEqual(stateOf(ask, [ask, other, me(12, '派', 11)], SAYS), { on: true, said: null }, 'that 派 answered #11');
  assert.deepEqual(stateOf(ask, [ask, me(11, '别的'), me(12, '派')], SAYS), { on: true, said: null },
    'an unquoted 派 two messages later is not taken as this answer');
});
test('#154: a page still being written is off; not an integer id → off', () => {
  assert.deepEqual(stateOf({ ...ask, end: 'open' }, [{ ...ask, end: 'open' }], SAYS), { on: false, said: null });
  assert.deepEqual(stateOf({ id: null }, [], SAYS), { on: false, said: null });
});

test('#182: Stop is universal; full aria labels remain, without a width fallback', () => {
  const r = src('js/relay.js');
  assert.ok(!r.includes('p403.stopShort'));
  const pad = r.match(/function padForCancel\(\)\{[\s\S]*?\n\}/)[0];
  assert.ok(!pad.includes('width < 44'));
  const en = JSON.parse(src('../i18n/web.en.json')), zh = JSON.parse(src('../i18n/web.zh.json'));
  for (const d of [en, zh]) assert.equal(d['p398.stopTurn'], 'Stop');
  assert.equal(zh['p328.stopAria'], '停止这一轮');
  assert.equal(en['p328.stopAria'], 'Stop this turn');
});

test('#155: the pill — 已送达 grace, the stop worded as a stop, a message tap never becomes a stop', () => {
  const r = src('js/relay.js');
  assert.match(r, /const DELIVERED_MS = \d{4};/);
  assert.match(r, /delivered: "p398\.delivered", turn: "p398\.stopTurn"/);
  assert.match(r, /if \(shown === "delivered" \|\| now === "delivered"\)\{ toast\(t\('r\.say\.tooLate'\), 2600\); return; \}/);
  assert.match(r, /if \(now === "turn" \|\| !now\) return;/, 'the thumb aimed at a message: never a stop in its place');
  assert.match(r, /activeTurn = q\.turn; sawDelivered\(\); dequeue\(m\.sid\);/);
  assert.match(r, /export function onReady\(\)\{ syncQueued\(\);/, 'a reconnect asks the host where the queued sends are');
  assert.match(r, /else if \(m\.s === "done"\) dequeue\(m\.sid\);/);
  const a = src('js/api.js');
  assert.match(a, /t: 'say_sync', sids: sids\.slice\(0, 32\)/);
  assert.match(a, /!peer\.ss/, 'only to a host announcing ss1');
  assert.match(src('js/session.js'), /peer\.ss = peer\.p33 && Array\.isArray\(m\.caps\) && m\.caps\.includes\('ss1'\)/);
});

test('#156: the same content twice is taken once', async () => {
  const f = (s, name = 'image') => new File([s], name, { type: 'image/png' });
  const a = f('AAAA'), b = f('AAAA', 'copy'), c = f('BBBB');
  assert.deepEqual(await attkeys.unique([b, c], [a]), [c], 'already in the tray');
  assert.deepEqual(await attkeys.unique([a, b, c], []), [a, c], 'twice in one take');
  assert.deepEqual(await attkeys.unique([c], []), [c]);
  assert.equal((await attkeys.keysOf(a))[0].startsWith('b:'), true);
  assert.equal(await attkeys.keysOf(a), await attkeys.keysOf(a), 'computed once per File');
});
test('#156: the switch lives in 网页版 App, stays on phones with a note, searchable as 截图', () => {
  const h = src('index.html');
  const app = h.slice(h.indexOf('id="set-s-app"'), h.indexOf('id="set-s-help"'));
  assert.ok(app.includes('id="set-shot-row"'), 'in 网页版 App');
  assert.ok(!h.slice(h.indexOf('id="set-s-read"'), h.indexOf('id="set-s-notify"')).includes('set-shot-row'), 'not in 阅读和声音');
  assert.match(app, /id="set-shot-row" data-keywords="截图[^"]*"/);
  assert.ok(!/id="set-shot-row"[^>]*hidden/.test(h), 'the row is never hidden');
  assert.match(app, /id="set-shot-na" hidden data-i18n="p398\.shotDesktopOnly"/);
  const s = src('js/settings.js');
  assert.match(s, /el\('set-shot'\)\.hidden = !ok;\n  el\('set-shot-na'\)\.hidden = ok;/);
  assert.match(src('app.js'), /attach: \(f\) => relay\.attachShot\(f, false, true\)/, 'only the automatic path marks its chip');
});

test('#157: menu pages end with --page-end', () => {
  const css = src('app.css'), menu = src('menu.css');
  assert.match(css, /--page-end:calc\(var\(--space-section\) \+ var\(--corner-arc\) \+ env\(safe-area-inset-bottom,0px\)\)/);
  assert.match(css, /@media \(pointer:coarse\)\{:root\{--corner-arc:28px\}\}/);
  assert.equal((css.match(/\.setmain,\.setsub\{[^}]*var\(--page-end\)\}/g) || []).length, 2, 'Settings, both widths');
  assert.match(css, /\.setmain\{grid-auto-rows:max-content\}/, 'the last row is as tall as the Brand Kit (it hung 33 px into the padding)');
  assert.match(css, /\.ajpages\{[^}]*padding:0 var\(--rg\) var\(--page-end\)/);
  assert.match(menu, /\.menubody\{[^}]*var\(--page-end\)\}/);
});
