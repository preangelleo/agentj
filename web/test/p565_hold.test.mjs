// P565 追加 C (#348, 董事长 10-28 14:27 iPhone): 「长按批准已经没有动态效果了，按下去看不到那个状态栏从左到右」.
// 追加 C 补充 (10-28 15:1x): 「以前就很好，后来改了一个难看的颜色……从来没让你改这个特效」 → the EARLIEST effect, exactly:
// 7e45af4f1's fill (width 0 → 100 %, linear over the hold, var(--state) at .55 over the resting button) and no hover / active
// colour on 长按批准 (P448 718b90b57 recoloured the fill; P458 e96cc19ae kept a Noir :active pair meant for the sudo card).
// Root cause of the missing motion: every snapshot (any host message) re-renders the card; renderCard hides #apprBtns and
// measureCmd (P154 5f65f8a4c) reads layout before it is shown again — that flush cancelled the CSS width transition and the fill
// jumped to full. relay.js holdFill runs the same width animation as a Web Animation from the press (its own clock).
// The browser leg that measures it frame by frame (Chromium + WebKit, the host talking during the hold): p565_hold.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const relay = read('../public/js/relay.js');
const css = read('../public/app.css');
const controls = read('../public/controls.css');
const fn = (name) => { const i = relay.indexOf(`function ${name}(`); assert.ok(i >= 0, name + ' exists'); return relay.slice(i, relay.indexOf('\n}\n', i) + 2); };

// the lines exactly as 7e45af4f1 (2026-10-03, PROMPT-33) shipped them
const EARLIEST = [
  '.apprallow{background:var(--btn-strong-bg);color:var(--btn-strong-ink)}',
  '.apprallow .fill{position:absolute;inset:0;width:0;background:var(--state);opacity:.55;pointer-events:none}',
  '.apprallow.holding .fill{width:100%;transition:width var(--hold-ms,1200ms) linear}',
  '.apprbatch .fill{position:absolute;inset:0;width:0;background:var(--state);opacity:.55;pointer-events:none}',
  '.apprbatch.holding .fill{width:100%;transition:width var(--hold-ms,1200ms) linear}',
];

test('app.css: the fill lines are the 7e45af4f1 lines, byte for byte, and nothing else styles the fill', () => {
  const lines = css.split('\n');
  for (const l of EARLIEST) assert.ok(lines.includes(l), 'present as at first: ' + l);
  for (const f of [css, controls, read('../public/cards.css'), read('../public/menu.css')]) {
    const fills = f.split('\n').filter((l) => /\.(apprallow|apprbatch)[^{]*\.fill/.test(l));
    for (const l of fills) assert.ok(EARLIEST.includes(l), 'no later fill rule: ' + l);
  }
  assert.doesNotMatch(css, /scaleX/, 'no scaleX fill left over');
});

test('批准 keeps its resting colours under the finger; the Noir pair is the sudo card\'s only (P458 #223)', () => {
  assert.match(controls, /\n#elev-allow:is\(:hover,:active,:focus\)\{background:var\(--c-noir\);color:#faf9f5\}/);
  for (const f of [css, controls]) assert.doesNotMatch(f, /\.apprallow[^{,]*:(hover|active|focus)|:is\([^)]*\.apprallow[^)]*\):is\(:hover/);
});

// P573 (chairman 10-29, item 3: 「新增『点一下』按钮档，不长按」): the hold is gone — 批准 / 批量批准 / 加进目标授权 answer on
// one tap; a Face ID card still starts its passkey sheet from the press. The fill lines above stay (CSS untouched), unused.
const tap = fn('tapToApprove');
test('P573: one tap approves — press and release on the same button, no timer, no hold hint, no fill animation', () => {
  assert.doesNotMatch(relay, /APPR_HOLD_MS|function holdToApprove|function holdFill|r\.appr\.holdHint|classList\.add\("holding"\)/);
  assert.match(tap, /addEventListener\("pointerdown"/);
  assert.match(tap, /addEventListener\("pointerup", e => \{[^]*?apprPost\(q, action\)/, 'the release on the button sends the answer');
  assert.doesNotMatch(tap, /setTimeout/, 'no timer: a tap, not a hold');
  for (const b of ['apprAllow', 'apprBatch', 'apprGoalAdd']) assert.match(relay, new RegExp(`tapToApprove\\(el\\("${b}"\\)`), b + ' is a one-tap button');
});
test('P573: a finger that slides off, or a scroll, answers nothing; a Face ID card keeps its passkey path', () => {
  assert.match(tap, /getBoundingClientRect\(\);\s*if \(e\.clientX < r\.left \|\| e\.clientX > r\.right \|\| e\.clientY < r\.top \|\| e\.clientY > r\.bottom\) return;/);
  assert.match(tap, /\["pointercancel", "pointerleave"\]\.forEach/);
  assert.match(tap, /if \(p\.passkey_required && action === "allow"\) \{ apprPost\(p, action\); return; \}/, 'Face ID: the passkey sheet from the press, as before');
  assert.match(tap, /if \(!p \|\| p\.local_only/, 'a host-only card never approves from the phone');
});
test('P573: the copy says tap, not hold (zh + en)', async () => {
  const d = (await import('../public/i18n.js')).default;
  assert.equal(d.zh['r.appr.hold'], '批准'); assert.equal(d.en['r.appr.hold'], 'Approve');
  for (const k of ['r.appr.allowAria', 'r.appr.batchHold', 'p339.how', 'p339.howAdd', 'p339.addHold', 'wc.confirm', 'pair.card.allow', 'ppReset.allow', 'ix.appr.approve', 'ixges.appr.approve']) {
    assert.doesNotMatch(d.zh[k], /长按|按住/, 'zh ' + k + ': ' + d.zh[k]);
    assert.doesNotMatch(d.en[k], /\bhold/i, 'en ' + k + ': ' + d.en[k]);
  }
});
test('the trigger is still there: a re-render hides #apprBtns while measureCmd reads layout — a tap must survive it (p565_hold.mjs)', () => {
  assert.match(fn('renderCard'), /el\("apprBtns"\)\.hidden = true;/, 'renderCard resets the buttons hidden first');
  assert.match(fn('paintCmd'), /measureCmd\(\);/, 'paintCmd measures (scrollHeight) before renderPermission shows the buttons again');
});
