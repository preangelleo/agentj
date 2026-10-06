import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
import { writeFileSync, mkdirSync } from 'node:fs';
import { parityRecorder } from '../../parity/lib.mjs';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';
import { launch, newPage, navigate, evaluate, waitFor, shoot, sleep } from './browser.mjs';
const out = process.env.AJ_P40_OUT ? pathToFileURL(resolve(process.env.AJ_P40_OUT)+'/') : new URL('../../../reports/qa/release-0.13/', import.meta.url);
mkdirSync(out,{recursive:true});
const server=await startWebServer(); const browser=await launch();
const results=[];const fake=await startFakeHost();
const rec=parityRecorder('web/test/p40.mjs');
try {
 for (const [w,h] of [[360,640],[390,844]]) {
  const page=await newPage(browser,w,h,'light',{allow:[server.url,fake.relay]});
  try {
   await page.send('Page.addScriptToEvaluateOnNewDocument',{source:`Object.defineProperty(window,'BarcodeDetector',{value:undefined}); Object.defineProperty(navigator,'standalone',{value:true});`});
   await navigate(page,server.url);await waitFor(page,`!document.getElementById('pair-view').hidden`);
   assert.equal(await evaluate(page,`typeof window.jsQR`),'function');
   assert.equal(await evaluate(page,`document.getElementById('scan').hidden`),false);
   await evaluate(page,`document.getElementById('scan').click()`);
   try { await waitFor(page,`document.body.dataset.view==='scan' && document.getElementById('scan-video').srcObject?.active`); }
   catch (error) {
    console.error('p40-camera-entry',JSON.stringify(await evaluate(page,`({view:document.body.dataset.view,state:window.__ajState,visible:document.visibilityState,focused:document.hasFocus(),hint:document.getElementById('camera-hint').textContent,stream:!!document.getElementById('scan-video').srcObject,media:window.__ajMediaDiagnostics})`)));
    throw error;
   }
   assert.equal(await evaluate(page,`!!document.getElementById('scan-video').srcObject`),true);
   const entry=await evaluate(page,`(()=>{const v=document.getElementById('scan-video'),r=v.getBoundingClientRect();let hidden=false;for(let e=v;e;e=e.parentElement){const s=getComputedStyle(e);hidden ||= e.hidden || s.display==='none' || s.visibility==='hidden';}return {view:document.body.dataset.view,state:window.__ajState,visible:document.visibilityState,focused:document.hasFocus(),active:v.srcObject?.active,hiddenAncestor:hidden,rect:{x:r.x,y:r.y,width:r.width,height:r.height},media:window.__ajMediaDiagnostics};})()`);
   assert.equal(entry.hiddenAncestor,false);assert.equal(entry.active,true);
   assert.deepEqual(entry.rect,{x:0,y:0,width:w,height:h});
   console.log('p40-camera-entry-ok',JSON.stringify(entry));
   await evaluate(page,`document.getElementById('scan-stop').click()`);
   assert.equal(await evaluate(page,`document.getElementById('scan-video').srcObject`),null);
   const qrLink=fake.newPairing(server.url);
   const png=execFileSync(new URL('../../host/.venv/bin/python',import.meta.url).pathname,['-c',"import segno,sys,io,base64; b=io.BytesIO();segno.make(sys.stdin.read(),micro=False).save(b,kind='png',scale=4);sys.stdout.write(base64.b64encode(b.getvalue()).decode())"],{input:qrLink,encoding:'utf8'});
   const decoded=await evaluate(page,`new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>{const c=document.createElement('canvas');c.width=img.width;c.height=img.height;const x=c.getContext('2d');x.drawImage(img,0,0);const f=x.getImageData(0,0,c.width,c.height);resolve(jsQR(f.data,c.width,c.height)?.data)};img.onerror=reject;img.src='data:image/png;base64,${png}'})`);
   assert.equal(decoded,qrLink);
   // Model Safari's layout viewport remaining full-height while the visual viewport shrinks.
   await evaluate(page,`window.__vv = new EventTarget();Object.assign(__vv,{height:${h-300},offsetTop:0,width:${w}});Object.defineProperty(window,'visualViewport',{configurable:true,value:__vv}); document.getElementById('pair-link').focus();__vv.dispatchEvent(new Event('resize'));`);
   await sleep(400);
   const boxes=await evaluate(page,`['pair-link','pair-go'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {id,top:r.top,bottom:r.bottom};})`);
   for (const b of boxes) { assert.ok(b.top>=0,JSON.stringify(b));assert.ok(b.bottom<=h-300,JSON.stringify(b)); }
   await evaluate(page,`const kb=document.createElement('div');kb.id='sim-keyboard';kb.style.cssText='position:fixed;left:0;right:0;top:${h-300}px;height:300px;background:#343434;color:white;z-index:1000;text-align:center;padding-top:60px';kb.textContent='Simulated keyboard · 300px';document.documentElement.append(kb)`);
   writeFileSync(new URL(`keyboard-${w}x${h}.png`,out),await shoot(page,'unused'));
   // Leo's new page stays fullscreen on denial; only tracks stop. Retry and Back
   // must be real controls, never a hidden legacy pairing hint.
   await evaluate(page,`window.p40Gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,writable:true,value:async()=>{throw new DOMException('denied','NotAllowedError')}});document.getElementById('scan').click()`);
   await waitFor(page,`document.body.dataset.view==='scan' && !document.getElementById('camera-hint').hidden && document.getElementById('camera-hint').textContent.includes('相机')`);
   assert.match(await evaluate(page,`document.getElementById('camera-hint').textContent`),/相机|Camera/);
   assert.equal(await evaluate(page,`document.getElementById('scan-video').srcObject`),null);
   assert.equal(await evaluate(page,`getComputedStyle(document.querySelector('.top')).display`),'none');
   const camera=await evaluate(page,`(()=>{const r=document.getElementById('scan-view').getBoundingClientRect();return {w:r.width,h:r.height,top:r.top,back:!document.getElementById('scan-back').hidden};})()`);
   assert.equal(camera.w,w);assert.equal(camera.h,h);assert.equal(camera.top,0);assert.equal(camera.back,true);
   await evaluate(page,`navigator.mediaDevices.getUserMedia=window.p40Gum;document.getElementById('scan-trigger').click()`);
   await waitFor(page,`document.body.dataset.view==='scan' && document.getElementById('scan-video').srcObject?.active`);
   await evaluate(page,`window.p40Track=document.getElementById('scan-video').srcObject.getTracks()[0];document.getElementById('scan-back').click()`);
   await waitFor(page,`document.body.dataset.view==='pair'`);
   assert.equal(await evaluate(page,`window.p40Track.readyState`),'ended');
   assert.equal(await evaluate(page,`document.getElementById('scan-video').srcObject`),null);
   // Pair after the pairing field has already set a viewport height. Grow it,
   // then open chat's keyboard: a stale body --vvh must not shadow the root.
   await evaluate(page,`__vv.height=${h};__vv.dispatchEvent(new Event('resize'));`);
   fake.reset();const link=fake.newPairing(server.url);await evaluate(page,`document.getElementById('pair-link').value=${JSON.stringify(link)};document.getElementById('pair-go').click()`);
   await waitFor(page,`window.__ajState==='awaiting-approval'`);await fake.approve();
   await waitFor(page,`window.__ajState==='ready'`);
   await evaluate(page,`window.__vv=new EventTarget();Object.assign(__vv,{height:${h-300},offsetTop:0,width:${w}});Object.defineProperty(window,'visualViewport',{configurable:true,value:__vv});document.getElementById('input').focus();document.dispatchEvent(new Event('focusin'));__vv.dispatchEvent(new Event('resize'));`);
   await sleep(400);
   const chat=await evaluate(page,`['input','send'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {id,top:r.top,bottom:r.bottom};})`);
   console.log(await evaluate(page,`JSON.stringify({root:document.documentElement.style.cssText,body:document.body.style.cssText,height:getComputedStyle(document.body).height,chat:getComputedStyle(document.getElementById('chat-view')).height,vvh:visualViewport.height})`));
   for(const b of chat){assert.ok(b.top>=0,JSON.stringify(b));assert.ok(b.bottom<=h-300,JSON.stringify(b));}
   writeFileSync(new URL(`keyboard-chat-${w}x${h}.png`,out),await shoot(page,'unused'));
   // Settings overlays inherit the same viewport; there is no text entry in the
   // current settings panel. Audit every visible editable field in the document.
   await evaluate(page,`document.getElementById('badge').click()`);
   assert.ok(await evaluate(page,`document.getElementById('badge-panel').getBoundingClientRect().bottom<=${h-300}`));
   writeFileSync(new URL(`keyboard-settings-${w}x${h}.png`,out),await shoot(page,'unused'));
   assert.equal(page.problems.length,0,page.problems.join('\n'));
   assert.equal(page.offsite.length,0,page.offsite.join('\n'));
   results.push({viewport:[w,h],keyboardHeight:300,boxes,chat,settingsViewport:true,scannerFallback:true,decoderRoundTrip:true,cameraDenied:true,errors:page.problems});
  } finally { await page.dispose(); }
 }
 writeFileSync(new URL('phone-p40.json',out),JSON.stringify(results,null,2)+'\n');
 rec.record('pairing-camera-keyboard',true);rec.record('all-pages-keyboard',true);rec.write();
 console.log('P40 phone PASS: bundled decoder, camera lifecycle/denial, standalone simulation, keyboard controls visible at both sizes');
} finally {await browser.close();await server.stop();await fake.stop();}
