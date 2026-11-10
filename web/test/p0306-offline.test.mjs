// 0.30.6 (chairman 11-10 12:14): no notice row under the header; the offline notice is the centred dialog after 10 s.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const HTML = read('../public/index.html'), CSS = read('../public/app.css'), APP = read('../public/app.js');
globalThis.window ??= globalThis;   // t.js / layers.js read window at import; the pure connMode needs no DOM
const { connMode, POP_AFTER_MS } = await import('../public/js/conn-dlg.js');

test('0306: the popup waits 10 s of a down link, on a paired page, with nothing in front', () => {
  assert.equal(POP_AFTER_MS, 10000);
  const base = { link: 'down', elapsed: 10000, painted: 'waiting-host', paired: true };
  assert.equal(connMode(base), 'down');
  assert.equal(connMode({ ...base, painted: 'connecting' }), 'reconnecting');
  assert.equal(connMode({ ...base, offer: '9.9.9' }), 'update');
  assert.equal(connMode({ ...base, elapsed: 9999 }), null, 'under 10 s: the dot only');
  assert.equal(connMode({ ...base, link: 'quiet', elapsed: 60000 }), null, 'the quiet window never pops');
  assert.equal(connMode({ ...base, link: 'up' }), null);
  assert.equal(connMode({ ...base, paired: false }), null, 'pairing / error pages own their screen');
  assert.equal(connMode({ ...base, covered: true }), null, 'Settings / a sheet / another dialog in front: wait');
});

test('0306: no row under the header for offline or session repair; both are the centred .gdlg', () => {
  assert.doesNotMatch(HTML, /id="connection-help"|id="connection-refresh"|class="connection-help/);
  assert.doesNotMatch(CSS, /\.connection-help|\.session-help\{display:flex/);
  const chat = HTML.slice(HTML.indexOf('<div class="chatv" id="chat-view">'), HTML.indexOf('<div class="stage" id="stage">'));
  assert.doesNotMatch(chat, /id="connDlg"|id="session-help"/, 'neither lives in the chat column above the stage');
  assert.match(HTML, /<aside id="connDlg" class="gdlg conndlg" role="dialog" aria-modal="false"[^>]* hidden/);
  assert.match(HTML, /<aside id="session-help" class="gdlg session-help" role="dialog" aria-modal="false"[^>]* hidden/);
  for (const id of ['connLater', 'connRetry', 'connRefresh', 'session-later', 'session-repair']) assert.match(HTML, new RegExp(`id="${id}"[^>]*data-aj-owner="aj-dialog-action"|class="[^"]*aj-dialog-action[^"]*"[^>]*id="${id}"|id="${id}" class="[^"]*aj-dialog-action`));
});

test('0306: the dot turns grey at once (quiet window), app.js drives the dialog from the link gate', () => {
  assert.match(CSS, /body\[data-relink\] \.dot\{background:var\(--c-offline\);box-shadow:none;animation:none\}/);
  assert.match(APP, /document\.body\.toggleAttribute\('data-relink', v === 'quiet'\);[\s\S]{0,200}conndlg\.update\(\);/);
  assert.match(APP, /conndlg\.init\(\{[\s\S]{0,400}link: \(\) => link\.state\(\), elapsed: \(\) => link\.elapsed\(\)/);
});

test('0306 fix1: the header has a visible link dot — green while connected, grey on a drop (quiet window) and while off', () => {
  const top = HTML.slice(HTML.indexOf('<header class="top"'), HTML.indexOf('</header>'));
  assert.match(top, /id="orb"[^>]*>\s*(?:<!--[^]*?-->\s*)?<span class="linkdot" id="linkDot" aria-hidden="true"/, 'right after the logo, decorative');
  assert.match(CSS, /\.top \.linkdot\{position:absolute;box-sizing:border-box;width:12px;height:12px;border:2px solid var\(--c-lotion\);[^}]*background:var\(--c-offline\)/);
  assert.match(CSS, /body\[data-conn=on\]:not\(\[data-relink\]\) \.top \.linkdot\{background:var\(--c-shield\)\}/);
  assert.doesNotMatch(CSS, /\.linkdot\{[^}]*(?:display:none|visibility:hidden)/);
});
