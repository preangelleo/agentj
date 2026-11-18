// Headless WebKit uses a synthetic camera frame; the click, scanner, decoder and encrypted pairing run normally.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {startFakeHost} from './fakehost.mjs';
import {startWebServer} from './serve.mjs';

const {webkit} = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const web = await startWebServer();
const fake = await startFakeHost();
const browser = await webkit.launch();
try {
  const link = fake.newPairing(web.url,300,{compact:true});
  const py = new URL('../../host/.venv/bin/python',import.meta.url).pathname;
  const rows = JSON.parse(execFileSync(py,['-c',`import json,sys
from agentj import wire
print(json.dumps(["".join("1" if v else "0" for v in row) for row in wire.pairing_qr(sys.stdin.read()).matrix_iter(border=4)]))`],
    {cwd:new URL('../../host/',import.meta.url),input:link,encoding:'utf8'}));
  const ctx = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const page = await ctx.newPage();
  const requests = [];
  page.on('request',r=>{if(new URL(r.url()).pathname.endsWith('/vendor/jsQR.js')) requests.push(r.method());});
  await page.addInitScript(rows=>{
    Object.defineProperty(window,'BarcodeDetector',{value:undefined});
    // The Linux WebKit media stack cannot produce a camera stream. Supply pixels and a track
    // at the DOM video boundary, retaining the production scanFrame/jsQR/QR parsing path.
    window.Audio=undefined;
    window.cameraCalls=0;
    const videoProto=HTMLVideoElement.prototype;
    Object.defineProperties(videoProto,{
      srcObject:{get(){return this.fixtureStream||null},set(s){this.fixtureStream=s}},
      readyState:{get(){return this.srcObject ? 4 : 0}},
      videoWidth:{get(){return this.srcObject ? 800 : 0}},
      videoHeight:{get(){return this.srcObject ? 800 : 0}},
      play:{value:async()=>{}}
    });
    window.cameraStopped=0;
    const media={getUserMedia:async()=>{
      if(++window.cameraCalls===1) throw new DOMException('test denial','NotAllowedError');
      return {getTracks:()=>[{stop(){window.cameraStopped++}}]};
    }};
    Object.defineProperty(navigator,'mediaDevices',{value:media});
    const draw=CanvasRenderingContext2D.prototype.drawImage;
    const frame=document.createElement('canvas');frame.width=frame.height=800;
    const g=frame.getContext('2d');
    g.fillStyle='white';g.fillRect(0,0,800,800);g.fillStyle='black';
    const scale=Math.floor(700/rows.length),offset=Math.floor((800-rows.length*scale)/2);
    rows.forEach((row,y)=>Array.from(row).forEach((v,x)=>{if(v==='1')g.fillRect(offset+x*scale,offset+y*scale,scale,scale)}));
    CanvasRenderingContext2D.prototype.drawImage=function(image,...params){return draw.call(this,image instanceof HTMLVideoElement ? frame : image,...params)};
  },rows);
  await page.goto(web.url);
  await page.locator('#pair-view').waitFor({state:'visible'});
  assert.deepEqual(requests,[]);
  assert.equal(await page.evaluate(()=>typeof window.jsQR),'undefined');
  await page.locator('#scan').click();
  await page.waitForFunction(()=>window.cameraCalls===1 && !document.body.dataset.scan && document.body.dataset.view==='scan');
  assert.deepEqual(requests,['GET']);
  assert.equal(await page.evaluate(()=>typeof window.jsQR),'function');
  await page.locator('#scan-trigger').click();
  await page.waitForFunction(()=>window.__ajState==='awaiting-approval');
  await fake.approve();
  await page.waitForFunction(()=>window.__ajState==='ready');
  assert.equal(await page.evaluate(()=>window.cameraCalls),2);
  assert.equal(await page.evaluate(()=>window.cameraStopped),1);
  assert.deepEqual(requests,['GET']);
  console.log(JSON.stringify({engine:'WebKit',version:browser.version(),viewport:390,camera:'synthetic pixels',coldDecoderRequests:0,scannerDecoderRequests:1,deniedThenRetried:true,decodedRealPairingCode:true,approved:true,stoppedTracks:1}));
} finally {await browser.close();await fake.stop();await web.stop();}
