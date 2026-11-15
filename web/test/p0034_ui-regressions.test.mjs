import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const CSS = read('../public/app.css');
const UI = read('../public/js/ui.js');
const CONN = read('../public/js/conn-dlg.js');
const RELAY = read('../public/js/relay.js');
const BOOT = read('../public/js/boot.js');
const IOS = read('../public/js/ios-chrome.js');
const HTML = read('../public/index.html');

const solid = (s) => !/rgba?\([^)]*[,/]\s*0(?:\.\d+)?\s*\)/i.test(s) && !/transparent/i.test(s);

test('R-0034 notices keep the Noir card and use a borderless Lotion Undo action', () => {
  assert.match(CSS, /\.toast\{[^}]*background:var\(--dlg-bg\);color:var\(--dlg-fg\)/s);
  const notices = CSS.indexOf('.reply-notices>.toast{');
  assert.ok(notices >= 0, 'reply notice layout selector exists');
  assert.ok(CSS.slice(notices).includes('.reply-notices>.toast{') && CSS.slice(notices).includes('background:var(--aj-notice-layout-background,var(--dlg-bg));color:var(--aj-notice-layout-color,var(--dlg-fg));'), 'reply notices retain Noir defaults');
  assert.match(CSS, /\.toast \.undo\{[^}]*color:var\(--noir\);background:var\(--lotion\);border:0\}/s);
  assert.doesNotMatch(CSS, /\.toast\[data-kind="success"\]/);
  assert.match(CONN, /if \(link === 'up'\)[\s\S]*?mode = null/);
  assert.match(CONN, /mode = 'up'/);
  assert.ok(solid('--lotion') && solid('--noir'));
});

test('R-0001 iOS top chrome is always Sooty and runtime theme/status writes are disabled', () => {
  assert.match(HTML, /<meta name="theme-color" content="#141414" media="\(prefers-color-scheme: light\)">/);
  assert.match(HTML, /<meta name="theme-color" content="#141414" media="\(prefers-color-scheme: dark\)">/);
  assert.match(BOOT, /__ajLaunchChrome\s*=\s*"#141414"/);
  assert.doesNotMatch(BOOT, /querySelectorAll\(['"]meta\[name=.?theme-color/);
  assert.match(RELAY, /function setThemeColor\(hex\)\{\s*return;/);
  assert.doesNotMatch(RELAY, /setThemeColor\(color\)/);
  assert.match(IOS, /const color = '#141414'/);
  assert.doesNotMatch(IOS, /setProperty\(['"]--ios-top/);
});
