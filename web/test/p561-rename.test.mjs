// P561 追加 13 (#338, Leo 10-28 Mac Safari: 「给遥控器改个名字，都需要给我全屏吗？……点击下边的确认并不是保存，是回到上一个页面，
// 然后还要再点保存」): a single-line field is edited where it stands (Enter / its own Save = one step); only multi-line text goes
// full screen, and there ✓ is the field's Save — when the field is all its form saves, ✓ submits the form.
// Static half; the browser half is p561-rename.mjs (Chromium both widths/languages + WebKit).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const editor = read('../public/js/text-editor.js');

test('P561 settings only hand multi-line text (textarea) to the full-screen editor', () => {
  const scan = editor.slice(editor.indexOf('export function watchSettings'));
  const sel = scan.match(/querySelectorAll\('([^']+)'\)/)[1];
  assert.equal(sel, 'textarea', 'watchSettings must not pick single-line inputs: ' + sel);
  assert.doesNotMatch(sel, /input/);
});

test('P561 field() leaves a single-line input inline (no readOnly, no launcher)', () => {
  assert.match(editor, /export const multiline = \(input\) => input\.tagName === 'TEXTAREA' \|\| \(input\.tagName === 'INPUT' && input\.type === 'hidden'\)/);
  assert.match(editor, /if \(input\.dataset\.textField \|\| !multiline\(input\)\) return input;/);
  // the old single-line launcher (click / Enter on the field opened the editor) is gone
  assert.doesNotMatch(editor, /input\.addEventListener\('click',launch\)/);
  assert.doesNotMatch(editor, /e\.key==='Enter'\|\|e\.key===' '\)\{e\.preventDefault\(\);launch\(\)/);
});

test('P561 the full-screen ✓ submits the field\'s form (P563 追加 3: whatever else the form holds)', () => {
  assert.match(editor, /export const saveForm = \(input\) => input\.form \|\| null;/);
  assert.doesNotMatch(editor, /soleForm/, 'P563: no more 「only when it is the sole field」');
  assert.match(editor, /const f=saveForm\(input\);if\(f\)queueMicrotask\(\(\)=>f\.requestSubmit\(\)\);/);
});

test('P561 the short rename fields stay plain single-line inputs with their own one-step save', () => {
  const html = read('../public/index.html');
  assert.match(html, /<input id="renameInput"[^>]*type="text"[^>]*maxlength="64"/);
  assert.match(html, /<input id="nameInput"[^>]*type="text"[^>]*maxlength="32"/);
  const remotes = read('../public/js/remotes.js');
  assert.match(remotes, /inp\.type = 'text'; inp\.maxLength = 64;/);
  assert.match(remotes, /f\.addEventListener\('submit', \(e\) => \{ e\.preventDefault\(\); rename\(d, inp\.value\); \}\);/);
  assert.doesNotMatch(remotes, /text-editor\.js|textField|openText/);
  const agent = read('../public/js/agentname.js');
  assert.match(agent, /\$\('nameForm'\)\.addEventListener\('submit'/);
  assert.doesNotMatch(agent, /text-editor\.js|textField|openText/);
  assert.match(read('../public/js/dev-rename.js'), /e\.key === 'Enter' && !e\.isComposing\) \{ e\.preventDefault\(\); save\(\);/);
});
