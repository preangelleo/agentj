// P393 #134 (#86 regression, Leo 10-19 「为啥我看 mac m1 agentj 顶部会继续出现 141414 以外的颜色」): on iOS the top status-bar area
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
  for(const scheme of ['light','dark']){
   await evaluate(p,`document.documentElement.dataset.theme=${JSON.stringify(scheme)}`);
   await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:scheme}]});
   for(const [state,d] of Object.entries(STATES)){
    // the relay's own repaint after a status change (and once with a different state colour, the old idle green)
    const r=await evaluate(p,`(async()=>{Object.assign(document.body.dataset,${JSON.stringify(d)});const {paintChrome}=await import('./js/relay.js');
     paintChrome(true);await new Promise(f=>setTimeout(f,120));document.documentElement.style.setProperty('--c-idle','#629887');paintChrome(true);
     await new Promise(f=>setTimeout(f,120));const t=document.getElementById('satband').getBoundingClientRect();
     return {meta:[...document.querySelectorAll('meta[name=theme-color]')].map(m=>m.content),top:getComputedStyle(document.getElementById('satband')).backgroundColor,
      body:getComputedStyle(document.body).backgroundColor,root:getComputedStyle(document.documentElement).backgroundColor,h:t.height,
      apple:document.querySelector('meta[name=apple-mobile-web-app-status-bar-style]').content};})()`);
    const f=join(out,`${name}-${scheme}-${state}.png`);writeFileSync(f,await shoot(p));
    if(isIos){
     assert.deepEqual(r.meta,['#141414'],`${name}/${scheme}/${state} theme-color`);
     for(const k of ['top','root'])assert.deepEqual(rgb(r[k]),sooty,`${name}/${scheme}/${state} ${k}`);
     assert.equal(r.apple,'black-translucent');assert.ok(r.h>=Math.max(1,top),`${name} top strip ${r.h}`);
     assert.deepEqual(px(f,5,0),sooty,`${name}/${scheme}/${state} top pixel`);assert.deepEqual(px(f,5,843),sooty,`${name}/${scheme}/${state} bottom pixel`);
    }else {assert.deepEqual(r.meta,['#141414']);assert.deepEqual(rgb(r.top),sooty);assert.deepEqual(px(f,5,843),sooty);}
    results.push({name,scheme,state,...r});
   }
  }
  await p.dispose();
 }
 writeFileSync(join(out,'results.json'),JSON.stringify({scope:'Chromium viewport and safe-area emulation; physical Safari system chrome not observed',cases:results},null,2)+'\n');
 console.log(`P393 #134 iOS chrome PASS (${results.length} cases)`);
}finally{await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
