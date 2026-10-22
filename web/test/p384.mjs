// Actual Chromium rendering + CDP viewport/safe-area simulation. Not physical iOS system-chrome proof.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,sleep,shoot} from './browser.mjs';
import {i18nModule} from '../build.mjs';
const before=process.env.AJ_P384_TAG==='before';
const out=fileURLToPath(new URL('../../../reports/qa/p384/web/',import.meta.url));mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p384-web-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});
if(before)for(const name of ['js/boot.js','js/relay.js','app.css','index.html'])writeFileSync(join(tmp,name),execFileSync('git',['show','HEAD:web/public/'+name]));
else writeFileSync(join(tmp,'i18n.js'),i18nModule());
const web=await startWebServer({port:0,publicDir:tmp,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();const results=[];
const ios='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const android='Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/147.0.0.0 Mobile Safari/537.36';
const rgb=c=>{const m=c.match(/^color\(srgb ([^)]+)\)$/);return (m?m[1]:c.match(/\(([^)]+)\)/)[1]).split(/[\s,]+/).slice(0,3).map(v=>Math.round(Number(v)*(m?255:1)));};
const sooty=[20,20,20];
try{
 for(const [name,w,h,ua,platform,touch,standalone,isIos] of [
 ['iphone-safari',390,844,ios,'iPhone',true,false,true],['iphone-pwa',390,844,ios,'iPhone',true,true,true],
 ['ipad-desktop',820,1180,'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15','MacIntel',true,false,true],
 ['android',390,844,android,'Linux armv8l',true,false,false],['desktop',1440,900,'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36','MacIntel',false,false,false]]){
  const p=await newPage(B,w,h,'light',{ua,touch,allow:[web.url,fake.relay+'/']});
  await p.send('Emulation.setUserAgentOverride',{userAgent:ua,platform});
  if(standalone)await p.send('Page.addScriptToEvaluateOnNewDocument',{source:'Object.defineProperty(navigator,"standalone",{value:true});'});
  const inset=name==='desktop'?0:34;await p.send('Emulation.setSafeAreaInsetsOverride',{insets:{top:inset?47:0,bottom:inset,left:0,right:0}});
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  for(const scheme of ['light','dark']){
   await evaluate(p,`document.documentElement.dataset.theme=${JSON.stringify(scheme)}`);
   await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:scheme}]});
   for(const state of ['idle','working','waiting','question','offline','reader','settings']){
    await evaluate(p,`Object.assign(document.body.dataset,{status:${JSON.stringify(state==='question'?'waiting':state==='offline'?'unknown':state)},kind:${JSON.stringify(state==='question'?'question':'')},conn:${JSON.stringify(state==='offline'?'off':'on')},reader:${JSON.stringify(state==='reader'?'1':'0')}});document.getElementById('settings').hidden=${state!=='settings'};`);
    await sleep(120);
    const r=await evaluate(p,`(()=>{const s=getComputedStyle(document.getElementById('sabband')),t=getComputedStyle(document.getElementById('satband')),b=document.getElementById('sabband').getBoundingClientRect();return {bottom:s.backgroundColor,top:t.backgroundColor,body:getComputedStyle(document.body).backgroundColor,meta:document.querySelector('meta[name=theme-color]').content,root:getComputedStyle(document.documentElement).backgroundColor,height:b.height,bottomY:b.bottom,ios:document.documentElement.dataset.ios||null,apple:document.querySelector('meta[name=apple-mobile-web-app-status-bar-style]').content};})()`);
    if(!before){if(isIos){for(const k of ['top','bottom','root'])assert.deepEqual(rgb(r[k]),sooty,`${name}/${scheme}/${state} ${k}`);assert.equal(r.meta,'#141414');assert.equal(r.apple,'black-translucent');}else {assert.deepEqual(rgb(r.top),sooty,'P422 top safe area stays Sooty');assert.deepEqual(rgb(r.bottom),sooty,'P422 bottom safe area stays Sooty');}assert.equal(r.height,inset);assert.equal(r.bottomY,h);}
    if(['idle','working','reader','settings'].includes(state)){const f=join(out,`${before?'before':'after'}-${name}-${scheme}-${state}.png`);writeFileSync(f,await shoot(p));if(!before&&isIos){const pixel=execFileSync('magick',[f,'-format',`%[pixel:p{5,${h-2}}]`,'info:'],{encoding:'utf8'}).trim();assert.deepEqual(rgb(pixel),sooty);const topPixel=execFileSync('magick',[f,'-format','%[pixel:p{5,2}]','info:'],{encoding:'utf8'}).trim();assert.deepEqual(rgb(topPixel),sooty);}}
    results.push({name,scheme,state,...r});
   }
  }
  if(!before&&isIos){
   assert.equal(await evaluate(p,`(async()=>{document.documentElement.style.setProperty('--c-idle','#71bbde');const {paintChrome}=await import('./js/relay.js');paintChrome(true);return document.querySelector('meta[name=theme-color]').content;})()`),'#141414','forced repaint keeps the exact iOS color');
   await evaluate(p,`document.body.dataset.kb='1'`);assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('sabband')).display`),'none');
   await evaluate(p,`document.body.dataset.kb='0'`);assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('sabband')).display`),'block');
  }
  await p.dispose();
 }
 for(const w of [390,1440])for(const scheme of ['light','dark'])for(const lang of ['zh','en']){
  const p=await newPage(B,w,900,scheme,{allow:[web.url,fake.relay+'/']});await navigate(p,`${web.url}?relay=${encodeURIComponent(fake.relay)}&lang=${lang}`);await sleep(500);
  const r=await evaluate(p,`(()=>{const a=document.getElementById('pair-learn');if(!a){document.getElementById('pages').scrollTop=1e6;return null;}a.scrollIntoView();const b=a.getBoundingClientRect();return {text:a.textContent,href:a.href,target:a.target,rel:a.rel,height:b.height,width:b.width,methods:[...document.querySelectorAll('#pair-view .pm')].map(e=>e.id),last:a===document.getElementById('pair-view').lastElementChild,overflow:document.getElementById('pair-view').scrollWidth>document.getElementById('pair-view').clientWidth};})()`);
  if(!before){assert.equal(r.text,lang==='zh'?'还没有 Agent J？去官网了解和注册 →':'New to Agent J? Learn more and sign up →');assert.equal(r.href,'https://agentj.app/');assert.equal(r.target,'_blank');assert.match(r.rel,/noopener/);assert.ok(r.last);assert.ok(r.height>=44);assert.ok(r.width>=44);assert.equal(r.overflow,false);assert.deepEqual(r.methods,['rv-card','pm-term','pm-acct']);}
  // Hide the synthetic reverse pairing QR before evidence: no pairing secrets in screenshots.
  await evaluate(p,`document.getElementById('rv-qr').style.visibility='hidden'`);
  writeFileSync(join(out,`${before?'before':'after'}-pair-${w}-${scheme}-${lang}.png`),await shoot(p));results.push({pair:true,w,scheme,lang,...r});await p.dispose();
 }
 writeFileSync(join(out,`${before?'before':'after'}-results.json`),JSON.stringify({scope:'Chromium viewport and safe-area emulation; physical Safari system chrome not observed',cases:results},null,2)+'\n');console.log(`P384 ${before?'before':'after'} browser PASS (${results.length} cases)`);
}finally{await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
