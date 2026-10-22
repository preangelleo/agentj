// P459 supersedes #86/#134: static black-translucent, document-owned current-page chrome.
// Historical P393: Leo 10-19 「为啥我看 mac m1 agentj 顶部会继续出现 141414 以外的颜色」): on iOS the top status-bar area
// and the bottom home-bar area stay #141414 after the phone connects, through every state, a forced repaint and a changed
// state colour; Android keeps its page-colour bars. Chromium + CDP safe-area emulation, not physical Safari system chrome:
// the static HTML check stands in for what iOS reads at launch (it ignores the runtime meta switch).
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,sleep,shoot} from './browser.mjs';
import {i18nModule} from '../build.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/p393/ios-chrome/',import.meta.url));mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p393-ios-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const web=await startWebServer({port:0,publicDir:tmp,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();const results=[];
const ios='Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Version/26.0 Mobile/15E148 Safari/604.1';
const android='Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/147.0.0.0 Mobile Safari/537.36';
const rgb=c=>{const m=c.match(/^color\(srgb ([^)]+)\)$/);return (m?m[1]:c.match(/\(([^)]+)\)/)[1]).split(/[\s,]+/).slice(0,3).map(v=>Math.round(Number(v)*(m?255:1)));};
const sooty=[20,20,20],px=(f,x,y)=>rgb(execFileSync('magick',[f,'-format',`%[pixel:p{${x},${y}}]`,'info:'],{encoding:'utf8'}).trim());
// P470 uses the same one-level tolerance when CSS sRGB is composited to 8-bit page pixels. Fixed Noir remains exact.
const pagePixel=(actual,expected,label)=>assert.ok(actual.every((v,i)=>Math.abs(v-expected[i])<=1),`${label}: ${actual} vs ${expected}`);
const STATES={idle:{status:'idle',kind:'',conn:'on'},working:{status:'working',kind:'',conn:'on'},waiting:{status:'waiting',kind:'',conn:'on'},
 question:{status:'waiting',kind:'question',conn:'on'},offline:{status:'unknown',kind:'',conn:'off'}};
try{
 // What iOS reads at launch: the served HTML itself says black-translucent (the runtime switch alone was ignored on device).
 const html=await (await fetch(new URL('index.html',web.url))).text();
 assert.match(html,/<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">/,'served HTML: static black-translucent');
 for(const [name,ua,platform,standalone,top,isIos] of [['iphone-pwa',ios,'iPhone',true,47,true],['iphone-pwa-opaque',ios,'iPhone',true,0,true],
  ['iphone-safari',ios,'iPhone',false,47,true],['android',android,'Linux armv8l',false,24,false]]){
  const p=await newPage(B,390,844,'light',{ua,touch:true,allow:[web.url,fake.relay+'/']});
  await p.send('Emulation.setUserAgentOverride',{userAgent:ua,platform});
  if(standalone)await p.send('Page.addScriptToEvaluateOnNewDocument',{source:'Object.defineProperty(navigator,"standalone",{value:true});'});
  await p.send('Emulation.setSafeAreaInsetsOverride',{insets:{top,bottom:34,left:0,right:0}});
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  await evaluate(p,`(()=>{const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important}';document.head.append(s)})()`);
  for(const scheme of ['light','dark']){
   await evaluate(p,`document.documentElement.dataset.theme=${JSON.stringify(scheme)}`);
   await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:scheme}]});
   for(const [state,d] of Object.entries(STATES)){
    // the relay's own repaint after a status change (and once with a different state colour, the old idle green)
    const r=await evaluate(p,`(async()=>{Object.assign(document.body.dataset,${JSON.stringify(d)});const {paintChrome}=await import('./js/relay.js');
     paintChrome(true);await new Promise(f=>setTimeout(f,120));document.documentElement.style.setProperty('--c-idle','#629887');paintChrome(true);
     await new Promise(f=>setTimeout(f,120));const t=document.getElementById('satband').getBoundingClientRect();
     const band=${isIos}?getComputedStyle(document.documentElement,'::before'):getComputedStyle(document.getElementById('satband'));return {meta:[...document.querySelectorAll('meta[name=theme-color]')].map(m=>m.content),top:band.backgroundColor,
      body:getComputedStyle(document.body).backgroundColor,root:getComputedStyle(document.documentElement).backgroundColor,h:${isIos}?parseFloat(band.height):t.height,
      apple:document.querySelector('meta[name=apple-mobile-web-app-status-bar-style]').content};})()`);
    const f=join(out,`${name}-${scheme}-${state}.png`);writeFileSync(f,await shoot(p));
    if(isIos){
     assert.ok(r.meta.every(v=>v==='#2c2b26'),`${name}/${scheme}/${state} static theme metadata`);
     assert.deepEqual(rgb(r.top),[44,43,38]);for(const k of ['root'])assert.deepEqual(rgb(r[k]),standalone?[44,43,38]:rgb(r.body),`${name}/${scheme}/${state} ${k}`);
     assert.equal(r.apple,'black-translucent');assert.ok(r.h>=top,`${name} top strip ${r.h}`);
     if(top>0)assert.deepEqual(px(f,5,0),[44,43,38],`${name}/${scheme}/${state} top pixel`);else {
      // With no safe area the waiting aura is legitimately visible; prove that a zero-height status layer paints nothing.
      assert.equal(r.h,0);
      await evaluate(p,`(()=>{const e=document.createElement('style');e.id='p476-no-status-layer';e.textContent='html[data-ios="1"]::before{display:none!important}';document.head.append(e)})()`);
      const ref=f.replace('.png','-without-status-layer.png');writeFileSync(ref,await shoot(p));
      await evaluate(p,`document.getElementById('p476-no-status-layer').remove()`);
      const row=file=>execFileSync('magick',[file,'-crop','390x1+0+0','-depth','8','rgb:-']);
      assert.deepEqual(row(f),row(ref),`${name}/${scheme}/${state} zero-inset root layer paints no pixel`);
     }pagePixel(px(f,5,843),rgb(r.body),`${name}/${scheme}/${state} bottom pixel`);
    }else {assert.deepEqual(r.meta,['#141414']);assert.deepEqual(rgb(r.top),sooty);assert.deepEqual(px(f,5,843),sooty);}
    results.push({name,scheme,state,...r});
   }
  }
  await p.dispose();
 }
 writeFileSync(join(out,'results.json'),JSON.stringify({scope:'Chromium viewport and safe-area emulation; physical Safari system chrome not observed',cases:results},null,2)+'\n');
 console.log(`P393 #134 iOS chrome PASS (${results.length} cases)`);
}finally{await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
