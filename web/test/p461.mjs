// P461: all audited A texts through the original/current component, 390px strips + real encrypted send.
import assert from 'node:assert/strict';
import {mkdirSync,readFileSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitState,waitFor,shoot,sleep} from './browser.mjs';
const out='reports/qa/p461';mkdirSync(out,{recursive:true});
const rows=JSON.parse(readFileSync(out+'/inventory.json','utf8'));
const cases=rows.flatMap(r=>r.samples.map(s=>({...s,id:r.id,site:r.site,glance:r.glance})));
const temp=mkdtempSync(join(tmpdir(),'p461-before-'));
cpSync(PUBLIC_DIR,temp,{recursive:true});
for(const file of ['app.css','menu.css','js/ui.js','js/copycode.js','i18n.js'])writeFileSync(join(temp,file),execFileSync('git',['show','e96cc19ae:web/public/'+file]));
const before=await startWebServer({publicDir:temp}),after=await startWebServer(),fake=await startFakeHost(),B=await launch();
const results=[];let checks=0;const check=(v,m)=>{assert.ok(v,m);checks++;};
const q=JSON.stringify;
try{for(const theme of ['light','dark'])for(const lang of ['zh','en'])for(const [phase,web] of [['before',before],['after',after]]){
 const tag=`${lang}-${theme}-${phase}`,p=await newPage(B,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
 try{
  fake.reset();fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=${q(theme)};`);
  await fake.addTurn({k:'phone',text:'P461 local send check'},'Choose: [Send](reply:Fixture answer)');
  await waitFor(p,`document.querySelector('#words .rbtn')`);
  await evaluate(p,`document.querySelector('#words .rbtn').click()`);
  await waitFor(p,`document.getElementById('toast').classList.contains('on')`);
  check(fake.st.says.some(m=>m.text==='Fixture answer'),tag+' actual Noise say received');
  const sent=await evaluate(p,`(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect();return {text:n.textContent,shape:n.classList.contains('circle')?'circle':n.classList.contains('square')?'square':'rect',cx:r.x+r.width/2,cy:r.y+r.height/2};})()`);
  if(phase==='after'){check(sent.text===(lang==='zh'?'已发送':'Sent'),tag+' single sent status');check(sent.shape==='circle',tag+' sent circle');check(Math.abs(sent.cx-195)<2&&Math.abs(sent.cy-422)<2,tag+' sent centred');}
  writeFileSync(`${out}/${tag}-send.png`,await shoot(p));
  await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,3000);
  // Exercise the current renderer on the actual reader and settings surfaces, preserving ownership and exact text.
  if(phase==='after'){
   const probes=await evaluate(p,`(async()=>{const u=await import('./js/ui.js');let out=[];for(const surface of ['body','settings','rd']){const page=surface==='body'?null:document.getElementById(surface);if(page)page.hidden=false;const n=u.notify({shape:'circle',text:'Keep this complete explanation for the owner.',ms:2200});const r=n.getBoundingClientRect();out.push({surface,parent:n.parentElement===document.body,shape:n.classList.contains('square'),text:n.textContent,w:r.width,h:r.height,cx:r.x+r.width/2,cy:r.y+r.height/2,sw:n.scrollWidth,cw:n.clientWidth,pe:getComputedStyle(n).pointerEvents,bg:getComputedStyle(n).backgroundColor,fg:getComputedStyle(n).color});if(page)page.hidden=true;}u.notify({shape:'circle',text:'x'.repeat(80)+' '+ 'details '.repeat(200),ms:2200});out.push({long:true,over:document.getElementById('toast').scrollWidth<=document.getElementById('toast').clientWidth});return out;})()`);
   for(const g of probes){if(g.long){check(g.over,tag+' long unbroken text wraps');continue;}check(g.parent&&g.shape&&Math.abs(g.cx-195)<2&&Math.abs(g.cy-422)<2&&g.w<=350&&g.sw<=g.cw,tag+' square centred '+JSON.stringify(g));check(g.pe==='none',tag+' ordinary square does not intercept underlying controls');check(g.text==='Keep this complete explanation for the owner.',tag+' no truncation');}
   await evaluate(p,`import('./js/ui.js').then(u=>u.notify({text:'A complete reason, without truncation.',shape:'circle',ms:1800}))`);
   await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,2400);
   check(true,tag+' original auto dismissal');
  }
  // A 390px-wide component strip per case. Each row retains the real renderer/classes/tokens; only its coordinate
  // origin moves into a labelled 390px cell so every baseline/current call fits a reviewable contact sheet.
  await evaluate(p,`(async()=>{document.body.replaceChildren(Object.assign(document.createElement('div'),{id:'toast',className:'toast'}));document.body.style.cssText='display:block;overflow:auto;height:auto;min-height:0;margin:0';document.documentElement.style.cssText='overflow:auto;height:auto';window.U=await import('./js/ui.js');window.sourceToast=document.getElementById('toast');sourceToast.style.display='none';})()`);
  for(let at=0;at<cases.length;at+=16){
   const chunk=cases.slice(at,at+16).map(c=>({id:c.id,key:c.key,text:c[phase][lang].replace(/\{\w+\}/g,'2'),glance:c.glance}));
   const facts=await evaluate(p,`(()=>{document.querySelectorAll('.p461-cell').forEach(n=>n.remove());const facts=[];for(const c of ${q(chunk)}){const n=U.notify({kind:'info',shape:'circle',text:c.text,glance:c.glance,ms:2200});clearTimeout(n._t);const cell=document.createElement('section');cell.className='p461-cell';cell.style.cssText='position:relative;width:390px;height:360px;box-sizing:border-box;border-bottom:1px solid var(--ink);overflow:hidden;background:var(--pg)';const label=document.createElement('div');label.textContent=c.id+' / '+c.key;label.style.cssText='padding:8px 12px;font:12px sans-serif;color:var(--ink);overflow-wrap:anywhere';cell.append(label);const copy=n.cloneNode(true);copy.id='';copy.style.removeProperty('display');copy.style.transition='none';copy.style.position='absolute';if(copy.classList.contains('rect')){copy.style.cssText+=';left:20px;top:auto;bottom:12px;width:350px';}else{copy.style.left='50%';copy.style.top='50%';}cell.append(copy);document.body.append(cell);const b=copy.querySelector('.nt-body');facts.push({id:c.id,key:c.key,text:b.textContent,shape:copy.classList.contains('circle')?'circle':copy.classList.contains('square')?'square':'rect',sw:copy.scrollWidth,cw:copy.clientWidth});}return facts;})()`);
   if(phase==='after')for(let i=0;i<facts.length;i++){const f=facts[i],c=chunk[i];check(f.text===c.text.replaceAll('`',''),tag+' full corpus text '+f.id+'/'+f.key);check(f.shape==='circle'||f.shape==='square',tag+' A placement '+f.id);check(f.sw<=f.cw+1,tag+' no horizontal clipping '+f.id);}
   const file=`${tag}-${String(at/16+1).padStart(2,'0')}.png`;
   writeFileSync(out+'/'+file,await shoot(p,null,{full:true}));
   results.push({tag,file,cases:chunk.map(c=>({id:c.id,key:c.key})),facts});
  }
  console.log('PASS',tag,cases.length,'cases');
 }finally{await p.dispose();}
}}finally{await B.close();await fake.stop();await before.stop();await after.stop();rmSync(temp,{recursive:true,force:true});}
writeFileSync(out+'/results.json',JSON.stringify({combined:JSON.parse(readFileSync('web/public/version.json','utf8')).combined,checks,callSites:rows.length,cases:cases.length,scope:'390px isolated Chromium; contact strips replay real component, send uses paired fake host; no physical phone claim',results},null,2));
let html='<!doctype html><meta charset="utf-8"><title>P461 before / after</title><style>body{font:16px sans-serif;background:#faf9f5;color:#252521}article{display:grid;grid-template-columns:390px 390px;gap:16px}img{width:390px}h2{position:sticky;top:0;background:#faf9f5;padding:8px}</style><h1>P461 · 390px before / after</h1><p>Each labelled row is one audited call/key, replayed through the original/current renderer. Send screenshots use the real local encrypted send path.</p>';
for(const theme of ['light','dark'])for(const lang of ['zh','en']){html+=`<h2>${lang} / ${theme} — Before | After</h2><article><img src="${lang}-${theme}-before-send.png"><img src="${lang}-${theme}-after-send.png"></article>`;for(let i=1;i<=Math.ceil(cases.length/16);i++){const n=String(i).padStart(2,'0');html+=`<article><img loading="lazy" src="${lang}-${theme}-before-${n}.png"><img loading="lazy" src="${lang}-${theme}-after-${n}.png"></article>`;}}
writeFileSync(out+'/index.html',html);console.log('P461',checks,'checks PASS');
