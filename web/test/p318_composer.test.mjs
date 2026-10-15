// P318 (Leo, 2026-10-15): (a) the four composer tool buttons are icon-only at every width — no CSS rule shows a label in a
// `.tools .tool` at any width (the old `@media (max-width:519px)` hid it only on phones, so a Pad showed 「单击锁定 · 按住说…」
// cut, the microphone half clipped); the words stay in aria-label / title. (b) the placeholder is one short hint
// (「说点什么…」 / "Message…"), the cap shows on the field only from 90 % on. (c) the input card's header label wraps to a
// second line before it is cut and runs up to the unread badge. Browser check (390 / 520 / 820 / 1280 / 1440): p318.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (f) => readFileSync(new URL(f, import.meta.url), 'utf8');
const css = read('../public/app.css');
const html = read('../public/index.html');
const relay = read('../public/js/relay.js');
const D = Object.fromEntries(['web.zh.src', 'web.zh.override', 'web.zh', 'web.en'].map((n) => [n, JSON.parse(read(`../i18n/${n}.json`))]));

// every rule (with its @media wrapper, if any) whose selector targets a .tools .tool descendant span / #micLabel
function toolLabelRules() {
  const out = [];
  const flat = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(flat))) {
    const sel = m[1].trim();
    if (/\.tools\s+\.tool[^,{]*\s(span|\*)|#micLabel/.test(sel)) {
      const before = flat.slice(0, m.index), open = before.lastIndexOf('@media'), close = before.lastIndexOf('}');
      out.push({ sel, body: m[2], media: open > close ? before.slice(open, before.indexOf('{', open)).trim() : '' });
    }
  }
  return out;
}

test('P318 (a): the tool buttons never show a text label — hidden at every width, no width-gated rule', () => {
  const rules = toolLabelRules();
  const hide = rules.filter((r) => /display\s*:\s*none/.test(r.body));
  assert.ok(hide.some((r) => !r.media && /^\.tools \.tool span$/.test(r.sel)), 'one unconditional `.tools .tool span{display:none}`');
  for (const r of rules) {
    if (/\*/.test(r.sel) && /pointer-events/.test(r.body) && !/display/.test(r.body)) continue;   // .mic *{pointer-events:none}
    assert.ok(/display\s*:\s*none/.test(r.body) && !r.media, `no rule shows or width-gates a tool label: ${r.media} ${r.sel}{${r.body}}`);
  }
  assert.doesNotMatch(css, /max-width:\s*519px\)\s*\{\s*\.tools/, 'the old phone-only rule is gone');
  // the four buttons carry their words for assistive tech
  for (const id of ['tDoc', 'tPhoto', 'tCamera', 'mic']) {
    const tag = html.match(new RegExp(`<button[^>]*id="${id}"[^>]*>`))?.[0] || '';
    assert.match(tag, /data-i18n-attr="aria-label=[\w.]+,title=[\w.]+"/, `${id}: aria-label + title from the dictionary`);
  }
  // icon centred in its box
  assert.match(css, /\.tools \.tool\{[^}]*justify-content:center/);
});

test('P318 (b): the placeholder is short in both languages and does not state the cap', () => {
  for (const n of Object.keys(D)) assert.equal(typeof D[n]['p318.ph'], 'string', `p318.ph in ${n}`);
  const en = D['web.en']['p318.ph'], zh = D['web.zh']['p318.ph'];
  assert.ok(en.length <= 16, `en placeholder ≤ 16 chars: ${en}`);
  assert.ok([...zh].length <= 8, `zh placeholder short: ${zh}`);
  for (const s of [en, zh]) assert.doesNotMatch(s, /\d|万|limit|≤|上限/, 'no cap in the placeholder');
  const fn = relay.match(/function restingPlaceholder\(\)\{[\s\S]*?\n\}/)[0];
  assert.match(fn, /t\('p318\.ph'\)/);
  assert.doesNotMatch(fn, /r\.ph\.(long|short)|twinHeight/, 'no width-dependent choice of hint');
});

test('P318 (b): the cap note — from 90 % of the cap, n / max, both languages', () => {
  assert.match(html, /<span class="capnote" id="capNote" hidden><\/span>/);
  assert.match(relay, /const CAP_NEAR = 0\.9;/);
  assert.match(relay, /function paintCapNote\(\)\{[\s\S]*?len >= Math\.ceil\(max \* CAP_NEAR\)[\s\S]*?t\('p318\.cap'/);
  assert.match(relay, /function refreshSend\(\)\{[\s\S]*?paintCapNote\(\);\n\}/, 'refreshed on every input');
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
  for (const n of Object.keys(D)) assert.equal(ph(D[n]['p318.cap']), 'max,n', n);
  assert.match(css, /\.capnote\[hidden\]\{display:none\}/);
});

test('P318 (c): the header label wraps to two lines and runs up to the badge at the end of the middle third', () => {
  assert.match(css, /\.om-first \.omfl>b\{white-space:normal;[^}]*-webkit-line-clamp:2/);
  assert.match(css, /\.om-first \.omfl\{[^}]*width:max-content;max-width:calc\(200% - 8px\)/);
  assert.match(css, /\.om\[data-unread="1"\] \.om-first \.omfl\{max-width:calc\(200% - 6px - 2\.8em\)\}/);
  assert.match(css, /\.om-unread\{grid-column:2;[^}]*place-items:center end/);
  assert.doesNotMatch(css, /calc\(150% - 20px\)/, 'the label no longer stops half-way into the middle third');
});
