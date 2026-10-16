// P342 (#32): the menu logo robot dances in place on Safari's engine (iPhone 13 profile, WebKit) — whole 32px frames only,
// never a sideways slide of the sheet. Also forces the keyframe easing to linear (what an engine that ignores steps()
// would do): the held keyframe pairs must still give whole frames. Menu shot → reports/qa/p342/dance-webkit-menu.png.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {webkit,devices}=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const out=new URL('../../../reports/qa/p342/',import.meta.url);mkdirSync(out,{recursive:true});
const FRAME=0.2, W=32;   // 2.4 s / 12 frames, 32 CSS px per frame
let web, fake, browser;
try {
 web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});fake=await startFakeHost();browser=await webkit.launch();
 const ctx=await browser.newContext({...devices['iPhone 13']});
 // as p264: this Linux headless WebKit's renderer crashes on media playback (the menu / settings chime)
 await ctx.addInitScript(()=>{ window.Audio=undefined;for(const k of ['play','pause','load']) HTMLMediaElement.prototype[k]=function(){return k==='play'?Promise.resolve():undefined;}; });
 const page=await ctx.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(fake.newPairing(web.url.replace('127.0.0.1','localhost')));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
 await page.evaluate(()=>document.getElementById('phone-env-dismiss')?.click());
 // the five-page menu, as on the phone: the orb is a pressable (pointerdown → pointerup = tap)
 await page.evaluate(()=>{const o=document.getElementById('orb');for(const t of ['pointerdown','pointerup'])o.dispatchEvent(new PointerEvent(t,{bubbles:true,button:0,isPrimary:true,pointerType:'touch'}));});
 await page.waitForFunction(()=>document.getElementById('five-menu')?.hidden===false);
 await page.waitForFunction(()=>document.getElementById('orb').classList.contains('father-dance'));
 const style=await page.evaluate(()=>{const c=getComputedStyle(document.getElementById('orb'));return {name:c.animationName,dur:c.animationDuration,tr:c.transitionDuration};});
 assert.equal(style.name,'father-dance');assert.equal(style.dur,'2.4s');
 assert.ok(style.tr.split(',').every(x=>parseFloat(x)===0),'no background transition while dancing: '+style.tr);
 // pause the dance and read the drawn position at many points inside each frame (away from the 0.024 ms seams)
 const sample=()=>page.evaluate(async(FRAME)=>{
   const a=document.getAnimations().find(x=>x.animationName==='father-dance');a.pause();const o=document.getElementById('orb');const r=[];
   for(let i=0;i<12;i++)for(const f of [0.02,0.25,0.5,0.75,0.98]){a.currentTime=(i+f)*FRAME*1000;r.push([i,f,parseFloat(getComputedStyle(o).backgroundPositionX)]);}
   a.play();return r;},FRAME);
 for(const [mode,setup] of [['css',null],['linear',()=>page.addStyleTag({content:'#orb.father-dance{animation-timing-function:linear!important}'})]]){
   if(setup)await setup();
   for(const [i,f,x] of await sample())assert.ok(x===-W*i,`${mode}: frame ${i} at ${f} drew x=${x}, want ${-W*i}`);
   console.log(`P342 dance ${mode}: 12 frames × 5 points, whole 32px frames`);
 }
 // pixels: within a frame the robot does not move; frame captures for the report
 const shot=async(t)=>{await page.evaluate((t)=>{const a=document.getAnimations().find(x=>x.animationName==='father-dance');a.pause();a.currentTime=t;},t);return page.locator('#orb').screenshot();};
 const frames=[];
 for(let i=0;i<12;i++){
   const [a,b,c]=[await shot((i+0.05)*FRAME*1000),await shot((i+0.5)*FRAME*1000),await shot((i+0.95)*FRAME*1000)];
   assert.ok(a.equals(b)&&b.equals(c),`frame ${i}: the picture changed inside one frame (a slide)`);
   frames.push(b);
 }
 assert.ok(new Set(frames.map(f=>f.toString('base64'))).size>=6,'the 12 frames are different poses');
 await page.evaluate(()=>document.getAnimations().find(x=>x.animationName==='father-dance')?.play());
 await page.screenshot({path:new URL('dance-webkit-menu.png',out).pathname});
 assert.deepEqual(errors,[]);
 await ctx.close();console.log('P342 dance WebKit PASS');
} finally {await Promise.allSettled([browser?.close(),fake?.stop(),web?.stop()]);}
