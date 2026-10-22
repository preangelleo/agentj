// P472: replay the hash-verified public P469 baseline and candidate in isolated local paired pages.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const base='e8c22f9b0',out='reports/qa/p472',combined=JSON.parse(readFileSync('web/public/version.json')).combined;
const baseline=JSON.parse(readFileSync(out+'/live-baseline.json'));assert.ok(baseline.rows.length===2&&baseline.rows.every(r=>r.matchesBase));
mkdirSync(out+'/screens',{recursive:true});
const temp=mkdtempSync(join(tmpdir(),'p472-baseline-'));execFileSync('tar',['-x','-C',temp],{input:execFileSync('git',['archive',base,'web/public'],{maxBuffer:100*1024*1024})});
const beforeWeb=await startWebServer({publicDir:join(temp,'web/public')}),web=await startWebServer(),fake=await startFakeHost(),wk=await pw.webkit.launch(),ch=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
const rows=[],failures=[];
try{for(const device of ['safari','desktop'])for(const theme of ['light','dark']){
 const images=new Map();
 for(const phase of ['before','after']){
 const ctx=await(device==='safari'?wk:ch).newContext({...(device==='safari'?pw.devices['iPhone 13']:{}),viewport:{width:device==='safari'?390:1280,height:844},deviceScaleFactor:1,colorScheme:theme,serviceWorkers:'block'});
 await ctx.addInitScript(()=>{window.Audio=undefined;Object.defineProperty(navigator,'standalone',{get:()=>false})});
 const p=await ctx.newPage();console.log('START',device,theme,phase);const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/*',async r=>{
  const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();
  if(u.pathname.startsWith('/.aj/'))return r.continue();
  return r.continue();
 });
 try{
 fake.reset();fake.setUp(true);fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:'en',theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.remotes({limit:10,items:Array.from({length:10},(_,i)=>({id:'remote'+i,name:'Remote '+(i+1),paired_at:1700000000-i,online:i===0,me:i===0,source:'phone'}))});
 fake.st.onApp=async(c,m,send)=>{if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards:[{id:'todo',name:{en:'To-do',zh:'待办'},on:true,official:true,accent:'windstorm'}]});return true;}if(m.t==='jcard_get'){await send({t:'jcard_get',r:m.r,ok:true,id:m.id,revision:'p472',template:'checklist',items:[{id:'t1',text:'Prepare project notes for the next discussion',owner:'agent',status:'pending',note:'Review details with the owner',acts:[]},{id:'t2',text:'Completed item',owner:'owner',status:'done',done:true}],todo:{counts:{all:2,agent:1,owner:1}}});return true;}return false;};
 await p.goto(fake.newPairing((phase==='before'?beforeWeb:web).url+'?lang=en'));try{await p.waitForFunction(()=>['awaiting-approval','error'].includes(window.__ajState));if(await p.evaluate(()=>window.__ajState==='error'))throw new Error('fixture startup');}catch(e){console.log('PAIR DIAG',JSON.stringify(await p.evaluate(()=>({state:window.__ajState,view:document.body.dataset.view,error:document.getElementById('error-text')?.textContent}))),errors);throw e;}await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await fake.addTurn({k:'phone',text:'Review the project notes'},'Project notes are ready.\n\n- Review the checklist\n- Prepare the next discussion','done');
 await p.evaluate(theme=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=theme;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)},theme);
 const shoot=async(view)=>{
  await p.evaluate(async()=>{document.getElementById('toast').classList.remove('on');await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})))});await p.mouse.move(1,400);await p.waitForTimeout(180);
  const key=`${device}-${theme}-${view}`,path=`screens/${key}-${phase}.png`;await p.screenshot({path:out+'/'+path});
  const raw=execFileSync('magick',[out+'/'+path,'-depth','8','rgb:-'],{maxBuffer:8*1024*1024});
  if(phase==='before')images.set(key,{raw,path});else{
   const prev=images.get(key);assert.equal(raw.length,prev.raw.length);let changed=0,minX=1e9,minY=1e9,maxX=-1,maxY=-1;const width=device==='safari'?390:1280;
   for(let i=0;i<raw.length;i+=3)if(raw[i]!==prev.raw[i]||raw[i+1]!==prev.raw[i+1]||raw[i+2]!==prev.raw[i+2]){changed++;const n=i/3,x=n%width,y=Math.floor(n/width);minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y)}
   const row={key,device,theme,view,before:prev.path,after:path,changedPixels:changed,bounds:changed?{minX,minY,maxX,maxY}:null};rows.push(row);if(changed)failures.push(row);
  }
 };
 for(const state of ['idle','working','waiting','question','offline']){
  await fake.send({t:'status',s:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':state==='waiting'?'permission':undefined,agent:'claude',name:'Agent J'});
  await p.waitForFunction(s=>document.getElementById('orb').dataset.v===s,state);await shoot('home-'+state);
 }
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await p.waitForFunction(()=>document.getElementById('orb').dataset.v==='idle');
 await p.evaluate(async()=>{document.getElementById('input').value='Draft for the next discussion';(await import('./js/fsedit.js')).open();document.activeElement?.blur()});await shoot('editor');
 await p.evaluate(async()=>{(await import('./js/fsedit.js')).close();document.getElementById('rd').hidden=false;document.body.dataset.reader='1';document.querySelector('#rd .words').textContent='Project notes are ready.\n\nReview the checklist and prepare the next discussion.'});await shoot('reader');
 await p.evaluate(async()=>{document.getElementById('rd').hidden=true;delete document.body.dataset.reader;await(await import('./js/jcards.js')).refresh();await(await import('./js/five-pages.js')).openTarget('todo')});await p.locator('.jc-body[data-card=todo]').waitFor();await shoot('todo');
 await p.evaluate(async()=>{await(await import('./js/five-pages.js')).openTarget('chat')});await p.locator('#setBtn').click();await shoot('settings');
 await p.locator('#set-remotes-open').click();await p.waitForFunction(()=>document.querySelectorAll('#rm-list .rmitem').length===10);await shoot('remotes');
 }finally{await ctx.close()}
 }
}}finally{await wk.close();await ch.close();await fake.stop();await web.stop();await beforeWeb.stop();rmSync(temp,{recursive:true,force:true})}
writeFileSync(out+'/comparison.json',JSON.stringify({base,combined,model:'Local replay of hash-verified public P469 bytes; Safari=desktop WebKit iPhone 390, desktop=Chromium 1280; no physical system chrome claim',rows,failures},null,2)+'\n');
let html='<!doctype html><meta charset="utf-8"><title>P472 / P469 screenshot comparison</title><style>body{font:16px system-ui;background:#faf9f5;color:#2c2b26;margin:24px}article{display:grid;grid-template-columns:1fr 1fr;gap:16px}img{width:100%;border:1px solid #999}h2{margin-top:40px}</style><h1>P469 live baseline → P472 candidate</h1><p>Public manifest and screenshot dependencies verified against both live domains. Isolated fixture replay, same content, English, light/dark. iPhone Safari: WebKit 390px; desktop: Chromium 1280px. These are page screenshots, not physical iPhone system UI.</p><p><a href="../p461/index.html">P461 notice corpus comparisons</a> · <a href="comparison.json">Pixel results</a> · <a href="live-baseline.json">Live source verification</a></p>';
for(const row of rows)html+=`<h2>${row.key} · changed pixels: ${row.changedPixels}</h2><article><div>P469<img loading="lazy" src="${row.before}"></div><div>P472<img loading="lazy" src="${row.after}"></div></article>`;
writeFileSync(out+'/index.html',html);console.log(JSON.stringify({pairs:rows.length,failures},null,2));assert.deepEqual(failures,[],'non-notification main surfaces must remain pixel-identical');
