import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const root = new URL('../', import.meta.url);
const read = path => readFileSync(new URL(path, root), 'utf8');
const css = read('public/app.css');
const html = read('public/index.html');
const js = read('public/js/mail-triage.js');
const mail = read('public/js/mail.js');
const zh = JSON.parse(read('i18n/web.zh.json'));
const en = JSON.parse(read('i18n/web.en.json'));
test('settings paragraph spacing uses one content token and constrains mobile content', () => {
  assert.match(css, /--set-paragraph-gap:var\(--space-content\)/);
  assert.match(css, /\.setpage \.setnote \+ :is\(/);
  assert.match(css, /\.setmain,\.setsub\{[^}]*overflow-x:hidden/);
  // 228b288ae4: narrow select rows stack; selects are capped by menu.css, inputs/textareas here.
  assert.match(css, /\.setpage :is\(input,textarea\)\{max-width:100%;min-width:0\}/);
  assert.match(read('public/menu.css'), /#settings :is\(\.aj-input,\.aj-select,\.aj-textarea\)\{max-width:100%;min-width:0\}/);
});
test('mail triage starts with one mode and hides advanced settings and records', () => {
  assert.match(js, /fields\.append\(advanced\)/);
  assert.match(js, /results\.append\(node\('summary', copy\.results\), list\)/);
  assert.match(js, /\(\['preset'\]\.includes\(key\) \? fields : advanced\)/);
  assert.doesNotMatch(js, /node\('br'\)/);
});
test('trusted-server checkbox is read-only and projects existing host status', () => {
  assert.match(html, /id="mail-trusted-check" disabled/);
  assert.match(mail, /el\('mail-trusted-check'\)\.checked = pinned/);
  assert.doesNotMatch(mail, /mail-trusted-check'\)\.addEventListener/);
  for (const key of ['p0304.mailTrusted','p0304.mailTrustedUnset']) assert.ok(zh[key] && en[key]);
});
