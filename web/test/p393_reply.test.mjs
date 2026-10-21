// #126 / #127: static contract of the reply pills' centring and the external-link colour (the browser proof is p393_reply.mjs).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
test('the one-tap answer centres its label and wraps inside the card',()=>{
  assert.match(css,/\.words \.rbtn\{display:inline-flex;align-items:center;justify-content:center;/);
  assert.match(css,/\.words \.rbtn\{max-width:100%;box-sizing:border-box;white-space:normal;overflow-wrap:anywhere\}/);
});
test('an external reply link is Peachy Feeling, lifted only where pure is too faint',()=>{
  // P424 #127: the faint places take a ready-made palette colour (Bisque), never a color-mix toward Lotion
  assert.match(css,/#words a\[href\^="http"\]:not\(\.mtap\):not\(\.mcard a\)\{color:var\(--xlink\);/);
  assert.match(css,/:root\{--xlink:var\(--c-bisque\)\}/);
  assert.match(css,/:root\[data-theme="dark"\]\{--xlink:var\(--c-peachy-feeling\)\}/);
  assert.match(css,/body\[data-status=waiting\]:not\(\[data-kind=question\]\)\{--xlink:var\(--c-bisque\)\}/);
  assert.doesNotMatch(css,/--xlink-lift/);
  assert.match(css,/#rdWords a\[href\^="http"\]:not\(\.mtap\):not\(\.mcard a\)\{color:var\(--c-peachy-feeling\);/);
});
