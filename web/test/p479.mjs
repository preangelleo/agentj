// P479: immutable live baseline versus candidate, offline encrypted fake host; no real sudo.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {generateKeyPairSync} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const fixed=new Date('2026-10-22T09:43:00Z').getTime();Date.now=()=>fixed;
const base='d6545fb98',out=process.env.P479_SMOKE?'reports/qa/p479/smoke':'reports/qa/p479',rows=[],failures=[];
mkdirSync(out+'/screens',{recursive:true});
const temp=mkdtempSync(join(tmpdir(),'p479-base-'));
execFileSync('tar',['-x','-C',temp],{input:execFileSync('git',['archive',base,'web/public'],{maxBuffer:100*1024*1024})});
const before=await startWebServer({publicDir:join(temp,'web/public')}),after=await startWebServer(),fake=await startFakeHost(),browser=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-gpu','--disable-lcd-text','--disable-partial-raster']});
const raw=path=>execFileSync('magick',[path,'-depth','8','rgb:-'],{maxBuffer:12*1024*1024});
const epk=generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x;
try{for(const width of [390,1280])for(const theme of ['light','dark'])for(const lang of ['zh','en']){
 if(process.env.P479_SMOKE && (width!==390 || theme!=='light' || lang!=='zh'))continue;
 const images=new Map();
 for(const phase of ['before','after']){
 const ctx=await browser.newContext({viewport:{width,height:844},deviceScaleFactor:1,colorScheme:theme,serviceWorkers:'block'}),p=await ctx.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 await ctx.addInitScript(fixed=>{const NativeDate=Date;window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[fixed]))}static now(){return fixed}}},fixed);
 try{
 fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 await p.route('**/*',r=>['127.0.0.1','localhost'].includes(new URL(r.request().url()).hostname)||/^(blob:|data:)/.test(r.request().url())?r.continue():r.abort());
 await p.goto(fake.newPairing((phase==='before'?before:after).url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await fake.addTurn({k:'phone',text:'Check shutdown schedule'},'Shutdown has been scheduled.\n\n'+('Review the command output below.\n\n'.repeat(15)),'done');
 await p.waitForFunction(()=>document.getElementById('pg').textContent==='1 / 1');
 await p.evaluate(theme=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme=theme;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)},theme);
 const snap=async(view,state='idle',equal=false)=>{
  await p.evaluate(async()=>{document.getElementById('toast').classList.remove('on');await document.fonts.ready});await p.mouse.move(1,400);
  // Force a complete repaint after dynamic insertion/removal. Preserve every native scroller.
  await p.evaluate(()=>{const scrolls=[...document.querySelectorAll('*')].filter(n=>n.scrollTop||n.scrollLeft).map(n=>[n,n.scrollTop,n.scrollLeft]);document.body.style.display='none';void document.body.offsetHeight;document.body.style.removeProperty('display');void document.body.offsetHeight;for(const [n,y,x]of scrolls){n.scrollTop=y;n.scrollLeft=x}});await p.waitForTimeout(100);
  const key=`${width}-${theme}-${lang}-${state}-${view}`,path=`screens/${key}-${phase}.png`;await p.screenshot({path:out+'/'+path});
  if(phase==='before')images.set(key,{path,raw:raw(out+'/'+path)});else{const prev=images.get(key),b=raw(out+'/'+path);let changedPixels=0;for(let i=0;i<b.length;i+=3)if(b[i]!==prev.raw[i]||b[i+1]!==prev.raw[i+1]||b[i+2]!==prev.raw[i+2])changedPixels++;
   rows.push({width,theme,lang,state,view,equal,before:prev.path,after:path,changedPixels});if(equal&&changedPixels)failures.push({key,changedPixels});}
 };
 const geometry=async(view)=>{
  const g=await p.evaluate(()=>{const rect=n=>{const r=n.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height}};const b=document.getElementById('elev-run'),o=document.getElementById('elev-run-out'),panel=document.getElementById('elev-run-panel'),note=document.getElementById('elev-run-note'),row=b.querySelector('.elev-run-row'),slot=document.getElementById('reply-notices');const cs=getComputedStyle(o);return {bar:rect(b),out:rect(o),panel:rect(panel),note:rect(note),row:rect(row),slot:rect(slot),footer:rect(document.getElementById('rmbar')),line:parseFloat(cs.lineHeight),scroll:o.scrollHeight,client:o.clientHeight,pad:parseFloat(cs.paddingTop)+parseFloat(cs.paddingBottom),hidden:panel.hidden,text:document.getElementById('elev-run-text').textContent}});
  rows.push({phase,width,theme,lang,view,geometry:g});
  if(phase==='after'){
   assert.ok(g.bar.bottom<=g.footer.top+1,JSON.stringify(g));assert.ok(g.bar.top>=g.slot.top-1,JSON.stringify(g));
   if(g.hidden)assert.equal(g.panel.height,0,'folded panel reserves no output height');
   if(!g.hidden){assert.ok(g.out.height>=g.line+g.pad-1,JSON.stringify(g));assert.ok(g.out.top>=g.panel.top-1,JSON.stringify(g));assert.ok(g.note.bottom<=g.row.top+1,JSON.stringify(g));assert.ok(g.out.bottom<=g.panel.bottom+1,JSON.stringify(g));assert.ok(g.slot.bottom<=g.footer.top+1,JSON.stringify(g));
    if(view==='multi')assert.ok(g.out.height>=g.line*3+g.pad-1,JSON.stringify(g));
    if(view==='long'){assert.ok(g.scroll>g.client,JSON.stringify(g));const scroll=await p.locator('#elev-run-out').evaluate(n=>{n.scrollTop=0;const first=n.scrollTop;n.scrollTop=n.scrollHeight;return {first,last:n.scrollTop}});assert.equal(scroll.first,0);assert.ok(scroll.last>0);}
   }
  }
 };
 for(const state of ['idle','working','waiting','question','offline']){
  await fake.send({t:'status',s:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':state==='waiting'?'permission':undefined,agent:'claude',name:'Agent J'});await p.waitForFunction(s=>document.getElementById('orb').dataset.v===s,state);
  await snap('chat',state,true);
  const id=String(['idle','working','waiting','question','offline'].indexOf(state)+1).repeat(32);
  await fake.send({t:'elev',id,kind:'sudo',n:'b'.repeat(32),epk,ttl:120,tries:3,cmd:'shutdown -h +1',why:'Schedule shutdown',effect:'Shutdown in one minute',state:'running',phase:'started',elapsed:8,timeout:20});
  await p.waitForFunction(()=>document.getElementById('elev-run')?.hidden===false);await geometry('collapsed');await snap('collapsed',state);
  await p.locator('#elev-run-main').click();
  for(const [view,text] of [['single','Shutdown at Thu Oct 22 18:44:09'],['multi','Shutdown scheduled\nSave your work\nShutdown at Thu Oct 22 18:44:09'],['long','Shutdown at Thu Oct 22 18:44:09\n'+('Safe synthetic output\n'.repeat(60))]]){
   await fake.send({t:'elev_out',id,seq:1,text,full:true});await p.waitForFunction(text=>document.getElementById('elev-run-out').textContent===text,text);await geometry(view);await snap(view,state);
  }
  // Existing two-tap Stop contract, including signing, is tested by p406-sudo-phone.
  await p.locator('#elev-run-main').click();await geometry('folded');await snap('folded',state);await p.locator('#elev-run-main').click();
  await fake.send({t:'elev_done',id,result:'done',code:state==='working'?7:0,tail:'Shutdown at Thu Oct 22 18:44:09\n'});await p.waitForFunction(()=>document.getElementById('elev-run').dataset.state==='done');await geometry('done');
  if(phase==='after')assert.match(await p.locator('#elev-run-text').textContent(),state==='working'?/7/:/0/);
  await snap('done',state);await p.locator('#elev-run-x').click();
 }
 await p.evaluate(async()=>{document.getElementById('input').value='Draft';(await import('./js/fsedit.js')).open();document.activeElement?.blur()});await snap('editor','idle',true);
 await p.evaluate(async()=>{(await import('./js/fsedit.js')).close();document.getElementById('rd').hidden=false;document.body.dataset.reader='1';document.querySelector('#rd .words').textContent='Reader stays unchanged'});await snap('reader','idle',true);
 await p.evaluate(()=>{document.getElementById('rd').hidden=true;delete document.body.dataset.reader});await p.locator('#setBtn').click();await snap('settings','idle',true);
 assert.deepEqual(errors,[]);
 }finally{await ctx.close()}
 console.log(width,theme,lang,phase,'PASS');
 }
}}finally{await browser.close();await fake.stop();await before.stop();await after.stop();rmSync(temp,{recursive:true,force:true})}
writeFileSync(out+'/comparison.json',JSON.stringify({base,combined:JSON.parse(readFileSync('web/public/version.json')).combined,rows,failures},null,2)+'\n');assert.deepEqual(failures,[]);
writeFileSync(out+'/index.html','<!doctype html><meta charset="utf-8"><title>P479</title><style>body{background:#faf9f5;font:16px system-ui}article{display:flex;gap:12px}img{width:48%;object-fit:contain}</style><h1>P479 baseline / candidate</h1>'+rows.filter(r=>r.before).map(r=>`<h2>${r.width} ${r.theme} ${r.lang} ${r.state} ${r.view} (${r.changedPixels} pixels)</h2><article><img loading="lazy" src="${r.before}"><img loading="lazy" src="${r.after}"></article>`).join(''));
