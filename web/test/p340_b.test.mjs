// P340 B (Leo 10-16 #21 #35 #38 #41): unit checks on the shipped sources — the reply bar's fit, the header gap curve,
// the quote fold words, and the send-keeps-the-page rule. The real page is checked by p340_b.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { topGap, topLayout, NARROW, WIDE, MIN, TAP } from '../public/js/topfit.js';

const relay = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../public/app.css', import.meta.url), 'utf8');
// a function's source: a one-liner (`function f(){ … }` on one line) or a block ending in a line `}`
const fn = (name) => (relay.match(new RegExp(`(?:export )?function ${name}\\([^\\n]*\\}[ \\t]*(?=\\n)`)) || relay.match(new RegExp(`(?:export )?function ${name}\\([\\s\\S]*?\\n\\}`)))[0].replace(/^export /, '');

test('#38 the reply bar: one --rm-gap is the glyph gap and the right margin', () => {
  assert.match(css, /\.rmbar\{[^}]*--rm-gap:var\(--space-content,16px\)/);
  assert.match(css, /\.rmbar\{[^}]*padding:0 calc\(var\(--rm-gap\) \/ 2\) 0 var\(--rmbar-inset\)/, 'right padding = half a gap');
  assert.match(css, /\.rmbar \.act\{[^}]*width:calc\(var\(--rm-icon,21px\) \+ var\(--rm-gap\)\)[^}]*flex:none/, 'button = glyph + one gap');
  assert.match(css, /\.rmbar \.act::after\{[^}]*width:max\(100%,44px\);height:44px/, '44 px target kept');
  assert.match(css, /\.rmicons\{[^}]*flex:none[^}]*margin-left:auto/, 'surplus goes to the left of the group');
  assert.doesNotMatch(css, /--rm-count,6\) \* 46px/, 'the P251 46 px pitch cap is gone');
});

test('#38 barFit: full size when it fits, then gap → 12, glyph → 18, gap → 6', () => {
  const ctx = {}; vm.runInNewContext(fn('barFit') + ';this.barFit=barFit;', ctx);
  const f = ctx.barFit;
  assert.deepEqual({...f(300, 6, 16)}, {icon: 21, gap: 16});
  assert.deepEqual({...f(6 * 37 + 16, 6, 16)}, {icon: 21, gap: 16}, 'exactly the need');
  const a = f(230, 6, 16); assert.equal(a.icon, 21); assert.ok(a.gap < 16 && a.gap >= 12); assert.ok(Math.abs(6 * (21 + a.gap) + a.gap - 230) < 1e-9);
  const b = f(200, 6, 16); assert.equal(b.gap, 12); assert.ok(b.icon >= 18 && b.icon < 21); assert.ok(Math.abs(6 * (b.icon + 12) + 12 - 200) < 1e-9);
  const c = f(150, 6, 16); assert.equal(c.icon, 18); assert.ok(c.gap >= 6 && c.gap < 12);
  assert.equal(f(60, 6, 16).gap, 6, 'never below 6 px');
  assert.deepEqual({...f(0, 6, 16)}, {icon: 21, gap: 16}, 'not laid out yet: untouched');
});

test('#41 topGap: equal to the gear margin up to 390 px, unchanged from 600 px, linear between, never wider', () => {
  assert.equal(NARROW, 390); assert.equal(WIDE, 600);
  for (const w of [320, 350, 360, 390]) assert.equal(topGap(w, 33.3, 27), 27);
  for (const w of [600, 690, 1440]) assert.ok(Math.abs(topGap(w, 33.3, 27) - 33.3) < 1e-9);
  const mid = topGap(495, 33.3, 27); assert.ok(Math.abs(mid - (27 + 6.3 / 2)) < 1e-9);
  let prev = 0; for (let w = 380; w <= 610; w += 1){ const g = topGap(w, 33.3, 27); assert.ok(g >= prev - 1e-9); assert.ok(g - prev < 0.05 || prev === 0, 'no jump'); prev = g; }
  assert.equal(topGap(350, 25, 27), 25, 'a gap tighter than the margin is left alone');
});

test('#35 qFoldWords: two to five quotes fold, one quote does not', () => {
  const ctx = {t: (k, v) => k + ':' + JSON.stringify(v || {})};
  vm.runInNewContext(fn('qFoldWords') + ';this.f=qFoldWords;', ctx);
  assert.equal(ctx.f(null), null);
  assert.equal(ctx.f({text: 'a'}, undefined), null);
  assert.equal(ctx.f({text: 'a'}, []), null);
  const r = ctx.f({text: '  第一段\n摘要  '}, [{}, {}, {}, {}]);
  assert.equal(r.n, 5); assert.equal(r.more, '+4'); assert.equal(r.text, '第一段 摘要'); assert.equal(r.head, 'p340b.qfold.head:{"n":5}');
  assert.match(relay, /paintQFold\(p\);/, 'painted with the page');
  assert.match(css, /\.om\[data-qfold="1"\] #omQuote,\.om\[data-qfold="1"\] #omQuoteMore\{display:none\}/);
  const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
  const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));
  for (const k of ['p340b.qfold.head', 'p340b.qfold.open', 'p340b.qfold.close', 'p340b.sentStay']){ assert.ok(zh[k], k); assert.ok(en[k], k); }
  assert.ok(zh['p340b.qfold.head'].includes('{n}') && en['p340b.qfold.head'].includes('{n}'));
});

test('#21 a send from an older page does not move it; from the newest page it follows', () => {
  const src = fn('onNewestPage') + '\nlet sentStay = false;\n' + fn('sendFollow') + ';this.go=sendFollow;this.stay=()=>sentStay;';
  const run = (state) => { const ctx = {...state, moved: 0, pages: () => new Array(state.n), toNewest(){ ctx.moved++; }}; vm.runInNewContext(src, ctx); ctx.go(); return ctx; };
  let c = run({follow: false, tailWait: false, pageAt: 3, n: 10}); assert.equal(c.moved, 0); assert.equal(c.stay(), true);
  c = run({follow: true, tailWait: false, pageAt: 9, n: 10}); assert.equal(c.moved, 1); assert.equal(c.stay(), false);
  c = run({follow: false, tailWait: true, pageAt: 8, n: 10}); assert.equal(c.moved, 1, 'the page still waiting for its reply counts as newest');
  c = run({follow: false, tailWait: false, pageAt: 9, n: 10}); assert.equal(c.moved, 1);
  // every send path uses it; the own-page arrival follows only from the newest page
  const say = relay.slice(relay.indexOf('async function say('), relay.indexOf('function drainOut('));
  assert.ok(!/toNewest\(\)/.test(say), 'say() never jumps on its own');
  assert.match(relay, /p\.end === "open" && !previous && onNewestPage\(\)\)\{ follow = true; shownKey = null; \}/);
  const later = relay.slice(relay.indexOf('function sayLater('), relay.indexOf('function sayLater(') + 2000);
  assert.match(later, /saveDraft\(\); sendFollow\(\);/);
});

test('#41 返工 topLayout: boxes ≥ MIN apart, 扫码 keeps 44 px, neighbouring centres ≥ 44 px, glyphs stay inside', () => {
  assert.equal(MIN, 8); assert.equal(TAP, 44);
  // the 350 px header measured at full width: insets of scan / search / gear, drawn widths
  const r = [14, 13, 11], l = [14, 13.5, 11], ink = [16, 17.5, 22];
  const gap = (o, i) => MIN + (r[i] - (44 - o.w[i]) / 2 - o.dx[i]) + (l[i + 1] - (44 - o.w[i + 1]) / 2 + o.dx[i + 1]);
  for (const want of [[27, 27], [20, 20], [33.3, 30.8], [30, 30]]){
    const o = topLayout(want, r, l, ink);
    assert.equal(o.w[0], 44, '扫码 box stays 44'); assert.equal(o.w[2], 44); assert.equal(o.dx[2], 0, 'the gear never moves');
    for (let i = 0; i < 2; i++){
      assert.ok(o.w[i] <= 44 && o.w[i] >= ink[i] + 1);
      assert.ok(o.w[i] / 2 + MIN + o.w[i + 1] / 2 >= 44 - 1e-9, 'centres ≥ 44 apart: ' + JSON.stringify(o));
      assert.ok(r[i] - (44 - o.w[i]) / 2 - o.dx[i] >= 0.5 - 1e-9 && l[i] - (44 - o.w[i]) / 2 + o.dx[i] >= 0.5 - 1e-9, 'glyph inside its box');
    }
    if (want[0] >= 27) for (let i = 0; i < 2; i++) assert.ok(Math.abs(gap(o, i) - want[i]) < 1e-9, `reachable gap ${i}: ${gap(o, i)} vs ${want[i]}`);
  }
  // the old spacing (want = natural at boxes MIN apart) changes nothing
  const o = topLayout([MIN + 14 + 13.5, MIN + 13 + 11], r, l, ink);
  assert.deepEqual(o.w, [44, 44, 44]); assert.ok(o.dx.every((x) => Math.abs(x) < 1e-9));
});
