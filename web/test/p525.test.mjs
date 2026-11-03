import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// #435 replaced the resident line: host state now drives a one-time toast, and only while exactly one remote is paired.
test('pairing hint follows host state as a one-time toast without reload', () => {
  const toasts = [], store = new Map();
  const ctx = vm.createContext({ t: k => k,
    notifyOnce: (spec, key) => { if (store.has(key)) return false; store.set(key, '1'); toasts.push([spec.text, spec.ms]); return true; } });
  const src = readFileSync(new URL('../public/js/pair-guide.js', import.meta.url), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/export (function|const)/g, '$1');
  vm.runInContext(src, ctx);
  const update = kind => vm.runInContext(`showPairGuide(${JSON.stringify(kind)})`, ctx);
  const list = n => vm.runInContext(`noteRemotes({items: Array(${n}).fill({})})`, ctx);
  list(2); update('computer'); assert.equal(toasts.length, 0);      // a second remote is already there
  list(1); update(null); assert.equal(toasts.length, 0);
  update('computer'); assert.deepEqual(toasts, [['pairGuide.computer', 10000]]);
  update(null); update('computer'); update('phone');                // reconnects repeat the state: no second toast
  assert.equal(toasts.length, 1);
});
