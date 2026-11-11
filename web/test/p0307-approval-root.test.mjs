// 0.30.7 approval root fix (ADR-0307-offlist) + one-card unbind — static half.
// One-card unbind: only when the host advertises `unbind_one` (it promises a signed dev_unbind of another remote without
// `fa` never runs); the signed request goes first, ONE Face ID sheet follows; older hosts keep the two-step flow.
// Typed source: a host-trust card (heartbeat / system notice / scheduled task / Settings) is never painted as external.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const remotes = read('../public/js/remotes.js');
const card = read('../public/js/approval541.js');

test('one-card unbind is gated on the host flag and keeps the two-step path for older hosts', () => {
  assert.match(remotes, /unbind_one: m\.unbind_one === true/);
  const fn = remotes.slice(remotes.indexOf('async function unbind(d)'), remotes.indexOf('function unbound('));
  assert.match(fn, /if \(!self && snap\?\.unbind_one === true\) \{\n\s+const res = await signedWriteFa\('dev_unbind', \{ device: d\.id \}, \{ t: 'dev_unbind', device: d\.id \}, faceIdUnbindOne\(nm\)\);/);
  const one = fn.slice(0, fn.indexOf('const ok = await confirmSheet'));
  assert.doesNotMatch(one.slice(one.indexOf('unbind_one')), /signedWrite\(/, 'the one-card branch never sends an unsigned-UV write');
  assert.match(fn, /faceIdUnbindOther\(nm\)/, 'older hosts: the two steps stay');
  assert.match(remotes, /faceIdStep\(res,\s*\{ titleText: t\('p0307\.rm\.oneTitle', \{ name: nm \}\), textText: t\('p0307\.rm\.oneText'\), yes: 'p0307\.rm\.oneYes' \}\)/);
});

test('the one-card strings exist in both languages', () => {
  for (const f of ['../i18n/web.en.json', '../i18n/web.zh.json', '../i18n/web.zh.src.json']) {
    const d = JSON.parse(read(f));
    for (const k of ['p0307.rm.oneTitle', 'p0307.rm.oneText', 'p0307.rm.oneYes']) assert.ok(d[k], f + ' ' + k);
    assert.match(d['p0307.rm.oneTitle'], /\{name\}/);
  }
});

test('a host-trust source is painted with its typed label, never as external / unknown', () => {
  assert.match(card, /const hostTyped=src\.source_trust==='host'\|\|src\.source_trust==='host_read';/);
  assert.match(card, /const suspect=!hostTyped&&\(src\.suspicious===true\|\|src\.verified!==true\)/);
  assert.match(card, /typed&&\(hostTyped\|\|src\.verified!==true\)\?typed:/);
});
