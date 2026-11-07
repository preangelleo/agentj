// 0.26.9 #468 (CONTRACT §B3 — §B2's terminal is void; ADR-0269-w-assist / w2-web): official remote assist, phone side — the pure
// parts and the static rules, in node (web-unit). The page itself (grant through a fake host, banner, read-only task feed,
// revoke) is test/p0269-assist.mjs; the feed helpers are also in test/p0269-w2.test.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { controlObject, CONTROL_ACTIONS } from '../../protocol/wire.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const AS = read('../public/js/assist.js'), APP = read('../public/app.js'), RS = read('../public/js/rescue.js');
const CSS = read('../public/menu.css');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const zhSrc = JSON.parse(read('../i18n/web.zh.src.json')), zhOv = JSON.parse(read('../i18n/web.zh.override.json'));
const code = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\s\/\/ .*$/gm, '');
/** the pure helpers, lifted out of assist.js (it imports browser modules) and run in a vm */
function helpers() {
  const pick = (name) => {
    const i = AS.indexOf(`export function ${name}(`);
    if (i >= 0) { const eol = AS.indexOf('\n', i), line = AS.slice(i, eol); return line.trimEnd().endsWith('}') ? line.slice(7) : AS.slice(i + 7, AS.indexOf('\n}\n', i) + 2); }
    const m = AS.match(new RegExp(`export const ${name} = ([^\\n]+);`));
    assert.ok(m, name);
    return `const ${name} = ${m[1]};`;
  };
  const consts = ['TASK_MAX', 'TEXT_MAX', 'STATES', 'MINUTES', 'DEFAULT_MINUTES', 'NOTICES', 'NONCE_MS'].map(pick).join('\n');
  const src = [consts, "const HEX32 = /^[0-9a-f]{32}$/; const ID = /^[A-Za-z0-9._:-]{1,64}$/;",
    ...['untilMs', 'minutesLeft', 'parseState', 'parseChallenge', 'grantObject'].map(pick)].join('\n');
  const box = {};
  vm.runInNewContext(src + '\nObject.assign(globalThis, {TASK_MAX, MINUTES, DEFAULT_MINUTES, NOTICES, NONCE_MS, untilMs, minutesLeft, parseState, parseChallenge, grantObject});', box);
  return box;
}
const h = helpers();
const plain = (x) => JSON.parse(JSON.stringify(x));

test('the grant signs minutes \\n key_id \\n seat; revoke signs "revoke"; both are control actions', () => {
  assert.ok(CONTROL_ACTIONS.includes('assist_grant') && CONTROL_ACTIONS.includes('assist_revoke'));
  assert.equal(controlObject('assist_grant', { minutes: 30, key_id: 'aj-assist-2026a', seat: 'seat-1' }), '30\naj-assist-2026a\nseat-1');
  assert.equal(controlObject('assist_revoke', {}), 'revoke');
  assert.deepEqual(plain(h.grantObject(15, { key_id: 'k1', seat: 's1', n: 'x', fa: 'y' })), { minutes: 15, key_id: 'k1', seat: 's1' }, 'nothing else goes into the signed object');
  assert.match(AS, /controlMessage\(channel\(\), await myDeviceId\(\), WIRE\.grant, c\.n, ts, grantObject\(minutes, c\)\)/, 'signed over the HOST nonce');
  assert.match(AS, /signedWrite\(WIRE\.revoke, \{\}, \{ t: WIRE\.revoke \}\)/, 'revoke = a plain signed write, no Face ID');
});

test('durations: 15 / 30 / 60, 30 by default', () => {
  assert.deepEqual(plain(h.MINUTES), [15, 30, 60]);
  assert.equal(h.DEFAULT_MINUTES, 30);
  assert.match(AS, /G = \{ \.\.\.G, open: true, minutes: DEFAULT_MINUTES,/, 'every opening of the step starts at 30');
});

test('assist_state: only active:true with a future until is active; seconds or ms; malformed fields dropped', () => {
  const now = 1_800_000_000_000;
  assert.equal(h.untilMs(1_800_000_600), 1_800_000_600_000);
  assert.equal(h.untilMs(1_800_000_600_000), 1_800_000_600_000);
  assert.equal(h.untilMs('soon'), 0);
  const ok = h.parseState({ active: true, until: (now + 600000) / 1000, key_id: 'aj-assist-2026a', id: 'as01', tasks: [{ id: 't1', state: 'running' }] }, now);
  assert.deepEqual(plain(ok), { active: true, until: now + 600000, key_id: 'aj-assist-2026a', id: 'as01', tasks: [{ id: 't1', state: 'running' }] });
  // negative controls
  assert.equal(h.parseState({ active: true, until: (now - 1000) / 1000 }, now).active, false, 'past until');
  assert.equal(h.parseState({ active: 'yes', until: (now + 600000) / 1000 }, now).active, false, 'active must be true');
  assert.equal(h.parseState({ active: true }, now).active, false, 'no until');
  assert.deepEqual(plain(h.parseState({ active: false, until: (now + 600000) / 1000, tasks: [{ id: 't1', state: 'done' }] }, now).tasks), []);
  const odd = h.parseState({ active: true, until: (now + 60000) / 1000, key_id: 'bad key!', id: '<x>', tasks: 'many' }, now);
  assert.deepEqual([odd.key_id, odd.id, odd.tasks.length], ['', null, 0]);
  assert.equal(h.parseState(null, now), null);
});

test('countdown: minutes rounded up, 0 when over', () => {
  const now = 1_000_000;
  assert.equal(h.minutesLeft(now + 30 * 60000 - 20000, now), 30);
  assert.equal(h.minutesLeft(now + 4.5 * 60000, now), 5);
  assert.equal(h.minutesLeft(now + 50000, now), 1);
  assert.equal(h.minutesLeft(now, now), 0);
  assert.equal(h.minutesLeft(now - 1, now), 0);
});

test('the host challenge: nonce + Face ID + key_id + seat, else nothing is signed', () => {
  const m = { ok: false, why: 'passkey_required', n: 'a'.repeat(32), fa: 'cred', fa_ch: 'chal', key_id: 'aj-assist-2026a', seat: 'seat-1' };
  const c = h.parseChallenge(m);
  assert.deepEqual([c.n, c.fa, c.fa_ch, c.key_id, c.seat], [m.n, 'cred', 'chal', 'aj-assist-2026a', 'seat-1']);
  assert.equal(h.parseChallenge({ ...m, seat: 3 }).seat, '3', 'a numeric seat is signed as its decimal text');
  for (const bad of [{ why: 'estop' }, { n: 'xyz' }, { fa: '' }, { fa_ch: 5 }, { key_id: 'a b' }, { seat: null }, { ok: true }]) assert.equal(h.parseChallenge({ ...m, ...bad }), null, JSON.stringify(bad));
});

test('「看实时画面」 is a read-only task feed: nothing terminal-shaped, nothing sent, nothing stored', () => {
  const c = code(AS);
  assert.doesNotMatch(c, /term_|xterm|readOnly|ptyApi/, 'no terminal anywhere in assist.js');
  assert.match(c, /task: 'assist_task'/);
  assert.match(APP, /if \(m\.assist === true && typeof m\.t === 'string' && m\.t\.startsWith\('term_'\)\) return;/, 'assist terminal frames never reach the owner\'s terminal');
  assert.ok(APP.indexOf("m.assist === true && typeof m.t === 'string'") < APP.indexOf("case 'term_out':"), 'dropped before the terminal routing');
  assert.match(APP, /case 'assist_state': assist\.onState\(m\); return;/);
  assert.match(APP, /case 'assist_notice': assistTerminal\.onNotice\(m\); assist\.onNotice\(m\); return;/);
  assert.match(APP, /case 'assist_task': assist\.onTask\(m\); return;/);
  assert.match(c, /if \(!x \|\| !S\.active\) return;/, 'no grant, no task in the feed');
  assert.doesNotMatch(c, /request\(\{ t: WIRE\.task|sendApp|signedWrite\(WIRE\.task/, 'the feed never sends anything');
  assert.doesNotMatch(c, /localStorage|sessionStorage|indexedDB|dbPut|setK\(|innerHTML/, 'nothing stored, DOM only');
});

test('② gains the step (no new top-level entry); the step follows the assist1 cap', () => {
  assert.match(RS, /paintHelp\(\); assist\.paintCard\(el\('rs-help-body'\)\);/);
  assert.match(AS, /const show = !!peer\.assist1 \|\| S\.active;/);
  assert.doesNotMatch(read('../public/index.html'), /id="(rs-assist|assist-(banner|float|view))/, 'no markup of its own in index.html');
});

test('the layer: Esc closes 「看实时画面」; controls ≥ 44 px; palette colours only', () => {
  assert.match(AS, /registerLayer\(\{ element: \(\) => el\('assist-view'\), open: \(\) => viewOpen, close: closeView/);
  const block = CSS.slice(CSS.lastIndexOf('/*', CSS.indexOf('0.26.9 backlog 468 (CONTRACT §B2, js/assist.js; §B3 task feed)'))).replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(block.length > 100);
  for (const m of block.matchAll(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/g)) assert.fail('hard-coded colour ' + m[0]);
  assert.match(block, /\.asbanner__go\{[^}]*min-height:44px/); assert.match(block, /\.asbtn\{[^}]*min-height:44px;min-width:44px/);
  assert.match(block, /\.asview__x\{[^}]*width:44px;height:44px/); assert.match(block, /\.rsassist__chip\{min-width:var\(--ui-tap\);min-height:var\(--ui-tap\)/);
  assert.match(block, /\.asview\{position:fixed;inset:0;z-index:96;display:flex;flex-direction:column;background:var\(--c-noir\);color:var\(--c-lotion\)/, 'Noir ground, Lotion words');
});

test('i18n: every key in both languages and all three Chinese files, plain words (no trial words, no wire names)', () => {
  const keys = [...new Set([...AS.matchAll(/t\('(p0269\.assist\.[A-Za-z0-9_.]+)'/g)].map((m) => m[1]).filter((k) => !k.endsWith('.')))];
  const dyn = ['p0269.assist.x.what', 'p0269.assist.x.rules', 'p0269.assist.x.ask', 'p0269.assist.x.see', 'p0269.assist.x.back', 'p0269.assist.x.long',
    ...['grant', 'open', 'close', 'expired', 'revoked', 'refused', 'task'].map((w) => 'p0269.assist.note.' + w),
    ...['queued', 'running', 'done', 'refused', 'stopped'].map((w) => 'p0269.assist.st.' + w),
    ...['ended', 'endedStop', 'length', 'admission_filter', 'busy', 'quota', 'main_unavailable', 'submit_failed', 'interrupted', 'failed', 'outbound_gate', 'not_sent', 'other', 'otherDone'].map((w) => 'p0269.assist.twhy.' + w),
    ...['estop', 'no_passkey', 'busy', 'unavailable', 'offline', 'timeout', 'bad_signature', 'stale', 'replay', 'no_key', 'face', 'device_not_allowed', 'shape', 'io', 'passkey_failed', 'passkey_required', 'other'].map((w) => 'p0269.assist.why.' + w)];
  for (const k of [...keys, ...dyn]) {
    for (const [name, d] of [['zh', zh], ['en', en], ['zh.src', zhSrc], ['zh.override', zhOv]]) assert.ok(typeof d[k] === 'string' && d[k].length, `${k} in ${name}`);
    const vz = (zh[k].match(/\{\w+\}/g) || []).sort().join(), ve = (en[k].match(/\{\w+\}/g) || []).sort().join();
    assert.equal(vz, ve, 'same placeholders ' + k);
    assert.doesNotMatch(zh[k] + en[k], /试用|体验版|trial/i, 'no trial words ' + k);
    assert.doesNotMatch(zh[k] + en[k], /assist_|term_|nonce|seat|key_id|PTY|xterm/i, 'no internal terms ' + k);
    assert.doesNotMatch(zh[k], /[A-Za-z]{4,} [A-Za-z]{4,} [A-Za-z]{4,}/, 'zh is Chinese ' + k);
  }
  assert.equal(zh['p0269.assist.banner'], '官方远程协助中 · 剩 {n} 分钟');
  assert.equal(zh['p0269.assist.revoke'], '收回');
  assert.equal(zh['p0269.assist.view'], '看实时画面');
});
