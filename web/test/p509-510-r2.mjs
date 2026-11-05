// Real client + local encrypted fakehost, isolated Chromium. Colour-state simulation
// retains a visible approval even for offline: it checks CSS, not disconnected UX.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
const OUT='reports/qa/509-510-r2/web';mkdirSync(OUT,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),rows=[];
const beforeCSS=execFileSync('git',['show','bc29f63841:web/public/app.css'],{encoding:'utf8'});
const afterCSS=execFileSync('cat',['web/public/app.css'],{encoding:'utf8'});
const styles=`(()=>{const ids=['sheet','rm','toast','cmd','apprAllow'];return Object.fromEntries(ids.map(id=>{const e=document.getElementById(id),s=getComputedStyle(e),r=e.getBoundingClientRect();return [id,{rect:{x:r.x,y:r.y,width:r.width,height:r.height},style:Object.fromEntries([...s].map(k=>[k,s.getPropertyValue(k)])),classes:e.className}]}));})()`;
async function toggle(p,old){await evaluate(p,`(()=>{const s=document.getElementById('r2-css')||Object.assign(document.createElement('style'),{id:'r2-css'});s.textContent=${JSON.stringify(old?beforeCSS:afterCSS)};document.head.append(s);})()`);await sleep(100);}
try{
for(const width of [390,1440])for(const theme of ['light','dark']){
 fake.reset();const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();{const now=Date.now();Date.now=()=>now;}`);
 await fake.send({t:'preferences',value:{appearance:{theme,language:'zh'}}});
 await fake.ask({t:'ask',id:(5090+width).toString(16).padStart(32,'0'),tool:'Bash',summary:'systemd-run --user --on-active=45 agentj service restart',cat:['service'],why:'重启 Agent J 服务',why_en:'Restart the Agent J service',ttl:600});
 await waitFor(p,`!document.getElementById('sheet').hidden`);
 await evaluate(p,`(()=>{document.getElementById('a2hs-ok')?.click();const s=document.createElement('style');document.head.append(s);s.sheet.insertRule('*,*::before,*::after{transition:none!important;animation:none!important}',0);})()`);
 for(const state of ['idle','working','waiting','question','offline']){
  await evaluate(p,`(()=>{document.body.dataset.status=${JSON.stringify(state==='question'?'waiting':state==='offline'?'unknown':state)};document.body.dataset.kind=${JSON.stringify(state==='question'?'question':'permission')};document.body.dataset.conn='on';})()`);await sleep(200);
  for(const kind of ['action','question']){
   // Both kinds use the same bottom sheet; the question fixture exercises its actual renderer below.
   if(kind==='question')continue;
   const label=`${state}-${theme}-${width}`;
   await toggle(p,true);const before=await evaluate(p,styles);writeFileSync(`${OUT}/${label}-before.png`,await shoot(p,''));
   await toggle(p,false);const after=await evaluate(p,styles);writeFileSync(`${OUT}/${label}-after.png`,await shoot(p,''));
   assert.equal(after.sheet.rect.width,before.sheet.rect.width,label+' same width');
   assert.equal(after.sheet.rect.x,before.sheet.rect.x,label+' same horizontal position');
   assert.ok(Math.abs(after.sheet.rect.height-before.sheet.rect.height)<=1,label+' only removed 1px border changes height');
   for(const id of ['sheet','rm','toast','cmd','apprAllow'])for(const k of Object.keys(before[id].style)){
    if(id==='sheet'&&(/^(background|border|outline|height|block-size|perspective-origin|transform-origin)/.test(k)))continue;
    if(id==='cmd'&&k==='background-color')continue;
    assert.equal(after[id].style[k],before[id].style[k],`${label} ${id} ${k} unchanged`);
   }
   assert.ok(after.sheet.classes.includes('aj-surface-outline'),label+' naturally receives outline');
   const colors=await evaluate(p,`(async()=>{const {rgba}=await import('./js/surface-outline.js');const s=getComputedStyle(document.getElementById('sheet'));return {fill:rgba(s.backgroundColor),page:rgba(getComputedStyle(document.body).backgroundColor),outline:s.outlineStyle,borders:['Top','Right','Bottom','Left'].map(k=>s['border'+k+'Width'])}})()`);
   assert.deepEqual(colors.fill,colors.page,label+' sheet same as composer painted page ground');
   assert.equal(colors.outline,'none');assert.deepEqual(colors.borders,['0px','0px','0px','0px']);
   rows.push({label,before:before.sheet,after:after.sheet,colors,noticeUnchanged:true,allOtherComputedStylesUnchanged:true});
  }
 }
 // A signed English note in Chinese appearance uses the service category; raw note remains in details.
 for(const language of ['zh','en']){
  await fake.send({t:'preferences',value:{appearance:{theme,language}}});
  const reason='Restart the Agent J service in 45 seconds',summary='systemd-run --user --on-active=45 agentj service restart\nAgent note:\n'+JSON.stringify({description:reason})+'\nRequest SHA-256: '+ 'a'.repeat(64);
  const id=(51000+width+(language==='en'?1:0)).toString(16).padStart(32,'0');
  await fake.ask({t:'ask',id,tool:'Bash',summary,cat:['service'],why:'重启 Agent J 服务',why_en:'Restart the Agent J service',reason,ttl:600});
  // Retire preceding card without approving anything.
  await fake.askDone((5090+width).toString(16).padStart(32,'0'),'deny');
  if(language==='en')await fake.askDone((51000+width).toString(16).padStart(32,'0'),'deny');
  await waitFor(p,`document.getElementById('cmdReason').textContent.includes(${JSON.stringify(language==='zh'?'重启 Agent J 服务':reason)})`);
  const note=await evaluate(p,`({short:document.getElementById('cmdReason').textContent,raw:document.getElementById('cmd').textContent,why:document.getElementById('why').textContent})`);
  assert.ok(note.raw.includes(reason),'raw signed note preserved');
  if(language==='zh')assert.ok(!note.short.includes(reason),'English not bare short copy');
  writeFileSync(`${OUT}/service-note-${language}-${theme}-${width}.png`,await shoot(p,''));
 }
 await fake.askDone((51000+width+1).toString(16).padStart(32,'0'),'deny');
 await fake.send({t:'question',id:(51900+width).toString(16).padStart(32,'0'),ttl:600,qs:[{q:'选择处理方式',h:'方式',m:false,o:[{l:'继续'},{l:'稍后'}]}]});
 await waitFor(p,`document.getElementById('sheetTitle').textContent.includes('选择处理方式')`);
 await toggle(p,true);const qBefore=await evaluate(p,styles);writeFileSync(`${OUT}/question-${theme}-${width}-before.png`,await shoot(p,''));
 await toggle(p,false);const qAfter=await evaluate(p,styles);writeFileSync(`${OUT}/question-${theme}-${width}-after.png`,await shoot(p,''));
 assert.equal(qAfter.sheet.rect.width,qBefore.sheet.rect.width,'question sheet width unchanged');
 const qc=await evaluate(p,`(async()=>{const {rgba}=await import('./js/surface-outline.js');const s=getComputedStyle(document.getElementById('sheet'));return {fill:rgba(s.backgroundColor),page:rgba(getComputedStyle(document.body).backgroundColor),outline:s.outlineStyle,border:s.borderBottomWidth}})()`);
 assert.deepEqual(qc.fill,qc.page);assert.equal(qc.outline,'none');assert.equal(qc.border,'0px');
 await p.dispose();
}
}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(`${OUT}/matrix.json`,JSON.stringify({sourceBase:'bc29f63841',model:'isolated desktop Chromium, 390x844 and 1440x900 CSS state simulation, no physical device',rows},null,2)+'\n');
console.log(`PASS ${rows.length} before/after pairs; five states × two themes × two widths; sheet has zero borders/outline and composer ground; non-sheet styles unchanged; Chinese/English signed-note UI checked`);

writeFileSync(`${OUT}/comparison.html`,'<!doctype html><meta charset="utf-8"><title>#509 r2 comparison</title><style>body{font:16px sans-serif;background:#faf9f5;color:#141414}section{margin:30px 0}img{width:min(46%,720px);vertical-align:top;margin:4px}</style><h1>#509 r2: before / after</h1>'+rows.map(r=>`<section><h2>${r.label}</h2><img src="${r.label}-before.png"><img src="${r.label}-after.png"></section>`).join(''));
