// 0.30.7 candidate, chairman's daytime look (11-11) — three notice fixes on the phone page:
//  1. the reload-stop notice has its own close button (a phone has no Esc); Esc and the 15 s auto-dismiss stay
//  2. the preview-failure strip never covers the reply card's title (its own strip in the chat flow) and a confirmation
//     card on top (body[data-ontop]) stays above it (security review S3: it was z 250 over the card's 150)
//  3. Settings does not say the same sentence twice: a notice that only repeats a Settings status line is not shown
// Browser proof: p0307-toast-dismiss-shots.mjs · p0307-secret-new-task-shots.mjs (zh/en × light/dark × 390/1440).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const ui = read('../public/js/ui.js'), app = read('../public/app.js'), css = read('../public/app.css');
const pf = read('../public/js/preview-failure.js'), mail = read('../public/js/mail.js');

test('1 reload stop: one notice function, 15 s, square, close button labelled from the dictionary', () => {
  assert.match(ui, /export const RELOAD_STOP_MS = 15000;/);
  assert.match(ui, /export function reloadStoppedNotice\(\) \{ return notify\(\{ kind: 'info', shape: 'square', text: tr\('reload\.stopped'\), ms: RELOAD_STOP_MS, close: tr\('reload\.close'\) \}\); \}/);
  assert.match(app, /notice: \(\) => reloadStoppedNotice\(\)/);
  assert.doesNotMatch(app, /toast\(t\('reload\.stopped'\)/, 'the old 6 s close-less toast is gone');
  // the button: aria-label + title from spec.close, closes through toastOff (same as Esc), keeps a field's focus
  const at = ui.indexOf('if (spec.close) {'), body = ui.slice(at, ui.indexOf('box.replaceChildren(...kids);', at));
  assert.ok(at > 0);
  assert.match(body, /x\.type = 'button'; x\.className = 'nt-close'/);
  assert.match(body, /x\.setAttribute\('aria-label', String\(spec\.close\)\)/);
  assert.match(body, /x\.addEventListener\('click', \(\) => toastOff\(\)\)/);
  assert.match(ui, /box\.classList\.toggle\('closable', !!spec\.close\)/);
  assert.match(ui, /'glance', 'over-dlg', 'closable'\);/, 'a later notice drops the close state');
  // Esc on a held notice still puts it away (unchanged global handler)
  assert.match(ui, /if \(n && n\.classList\.contains\('on'\)\) \{ toastOff\(\);/);
});

test('1 reload stop: the button is a 44 px Lotion-on-Noir target and the square takes taps while it shows', () => {
  assert.match(css, /\.toast \.nt-close\{[^}]*width:44px;height:44px;min-width:44px;min-height:44px;[^}]*background:transparent;color:var\(--dlg-fg\)/);
  assert.match(css, /\.toast\.closable\.on\{pointer-events:auto\}/);
  assert.match(css, /\.toast:is\(\.circle,\.square\)\.closable\{padding-top:44px\}/, 'text never runs under the button');
});

test('1 reload stop: both dictionaries carry reload.close (and the old wording is unchanged)', () => {
  const want = { '../i18n/web.zh.json': '关闭这条提示', '../i18n/web.zh.src.json': '关闭这条提示', '../i18n/web.zh.override.json': '关闭这条提示', '../i18n/web.en.json': 'Close this notice' };
  for (const [f, v] of Object.entries(want)) assert.equal(JSON.parse(read(f))['reload.close'], v, f);
  assert.equal(JSON.parse(read('../i18n/web.zh.json'))['reload.stopped'], '页面刷新没有生效，已停止自动刷新');
  const bundle = read('../public/i18n.js');
  assert.equal(bundle.split('"reload.close"').length - 1, 2, 'built for zh + en');
});

test('2 preview failure: in the chat it is a strip in the flow before the stage, never fixed over the card', () => {
  assert.match(pf, /if \(chat && stage\?\.parentElement === chat && document\.body\.dataset\.view === 'chat'\) chat\.insertBefore\(banner, stage\);/);
  assert.match(pf, /else document\.body\.appendChild\(banner\);/);
  assert.match(pf, /actions:\[\{label:t\('preview\.dismiss'\), fn:\(\)=>\{\}\}\], ms:10000\}/, '「知道了」 and 10 s unchanged');
  assert.match(css, /\.chatv>#toast\[data-preview-failure="1"\]\.on\{position:relative;top:auto;left:auto;bottom:auto;transform:none;flex:none;/);
});

test('2 preview failure (S3): a confirmation card on top is above the strip', () => {
  const m = /body\[data-ontop="1"\] #toast\[data-preview-failure="1"\]\{z-index:calc\(var\(--z-confirm\) - 1\)!important\}/.exec(css);
  assert.ok(m, 'below --z-confirm while a card is on top');
  // it must come after (and out-rank) the generic notice-over-card rule P551 keeps for every other notice
  assert.ok(m.index > css.indexOf('body[data-ontop="1"] #toast{z-index:var(--z-notice-top)!important}'));
});

test('3 Settings: a notice that repeats a Settings status line is not shown; a later line puts it away', () => {
  assert.match(ui, /function settingsLine\(text\) \{/);
  assert.match(ui, /page\.querySelectorAll\('\[role="status"\]'\)/);
  assert.match(ui, /if \(!\(spec\.actions \|\| \[\]\)\.length\) \{ const line = settingsLine\(spec\.text\); if \(line\) \{ line\.scrollIntoView\?\.\(\{ block: 'nearest' \}\); return null; \} \}/);
  assert.match(ui, /export function settingsSaid\(text\) \{/);
  assert.match(mail, /function say\(s\) \{ el\('mail-msg'\)\.textContent = s \|\| ''; settingsSaid\(s\); \}/);
  assert.match(read('../public/index.html'), /id="mail-msg" role="status"/);
});
