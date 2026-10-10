// P115 (P92 ADR-A196): the first-run checklist card — host snapshot only, owner choices signed (setup_mark), nothing stored.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const wire = await import('../../protocol/wire.js');
const SRC = readFileSync(new URL('../public/js/setup.js', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const CSS = readFileSync(new URL('../public/app.css', import.meta.url), 'utf8');
const FIX = JSON.parse(readFileSync(new URL('./fixtures/p115-setup.json', import.meta.url), 'utf8'));
const hex = (u) => Buffer.from(u).toString('hex');

test('setup card renders host snapshot and signs owner choices', async () => {
  // signed bytes = the host's controls.signed_message (same vector as host/tests/test_p115.py)
  assert.ok(wire.CONTROL_ACTIONS.includes('setup_mark'));
  assert.equal(wire.controlObject('setup_mark', { item: 'tg', choice: 'unused', rev: 7 }), 'tg\nunused\n7');
  const m = await wire.controlMessage('CH', 'DEV', 'setup_mark', 'a'.repeat(32), 1790000000123, { item: 'tg', choice: 'unused', rev: 7 });
  assert.match(new TextDecoder().decode(m), /^agentjarvis-control-v1\nCH\nDEV\nsetup_mark\na{32}\n1790000000123\n[0-9a-f]{64}$/);
  assert.equal(hex(m).length > 0, true);
  // the card only reads the snapshot and sends setup_get / signed setup_mark
  const code = SRC.split('\n').filter((l) => !/^\s*\/\//.test(l)).join('\n');
  for (const bad of ['localStorage', 'indexedDB', 'sessionStorage', "from './store.js'", 'innerHTML', 'fetch(']) assert.ok(!code.includes(bad), bad);
  assert.match(SRC, /signedWrite\('setup_mark', \{ item, choice, rev: S\.revision \}/);
  assert.match(SRC, /t: 'setup_get', check: !!check/);
  assert.ok(APP.includes("case 'setup_card': setupcard.card(m); return;"));
  assert.ok(APP.includes('setupcard.clear();'), 'forgotten with the pairing');
  // required items have no "I won't use this"; "Later" is never the same as unused
  assert.match(SRC, /const optional = i\.need !== 'required';/);
  assert.equal((SRC.match(/if \(optional\) unused\(\);/g) || []).length, 3);
  assert.match(SRC, /const unused = \(\) => row\.append\(btn\('', t\('setup\.unused'\)/);
  // every string the card shows is in both dictionaries with the same placeholders
  const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
  const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));
  const get = (d, k) => k.split('.').reduce((o, p) => (o ? o[p] : undefined), d);
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');
  const keys = [...SRC.matchAll(/t\('(setup\.[\w.]+)'/g)].map((m) => m[1]).filter((k) => !k.endsWith('.'));
  for (const s of ['verified', 'pending', 'waiting_local', 'attention', 'deferred', 'unsupported', 'not_applicable', 'stale']) keys.push('setup.st.' + s);
  for (const w of ['auto', 'local', 'phone']) keys.push('setup.where.' + w);
  assert.ok(keys.length > 30);
  for (const k of keys) { assert.equal(typeof get(zh, k), 'string', 'zh ' + k); assert.equal(typeof get(en, k), 'string', 'en ' + k); assert.equal(ph(get(zh, k)), ph(get(en, k)), k); }
  assert.ok(!/copy\(/.test(SRC), 'no inline strings');
  assert.match(SRC, /mark\(i\.id, 'later'\)/);
  // touch targets
  assert.match(CSS, /\.setup-actions \.aj-btn,\.setup-more \.aj-btn\{[^}]*min-height:56px/);
  // the fixture is a real host snapshot: 30 items, six groups, titles in both languages, no ids of devices or paths
  assert.equal(FIX.items.length, 30);
  assert.deepEqual(FIX.groups.map((g) => g.id), ['core', 'machine', 'browser', 'google', 'privacy', 'optional']);
  for (const i of FIX.items) { assert.ok(i.title.zh && i.title.en && i.hint.zh && i.hint.en, i.id); }
  assert.ok(!/\/home\/|\/Users\/|AJ-[A-Z0-9]{4}-/.test(JSON.stringify(FIX)));
});
