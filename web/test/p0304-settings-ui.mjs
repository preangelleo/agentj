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
test('trusted-server checkbox projects host status and writes only through the signed save (0.30.6)', () => {
  // Disabled until render() sees servid_status + mailuv1; the box shows the host's state, never a local flag.
  assert.match(html, /id="mail-trusted-check" disabled/);
  assert.match(mail, /box\.checked = trustWant \?\? pinned/);
  assert.match(mail, /box\.disabled = busy \|\| !writable \|\| !\(pinned \|\| known\)/);
  assert.match(mail, /const writable = !!view\.servid_status && peer\.mailuv1 === true/);
  // The only write is save() → call() → mail_set with signed('host_write', …); no other message type.
  assert.match(mail, /el\('mail-trusted-check'\)\.addEventListener\('change'[\s\S]{0,240}await save\(patch\)/);
  assert.doesNotMatch(mail, /auth_servid: *(status|s)\.detected/);
  for (const key of ['p0304.mailTrusted','p0304.mailTrustedUnset','p0306.mailTrustedClear','p0306.mailTrustedPin',
    'p0306.mailTrustedOptout','p0306.mailTrustedOutlook','p0306.mailTrustedCustom']) assert.ok(zh[key] && en[key], key);
});
