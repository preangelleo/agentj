// P352 「模型和 API Key」 — node unit checks of the page side: the Face ID digest matches the host's (fixed vector computed in
// Python: hashlib.sha256(("agentjarvis-keys-v1\ncopy\nOPENAI_API_KEY").encode()).hexdigest()), the `keys` message parser drops
// anything malformed and never carries a value, every string the module shows exists in both languages, and the module has
// no path that could put a key value into storage, logs or markup. Rendering itself is checked in a real browser (p352.mjs).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { keysMsg } from './p352-fixture.mjs';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document ??= { addEventListener() {}, documentElement: { dataset: {} }, getElementById: () => null, querySelectorAll: () => [], body: {} };
const { keysDigest, KEYS_CTX, elevateChallenge } = await import('../public/js/faceid.js');
const keys = await import('../public/js/keys.js');
const DICT = (await import('../public/i18n.js')).default;
const SRC = readFileSync(new URL('../public/js/keys.js', import.meta.url), 'utf8');

test('keysDigest = hex sha256("agentjarvis-keys-v1\\n" + op + "\\n" + NAME), same as the host', async () => {
  assert.equal(KEYS_CTX, 'agentjarvis-keys-v1');
  assert.equal(await keysDigest('copy', 'OPENAI_API_KEY'), '1cb0bc7cf7fa3363d9e82d9ff3ce15f43ac543493a4b748b524d6bbb7f08f7a2');
  assert.equal(await keysDigest('delete', 'ELEVENLABS_API_KEY'), 'e2ce5e535cbc600e4e558a78dda50d589e8fd52028e2cf1a598a6d04fe65ab43');
  assert.notEqual(await keysDigest('copy', 'A'), await keysDigest('delete', 'A'));
  // the digest is a valid elevate challenge part (no newline, non-empty) for kind key_copy / key_delete
  const ch = await elevateChallenge('AAAAAAAAAAAAAAAAAAAAAA', 'dev', 'a'.repeat(32), 'key_copy', 'b'.repeat(32), await keysDigest('copy', 'X'));
  assert.equal(ch.length, 32);
});

test('parse: names and states only; malformed rows, bad ids / nonces and long tails are dropped', () => {
  const m = keysMsg('Y3JlZC1pZA');
  const s = keys.parse(m);
  assert.equal(s.id, m.id); assert.equal(s.n, m.n); assert.equal(s.fa, 'Y3JlZC1pZA');
  assert.equal(s.base.length, 4);
  assert.deepEqual(s.base[0].rows[0], { type: 'oauth', name: 'claude', state: 'in' });
  assert.deepEqual(s.base[0].rows[2], { type: 'key', name: 'ANTHROPIC_API_KEY', set: true, tail: 'x9Qa', in_use: true });
  assert.equal(s.base[2].rows[0].tail, null, 'no tail sent → none shown');
  assert.equal(s.third.length, 2);
  assert.ok(s.third.every((r) => r.set === true && r.type === 'key'));
  assert.equal(keys.parse({ ...m, fa: undefined }).fa, null, 'no passkey on this phone');
  assert.equal(keys.parse({ ...m, id: 'nothex' }), null);
  assert.equal(keys.parse({ ...m, n: 'x'.repeat(32) }), null);
  assert.equal(keys.parse({ ...m, t: 'providers' }), null);
  const bad = keys.parse({ ...m, base: [{ id: 'v', name: { zh: 'V' }, rows: [
    { type: 'key', name: 'OK_KEY', set: true, tail: 'toolong', value: 'never' },
    { type: 'key', name: '9BAD', set: true }, { type: 'key', name: 'A B', set: true }, { type: 'weird', name: 'X', set: true },
    { type: 'key', name: 'X'.repeat(65), set: true }] }], third: [{ name: 'has-dash', set: true }, { name: 'GOOD_TOKEN', value: 'never' }] });
  assert.deepEqual(bad.base[0].rows, [{ type: 'key', name: 'OK_KEY', set: true, tail: null, in_use: false }]);
  assert.deepEqual(bad.third.map((r) => r.name), ['GOOD_TOKEN']);
  assert.ok(!JSON.stringify(bad).includes('never'), 'a stray value field is never kept');
});

test('NAME_RE for 「添加」 = the host elevate NAME_RE, ≤ 64', () => {
  for (const ok of ['ELEVENLABS_API_KEY', '_X', 'a1', 'X'.repeat(64)]) assert.ok(keys.NAME_RE.test(ok) && ok.length <= keys.NAME_MAX, ok);
  for (const no of ['', '1A', 'A-B', 'A B', 'A=B', 'A\nB']) assert.ok(!keys.NAME_RE.test(no), JSON.stringify(no));
});

test('every p352.* string the module uses exists in zh and en; the page title is renamed in both', () => {
  const used = new Set([...SRC.matchAll(/'(p352\.[\w.]+)'/g)].map((x) => x[1]));
  for (const ty of ['oauth', 'sub', 'key']) used.add('p352.tag.' + ty);
  for (const op of ['copy', 'edit', 'delete', 'set']) used.add('p352.' + op);
  for (const l of ['claude', 'codex']) used.add('p352.login.' + l);
  used.delete('p352.tag.'); used.delete('p352.login.');
  for (const k of used) { assert.ok(DICT.zh[k], 'zh ' + k); assert.ok(DICT.en[k], 'en ' + k); }
  assert.equal(DICT.zh['models.title'], '模型和 API Key');
  assert.equal(DICT.en['models.title'], 'Models & API keys');
  assert.equal(DICT.zh['p352.copy'], '拷贝'); assert.equal(DICT.en['p352.tag.key'], 'Pay-as-you-go');
});

test('the value has no way into storage, logs or markup', () => {
  for (const bad of ['localStorage', 'sessionStorage', 'indexedDB', 'dbPut', 'console.', 'innerHTML', 'outerHTML', 'insertAdjacentHTML', 'history.', 'dataset.value', 'setAttribute(\'value\'']) assert.ok(!SRC.includes(bad), bad);
  // the copy reply's value only goes to the clipboard promise / the held closure
  const uses = [...SRC.matchAll(/res\.value/g)].length;
  assert.equal(uses, 3, 'res.value: type check, read once, then cleared');
  assert.match(SRC, /new CI\(\{ 'text\/plain': blob \}\)/, 'ClipboardItem with a Promise, built in the tap');
  assert.match(SRC, /HOLD_MS = 30000/);
  assert.match(SRC, /addEventListener\('pagehide'/); assert.match(SRC, /visibilitychange/);
  // Face ID starts synchronously in the tap: approve() is called before any await in copy()
  const copyFn = SRC.slice(SRC.indexOf('function copy('), SRC.indexOf('function hold('));
  assert.ok(copyFn.indexOf('approve(ch, S.fa)') > 0 && copyFn.indexOf('approve(ch, S.fa)') < copyFn.indexOf('await'), 'approve before await');
  assert.ok(!/^async function copy/m.test(SRC), 'copy() itself is not async');
});

test('hold / wipe: a refused clipboard keeps nothing after wipe()', () => {
  assert.equal(keys.holding(), false);
  keys.wipe();
  assert.equal(keys.holding(), false);
});
