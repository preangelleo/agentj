// P472: real status/send path, live animations, whole 390x47 top strip at each requested deadline.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const baseline=process.argv.includes('--baseline'),out='reports/qa/p472/status'+(baseline?'-baseline':'');mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),browser=await pw.webkit.launch();
const rows=[],failures=[],combined=JSON.parse(readFileSync('web/public/version.json')).combined;
try{for(const mode of ['standalone','safari','crios']){
 const ua=pw.devices['iPhone 13'].userAgent;
 const context=await browser.newContext({...pw.devices['iPhone 13'],viewport:{width:390,height:844},deviceScaleFactor:1,serviceWorkers:'block',userAgent:mode==='crios'?ua.replace(/Version\/[\d.]+/,'CriOS/131.0.6778.81'):ua});
 await context.addInitScript(s=>{Object.defineProperty(navigator,'standalone',{get:()=>s});window.Audio=undefined;},mode==='standalone');
 const p=await context.newPage();
 await p.route('**/*',async r=>{
  const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();
  if(u.pathname.endsWith('.css')){
   let css=baseline?execFileSync('git',['show','e8c22f9b0:web/public'+u.pathname],{encoding:'utf8'}):await(await r.fetch()).text();
   css=css.replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,s)=>({top:'47px',bottom:'34px',left:'0px',right:'0px'}[s]));
   return r.fulfill({contentType:'text/css',body:css});
  }return r.continue();
 });
 const sample=async(label,targets=[0,50,300,1000])=>{
  const start=Date.now();
  for(const ms of targets){await p.waitForTimeout(Math.max(0,ms-(Date.now()-start)));
   const actualMs=Date.now()-start,path=`${mode}-${label}-${ms}.png`;
   const png=await p.screenshot({path:out+'/'+path,clip:{x:0,y:0,width:390,height:47}});
   const rgb=execFileSync('magick',['png:-','-depth','8','rgb:-'],{input:png});let badPixels=0;
   for(let i=0;i<rgb.length;i+=3)if(rgb[i]!==44||rgb[i+1]!==43||rgb[i+2]!==38)badPixels++;
   const facts=await p.evaluate(()=>{const s=getComputedStyle(document.documentElement,'::before');return{color:s.backgroundColor,content:s.content,transition:s.transitionDuration,meta:[...document.querySelectorAll('meta[name="theme-color"]')].map(m=>m.content),state:document.body.dataset.status};});
   const pass=badPixels===0&&facts.color==='rgb(44, 43, 38)'&&facts.content==='""'&&facts.transition==='0s'&&facts.meta.every(c=>c==='#2c2b26');
   const row={mode,label,ms,actualMs,badPixels,...facts,pass,path};rows.push(row);if(!pass)failures.push(row);
  }
 };
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=en'),{waitUntil:'domcontentloaded'});await sample('cold');
 await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()});
 const state=async s=>{await fake.send({t:'status',s:s==='question'?'waiting':s==='offline'?'unknown':s,kind:s==='question'?'question':s==='waiting'?'permission':undefined,agent:'claude',name:'Agent J'});await p.waitForFunction(s=>document.getElementById('orb').dataset.v===s,s);};
 for(const s of ['idle','working','waiting','question','offline','question','waiting','working','idle']){await state(s);await sample('state-'+rows.length+'-'+s)}
 await p.evaluate(()=>{const b=document.body;for(let i=0;i<40;i++){b.dataset.status=['idle','working','waiting','unknown'][i%4];b.dataset.kind=i%2?'question':'';}b.dataset.status='idle';b.dataset.kind='';});await sample('rapid-40');
 fake.st.onApp=async(c,m,send)=>{if(m.t==='say')await send({t:'status',s:'working',agent:'claude',name:'Agent J'});return false;};
 await state('idle');await p.locator('#input').fill('P472 local encrypted send');await p.locator('#send').click();await p.waitForFunction(()=>document.getElementById('orb').dataset.v==='working');assert.ok(fake.st.says.some(m=>m.text==='P472 local encrypted send'));await sample('send-idle-working');
 await p.evaluate(()=>{document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));});await sample('foreground');
 await context.close();
}}finally{await browser.close();await fake.stop();await web.stop()}
writeFileSync(out+'/results.json',JSON.stringify({combined,baseline,model:'Desktop WebKit iPhone emulation, 47px substituted safe area; sample deadline is host screenshot request time, actualMs recorded; not physical iOS system/compositor evidence',rows,failures},null,2)+'\n');
console.log(JSON.stringify({samples:rows.length,failures:failures.length,combined}));if(!baseline)assert.deepEqual(failures,[]);
