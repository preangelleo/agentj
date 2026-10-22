import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {demo,sections} from './p477-fixture.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const base=process.env.P477_BASE||'35f064643',out='reports/qa/p477',combined=JSON.parse(readFileSync('web/public/version.json')).combined;
mkdirSync(out+'/screens',{recursive:true});
const temp=mkdtempSync(join(tmpdir(),'p477-base-'));execFileSync('tar',['-x','-C',temp],{input:execFileSync('git',['archive',base,'web/public'],{maxBuffer:100*1024*1024})});
const beforeWeb=await startWebServer({publicDir:join(temp,'web/public')}),web=await startWebServer(),fake=await startFakeHost(),browser=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
const rows=[],audit=[],failures=[],snapshots=new Map();
function raw(path){return execFileSync('magick',[path,'-depth','8','rgb:-'],{maxBuffer:12*1024*1024})}
try{for(const width of (process.env.P477_OWNER_ONLY?[1280]:[390,1280]))for(const theme of (process.env.P477_OWNER_ONLY?['dark']:['light','dark']))for(const phase of ['before','after']){
 const ctx=await browser.newContext({viewport:{width,height:844},deviceScaleFactor:1,colorScheme:theme,serviceWorkers:'block'}),p=await ctx.newPage();const errors=[];p.on('pageerror',e=>errors.push(e.message));
 try{
 await p.addInitScript(()=>{window.__blobs=new Map();const create=URL.createObjectURL.bind(URL);URL.createObjectURL=b=>{const u=create(b);window.__blobs.set(u,b);return u}});
 fake.reset();fake.setUp(true);fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:'en',theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 await p.route('**/*',r=>['127.0.0.1','localhost'].includes(new URL(r.request().url()).hostname)||/^(blob:|data:)/.test(r.request().url())?r.continue():r.abort());
 await p.goto(fake.newPairing((phase==='before'?beforeWeb:web).url+'?lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await fake.addTurn({k:'phone',text:'Show eleven formats with synthetic data'},demo,'done');
 await p.waitForFunction(()=>document.querySelectorAll('#words .reply-component').length===6);
 await p.evaluate(theme=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme=theme;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)},theme);
 // Force every lazy code/diagram renderer to settle before colour assertions and screenshots.
 for(const [name]of sections){await p.locator('#words > p > strong').filter({hasText:sections.find(x=>x[0]===name)[1]}).first().evaluate(el=>el.scrollIntoView({block:'start'}));await p.waitForTimeout(200)}
 for(const el of await p.locator('#words pre[data-lang],#words .mermaid-src').all()){await el.scrollIntoViewIfNeeded();await p.waitForTimeout(500)}
 await p.waitForFunction(()=>document.querySelector('#words .diagram img')?.complete && document.querySelector('#words code.hljs'));
 const shoot=async(view,state='idle',equal=false)=>{
  await p.evaluate(async()=>{document.getElementById('toast').classList.remove('on');await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})))});await p.mouse.move(1,400);await p.waitForTimeout(200);
  const key=`${width}-${theme}-${state}-${view}`,path=`screens/${key}-${phase}${process.env.P477_OWNER_ONLY?'-recheck':''}.png`;await p.screenshot({path:out+'/'+path});
  if(phase==='before')snapshots.set(key,{raw:raw(out+'/'+path),path});else{
   const prev=snapshots.get(key),bytes=raw(out+'/'+path);assert.equal(bytes.length,prev.raw.length);let changedPixels=0;for(let i=0;i<bytes.length;i+=3)if(bytes[i]!==prev.raw[i]||bytes[i+1]!==prev.raw[i+1]||bytes[i+2]!==prev.raw[i+2])changedPixels++;
   const row={key,width,theme,state,view,before:prev.path,after:path,changedPixels,equal};rows.push(row);if(equal&&changedPixels)failures.push(row);
  }
 };
 const inspect=async(state)=>{
  const data=await p.evaluate(async()=>{
   const root=document.querySelector('.rm #words'),result=[];
   for(const el of root.querySelectorAll('*')){
    if((el.tagName==='text'&&el.textContent==='◀')||el.closest('button:not(.fp-link),.mcard,.mtap')||['STYLE','SCRIPT'].includes(el.tagName))continue;
    if(![...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()))continue;
    const s=getComputedStyle(el),selector=el.tagName.toLowerCase()+(el.classList.length?'.'+[...el.classList].join('.'):'');
    result.push({selector,text:el.textContent.slice(0,60),color:el.namespaceURI.includes('svg')?s.color:s.webkitTextFillColor,fill:el.namespaceURI.includes('svg')?s.fill:null});
    if(el.tagName==='LI')result.push({selector:selector+'::marker',text:'marker',color:getComputedStyle(el,'::marker').color,fill:null});
   }
   const img=root.querySelector('.diagram img'),svg=await window.__blobs.get(img.src).text();
   const shapes=[...root.querySelectorAll('.reply-component svg :is(rect,path,polyline,circle,line,polygon,ellipse),.reply-component svg text:not([text-anchor])')].map(el=>({tag:el.tagName,fill:getComputedStyle(el).fill,stroke:getComputedStyle(el).stroke}));
   const borders=[...root.querySelectorAll('blockquote,.reply-component,th,td,input,select,button')].map(el=>({tag:el.tagName,border:getComputedStyle(el).borderColor,background:getComputedStyle(el).backgroundColor,color:el.matches('input,select,button:not(.fp-link)')?getComputedStyle(el).color:null}));
   return {text:result,shapes,borders,svg};
  });
  const svgPage=await ctx.newPage();await svgPage.setContent(data.svg);
  data.text.push(...await svgPage.locator('text,tspan').evaluateAll(els=>els.map(el=>({selector:'mermaid '+el.tagName,text:el.textContent,color:getComputedStyle(el).color,fill:getComputedStyle(el).fill}))));await svgPage.close();delete data.svg;
  audit.push({phase,width,theme,state,...data});
  if(phase==='after'){
   const before=audit.find(r=>r.phase==='before'&&r.width===width&&r.theme===theme&&r.state===state);
   assert.deepEqual(data.shapes,before.shapes,'chart/flow shapes retain colours');assert.deepEqual(data.borders,before.borders,'borders/backgrounds/controls retain colours');
   for(const row of data.text)if(!/^rgba?\(250, 249, 245(?:, [\d.]+)?\)$/.test(row.fill||row.color))failures.push({width,theme,state,...row});
  }
 };
 for(const state of (process.env.P477_OWNER_ONLY?['offline']:['idle','working','waiting','question','offline'])){
  await fake.send({t:'status',s:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':state==='waiting'?'permission':undefined,agent:'claude',name:'Agent J'});await p.waitForFunction(s=>document.getElementById('orb').dataset.v===s,state);
  await inspect(state);
  if(!process.env.P477_OWNER_ONLY)for(const [name]of sections){await p.locator('#words > p > strong').filter({hasText:sections.find(x=>x[0]===name)[1]}).first().evaluate(el=>el.scrollIntoView({block:'start'}));await shoot(name,state)}
 }
 // Entire viewport comparisons for the excluded surfaces, including the owner card in the main scene.
 await p.evaluate(()=>document.getElementById('words').replaceChildren());await shoot('owner-only','offline',true);
 await p.evaluate(async()=>{document.getElementById('input').value='Draft';(await import('./js/fsedit.js')).open();document.activeElement?.blur()});await shoot('editor','idle',true);
 await p.evaluate(async()=>{(await import('./js/fsedit.js')).close();document.getElementById('rd').hidden=false;document.body.dataset.reader='1';document.querySelector('#rd .words').textContent='Reader stays unchanged'});await shoot('reader','idle',true);
 await p.evaluate(()=>{document.getElementById('rd').hidden=true;delete document.body.dataset.reader});await p.locator('#setBtn').click();await shoot('settings','idle',true);
 assert.deepEqual(errors,[]);
 }finally{await ctx.close()}
 console.log(width,theme,phase,'PASS');
}}finally{await browser.close();await fake.stop();await web.stop();await beforeWeb.stop();rmSync(temp,{recursive:true,force:true})}
if(process.env.P477_OWNER_ONLY){writeFileSync(out+'/owner-recheck.json',JSON.stringify({base,combined,rows,failures},null,2));assert.deepEqual(failures,[]);process.exit(0)}
writeFileSync(out+'/audit.json',JSON.stringify(audit,null,2)+'\n');writeFileSync(out+'/comparison.json',JSON.stringify({base,combined,fixture:'Exact Jarvis 2026-10-22 13:02:24 JST synthetic demonstration plus heading/code audit appendix',rows,failures},null,2)+'\n');
let html='<!doctype html><meta charset="utf-8"><title>P477 comparisons</title><style>body{font:16px system-ui;background:#faf9f5;color:#2c2b26;margin:24px}article{display:grid;grid-template-columns:1fr 1fr;gap:16px}img{width:100%}h2{margin-top:40px}</style><h1>P477 · 11 formats · synthetic data</h1><p>Chromium 390/1280 × light/dark × five states. Before = P472 commit 35f064643. Exact 13:02:24 JST demonstration with synthetic data, plus heading/code audit appendix.</p><a href="audit.json">Computed colour audit</a> · <a href="comparison.json">Pixel results</a>';
for(const r of rows)html+=`<h2>${r.key} · ${r.changedPixels} changed pixels</h2><article><div>Before<img loading="lazy" src="${r.before}"></div><div>After<img loading="lazy" src="${r.after}"></div></article>`;
writeFileSync(out+'/index.html',html);console.log({pairs:rows.length,failures});assert.deepEqual(failures,[]);
