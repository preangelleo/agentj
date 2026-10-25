// P518 #280: real selected text, shared colour audit and cropped screenshot baselines.
// node web/test/p518.mjs [--update] (baseline changes require visual approval).
// AJ_P518_OUT writes exactly four full-page review shots; all data/providers are local fixtures.
import assert from 'node:assert/strict';
import {mkdirSync,readFileSync,writeFileSync,mkdtempSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
const update=process.argv.includes('--update');
const baselines=new URL('./fixtures/p518-selection/',import.meta.url);
const out=process.env.AJ_P518_OUT || mkdtempSync(join(tmpdir(),'p518-selection-'));
mkdirSync(out,{recursive:true});if(update)mkdirSync(baselines,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),results=[];
const expected={background:'rgb(199, 222, 220)',color:'rgb(20, 20, 20)'};
try {
 for(const scheme of ['light','dark']) {
  fake.reset();const p=await newPage(B,1024,768,scheme,{allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'preferences',value:{appearance:{theme:scheme,language:'zh'}}});
   await fake.send({t:'status',s:'idle',agent:'claude',name:'P518 视觉审阅'});
   await waitFor(p,`document.documentElement.dataset.theme===${JSON.stringify(scheme)}`);
   await evaluate(p,`(async()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();await document.fonts.ready;
    const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);
    const e=document.getElementById('input');e.value='统一选中色 Peek-a-Blue，文字清晰可读。';e.dispatchEvent(new Event('input'));
   })()`);
   // Audit every mounted control, including hidden settings / contact / pairing fields,
   // and later-mounted card/contenteditable descendants (without using real owner data).
   const audit=await evaluate(p,`(()=>{const box=document.createElement('div');box.className='jc-dialog';
    box.innerHTML='<textarea aria-label="P518 card"></textarea><input type="search" aria-label="P518 search"><div class="fp-prose" contenteditable="true"><b>P518 nested editable</b></div>';document.body.append(box);
    const rows=[...document.querySelectorAll('input,textarea,[contenteditable], [contenteditable] b, #words, #rd, .fp-prose')].map(e=>{const s=getComputedStyle(e,'::selection');return {id:e.id||e.getAttribute('aria-label')||e.className||e.tagName,background:s.backgroundColor,color:s.color};});box.remove();return rows;})()`);
   for(const row of audit)assert.deepEqual({background:row.background,color:row.color},expected,`${scheme}: ${row.id}`);
   for(const mode of ['input','fullscreen']) {
    if(mode==='fullscreen')await evaluate(p,`import('./js/fsedit.js').then(f=>f.open())`);
    const selector=mode==='input'?'#input':'#fsText';
    await waitFor(p,`!document.querySelector(${JSON.stringify(selector)}).disabled`);
    const geo=await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.focus();const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {x:r.x+parseFloat(s.paddingLeft)+2,y:r.y+parseFloat(s.paddingTop)+parseFloat(s.lineHeight)/2};})()`);
    // A native mouse drag must select actual characters, then fix the range for repeatable screenshots.
    await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:geo.x,y:geo.y,button:'left',clickCount:1});
    await p.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:geo.x+155,y:geo.y,button:'left',buttons:1});
    await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:geo.x+155,y:geo.y,button:'left',clickCount:1});
    assert.ok(await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(selector)});return e.selectionEnd>e.selectionStart;})()`),`${scheme} ${mode}: mouse selects text`);
    await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.setSelectionRange(0,19);})()`);await sleep(250);
    const name=`${mode}-${scheme}`,path=join(out,name+'.png');writeFileSync(path,await shoot(p,''));
    // Only the first text line is compared, excluding clocks / water / status animations.
    const clip=await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect(),s=getComputedStyle(e);return {x:Math.floor(r.x+parseFloat(s.paddingLeft)),y:Math.floor(r.y+parseFloat(s.paddingTop)),width:420,height:Math.ceil(parseFloat(s.lineHeight)),scale:1};})()`);
    const selected=Buffer.from((await p.send('Page.captureScreenshot',{format:'png',clip})).data,'base64');
    const baseline=new URL(name+'.png',baselines);
    if(update)writeFileSync(baseline,selected);else assert.ok(selected.equals(readFileSync(baseline)),`${name}: selection screenshot baseline (use --update only after review)`);
    // Positive rendering control: selected and unselected pixels must differ.
    await evaluate(p,`document.querySelector(${JSON.stringify(selector)}).setSelectionRange(0,0)`);
    const plain=Buffer.from((await p.send('Page.captureScreenshot',{format:'png',clip})).data,'base64');
    assert.ok(!selected.equals(plain),`${name}: actual highlighted pixels, not just computed CSS`);
    results.push({name,path,theme:await evaluate(p,'document.documentElement.dataset.theme'),selection:expected,mouse:true,baseline:update?'provisional updated':'matched',audit});
   }
   assert.deepEqual(p.problems,[],'no browser errors');assert.deepEqual(p.offsite,[],'local fixtures only');
  } finally {await p.dispose();}
 }
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(join(out,'results.json'),JSON.stringify({ok:true,update,scope:'Desktop Chromium mouse selection; no physical mobile claim',results},null,2)+'\n');
console.log(`P518 PASS: four selected-state shots/baselines; ${results[0].audit.length} controls/cards audited per theme; ${out}`);
