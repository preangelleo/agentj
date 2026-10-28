// P336: the approval level (安全 / 中等 / 全放行) and 「设了目标就不弹窗」 change only by the signed approval_set control;
// the rows exist only for a host that lists them in host.signed_prefs. Browser behaviour: test/p336.mjs.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {controlObject, CONTROL_ACTIONS} from '../public/proto/wire.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const settings = read('../public/js/settings.js'), controls = read('../public/js/controls.js'), html = read('../public/index.html');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));

test('approval.* never goes through pref_set; the write is the signed approval_set', () => {
  const keys = [...settings.matchAll(/PREF_KEYS(?:\.push\(|\s*=\s*\[)([^\]\)]*)/g)].map((m) => m[1]).join(',');
  assert.ok(!/approval\.level|approval\.goal_nocard/.test(keys), 'not in PREF_KEYS');
  assert.match(settings, /signedWriteFa\('approval_set', \{ key, value \}, \{ t: 'approval_set', key, value \}, faceIdStep\)/);
  assert.ok(CONTROL_ACTIONS.includes('approval_set'));
  assert.equal(controlObject('approval_set', {key: 'approval.level', value: 'full'}), 'approval.level\nfull');
  assert.equal(controlObject('approval_set', {key: 'approval.goal_nocard', value: false}), 'approval.goal_nocard\nfalse');
});

test('rows hidden by default and shown only for keys in host.signed_prefs', () => {
  assert.match(html, /<div class="setrow" data-paired id="set-level-row" hidden>/);
  assert.match(html, /<div class="setrow" data-paired id="set-gnc-row" hidden>/);
  assert.match(settings, /hostInfo\.signed_prefs\.includes\(key\)/);
  assert.match(settings, /el\('set-level-row'\)\.hidden = !paired \|\| !signedKey\('approval\.level'\)/);
  assert.match(settings, /el\('set-gnc-row'\)\.hidden = !paired \|\| !signedKey\('approval\.goal_nocard'\)/);
});

test('full access confirm is red; auto by:"full" toast; activity words; rejected reason', () => {
  assert.match(settings, /confirmSheet\(title, text, yes, full\)/);
  assert.match(controls, /m\.by === 'full' \? 'p336\.autoFull'/);
  assert.match(controls, /'parked'\]/);
  assert.match(controls, /'replay', 'rejected'/);
  for (const [d, words] of [[zh, ['疑似被攻击', '定时任务', '直到你切回来', '花钱', '改价']], [en, ['suspected attacks', 'scheduled tasks', 'until you switch back', 'spending money', 'price changes']]]) {
    for (const w of words) assert.ok(d['p336.fullText'].includes(w), w);
  }
});

test('texts exist in both languages with the same placeholders', () => {
  const ks = Object.keys(zh).filter((k) => k.startsWith('p336.')).concat(['act.result.parked', 'why.rejected']);
  assert.ok(ks.length >= 27);
  for (const k of ks) {
    assert.ok(zh[k] && en[k], k);
    const ph = (s) => [...s.matchAll(/\{\w+\}/g)].map((m) => m[0]).sort().join();
    assert.equal(ph(zh[k]), ph(en[k]), k);
  }
});

test('addendum: widening asks Face ID on passkey_required, resends the same signed object, narrowing never asks', () => {
  assert.match(controls, /export async function signedWriteFa\(action, target, extra, faceId\)/);
  assert.match(controls, /if \(res\.ok \|\| res\.why !== 'passkey_required'\) return res;/);
  // P566 (#364): the same signed object + fa, handed in on the reconnected session when the Face ID sheet cost the socket
  assert.match(controls, /return fa \? commandFa\(\{ \.\.\.obj, fa \}, ch\) : \{ ok: false, why: 'cancel' \};/, 'same n / ts / sig + fa');
  assert.match(controls, /const res = await command\(\{ \.\.\.obj, \[GEN\]: g \}, \(\) => \{ sent = true; \}\);/);
  assert.match(controls, /if \(res\.why !== 'offline' \|\| sent \|\| Date\.now\(\) >= until\) return res;/, 'never a second copy once one reached the socket');
  // Face ID starts inside the tap: a click listener on the sheet's button calls approve() synchronously
  assert.match(settings, /const grab = \(\) => \{ p = faApprove\(unb64u\(res\.fa_ch\), res\.fa\);/);
  assert.match(settings, /yes\.addEventListener\('click', grab\);/);
  assert.match(settings, /toast\(m\.why === 'passkey_failed' \? whyText\(m\.why\)/);
  assert.match(controls, /'rejected', 'passkey_failed'\]/);
  assert.equal(zh['why.passkey_failed'], 'Face ID 没通过，请再试一次');
  assert.equal(en['why.passkey_failed'], "Face ID didn't go through — try again");
  assert.match(controls, /\['level_full', 'goal_nocard', 'parked', 'background', 'dispatch_headless'\]\.includes\(r\.by\)/);
});
