// P590 integration follow-up: local fake host + formal built client, no real pairing or external requests.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync, readFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,waitFor,shoot,key,sleep} from './browser.mjs';
const dir=resolve('reports/qa/p590-settings-overlay');mkdirSync(dir,{recursive:true});
const web=await startWebServer({port:0}), fake=await startFakeHost(), browser=await launch();
const shot=async(p,n)=>writeFileSync(join(dir,n+'.png'),await shoot(p));
let checks=0;
const check=(yes,what)=>{assert.ok(yes,what);checks++};
try {
 const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
 // 0308 hotfix: Settings is again the 0.30.6 full-screen page; only interactions it starts are centred (p0308-settings-fullscreen.mjs)
 check(/\.setpage\{display:block;padding:0;background:var\(--bg\)/.test(css),'full-screen Settings rule restored');
 check(!/#settings\.setpage\{display:grid;place-items:center/.test(css),'no fixed centred Settings panel');
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const [width,height] of [[390,844],[1440,900]]) {
  fake.reset();fake.st.prefSet=true;fake.st.prefs={appearance:{language:lang,theme}};
  const p=await newPage(browser,width,height,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.getElementById('setBtn').click();true`);
   await waitFor(p,`!document.getElementById('settings').hidden`);
   const r=await evaluate(p,`(()=>{const e=document.querySelector('#settings>.setbox'),o=document.getElementById('settings'),r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,viewport:innerWidth,night:getComputedStyle(e).backgroundColor,mode:getComputedStyle(o).display,text:getComputedStyle(e).color}})()`);
   check(r.mode==='block'&&r.x<=1&&r.y<=1&&r.w>=0.9*width&&r.h>=0.9*height,`${lang} ${theme} ${width}: Settings follows the whole screen ${JSON.stringify(r)}`);
   await shot(p,`settings-${lang}-${theme}-${width}`);
   for(const [state,open] of Object.entries({rescue:'set-s-rescue',look:'set-s-look',help:'set-s-help',remotes:'set-remotes-open',mail:'set-mail-nav',auth:'set-auth-nav'})) {
    if(open.startsWith('set-s-')) await evaluate(p,`document.getElementById(${JSON.stringify(open)}).scrollIntoView({block:'start'});true`);
    else if(await evaluate(p,`(()=>{const b=document.getElementById(${JSON.stringify(open)});return b&&!b.hidden&&b.getClientRects().length>0})()`)) await evaluate(p,`document.getElementById(${JSON.stringify(open)}).click();true`);
    else continue;
    await sleep(150);
    if(state==='remotes') await evaluate(p,`(()=>{for(const e of document.querySelectorAll('#set-remotes code,#set-remotes .mono,#set-remotes [data-device-id]'))e.textContent='[redacted]';return true})()`);
    await shot(p,`settings-${state}-${lang}-${theme}-${width}`);
    if(!open.startsWith('set-s-')) await evaluate(p,`document.getElementById('set-back').click();true`);
   }
   // 0.30.6 behaviour restored: with the floating assist banner the full-screen page keeps room for it at the bottom.
   await evaluate(p,`document.body.dataset.assist='1';document.body.dataset.assistTerminal='1';true`);
   check(await evaluate(p,`parseFloat(getComputedStyle(document.querySelector('#settings>.setbox')).paddingBottom)>=76`),`${lang} ${theme} ${width}: assist banner keeps its room at the page bottom`);
   await shot(p,`settings-assist-${lang}-${theme}-${width}`);
   await evaluate(p,`delete document.body.dataset.assist;delete document.body.dataset.assistTerminal;true`);
   await key(p,'Escape');await waitFor(p,`document.getElementById('settings').hidden`);
   check(await evaluate(p,`document.getElementById('settings').hidden`),`${lang} ${theme} ${width}: Esc closes`);
   // Real formal translation + real circuit breaker, seeded with three expired attempts; no navigation occurs.
   const stopped=await evaluate(p,`(async()=>{const {createReloadGuard}=await import('./js/reload-guard.js');const {reloadStoppedNotice}=await import('./js/ui.js');const stamp=Date.now(),key='aj.reloadGuard.v1',data={'version.follow':{times:[stamp-3000,stamp-2000,stamp-1000]}};sessionStorage.setItem(key,JSON.stringify(data));localStorage.setItem(key,JSON.stringify(data));let navigation=0,reported=0;const guard=createReloadGuard({navigate:()=>navigation++,report:()=>reported++,notice:()=>reloadStoppedNotice()});const result=guard('version.follow');await Promise.resolve();return {result,navigation,reported,visible:document.getElementById('toast').classList.contains('on'),text:document.getElementById('toast').textContent,close:(()=>{const x=document.querySelector('#toast .nt-close'),r=x?.getBoundingClientRect();return x?{w:r.width,h:r.height,label:x.getAttribute('aria-label')}:null})()}})()`);
   check(stopped.result===false&&stopped.navigation===0&&stopped.reported===1&&stopped.visible&&stopped.text.includes(lang==='zh'?'页面刷新没有生效':'Page refresh did not take effect'),`${lang} ${theme} ${width}: actual reload guard + formal i18n`);
   check(stopped.close&&stopped.close.w>=44&&stopped.close.h>=44&&stopped.close.label===(lang==='zh'?'关闭这条提示':'Close this notice'),`${lang} ${theme} ${width}: reload-stop close button (0.30.7 candidate)`);
   await shot(p,`reload-stopped-${lang}-${theme}-${width}`);
   if(lang==='zh'&&theme==='light'&&width===390) await shot(p,'preview-failure-reload-blocked-zh-light-390');
   check(p.offsite.length===0,`${lang} ${theme} ${width}: local-only requests`);
  }finally{await p.dispose();}
 }
 // Preview bootstrap has no user-facing failure surface: fixture the existing stopped-navigation notice,
 // which is the visible failure if the preview bootstrap's reload guard trips. Do not invent a preview success UI.
 console.log(`P590 settings overlay: ${checks} checks PASS; screenshots ${dir}`);
}finally{await browser.close();await fake.stop();await web.stop();}
