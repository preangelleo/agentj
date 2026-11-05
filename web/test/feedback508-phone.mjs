// #508: the real paired page with controlled native permission/focus and recorder seams.
// WebKit permission sheets and iPhone hardware are not emulated by Playwright.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const out=process.env.OLARES_RUN_OUT || 'reports/qa/508/phone';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),browser=await pw.webkit.launch(),rows=[];
const stubs=()=>{
 window.Audio=undefined;window.__closedContexts=0;window.AudioContext=window.webkitAudioContext=class{resume(){return Promise.reject(new DOMException('Interrupted','NotAllowedError'));}close(){__closedContexts++;return Promise.resolve();}createMediaStreamSource(){throw Error('meter unavailable');}};
 window.__vis='visible';Object.defineProperty(document,'visibilityState',{get:()=>__vis,configurable:true});Object.defineProperty(document,'hidden',{get:()=>__vis!=='visible',configurable:true});
 Object.defineProperty(navigator,'standalone',{get:()=>true});
 window.__requests=[];window.__tracks=[];window.__recs=[];window.__stops=[];
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:()=>new Promise((resolve,reject)=>__requests.push({resolve,reject}))}});
 window.__source=()=>{const tracks=[0,1].map(()=>Object.assign(new EventTarget(),{enabled:true,readyState:'live',stop(){this.enabled=false;this.readyState='ended';},getSettings:()=>({noiseSuppression:false})}));__tracks.push(tracks);return {getTracks:()=>tracks,getAudioTracks:()=>tracks};};
 window.MediaRecorder=class{static isTypeSupported(){return true;}constructor(s){this.stream=s;this.state='inactive';this.mimeType='audio/mp4';__recs.push(this);}start(){this.state='recording';}requestData(){}stop(){this.state='inactive';__stops.push(()=>this.onstop?.());}};
};
try{for(const width of [390,1440])for(const scheme of ['dark','light']){
 const ctx=await browser.newContext({viewport:{width,height:844},colorScheme:scheme,isMobile:width===390,hasTouch:true,serviceWorkers:'block'});await ctx.addInitScript(stubs);
 const p=await ctx.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();});
 const begin=async()=>{const n=await p.evaluate(()=>__requests.length);await p.locator('#mic').dispatchEvent('pointerdown',{pointerId:1,pointerType:'touch',button:0,isPrimary:true,clientX:100,clientY:700});await p.waitForFunction(n=>__requests.length===n+1,n);};
 const up=()=>p.locator('#mic').dispatchEvent('pointerup',{pointerId:1,pointerType:'touch',button:0,isPrimary:true});
 const resolve=()=>p.evaluate(()=>__requests.at(-1).resolve(__source()));
 const stopped=()=>p.evaluate(()=>__tracks.every(ts=>ts.every(t=>!t.enabled&&t.readyState==='ended')));
 const check=async(name,fn)=>{try{await fn();rows.push({width,scheme,name,pass:true});}catch(e){rows.push({width,scheme,name,pass:false,error:e.message});}await p.evaluate(()=>dispatchEvent(new Event('pagehide')));};
 await check('positive: ordinary first tap locks, stop releases all tracks',async()=>{await begin();await up();await resolve();await p.waitForFunction(()=>__recs.at(-1)?.state==='recording');await p.locator('#pttCancel').click();assert.ok(await stopped());});
 // A native permission sheet can blur a still-visible standalone page before pointerup.
 await p.waitForTimeout(450);
 await check('permission blur while visible preserves first tap',async()=>{await begin();await p.evaluate(()=>dispatchEvent(new Event('blur')));await up();await resolve();await p.waitForTimeout(120);await p.screenshot({path:`${out}/${width}-${scheme}-permission.png`});assert.ok(await p.evaluate(()=>!document.getElementById('ptt').hidden&&__recs.at(-1)?.state==='recording'),'first tap must keep recording after visible permission blur');await p.locator('#pttCancel').click();assert.ok(await stopped());});
 await p.waitForTimeout(450);
 await check('hidden permission result is stopped, return never acquires',async()=>{await begin();await p.evaluate(()=>{__vis='hidden';document.dispatchEvent(new Event('visibilitychange'));});await resolve();await p.waitForTimeout(50);assert.ok(await stopped());const n=await p.evaluate(()=>__requests.length);await p.evaluate(()=>{__vis='visible';document.dispatchEvent(new Event('visibilitychange'));});assert.equal(await p.evaluate(()=>__requests.length),n);});
 await p.waitForTimeout(450);
 await check('background return first tap with focus blur and slow prior recorder cleanup',async()=>{await begin();await up();await resolve();await p.waitForFunction(()=>__recs.at(-1)?.state==='recording');await p.locator('#pttCancel').click();assert.ok(await stopped());await p.evaluate(()=>{dispatchEvent(new Event('pageshow'));});await p.waitForTimeout(450);await begin();await p.evaluate(()=>dispatchEvent(new Event('blur')));await up();await resolve();await p.evaluate(()=>__stops.splice(0).forEach(fn=>fn()));await p.waitForTimeout(50);assert.ok(await p.evaluate(()=>!document.getElementById('ptt').hidden&&__recs.at(-1)?.state==='recording'&&__recs.at(-1).stream.getTracks().every(t=>t.enabled&&t.readyState==='live')));await p.locator('#pttCancel').click();assert.ok(await stopped());});
 await p.waitForTimeout(450);
 await check('explicit cancel while permission pending stops late tracks',async()=>{await begin();await up();await p.locator('#pttCancel').click();await resolve();await p.waitForTimeout(50);assert.ok(await stopped());assert.ok(await p.locator('#ptt').isHidden());});
 await p.waitForTimeout(450);
 await check('keyboard blur while permission pending still cancels',async()=>{await p.locator('#mic').dispatchEvent('keydown',{key:'Enter'});await p.evaluate(()=>dispatchEvent(new Event('blur')));await resolve();await p.waitForTimeout(50);assert.ok(await stopped());assert.ok(await p.locator('#ptt').isHidden());});
 await p.waitForTimeout(450);
 await check('active unlocked touch hold blur still cancels',async()=>{await begin();await resolve();await p.waitForFunction(()=>__recs.at(-1)?.state==='recording');await p.evaluate(()=>dispatchEvent(new Event('blur')));assert.ok(await stopped());assert.ok(await p.locator('#ptt').isHidden());});
 for(const event of ['pointercancel','lostpointercapture']){
  await p.waitForTimeout(450);
  await check(`permission blur with swallowed up and ${event} keeps accessible cancel`,async()=>{await begin();await p.evaluate(()=>dispatchEvent(new Event('blur')));await p.locator('#mic').dispatchEvent(event,{pointerId:1,pointerType:'touch',isPrimary:true});await resolve();await p.waitForFunction(()=>__recs.at(-1)?.state==='recording');assert.ok(await p.locator('#pttCancel').isVisible());await p.locator('#pttCancel').click();assert.ok(await stopped());});
 }
 assert.deepEqual(errors,[]);await ctx.close();
 }}finally{await browser.close();await fake.stop();await web.stop();writeFileSync(`${out}/results.json`,JSON.stringify(rows,null,2)+'\n');}
for(const row of rows)console.log(`${row.pass?'PASS':'FAIL'} ${row.width}/${row.scheme} ${row.name}${row.error?' — '+row.error:''}`);
assert.ok(rows.every(r=>r.pass),'#508 real-page lifecycle assertions failed');
