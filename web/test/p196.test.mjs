// P196 · the full-screen preview is the top layer and owns ← / → and the sideways swipe: the static rules behind it
// (the browser test p196.mjs drives the real page).
// Run: node --test web/test/p196.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const read = (r) => readFileSync(new URL(`../public/${r}`, import.meta.url), 'utf8');
const media = read('js/media.js');

test('preview layer: the highest layer rank, keys taken in the capture phase before the chat screen, Esc / Tab left to the layers', () => {
  const ranks = readdirSync(new URL('../public/js/', import.meta.url)).filter((f) => f.endsWith('.js'))
    .flatMap((f) => [...read('js/' + f).matchAll(/registerLayer\(\{[^\n]*?rank:(\d+)/g)].map((m) => [f, Number(m[1])]));
  const mine = ranks.find(([f, r]) => f === 'media.js');
  assert.ok(mine, 'media.js registers its layer');
  for (const [f, r] of ranks) if (f !== 'media.js') assert.ok(r < mine[1], `${f} rank ${r} < preview ${mine[1]}`);
  assert.match(media, /document\.addEventListener\('keydown', viewerKey, true\)/);
  assert.match(media, /e\.key === 'Escape' \|\| e\.key === 'Tab'/, 'Esc / Tab go on to layers.js');
  assert.match(media, /e\.stopImmediatePropagation\(\);\n\}/, 'every other key stops at the preview');
});

test('stepping: the same reply only (the scope\'s cards), unpreviewable files skipped, no wrap, players paused, the doc swipe never the browser\'s', () => {
  assert.match(media, /shown\.cards = \(\) => \[\.\.\.words\.querySelectorAll\('\.mcard'\), \.\.\.box\.querySelectorAll\('\.mcard'\)\]/);
  assert.match(media, /x\.dataset\.pv !== 'none'/);
  assert.match(media, /if \(i < 0 \|\| !c\) \{ edge\(d\); return false; \}/, 'no wrap-around');
  assert.match(media, /function pausePlayers\(V\)/);
  assert.match(media, /if \(e\.cancelable\) e\.preventDefault\(\);\s+\/\/ ours: never the browser's overscroll/);
  assert.match(media, /\}, \{ passive: false \}\);/);
  assert.match(read('app.css'), /\.mview\{overscroll-behavior:none\}/);
});

test('zoomed pictures pan: a gesture that started zoomed or had two fingers never steps', () => {
  assert.match(media, /if \(last && gg && !gg\.multi && gg\.s === 1 && s === 1 && gg\.moved\)/);
});

test('i18n: the preview layer\'s words in both languages', () => {
  const d = (l) => JSON.parse(readFileSync(new URL(`../i18n/web.${l}.json`, import.meta.url), 'utf8')).media.pv;
  for (const l of ['zh', 'en']) for (const k of ['prev', 'next', 'pos', 'first', 'last']) assert.ok(d(l)[k], `${l} media.pv.${k}`);
  assert.equal(d('zh').first, '已经是第一个了');
  assert.match(d('en').pos, /\{n\}.*\{of\}/);
});
