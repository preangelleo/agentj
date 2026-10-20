// #136: static contract — the /model option line wraps inside its card (the browser proof is p393_options.mjs).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
test('a /model option line wraps inside the card, at a space or 「·」 first',()=>{
  assert.match(css,/\.cmdmodel,\.cmdmodel b,\.cmdmodel span\{max-width:100%;min-width:0;white-space:normal\}/);
  assert.match(css,/\.cmdmodel span\{[^}]*overflow-wrap:anywhere\}/);
  assert.doesNotMatch(css,/\.cmdmodel span\{[^}]*break-all/);
});
