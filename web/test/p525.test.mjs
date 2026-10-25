import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('open page hides and restores pairing hint from host state without reload', () => {
  const node = { hidden: true, dataset: {} };
  let relang;
  const ctx = vm.createContext({ el: () => node, t: k => k, onLang: f => { relang = f; } });
  const src = readFileSync(new URL('../public/js/pair-guide.js', import.meta.url), 'utf8')
    .replace(/^import .*;$/gm, '').replaceAll('export function', 'function');
  vm.runInContext(src, ctx);
  const update = kind => vm.runInContext(`showPairGuide(${JSON.stringify(kind)})`, ctx);
  update('computer'); assert.equal(node.hidden, false);
  update(null); assert.equal(node.hidden, true);
  relang(); assert.equal(node.hidden, true);
  update('computer'); assert.equal(node.hidden, false);
  update(null); assert.equal(node.hidden, true); // missed change repaired on reconnect
  update('phone'); assert.equal(node.hidden, false);
});
