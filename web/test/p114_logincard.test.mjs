// P114 (ADR-A194 §5): the sign-in QR card never stores the QR, sends only its four fixed requests, and has both languages.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(new URL('../public/js/logincard.js', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));

test('P114 sign-in card: no storage, no history, only the QR image as a data: PNG', () => {
  const code = SRC.split('\n').filter((l) => !/^\s*(\/\/|\/\*\*?|\*)/.test(l)).join('\n');
  for (const bad of ['localStorage', 'indexedDB', 'sessionStorage', 'dbPut', "from './store.js'", 'hist', 'caches.', 'fetch(', 'navigator.clipboard']) {
    assert.ok(!code.includes(bad), `logincard.js must not touch ${bad}`);
  }
  assert.match(SRC, /'data:image\/png;base64,' \+ m\.img/);
  assert.match(SRC, /m\.img\.length > 700000/, 'bounded image');
  assert.match(SRC, /removeAttribute\('src'\)/, 'the image leaves the page when the card ends');
  const sends = [...SRC.matchAll(/sendApp\(\{ t: '?([a-z_]+)'?/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(sends)].sort(), ['kind', 'login_qr_refresh', 'login_qr_tg']);
  assert.match(SRC, /act\('login_qr_check'\)/); assert.match(SRC, /act\('login_qr_cancel'\)/);
  for (const t of ['login_qr', 'login_qr_done', 'login_qr_state', 'login_qr_err']) assert.ok(APP.includes(`case '${t}':`), t);
  assert.match(APP, /async function forgetLocal\(\) \{[^}]*logincard\.clear\(\);[^}]*await wipeLocal\(\);/, 'unpair forgets open cards');
});

test('P114 sign-in card strings exist in both languages, the channel name only as data', () => {
  const keys = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' ? keys(v, p + k + '.') : [p + k]));
  assert.deepEqual(keys(zh.lqr).sort(), keys(en.lqr).sort());
  for (const d of [zh.lqr, en.lqr]) {
    assert.ok(!JSON.stringify(d).includes('Telegram'), 'the channel name arrives from the computer as data');
    assert.match(d.tgTextNote, /\{channel\}/);
    for (const k of ['title', 'alt', 'signedIn']) assert.match(d[k], /\{site\}/, k);
  }
  assert.match(zh.lqr.note, /另一台设备/); assert.match(en.lqr.note, /another device/);
});
