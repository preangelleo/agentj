// P264: Safari engine smoke, local fake host; not physical iPhone acceptance.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {webkit,devices}=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const out=new URL('../../../reports/qa/p264/',import.meta.url);mkdirSync(out,{recursive:true});
let web, fake, browser;
try {
 web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});fake=await startFakeHost();browser=await webkit.launch();
 const ctx=await browser.newContext({...devices['iPhone 13']});
 await ctx.addInitScript(()=>{ window.Audio=undefined;for(const k of ['play','pause','load']) HTMLMediaElement.prototype[k]=function(){return k==='play'?Promise.resolve():undefined;}; });
 const page=await ctx.newPage();
 await page.goto(fake.newPairing(web.url.replace('127.0.0.1','localhost')));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
 await page.locator('#setBtn').click();await page.locator('#srchBtn').click();await page.locator('#set-find-input').fill('语言');await page.locator('#set-find-results button').first().click();
 const shot=await page.evaluate(async()=>{const m=await import('./js/pageshot.js');const f=await m.capturePage();const img=new Image();img.src=URL.createObjectURL(f);await img.decode();const c=document.createElement('canvas');c.width=img.width;c.height=img.height;c.getContext('2d').drawImage(img,0,0);const pix=[...c.getContext('2d').getImageData(10,10,1,1).data];URL.revokeObjectURL(img.src);const data=await new Promise(r=>{const x=new FileReader();x.onload=()=>r(x.result.split(',')[1]);x.readAsDataURL(f);});return {type:f.type,size:f.size,data,pix};});
 assert.equal(shot.type,'image/png');assert.ok(shot.size>1000);assert.equal(shot.pix[3],255,'actual pixels, not a blank PNG');writeFileSync(new URL('dom-webkit.png',out),Buffer.from(shot.data,'base64'));
 await page.screenshot({path:new URL('settings-webkit.png',out).pathname});
 await ctx.close();console.log('P264 WebKit PASS');
} finally {await Promise.allSettled([browser?.close(),fake?.stop(),web?.stop()]);}
