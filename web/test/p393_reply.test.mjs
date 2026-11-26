// #126 / #127: static contract of the reply pills' centring and the external-link colour (the browser proof is p393_reply.mjs).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
test('the one-tap answer centres its label and wraps inside the card',()=>{
  assert.match(css,/\.words \.rbtn\{display:inline-flex;align-items:center;justify-content:center;/);
  assert.match(css,/\.words \.rbtn\{max-width:100%;box-sizing:border-box;white-space:normal;overflow-wrap:anywhere\}/);
});
test('a reply or reader link uses its surface foreground and remains underlined',()=>{
  assert.match(css,/\.rm #words a:not\(\.mtap\):not\(\.mcard a\)\{color:var\(--lotion\);text-decoration-line:underline\}/);
  assert.match(css,/\.rd #rdWords a:not\(\.mtap\):not\(\.mcard a\)\{color:var\(--dlg-fg\);text-decoration-line:underline\}/);
  for(const id of ['words','rdWords']){
    const rule='#'+id+' a[href^="http"]:not(.mtap):not(.mcard a){font-weight:600;text-decoration-color:currentColor;text-decoration-thickness:1.5px}';
    assert.ok(css.includes(rule), 'external destinations keep the heavier underline in '+id);
  }
});
