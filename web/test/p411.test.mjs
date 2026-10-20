// P411 (#172 + #76): the screen-help card never stores the picture, sends only its three fixed requests, and has both languages.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(new URL('../public/js/helpcard.js', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const SYNC = readFileSync(new URL('../public/js/cardsync.js', import.meta.url), 'utf8');
const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));

test('P411 screen-help card: no storage, no history, a bounded picture of three image types only', () => {
  const code = SRC.split('\n').filter((l) => !/^\s*(\/\/|\/\*\*?|\*)/.test(l)).join('\n');
  for (const bad of ['localStorage', 'indexedDB', 'sessionStorage', 'dbPut', "from './store.js'", 'hist', 'caches.', 'fetch(', 'navigator.clipboard', 'innerHTML']) {
    assert.ok(!code.includes(bad), `helpcard.js must not touch ${bad}`);
  }
  assert.match(SRC, /const MIMES = \['image\/png', 'image\/jpeg', 'image\/webp'\]/);
  assert.match(SRC, /m\.img\.length > IMG_MAX_B64/, 'bounded image');
  assert.match(SRC, /removeAttribute\('src'\)/, 'the picture leaves the page when the card ends');
  assert.match(SRC, /'ajmodal dlgcard hlp'/);
  const sends = [...SRC.matchAll(/(?:act|send)\(\{? ?t: '([a-z_]+)'|act\('([a-z_]+)'\)/g)].map((m) => m[1] || m[2]);
  assert.deepEqual([...new Set(sends)].sort(), ['help_cancel', 'help_done', 'help_text']);
  assert.match(SRC, /text: text\.slice\(0, ANSWER_MAX\)/);
  for (const t of ['help_card', 'help_end', 'help_err']) assert.ok(APP.includes(`case '${t}':`), t);
  assert.match(APP, /async function forgetLocal\(\) \{[^}]*helpcard\.clear\(\);[^}]*await wipeLocal\(\);/, 'unpair forgets open cards');
  assert.match(SYNC, /\[helpcard\.ids, helpcard\.prune\]/, 'P203 card sync knows the card');
});

test('P411 strings exist in both languages with the same placeholders', () => {
  const ks = Object.keys(zh).filter((k) => k.startsWith('p411.'));
  assert.ok(ks.length >= 20);
  assert.deepEqual(ks.sort(), Object.keys(en).filter((k) => k.startsWith('p411.')).sort());
  const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
  for (const k of ks) assert.equal(ph(zh[k]), ph(en[k]), k);
  const used = [...SRC.matchAll(/t\('(p411\.[\w.]*\w)'/g)].map((m) => m[1]);
  for (const k of used) assert.ok(k in zh, k);
  for (const why of ['secret', 'gone', 'shape']) assert.ok(`p411.err.${why}` in zh, why);
  assert.match(zh['p411.privacy'], /不要在这里填密码/);
});
