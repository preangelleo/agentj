// P73 (ADR-A180): F32 secret pickup card + the secret card's buttons (support ticket: two `gone`, one `denied`).
// - the card's shown digest and Face ID challenge equal the host's (shared vector with host/tests/test_p73_secret.py);
// - secretout.js never stores a value (no localStorage / IndexedDB / store.js / history), never sends one, opens only after
//   Face ID (the assertion comes before the request), and offers 「设置 Face ID」 instead of any fallback;
// - elevate.js: Enter only submits, an empty field says what is missing instead of sending a decline, 「不提供」 sits on its
//   own line as a quiet button and needs a second tap; the strings exist in both languages.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const { secretOutDigest, secretOutFields, SECRET_OUT_CTX, SECRET_OUT_KIND, elevateChallenge } = await import('../public/js/faceid.js');
const SRC = readFileSync(new URL('../public/js/secretout.js', import.meta.url), 'utf8');
const ELEV = readFileSync(new URL('../public/js/elevate.js', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));
const hex = (u) => Buffer.from(u).toString('hex');
const DIGEST_VEC = '1666e5517932f328f0a6fd71d4f69e86fe47e796fe2a7438af7cdc71dc3625ee';   // = test_p73_secret.DIGEST_VEC

test('§18 shown digest = the host\'s (shared vector), binds every shown field', async () => {
  assert.equal(SECRET_OUT_CTX, 'agentjarvis-secret-out-v1');
  assert.equal(SECRET_OUT_KIND, 'secret_out');
  const base = { name: 'n', purpose: 'p', kind: 'file', filename: 'a.yaml', size: 10 };
  const d = await secretOutDigest(secretOutFields(base));
  assert.equal(d, DIGEST_VEC);
  for (const [k, v] of [['name', 'm'], ['purpose', 'q'], ['kind', 'text'], ['filename', 'b.yaml'], ['size', 11]]) {
    assert.notEqual(await secretOutDigest(secretOutFields({ ...base, [k]: v })), d, k);
  }
  // the Face ID challenge uses its own kind: an assertion for a F17 secret card never opens a pickup card
  const a = hex(await elevateChallenge('ch', 'dev', '0'.repeat(32), 'secret_out', 'f'.repeat(32), d));
  const b = hex(await elevateChallenge('ch', 'dev', '0'.repeat(32), 'secret', 'f'.repeat(32), d));
  assert.notEqual(a, b);
});

test('§18 the page never stores or sends a value; Face ID comes before the open request', () => {
  const code = SRC.split('\n').filter((l) => !/^\s*(\/\/|\/\*\*?|\*)/.test(l)).join('\n');   // comments may name them
  for (const bad of ['localStorage', 'indexedDB', 'sessionStorage', 'dbPut', "from './store.js'", 'hist', 'caches.']) {
    assert.ok(!code.includes(bad), `secretout.js must not touch ${bad}`);
  }
  const sends = [...SRC.matchAll(/sendApp\(\{ t: '([a-z_]+)'([^}]*)\}/g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(sends.map((s) => s[0]).sort(), ['secret_out_decline', 'secret_out_open']);
  for (const [, body] of sends) assert.ok(!/value|data/.test(body), 'no value goes back to the host');
  const open = SRC.slice(SRC.indexOf('async function openCard'), SRC.indexOf('async function declineCard'));
  assert.ok(open.indexOf('approve(c.faCh, c.fa)') > 0 && open.indexOf('approve(') < open.indexOf("t: 'secret_out_open'"),
    'the assertion is made before the request');
  assert.match(open, /if \(!c\.fa\) \{ toast\(t\('sout\.noPasskey'\)/, 'no passkey → no open, no fallback');
  assert.match(SRC, /opened\.bytes\.fill\(0\)/, 'file bytes are zeroed when closed');
  assert.match(SRC, /SHOW_MS = 120000/);
  assert.match(SRC, /visibilitychange/);
  for (const t of ['secret_out', 'secret_out_val', 'secret_out_done', 'secret_out_err']) assert.ok(APP.includes(`case '${t}':`), t);
  assert.match(APP, /\(h\.pkAsked && !forced\)/, '「设置 Face ID」 can ask for an offer after 「以后再说」');
});

test('P73 secret card: Enter only submits, empty says so, 「不提供」 is separate and needs a second tap', () => {
  assert.match(ELEV, /addEventListener\('keydown', \(e\) => \{\n\s+if \(e\.key !== 'Enter' \|\| e\.isComposing\) return;\n\s+e\.preventDefault\(\); e\.stopPropagation\(\);\n\s+decide\(true\);/);
  assert.ok(!/decide\(false\)[^;]*Enter|Enter[^;]*decide\(false\)/.test(ELEV), 'Enter never declines');
  assert.match(ELEV, /acts\.append\(allow\);/, 'the main action is alone on its row');
  assert.match(ELEV, /acts, deny\);/, '「拒绝 / 不提供」 comes after the row, on its own line');
  assert.match(ELEV, /aj-btn aj-btn--quiet elev-deny/, 'a quiet button');
  assert.match(ELEV, /elev\.secret\.cancelSure/, 'a second tap confirms 「不提供」');
  assert.match(ELEV, /never send false/);
  assert.ok(!/\$\('elev-allow'\)\.disabled = busy \|\| !c \|\| \(!viaHelper\(c\) && \(!v/.test(ELEV), 'an empty field no longer disables the button silently');
});

test('P73 strings exist in both languages with the same placeholders', () => {
  const keys = ['elev.secret.empty', 'elev.secret.cancelSure', 'elev.sudo.empty', 'sout.kicker', 'sout.note', 'sout.noteOpen',
    'sout.noPasskey', 'sout.noFaceId', 'sout.open', 'sout.setup', 'sout.copy', 'sout.copyFile', 'sout.download', 'sout.done',
    'sout.decline', 'sout.left', 'sout.picked', 'sout.text', 'sout.file', 'sout.binary', 'sout.sendFail', 'sout.r.picked',
    'sout.r.expired', 'sout.r.declined', 'sout.r.stopped', 'sout.r.gone'];
  const get = (d, k) => k.split('.').reduce((o, p) => (o ? o[p] : undefined), d);
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');
  for (const k of keys) {
    assert.equal(typeof get(zh, k), 'string', 'zh ' + k);
    assert.equal(typeof get(en, k), 'string', 'en ' + k);
    assert.equal(ph(get(zh, k)), ph(get(en, k)), k);
  }
  for (const m of SRC.matchAll(/t\('(sout\.[\w.]+)'/g)) assert.equal(typeof get(zh, m[1]), 'string', m[1]);
});
