// P362 「模型和 API Key」 finds keys the computer already has — node unit checks of the page side: the import digest matches the
// host's (fixed vectors also asserted in host/tests/test_p362_keys_found.py), the parser keeps only well-formed `found` /
// `use` / `src` fields (never a value), and every p362.* string the module shows exists in zh and en.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { keysMsg, ENVD, FILE, FP } from './p362-fixture.mjs';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document ??= { addEventListener() {}, documentElement: { dataset: {} }, getElementById: () => null, querySelectorAll: () => [], body: {} };
const { keysDigest } = await import('../public/js/faceid.js');
const keys = await import('../public/js/keys.js');
const DICT = (await import('../public/i18n.js')).default;
const SRC = readFileSync(new URL('../public/js/keys.js', import.meta.url), 'utf8');

test('import digest binds NAME + source + keep / comment + value fingerprint, same hex as the host', async () => {
  const fp = '0123456789abcdef';
  assert.equal(keys.importExtra('s0123456789abcdef', true, fp), 's0123456789abcdef\nkeep\n' + fp);
  assert.equal(await keysDigest('import', 'CLAUDE_CODE_OAUTH_TOKEN', keys.importExtra('s0123456789abcdef', true, fp)),
    '5bf1c2379b2969133661a8e67816d3f04538e380219367636051a8b94cba0212');
  assert.equal(await keysDigest('import', 'CLAUDE_CODE_OAUTH_TOKEN', keys.importExtra('s0123456789abcdef', false, fp)),
    '0b81b419b49fa8b4f050dd38ef7ed662918a2b38690957aa2c7d218577e508a8');
  assert.equal(await keysDigest('copy', 'OPENAI_API_KEY'), '1cb0bc7cf7fa3363d9e82d9ff3ce15f43ac543493a4b748b524d6bbb7f08f7a2', 'P352 unchanged');
});

test('parse keeps found / use / src, drops anything malformed', () => {
  const s = keys.parse(keysMsg('Y3JlZC1pZA'));
  const claude = s.base[0].rows[0];
  assert.deepEqual(claude, { type: 'oauth', name: 'claude', state: 'in', use: 'sub', use_name: 'CLAUDE_CODE_OAUTH_TOKEN', use_src: ENVD });
  assert.deepEqual(s.base[0].rows[1].found, [{ src: ENVD, tail: 'k7Qz', live: true, fp: FP }]);
  assert.deepEqual(s.base[1].rows[1].found, [{ src: FILE, tail: '9a9a', same: false }]);
  assert.equal(s.third.find((r) => r.name === 'GITHUB_TOKEN').set, false, 'a found-only third-party name is not set');
  assert.equal(s.third.find((r) => r.name === 'WOLFRAM_APP_TOKEN').set, true, 'P352 rows without `set: false` stay set');
  assert.deepEqual(s.src.map((x) => [x.kind, x.why || '']), [['envd', ''], ['systemd', ''], ['file', ''], ['file', 'symlink']]);
  const bad = keys.parse({ ...keysMsg(null), base: [{ id: 'v', name: { zh: 'V' }, rows: [
    { type: 'oauth', name: 'claude', state: 'in', use: 'steal', use_name: 'A B', value: 'never' },
    { type: 'key', name: 'X_KEY', set: false, found: [{ src: '../etc', tail: 'abcd' }, { src: FILE, tail: 'toolong', value: 'never', fp: 'XYZ' }, { src: 'systemd', same: 'yes', live: 1 }] }] }],
    src: [{ id: 'x', label: 'a', kind: 'file' }, { id: FILE, label: '', kind: 'file' }, { id: FILE, label: 'ok', kind: 'web' }, { id: FILE, label: '~/a.env', kind: 'file', count: -1, why: 'nope', value: 'never' }] });
  assert.deepEqual(bad.base[0].rows[0], { type: 'oauth', name: 'claude', state: 'in' });
  assert.deepEqual(bad.base[0].rows[1].found, [{ src: FILE, tail: null }, { src: 'systemd', tail: null }]);
  assert.deepEqual(bad.src, [{ id: FILE, kind: 'file', label: '~/a.env', count: 0 }]);
  assert.ok(!JSON.stringify(bad).includes('never'));
});

test('every p362.* string the module uses exists in zh and en', () => {
  const used = new Set([...SRC.matchAll(/'(p362\.[\w.]+)'/g)].map((x) => x[1]));
  for (const u of ['login', 'sub', 'key', 'cloud', 'helper', 'none']) used.add('p362.use.' + u);
  for (const w of keys.WHYS) used.add('p362.why.' + w);
  used.delete('p362.use.'); used.delete('p362.why.');
  assert.ok(used.size > 30);
  for (const k of used) { assert.ok(DICT.zh[k], 'zh ' + k); assert.ok(DICT.en[k], 'en ' + k); }
});

test('import never carries or shows a value: the page sends NAME + source id + keep + fingerprint only', () => {
  const body = SRC.slice(SRC.indexOf('async function importKey'), SRC.indexOf('/** 删除'));
  assert.ok(body.includes("run('import', r.name, faP, { src: f.src, keep, fp: f.fp })"));
  assert.ok(!/value/.test(body), 'no value handling in the import path');
});
