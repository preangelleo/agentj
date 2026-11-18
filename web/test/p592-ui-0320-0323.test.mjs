// P592 (chairman INCOMING-0320 / 0321 / 0323 ⑥): shared ⌘/Ctrl+Enter save helper, contact dialog 保存, void cards put away.
// Browser half (390 + 1440 × zh / en × light / dark, real client + fake host): p592-ui-0320-0323.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (name) => readFileSync(new URL(name, import.meta.url), 'utf8');
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};

test('592: the save chord is Ctrl/⌘+Enter only — never while composing, never with Alt/Shift, never a repeat', async () => {
  const { isSaveChord } = await import('../public/js/card-form.js');
  const k = (x) => ({ key: 'Enter', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, isComposing: false, keyCode: 13, repeat: false, ...x });
  assert.equal(isSaveChord(k({ ctrlKey: true })), true);
  assert.equal(isSaveChord(k({ metaKey: true })), true);
  assert.equal(isSaveChord(k({})), false, 'plain Enter keeps its native meaning (newline in a textarea)');
  assert.equal(isSaveChord(k({ ctrlKey: true, isComposing: true })), false);
  assert.equal(isSaveChord(k({ ctrlKey: true, keyCode: 229 })), false);
  assert.equal(isSaveChord(k({ ctrlKey: true, shiftKey: true })), false);
  assert.equal(isSaveChord(k({ metaKey: true, altKey: true })), false);
  assert.equal(isSaveChord(k({ ctrlKey: true, repeat: true })), false);
  assert.equal(isSaveChord(k({ key: 'a', ctrlKey: true })), false);
});

test('592: the helper submits once, respects a disabled 保存, and marks the button', async () => {
  const { attachCardSaveShortcut } = await import('../public/js/card-form.js');
  let handler = null, saved = 0;
  const attrs = {};
  const root = { addEventListener: (type, fn) => { assert.equal(type, 'keydown'); handler = fn; } };
  const button = { disabled: false, setAttribute: (k, v) => { attrs[k] = v; } };
  const hint = attachCardSaveShortcut(root, () => saved++, button, 'aj-wallet');
  assert.equal(hint, null, 'no matchMedia here = no keyboard hint (phones show none)');
  assert.equal(attrs['aria-keyshortcuts'], 'Control+Enter Meta+Enter');
  let prevented = 0;
  const ev = (x) => ({ key: 'Enter', ctrlKey: true, metaKey: false, altKey: false, shiftKey: false, isComposing: false, keyCode: 13, repeat: false, preventDefault: () => prevented++, stopPropagation() {}, ...x });
  handler(ev({}));
  assert.equal(saved, 1);
  button.disabled = true; handler(ev({}));
  assert.equal(saved, 1, 'a save already in flight is not sent twice');
  button.disabled = false; handler(ev({ ctrlKey: false }));
  assert.equal(saved, 1); assert.equal(prevented, 2, 'the chord is consumed while saving; a plain Enter is not');
});

test('592: every card form uses the one helper (calendar, notes/to-do editor, contacts) — no private copies', () => {
  const calendar = read('../public/js/calendar.js'), cards = read('../public/js/jcards.js'), ct = read('../public/js/ctmanage.js');
  assert.match(calendar, /attachCardSaveShortcut\(form,\(\)=>form\.requestSubmit\(\),save,'aj-wallet-calendar'\)/);
  assert.match(cards, /attachCardSaveShortcut\(dialog,submit,ok,'aj-wallet'\)/);
  assert.doesNotMatch(cards, /e\.key==='Enter'&&\(e\.metaKey\|\|e\.ctrlKey\)/, 'the P415 inline handler is replaced, not doubled');
  assert.match(ct, /attachCardSaveShortcut\(el\('ctm-form'\), \(\) => el\('ctm-form'\)\.requestSubmit\(\), el\('ctm-e-save'\), 'aj-contacts'\)/);
});

test('592: the contact dialog 保存 is a worded button the size of 取消, Lotion ground with Sooty text', () => {
  const html = read('../public/index.html'), menu = read('../public/menu.css');
  assert.match(html, /<button type="submit" id="ctm-e-save" class="go aj-dialog-action aj-dialog-action--primary" data-i18n="ct\.cardSave" data-aj-owner="aj-dialog-action">保存<\/button>/);
  assert.doesNotMatch(menu, /#ctm-e-save\{\s*display:inline-flex[^}]*width:44px/, 'no longer the 44 px icon-only control');
  assert.match(menu, /#ctmEdit \.gdlg-row #ctm-e-save\{flex:1 1 0;background:var\(--c-lotion\);color:var\(--c-sooty\);border-color:var\(--c-lotion\)\}/);
  assert.match(menu, /#ctmEdit \.gdlg-row #ctm-e-cancel\{flex:1 1 0\}/);
});

test('592: a voided card is put away (web view only) — approval card by id, admin card closed on `context`', () => {
  const relay = read('../public/js/relay.js'), elev = read('../public/js/elevate.js');
  assert.match(relay, /if\(!say&&waitsForNewRequest\(m\.id\)\)\{voidFold=m\.id;/);
  assert.match(relay, /if \(voidFold\) foldedCard = cardKey\(card\);/);
  assert.match(relay, /clearTimeout\(leftTimer\);el\('askLeft'\)\.hidden=true;/);
  assert.match(elev, /else if \(m\.why === 'context'\) done\(\{ id: c\.id, result: 'context' \}\);/);
  assert.doesNotMatch(elev, /RESULT = \{[^}]*context:/, 'no 「已撤回」 notice on top of the refusal notice');
});

test('592: the hint string exists in both languages (appended)', () => {
  const zh = JSON.parse(read('../i18n/web.zh.src.json')), en = JSON.parse(read('../i18n/web.en.json'));
  assert.equal(zh['p0321.saveKey'], '{key} 保存');
  assert.equal(en['p0321.saveKey'], '{key} to save');
});
