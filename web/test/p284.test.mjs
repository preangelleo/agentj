// P284 (owner, 2026-10-14; rework 22:5x 「有点过度设计了」): only the quote's left bar and the list dots / numbers change —
// Peachy Feeling #d97757, on the orange waiting page Emerald Oasis #629887; the Noir reader keeps Peachy. Static half; the
// measured half (ten cards + the reader, everything else equal to the base build) is test/p284.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('../public/app.css', import.meta.url), 'utf8');
const palette = JSON.parse(readFileSync(new URL('./fixtures/leo-palette-hex.json', import.meta.url), 'utf8'));

test('P284: one mark token — Peachy Feeling, Emerald Oasis on the orange waiting page, Peachy in the reader', () => {
  assert.match(css, /--peachy-feeling:var\(--c-peachy-feeling\)/);
  assert.match(css, /--md-mark:var\(--peachy-feeling\);/);
  assert.match(css, /body\[data-status=waiting\]:not\(\[data-kind=question\]\)\{--md-mark:#629887\}/);
  assert.ok(palette.includes('#629887') && palette.includes('#d97757'), 'both are palette colours');
  assert.match(css, /\.rd\{--md-mark:var\(--peachy-feeling\)\}/);
  assert.equal((css.match(/--md-[a-z-]+:/g) || []).filter((t) => t !== '--md-mark:').length, 0, 'no other --md-* token');
});

test('P284: only the quote bar and the list markers use it; the rest is untouched', () => {
  assert.match(css, /\.words li::marker\{color:var\(--md-mark\)\}/);
  assert.match(css, /\.words blockquote\{margin:0 0 \.7em;padding:\.05em 0 \.05em \.85em;border-left:3px solid var\(--md-mark\);color:var\(--ink-soft\)\}/,
    'quote: same box, no background, only the bar colour');
  assert.equal((css.match(/var\(--md-mark\)/g) || []).length, 2, 'used by exactly two rules');
  assert.match(css, /\.words hr\{border:0;border-top:1\.5px dashed var\(--line\);/);
  assert.match(css, /\.words th,\.words td\{border:1px solid var\(--line\);/);
  assert.match(css, /\.words th\{background:var\(--surface\);font-weight:600\}/);
  assert.match(css, /\.words code\{font:500 \.8em\/1\.45 var\(--font-mono\);padding:\.08em \.38em;border-radius:6px;\n  background:rgba\(20,20,20,\.18\);/);
  assert.match(css, /\.words a\{color:inherit;text-decoration:underline;text-decoration-thickness:1px;/);
});
