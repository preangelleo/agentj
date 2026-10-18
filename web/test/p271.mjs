// Real Chromium page rendering with CDP safe-area overrides. These are viewport simulations,
// not Android OS / Chrome WebAPK screenshots. Only local fake host and an owned browser.
import { parityRecorder } from '../../parity/lib.mjs';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitState, sleep, shoot } from './browser.mjs';
const out = fileURLToPath(new URL('../../../reports/qa/p271/', import.meta.url));
mkdirSync(out, { recursive: true });
const web = await startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'});
const fake = await startFakeHost();
const B = await launch();
const results = [];
const rec = parityRecorder('web/test/p271.mjs');
let passed = false;
const pixel = (file,x,y) => execFileSync('magick',[file,'-format',`%[pixel:p{${x},${y}}]`,'info:'],{encoding:'utf8'}).trim();
const rgb = c => {
 const m = c.match(/^color\(srgb ([^)]+)\)$/);
 const a = (m ? m[1] : c.match(/\(([^)]+)\)/)[1]).split(/[\s,]+/).slice(0,3).map(Number);
 return a.map(v=>Math.round(v*(m?255:1)));
};
try {
 for (const [name,w,h,inset,ua] of [
  ['android390',390,844,24,'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/147.0.0.0 Mobile Safari/537.36'],
  ['fold744',744,1000,24,'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36'],
  ['ios390',390,844,34,'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'],
  ['desktop',1440,900,0,null]]) {
  const p = await newPage(B,w,h,'light',{ua,allow:[web.url,fake.relay+'/']});
  await p.send('Emulation.setSafeAreaInsetsOverride',{insets:{top:inset?47:0,bottom:inset,left:0,right:0}});
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));
  await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  for (const scheme of ['light','dark']) {
   await evaluate(p,`document.documentElement.dataset.theme=${JSON.stringify(scheme)}`);
   await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:scheme}]});
   for (const state of ['idle','working','waiting','question','offline']) {
    await evaluate(p,`Object.assign(document.body.dataset,{status:${JSON.stringify(state==='question'?'waiting':state==='offline'?'unknown':state)},kind:${JSON.stringify(state==='question'?'question':'')},conn:${JSON.stringify(state==='offline'?'off':'on')}})`);
    await sleep(150);
    const r = await evaluate(p,`(() => {const b=document.getElementById('sabband'),s=getComputedStyle(b),r=b.getBoundingClientRect(),c=document.querySelector('.composer'),inp=document.getElementById('input'); return {band:s.backgroundColor,body:getComputedStyle(document.body).backgroundColor,theme:document.querySelector('meta[name=theme-color]').content,top:r.top,bottom:r.bottom,height:r.height,padding:parseFloat(getComputedStyle(c).paddingBottom),inputBottom:inp.getBoundingClientRect().bottom,hit:s.pointerEvents,topBand:getComputedStyle(document.getElementById('satband')).backgroundColor};})()`);
    assert.deepEqual(rgb(r.band),name==='ios390'?[20,20,20]:rgb(r.body),`${name}/${scheme}/${state}: band = body`);
    assert.deepEqual(rgb(r.topBand),name==='ios390'?[20,20,20]:rgb(r.body),'top status area remains correct');
    assert.equal(r.theme,'#'+(name==='ios390'?[20,20,20]:rgb(r.body)).map(v=>v.toString(16).padStart(2,'0')).join(''));
    assert.equal(r.height,inset); assert.equal(r.bottom,h); assert.equal(r.hit,'none');
    assert.equal(r.padding,12+inset); assert.ok(r.inputBottom<=h-inset);
    const file=`${out}${name}-${scheme}-${state}.png`;
    writeFileSync(file,await shoot(p));
    if(inset) assert.deepEqual(rgb(pixel(file,5,h-2)),name==='ios390'?[20,20,20]:rgb(r.body),'actual bottom screenshot pixel = body');
    results.push({name,scheme,state,...r});
   }
  }
  if(inset) {
   await evaluate(p,`document.body.dataset.reader='1'`);await sleep(100);
   // P290 merge: P273's reader paints with the one dialog token (--dlg-bg, Noir); the band follows the reader, not a fixed Sooty
   assert.deepEqual(rgb(await evaluate(p,`getComputedStyle(document.getElementById('sabband')).backgroundColor`)),name==='ios390'?[20,20,20]:rgb(await evaluate(p,`getComputedStyle(document.getElementById('rd')).backgroundColor`)),'reader band = the reader colour');
   await evaluate(p,`document.body.dataset.reader='0'`);
   // A positive control: water would tint the navigation area without the opaque band.
   await evaluate(p,`document.getElementById('water').style.cssText='display:block;height:100%;background:rgb(1,2,3)';document.getElementById('sabband').style.display='none'`);
   const file=out+name+'-negative.png';writeFileSync(file,await shoot(p));
   assert.deepEqual(rgb(pixel(file,5,h-2)),[1,2,3]);
   await evaluate(p,`document.getElementById('sabband').style.display='';document.body.dataset.kb='1'`);
   assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('sabband')).display`),'none');
   await evaluate(p,`document.body.dataset.kb='0'`);
   assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('sabband')).display`),'block');
  }
  assert.deepEqual(p.problems,[],'no browser/CSP errors');
  await p.dispose();
 }
 writeFileSync(out+'results.json',JSON.stringify({scope:'Chromium viewport simulations, not Android OS or Safari',cases:results},null,2)+'\n');
 passed = true;
 console.log(`P271: ${results.length} state/scheme/viewport cases passed; screenshots: ${out}`);
} finally {rec.record('gesture-safe-area',passed);rec.write();await B.close();await fake.stop();await web.stop();}
