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

test('长按批准 keeps its resting colours under the finger; the Noir pair is the sudo card\'s only (P458 #223)', () => {
  assert.match(controls, /\n#elev-allow:is\(:hover,:active,:focus\)\{background:var\(--c-noir\);color:#faf9f5\}/);
  for (const f of [css, controls]) assert.doesNotMatch(f, /\.apprallow[^{,]*:(hover|active|focus)|:is\([^)]*\.apprallow[^)]*\):is\(:hover/);
});

test('the fill is a width Web Animation from the press: 0 % → 100 %, linear, the hold time', () => {
  const f = fn('holdFill');
  assert.match(f, /f\.animate\(\[\{width: "0%"\}, \{width: "100%"\}\], \{duration: APPR_HOLD_MS, easing: "linear", fill: "forwards"\}\)/);
  assert.match(f, /typeof f\.animate !== "function"\) return;/, 'no Web Animations → the .holding CSS transition still draws it');
  assert.doesNotMatch(relay, /holdScale|HOLD_BACK_MS|scaleX/, 'no roll-back animation: an early release snaps back like the earliest');
});

test('press starts it; an early release cancels it (straight back to 0); a finished hold clears immediately', () => {
  const h = fn('holdToApprove');
  assert.match(h, /btn\.classList\.add\("holding"\);\s*holdFill\(btn, "start"\);/, 'start at pointerdown');
  assert.match(h, /apprCancelHold\(true\);\s*if \(q && q\.id === id/, 'the timer ends the hold as done');
  assert.match(fn('apprCancelHold'), /holdFill\(apprHold\.btn, done \? "done" : "back"\)/);
  const f = fn('holdFill');
  assert.match(f, /if \(prev\) prev\.cancel\(\);[^\n]*\n\s*f\._hold = mode === "start" \? f\.animate\([^\n]*\) : null;\n\}/, 'back = cancel, nothing animated');
  assert.match(f, /if \(mode === "done"\)\{[^]*?if \(prev\) prev\.cancel\(\);\s*f\._hold = null;\s*return;/, 'done: clears immediately');
  assert.doesNotMatch(f, /setTimeout|HOLD_DONE_MS/);
});

test('the trigger is still there: a re-render hides #apprBtns while measureCmd reads layout — the fill must not depend on CSS continuity', () => {
  assert.match(fn('renderCard'), /el\("apprBtns"\)\.hidden = true;/, 'renderCard resets the buttons hidden first');
  assert.match(fn('paintCmd'), /measureCmd\(\);/, 'paintCmd measures (scrollHeight) before renderPermission shows the buttons again');
  assert.ok(relay.indexOf('holdFill(btn, "start")') > 0, 'so the fill runs on its own clock');
});
