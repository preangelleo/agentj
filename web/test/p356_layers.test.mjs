// P356: every Esc / focus-trap layer that relay.js registers is real code, not text inside a trailing comment.
// (P344 appended its comment to the 'keys' entry and the 'silent' entry ended up after the //: Esc no longer closed 静默记录.)
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');

test('the registerLayer table keeps all seven layers outside comments', () => {
  const at = src.indexOf('for(const [id,close,fallback,rank,trap] of [');
  assert.ok(at > 0, 'the layer table');
  const table = src.slice(at, src.indexOf('])registerLayer(', at));
  const code = table.split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
  const ids = [...code.matchAll(/\['(\w+)',/g)].map((m) => m[1]);
  assert.deepEqual(ids, ['rd', 'cam', 'keys', 'silent', 'menu', 'sug', 'qbar']);
});
