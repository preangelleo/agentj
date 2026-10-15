// P273: static guards — no stylesheet the phone app ships draws a scrollbar (Leo 10-14 19:10), the reader is laid out as
// header / words / footer on the dialogs' Noir, and its footer clones the reply card's six icons in the card's order.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FRAME_NOBAR } from '../public/js/mediawire.js';

const PUB = fileURLToPath(new URL('../public/', import.meta.url));
const css = (f) => readFileSync(join(PUB, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const SHEETS = [...readdirSync(PUB).filter((f) => f.endsWith('.css')), ...readdirSync(join(PUB, 'brand')).filter((f) => f.endsWith('.css')).map((f) => 'brand/' + f)];

test('no shipped stylesheet asks for a visible scrollbar', () => {
  assert.ok(SHEETS.includes('app.css') && SHEETS.length >= 4, SHEETS.join(','));
  for (const f of SHEETS) {
    const s = css(f);
    assert.doesNotMatch(s, /scrollbar-width\s*:\s*(thin|auto)/, f + ': scrollbar-width other than none');
    assert.doesNotMatch(s, /scrollbar-color\s*:/, f + ': scrollbar-color paints a bar');
    assert.doesNotMatch(s, /scrollbar-gutter\s*:\s*stable/, f + ': a reserved gutter');
    // every ::-webkit-scrollbar rule hides it (display:none or 0 size)
    for (const m of s.matchAll(/([^{}]*::-webkit-scrollbar[^{}]*)\{([^}]*)\}/g))
      assert.match(m[2], /display\s*:\s*none|width\s*:\s*0/, `${f}: ${m[1].trim()} draws a bar`);
  }
});

test('the global rule: every element, every engine', () => {
  const s = css('app.css');
  assert.match(s, /\*,\*::before,\*::after\{scrollbar-width:none\}/);
  assert.match(s, /::-webkit-scrollbar,::-webkit-scrollbar-button,::-webkit-scrollbar-track,::-webkit-scrollbar-thumb,::-webkit-scrollbar-corner\{display:none;width:0;height:0/);
});

test('preview frames (srcdoc) carry the same no-bar style', () => {
  assert.match(FRAME_NOBAR, /scrollbar-width:none/);
  assert.match(FRAME_NOBAR, /::-webkit-scrollbar\{display:none/);
  assert.match(readFileSync(join(PUB, 'js/media.js'), 'utf8'), /f\.srcdoc = html \+ FRAME_NOBAR/);
  assert.match(readFileSync(join(PUB, 'js/preview.js'), 'utf8'), /css \+ FRAME_NOBAR/);
});

test('reader: fixed header + scrolling words + fixed footer, on --dlg-bg', () => {
  const html = readFileSync(join(PUB, 'index.html'), 'utf8');
  const rd = html.slice(html.indexOf('<div class="rd" id="rd"'), html.indexOf('<div class="silent menupage"'));
  const order = ['id="rdHd"', 'id="rdWho"', 'id="rdUnread"', 'id="rdPgBtn"', 'id="rdClose"', 'id="rdScroll"', 'id="rdFt"', 'id="rdFsBtn"', 'id="rdActs"', 'id="rdFsPop"'];
  let at = -1;
  for (const id of order) { const i = rd.indexOf(id); assert.ok(i > at, id + ' in order'); at = i; }
  assert.ok(rd.indexOf('id="rdSlider"') > rd.indexOf('id="rdFsPop"'), 'the slider lives in the footer popover, not the header');
  // rework (Leo 23:1x): no × in the corner — #rdClose is the visually hidden exit for screen readers; the page number is the corner
  assert.match(rd, /<button class="rdsr" id="rdClose"[^>]*aria-label=/);
  assert.doesNotMatch(rd, /class="rdcorner"|M6 6l12 12M18 6 6 18/, 'no × drawn in the reader');
  const s = css('app.css');
  assert.match(s, /\.rd\{position:fixed;inset:0;z-index:29;display:flex;flex-direction:column;background:var\(--dlg-bg\)/);
  assert.match(s, /body\[data-reader="1"\] \.probe\{background:var\(--dlg-bg\)\}/);
  assert.match(s, /\.rdhd\{flex:none;[^}]*height:44px/);
  assert.match(s, /\.rd \.rdsr\{position:absolute;[^}]*opacity:0;pointer-events:none;/); assert.match(s, /\.rd \.rdsr:focus-visible\{opacity:1;pointer-events:auto\}/); assert.match(s, /\.rdft\{flex:none;/);
  assert.match(s, /\.rdsc\{flex:1;min-height:0;overflow-x:hidden;overflow-y:auto;/);
});

test('reader footer = the reply card icons, card order, 回复 last; copy / reply leave, the rest stay', () => {
  const js = readFileSync(join(PUB, 'js/relay.js'), 'utf8');
  const html = readFileSync(join(PUB, 'index.html'), 'utf8');
  const bar = html.slice(html.indexOf('id="rmbar"'), html.indexOf('id="rmMore"'));
  const card = [...bar.matchAll(/class="act" id="(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(card, ['readBtn', 'speakBtn', 'fwdBtn', 'mailBtn', 'copyReply', 'replyBtn']);
  const m = js.match(/const RD_ACTS = (\[[\s\S]*?\]);/);
  const acts = JSON.parse(m[1].replace(/\s+/g, ''));
  assert.deepEqual(acts.map((a) => a[0]), card, 'same order as the card');
  assert.equal(acts.at(-1)[1], 'rdReplyBtn');
  const body = (name) => js.slice(js.indexOf('function ' + name + '('), js.indexOf('\n}', js.indexOf('function ' + name + '(')));
  assert.match(body('rdCopy'), /copyOut[\s\S]*closeReader\(\)/, 'copy then leave');
  assert.match(body('rdReply'), /closeReader\(\);\s*startReply\(p\)/, 'leave then quote + focus');
  for (const [id, fn] of [['rdSpeakBtn', 'speakTap'], ['rdFwdBtn', 'forwardTap'], ['rdMailBtn', 'mailTap']])
    assert.match(js, new RegExp(`el\\("${id}"\\)\\.addEventListener\\("click", ${fn}\\)`), id + ' stays in the reader');
});
