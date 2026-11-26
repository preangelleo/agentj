// P424 #127 (static): external reply and reader links use the approved foreground (0.30.78).
// Keep the strict palette guard against mixing a new link colour.
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
test('reply and reader links use the approved Lotion foreground and an underline',()=>{
  const css=sheets.find(([f])=>f==='app.css')[1];
  const expected=[
    ['.rm #words a:not(.mtap):not(.mcard a)','--lotion'],
    ['.rd #rdWords a:not(.mtap):not(.mcard a)','--dlg-fg'],
  ];
  for(const [selector,foreground] of expected){
    const matches=rules(css).filter(rule=>rule.sel===selector);
    assert.equal(matches.length,1,selector);
    const properties=matches[0].body.split(';').map(declaration=>declaration.trim());
    assert.ok(properties.includes('color:var('+foreground+')'),selector);
    assert.ok(properties.includes('text-decoration-line:underline'),selector);
  }
  assert.match(css,/--lotion:\s*var\(--c-lotion\)/);
  assert.match(css,/--dlg-fg:\s*var\(--c-lotion\)/);
  assert.match(palette,/--c-lotion:\s*#faf9f5;/i);
});
