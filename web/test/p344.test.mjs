// P344 (backlog #52) static contract: the shortcut sheet's search — a line magnifier at the top right of the sheet's
// header, one search field, a "no match" line; texts in zh and en; colours only from existing tokens; both keys and the
// tap registered in interactions.json (scope keys), ⌘K / Ctrl+K handed from chat search to the sheet while it is open.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const html = read('../public/index.html'), menu = read('../public/menu.css'), relay = read('../public/js/relay.js');
const ix = JSON.parse(read('../interactions.json'));
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));

test('P344: magnifier at the far right of the sheet header, search field under it, no-match line', () => {
  const head = html.slice(html.indexOf('id="keys" hidden'), html.indexOf('id="keysFind"'));
  assert.match(head, /<kbd class="setesc" aria-hidden="true">Esc<\/kbd><button type="button" class="menuicon" id="keysFindBtn" aria-controls="keysFind" aria-expanded="false" data-i18n-attr="aria-label=r\.keys\.find,title=r\.keys\.findTitle"[^>]*><svg[^>]*stroke="currentColor"[^>]*><circle[^>]*\/><path[^>]*\/><\/svg><\/button><\/div>/,
    'line icon (stroke, no fill), last in the header bar');
  assert.match(html, /<div id="keysFind" class="set-find keys-find" hidden><input id="keysFindInput" type="search"[^>]*data-i18n-attr="placeholder=r\.keys\.findPh,aria-label=r\.keys\.find"/);
  assert.match(html, /<p id="keysNone" class="keys-none" role="status" hidden><\/p><div class="menucols" id="keysCols">/);
});

test('P344: zh and en texts', () => {
  for (const d of [zh, en]) for (const k of ['find', 'findTitle', 'findPh', 'findNone']) assert.ok(d.r.keys[k], k);
  assert.match(zh.r.keys.findTitle, /\/.*Ctrl\+K.*⌘K/);
  assert.match(en.r.keys.findTitle, /\/.*Ctrl\+K.*⌘K/);
  assert.ok(zh.ixgrp.keys && en.ixgrp.keys && zh.ix.keys.search && en.ix.keys.search_close);
});

test('P344: new CSS uses tokens only (no literal colours), text kept off the field edges', () => {
  const css = menu.slice(menu.indexOf('/* P344'));
  assert.ok(css.length > 100);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i);
  assert.match(css, /#keysFindInput\{[^}]*padding:0 var\(--sp-3\)/);
  assert.match(css, /#keysFindInput\{[^}]*min-height:var\(--ui-tap\)/);
});

test('P344: registered in interactions.json; ⌘K / Ctrl+K yields to the sheet only', () => {
  const by = Object.fromEntries(ix.entries.map((e) => [e.id, e]));
  assert.deepEqual(by['keys.search'].keys.map((k) => k.combo), ['/', 'Mod+K']);
  assert.equal(by['keys.search'].scope, 'keys');
  assert.equal(by['keys.search'].target.selector, '#keysFindBtn');
  assert.deepEqual(by['keys.search_close'].keys.map((k) => k.combo), ['Escape']);
  assert.deepEqual(by['app.search'].keys.find((k) => k.combo === 'Mod+K').yields, ['keys']);
  assert.ok(ix.groups.some((g) => g.id === 'keys'));
  for (const m of ['@ix keys.search', '@ix keys.search_close']) assert.ok(relay.includes(m), m);
  // the sheet's keys come before chat search's ⌘K / Ctrl+K, and never from another field
  assert.ok(relay.indexOf('if (keysFindKey(e, tg))') < relay.indexOf('if ((e.metaKey || (e.ctrlKey && tg !== input))'));
  assert.match(relay, /if \(e\.key === "\/" && !e\.ctrlKey && !e\.metaKey && !e\.altKey\) return !own && !typingIn\(tg\);/);
});
