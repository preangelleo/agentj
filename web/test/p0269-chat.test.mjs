// 0.26.9 selfheal lane w-chat (CONTRACT §C, #461 #464): the pure parts — which reply gets 「重新登录」, the waited time and
// when 「强制结束这一轮」 is offered, what a turn carries outside its card, the signed auth_rescue object, both dictionaries.
// The page itself (buttons, sheet, signed messages through a fake host) is web/test/p0269-chat.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as SH from '../public/js/selfheal.js';
import { controlObject, CONTROL_ACTIONS } from '../../protocol/wire.js';

test('loginOf: only the host field gives a reply 「重新登录」 — prose saying "login" never does', () => {
  assert.deepEqual(SH.loginOf({ card: { login: { agent: 'claude', why: 'not_logged_in' } } }), { agent: 'claude', why: 'not_logged_in' });
  assert.deepEqual(SH.loginOf({ login: { agent: 'codex', why: 'device' } }), { agent: 'codex', why: 'device' });
  assert.deepEqual(SH.loginOf({ login: { agent: 'opencode', why: 'weird' } }), { agent: 'opencode', why: 'not_logged_in' });
  // negative controls: plain words, an unknown agent, a malformed field
  assert.equal(SH.loginOf({ reply: 'Not logged in · Please run /login', card: null }), null);
  assert.equal(SH.loginOf({ reply: 'You can login later', card: { queued: true } }), null);
  assert.equal(SH.loginOf({ login: { agent: 'gpt', why: 'expired' } }), null);
  assert.equal(SH.loginOf({ login: 'claude' }), null);
  assert.equal(SH.loginOf({ card: { login: ['claude'] } }), null);
  assert.equal(SH.loginOf(null), null);
});

test('carry: a top-level `login` (host history.py) or one on the reply survives snap.js; junk does not', () => {
  assert.deepEqual(SH.carry({ login: { agent: 'claude', why: 'org_disabled' } }), { login: { agent: 'claude', why: 'org_disabled' } });
  assert.deepEqual(SH.carry({ reply: { text: 'x', login: { agent: 'codex', why: 'expired' } } }), { login: { agent: 'codex', why: 'expired' } });
  assert.deepEqual(SH.carry({ waited_s: 130.7, stuck: true }), { queue: { waited_s: 130, stuck: true } });
  assert.deepEqual(SH.carry({ login: { agent: 'nope' }, waited_s: -3 }), {});
  assert.deepEqual(SH.carry({}), {});
});

test('queueOf + waited text + the 120 s / stuck threshold', () => {
  assert.equal(SH.queueOf({ card: { queued: true } }), null);
  assert.deepEqual(SH.queueOf({ card: { queued: true, waited_s: 42 } }), { waited: 42, stuck: false });
  assert.deepEqual(SH.queueOf({ card: { stuck: true } }), { waited: 0, stuck: true });
  assert.deepEqual(SH.queueOf({ queue: { waited_s: 7 } }), { waited: 7, stuck: false });
  assert.equal(SH.queueOf({ card: { waited_s: 'a lot' } }), null);
  assert.deepEqual(SH.waitedText(59), ['p0269.queue.waitedLess', {}]);
  assert.deepEqual(SH.waitedText(60), ['p0269.queue.waited', { n: 1 }]);
  assert.deepEqual(SH.waitedText(3725), ['p0269.queue.waited', { n: 62 }]);
  assert.equal(SH.canEnd({ waited: 119, stuck: false }, 119), false);
  assert.equal(SH.canEnd({ waited: 120, stuck: false }, 120), true);
  assert.equal(SH.canEnd({ waited: 5, stuck: true }, 5), true);
  assert.equal(SH.canEnd(null, 999), false);
});

test('waitedNow keeps counting between host updates only while the notice is live', () => {
  const t0 = 1_000_000;
  assert.equal(SH.waitedNow(901, { waited: 100 }, true, t0), 100);
  assert.equal(SH.waitedNow(901, { waited: 100 }, true, t0 + 30_000), 130);
  assert.equal(SH.waitedNow(901, { waited: 100 }, false, t0 + 30_000), 100, 'an old notice is history: no counting');
  assert.equal(SH.waitedNow(901, { waited: 200 }, true, t0 + 31_000), 200, 'a new host value restarts the count');
});

test('auth_rescue is a control action whose signed text mirrors host controls.object_text', () => {
  assert.ok(CONTROL_ACTIONS.includes('auth_rescue'));
  assert.equal(controlObject('auth_rescue', { action: 'login', agent: 'claude' }), 'login\nclaude');
  assert.equal(controlObject('auth_rescue', { action: 'code', code: 'ab#cd' }), 'code\nab#cd');
  assert.equal(controlObject('auth_rescue', { action: 'cancel' }), 'cancel');
  assert.throws(() => controlObject('auth_rescue', { action: 'shell' }), /bad action/);
  // end_turn / takeover ride the existing rescue_do envelope
  assert.equal(controlObject('rescue_do', { action: 'end_turn' }), 'end_turn');
  assert.equal(controlObject('rescue_do', { action: 'takeover' }), 'takeover');
});

test('every new string exists in zh and en, one language each, no trial wording', () => {
  const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
  const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));
  const src = JSON.parse(readFileSync(new URL('../i18n/web.zh.src.json', import.meta.url), 'utf8'));
  const keys = Object.keys(zh).filter((k) => k.startsWith('p0269.'));
  assert.ok(keys.length >= 40, String(keys.length));
  assert.deepEqual(Object.keys(en).filter((k) => k.startsWith('p0269.')).sort(), keys.sort());
  for (const k of keys) {
    assert.ok(src[k], 'src ' + k);
    assert.match(zh[k], /[一-鿿]/, 'zh ' + k);
    assert.doesNotMatch(en[k], /[一-鿿]/, 'en ' + k);
    for (const s of [zh[k], en[k]]) assert.doesNotMatch(s, /(?<!测)试用|\btrial\b/i, k);
  }
  for (const a of SH.AGENTS) for (const w of SH.WHYS) assert.match(zh['p0269.login.why.' + w], /\{agent\}/, w + a);
  // the generated module carries them
  const gen = readFileSync(new URL('../public/i18n.js', import.meta.url), 'utf8');
  assert.ok(gen.includes('"p0269.login.btn": "重新登录"') && gen.includes('"p0269.queue.end": "Force-end this turn"'));
});

test('the code never lands in storage or the console from the login sheet', () => {
  const src = readFileSync(new URL('../public/js/loginsheet.js', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /localStorage|sessionStorage|indexedDB|console\./);
  assert.match(src, /f\.value = ''/);
});
