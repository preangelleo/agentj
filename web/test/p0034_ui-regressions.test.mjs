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
  // P593: iOS 26 Home Screen apps colour the iOS-painted status bar from the page's top row (WebKit
  // sampledPageTopColor, frozen per document) — so the root layer is Sooty and never thinner than 2 px,
  // whatever the safe-area inset, and the Home Screen under-page colour is Sooty.
  const top = CSS.match(/html\[data-ios="1"\]::before\{([^}]+)\}/)?.[1];
  assert.ok(top, 'root status layer');
  assert.match(top, /background:#141414;/);
  assert.match(top, /min-height:2px;/);
  assert.match(top, /position:fixed;left:0;right:0;top:var\(--vvtop,0px\);/);
  assert.doesNotMatch(top, /--(?:pg|state|c-noir|ios-chrome|ios-bottom)|opacity|transparent/);
  assert.match(CSS, /html\[data-ios="1"\]\[data-standalone="1"\]\{background:#141414!important\}/);
  assert.match(HTML, /<meta name="apple-mobile-web-app-status-bar-style" content="default">/);
});
