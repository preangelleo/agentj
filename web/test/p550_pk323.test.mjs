// P550 (FEEDBACK #323 + 「#323 补」, OPPO / Huawei tablet): every 「设置 Face ID / 用 Face ID 记住这台手机」 entry goes
// through P547's 「另一台批准 → 本机新建」 (never get() on the old key first); a key the host refuses says why + the one way
// on (never 「没记住，不影响现在的配对」); the same device makes a key for the same user handle, so a key left behind in the
// password manager is replaced, not stacked; Android / HarmonyOS words name Touch ID (P559), never Face ID.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window ??= {};
const T = await import('../public/js/t.js');
const A = await import('../public/js/pkassist.js');
const DICT = (await import('../public/i18n.js')).default;
const LANG = (l) => { window.AJLang = { get: () => l, t: (d, k, v) => { const s = d[l][k] ?? d.zh[k] ?? k; return v ? s.replace(/\{(\w+)\}/g, (m, x) => (x in v ? String(v[x]) : m)) : s; }, onChange() {} }; };
const src = (p) => readFileSync(new URL('../public/' + p, import.meta.url), 'utf8');
const APP = src('app.js');

test('#323 ①: every Face ID entry is app.js setupFaceId, which asks the host (pk_peer_req) before any offer', () => {
  assert.match(APP, /\$\('setup-faceid'\)\.addEventListener\('click', \(\) => \{ closeMenu\(\); setupFaceId\(\)\.catch\(\(\) => \{\}\); \}\);/);
  assert.match(APP, /\$\('set-pk-setup'\)\.addEventListener\('click', \(\) => \{ setupFaceId\(\)\.catch\(\(\) => \{\}\); \}\);/);
  for (const m of ['secretout', 'keys', 'cmdrun', 'termhook', 'vaultrow', 'pkassist']) assert.match(APP, new RegExp(m + '\\.configure\\(\\{ setupFaceId'), m);
  assert.match(APP, /async function setupFaceId\(\) \{[\s\S]{0,400}const how = await pkpeer\.startPeerSetup\(\);/);
  // the old direct request only where a host from before P547 never answered pk_peer_req
  assert.equal((APP.match(/t: 'pk_offer_req'/g) || []).length, 1);
  assert.match(APP, /function legacyOffer\(\) \{\n  pkForce = true;\n  session\.sendApp\(\{ t: 'pk_offer_req' \}\)/);
  const peer = src('js/pkpeer.js');
  assert.match(peer, /m = await H\.ask\(\{ t: 'pk_peer_req' \}, 'pk_peer_res', ASK_MS\)/);
  // an approved offer never verifies the old key: register(…, replace: null, peer: true)
  assert.match(peer, /H\.register\(o\.nonce, \{ \.\.\.o\.opts, replace: null, peer: true \}\)/);
  assert.match(src('js/faceid.js'), /const replace_fa = replace && !peer \?/);
});

test('#323 补 ②: a key the host did not take → why + 「在本机设置（需另一台批准）」, never the old 「没记住」', () => {
  assert.match(APP, /if \(m\.ok !== true\) \{[\s\S]{0,400}return toastAction\(t\(pkassist\.regWhy\(m\.why\)\), t\('pkp\.setupHere'\), \(\) => \{ setupFaceId\(\)\.catch\(\(\) => \{\}\); \}, 12000\);/);
  assert.doesNotMatch(APP, /m\.ok \? 'pk\.saved' : 'pk\.saveFail'/);
  assert.equal(A.regWhy('proof'), 'p550.pkReg.proof');
  assert.equal(A.regWhy('expired'), 'p550.pkReg.expired');
  for (const w of ['unknown', 'bad', undefined, 'x']) assert.equal(A.regWhy(w), 'p550.pkReg.fail');
  for (const l of ['zh', 'en']) {
    for (const k of ['p550.pkReg.proof', 'p550.pkReg.expired', 'p550.pkReg.fail']) {
      const s = DICT[l][k];
      assert.ok(s && s.length > 40, k);
      assert.doesNotMatch(s, /没记住|Couldn't remember/, k);
    }
    assert.doesNotMatch(DICT[l]['pk.saveFail'], /没记住|Couldn't remember/);
  }
  assert.match(DICT.zh['p550.pkReg.proof'], /另一台/);
  assert.match(DICT.en['p550.pkReg.proof'], /another of your remotes/);
});

test('#323 补 ③: the same device replaces its own left-behind key (same user handle, nothing excluded)', () => {
  const f = src('js/faceid.js');
  assert.match(f, /user: \{ id: encodeHandle\(\{ relayIndex: ri, channel, hostPub \}\), name, displayName: name \+ displaySuffix \}/);
  assert.match(f, /excludeCredentials: \[\]/);
  assert.doesNotMatch(f.slice(f.indexOf('export async function register'), f.indexOf('export async function register') + 1500), /getRandomValues|Date\.now/);
});

test('#323 ④: on Android / HarmonyOS the words are Touch ID (P559) — never Face ID', () => {
  LANG('zh');
  T.setTextOs('android');
  for (const k of ['pk.askTitle', 'pk.saved', 'pkp.setupHere', 'p550.pkReg.proof', 'p551.noPasskey', 'phoneEnv.setup', 'pk.restore']) {
    const s = T.t(k);
    assert.doesNotMatch(s, /Face ID/, k + ': ' + s);
  }
  assert.equal(T.t('pk.askTitle'), '用 Touch ID 记住这台手机？');
  assert.match(T.t('pkp.setupHere'), /^在本机设置 Touch ID（需另一台批准）$/);
  LANG('en');
  assert.doesNotMatch(T.t('p550.pkReg.proof'), /Face ID/);
  T.setTextOs(undefined); LANG('zh');
});
