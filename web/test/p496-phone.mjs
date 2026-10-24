// Real paired page + instrumented media seam. WebKit's system permission UI is not emulated.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const baseline=process.argv.includes('--baseline'),out=`reports/qa/p496/microphone/${baseline?'before':'after'}`,temp=mkdtempSync('/tmp/p496-base-');mkdirSync(out,{recursive:true});
if(baseline)execFileSync('tar',['-x','-C',temp],{input:execFileSync('git',['archive','d3ca5aed3','web/public'],{maxBuffer:100e6})});
const web=await startWebServer(baseline?{publicDir:temp+'/web/public'}:{}),fake=await startFakeHost(),browser=await pw.webkit.launch(),rows=[];
const stubs=()=>{
 window.Audio=window.AudioContext=window.webkitAudioContext=undefined;
 window.__vis='visible';Object.defineProperty(document,'visibilityState',{get:()=>window.__vis,configurable:true});Object.defineProperty(document,'hidden',{get:()=>window.__vis!=='visible',configurable:true});
 window.__calls=0;window.__tracks=[];window.__recs=[];
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{window.__calls++;const tr=Object.assign(new EventTarget(),{readyState:'live',enabled:true,stop(){this.readyState='ended';this.enabled=false;},getSettings:()=>({noiseSuppression:false})});window.__tracks.push(tr);return {getTracks:()=>[tr],getAudioTracks:()=>[tr]};}}});
 window.MediaRecorder=class {static isTypeSupported(){return true;}constructor(s){this.stream=s;this.state='inactive';this.mimeType='audio/webm';window.__recs.push(this);}start(){this.state='recording';}requestData(){}stop(){this.state='inactive';setTimeout(()=>this.onstop?.(),30);}};
};
try{for(const mode of ['safari','crios','standalone']){
 const safari=pw.devices['iPhone 13'].userAgent,ctx=await browser.newContext({...pw.devices['iPhone 13'],userAgent:mode==='crios'?safari.replace(/Version\/[\d.]+/,'CriOS/131.0'):safari,serviceWorkers:'block'});
 await ctx.addInitScript(stubs);await ctx.addInitScript(s=>Object.defineProperty(navigator,'standalone',{get:()=>s}),mode==='standalone');
 const p=await ctx.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');await p.evaluate(()=>document.getElementById('phone-env-dismiss')?.click());
 const begin=async()=>{const n=await p.evaluate(()=>window.__recs.length);await p.evaluate(()=>document.getElementById('mic').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'})));await p.waitForFunction(n=>window.__recs.length>n,n);};
 const end=async()=>{await p.evaluate(()=>document.getElementById('mic').dispatchEvent(new KeyboardEvent('keyup',{key:'Enter'})));await p.waitForTimeout(50);};
 const vis=v=>p.evaluate(v=>{window.__vis=v;document.dispatchEvent(new Event('visibilitychange'));},v);
 for(let i=0;i<10;i++){await begin();await end();}
 const count=await p.evaluate(()=>window.__calls),idleEnabled=await p.evaluate(()=>window.__tracks.some(t=>t.readyState==='live'&&t.enabled));
 // Delayed old onstop must not disable a newer take.
 await begin();await p.evaluate(()=>document.getElementById('mic').dispatchEvent(new KeyboardEvent('keyup',{key:'Enter'})));await begin();await p.waitForTimeout(60);
 const nextEnabled=await p.evaluate(()=>window.__recs.at(-1).stream.getAudioTracks()[0].enabled);await end();
 // Locked recording, hidden -> interrupted and stopped now, never resumed automatically.
 const b=await p.locator('#mic').boundingBox();await p.touchscreen.tap(b.x+b.width/2,b.y+b.height/2);await p.waitForTimeout(70);await p.touchscreen.tap(b.x+b.width/2,b.y+b.height/2);await p.waitForFunction(()=>document.getElementById('mic').classList.contains('lock'));
 await vis('hidden');const hiddenStopped=await p.evaluate(()=>window.__tracks.every(t=>t.readyState==='ended')&&window.__recs.every(r=>r.state==='inactive'));const hiddenCalls=await p.evaluate(()=>window.__calls);await vis('visible');await p.waitForTimeout(70);
 const returnCalls=await p.evaluate(()=>window.__calls),cycles=[];
 if(!baseline){
  for(let i=0;i<5;i++){
   await begin();await vis('hidden');
   assert.ok(await p.evaluate(()=>window.__tracks.every(t=>t.readyState==='ended')&&window.__recs.every(r=>r.state==='inactive')));
   const before=await p.evaluate(()=>window.__calls);await vis('visible');await p.waitForTimeout(40);
   const after=await p.evaluate(()=>window.__calls);assert.equal(after,before);cycles.push({before,after,stopped:true});
  }
  await begin();await end();
 }

 const row={mode,foregroundTakes:10,calls:count,idleEnabled,nextEnabled,hiddenStopped,hiddenCalls,returnCalls,cycles,errors};rows.push(row);console.log(JSON.stringify(row));await p.screenshot({path:`${out}/${mode}.png`});await ctx.close();
}}finally{await browser.close();await fake.stop();await web.stop();rmSync(temp,{recursive:true,force:true});}
writeFileSync(out+'/results.json',JSON.stringify({baseline,rows},null,2)+'\n');
for(const r of rows){assert.equal(r.calls,1,`${r.mode}: foreground acquisitions`);assert.equal(r.idleEnabled,false);assert.equal(r.nextEnabled,true);assert.equal(r.hiddenStopped,true);assert.equal(r.returnCalls,r.hiddenCalls);assert.deepEqual(r.errors,[]);}

const rec=parityRecorder('web/test/p496-phone.mjs');rec.record('foreground-reuse-hide-stop',true);rec.write();
