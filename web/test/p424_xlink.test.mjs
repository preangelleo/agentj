// P424 #127 (static): the reply card's external-link colour comes only from the closed palette — no color-mix, no lifted
// hue. Every rule that colours an <a> takes a palette token (or inherits); --xlink is Peachy Feeling or Bisque, both of
// which brand/palette.css (the build copy of the Leo palette) defines as ready-made hex values.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
const pub=new URL('../public/',import.meta.url);
const sheets=readdirSync(pub).filter(f=>f.endsWith('.css')).map(f=>[f,readFileSync(new URL(f,pub),'utf8').replace(/\/\*[\s\S]*?\*\//g,'')]);
const palette=readFileSync(new URL('brand/palette.css',pub),'utf8');
const rules=css=>[...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m=>({sel:m[1].trim(),body:m[2]}));
const anchor=/(^|[\s,>+~(])a(\[[^\]]*\]|:[a-z-]+(\([^)]*\))?)*\s*($|,|:not|\s)/;
test('no link colour is mixed: rules that colour an <a> never use color-mix',()=>{
  const bad=[];
  for(const [f,css] of sheets)for(const r of rules(css)){
    if(!r.sel.split(',').some(s=>anchor.test(s.trim())))continue;
    const m=/(^|;)\s*(color|text-decoration-color)\s*:\s*([^;]+)/g;let x;
    while((x=m.exec(r.body)))if(/color-mix/.test(x[3]))bad.push(`${f}: ${r.sel} → ${x[2]}:${x[3]}`);
  }
  assert.deepEqual(bad,[],'link colours must be palette colours (P424 #127)');
});
test('--xlink only ever names Peachy Feeling or Bisque, both ready-made palette hex values',()=>{
  const css=sheets.find(([f])=>f==='app.css')[1];
  const vals=[...css.matchAll(/--xlink\s*:\s*([^;}]+)/g)].map(m=>m[1].trim());
  assert.ok(vals.length>=4,'light, dark (media + attribute) and the waiting card');
  for(const v of vals)assert.match(v,/^var\(--c-(peachy-feeling|bisque)\)$/,v);
  assert.match(palette,/--c-peachy-feeling:\s*#d97757;/i);
  assert.match(palette,/--c-bisque:\s*#fee2c5;/i);
  assert.match(css,/#words a\[href\^="http"\]:not\(\.mtap\):not\(\.mcard a\)\{color:var\(--xlink\);/);
});
