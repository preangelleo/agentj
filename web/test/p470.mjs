// P470: model Safari's exposed root canvas and independently panned visual viewport.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const frozenHash=execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim();
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const baseline=process.argv.includes('--baseline'),out=`reports/qa/p470/${baseline?'red':'after'}`;
mkdirSync(out,{recursive:true});
const failures=[],matrix=[],ok=(v,s)=>{if(!v)failures.push(s)},noir=[44,43,38];
const web=await startWebServer(),fake=await startFakeHost(),browser=await pw.webkit.launch();
try{
 for(const device of ['safari','standalone']){
 const ctx=await browser.newContext({...pw.devices['iPhone 13'],viewport:{width:390,height:844},deviceScaleFactor:1,serviceWorkers:'block'});
 await ctx.addInitScript(standalone=>{
  Object.defineProperty(navigator,'standalone',{get:()=>standalone});window.Audio=undefined;
  const vv=new EventTarget();Object.assign(vv,{height:844,width:390,offsetTop:0,scale:1});Object.defineProperty(window,'visualViewport',{value:vv});
  window.__vv=(h,t)=>{vv.height=h;vv.offsetTop=t;vv.dispatchEvent(new Event('resize'));vv.dispatchEvent(new Event('scroll'))};
 },device==='standalone');
 const p=await ctx.newPage();
 await p.route('**/*',async r=>{
  const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();
  const path='web/public'+(u.pathname==='/'?'/index.html':u.pathname);
  if(u.pathname.endsWith('.css')||(baseline&&u.pathname.endsWith('.js'))){
   let body=baseline?execFileSync('git',['show','08e49f18a:'+path],{encoding:'utf8'}):await(await r.fetch()).text();
   if(u.pathname.endsWith('.css'))body=body.replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,s)=>({top:'47px',bottom:'34px',left:'0px',right:'0px'}[s]));
   return r.fulfill({contentType:u.pathname.endsWith('.css')?'text/css':'text/javascript',body});
  }return r.continue();
 });
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)});
 for(const waterMode of ['visible','zero','unknown'])for(const state of ['idle','working','waiting','question','offline'])for(const view of ['home','editor','reader'])for(const offset of (device==='safari'?[0,90,220]:[0])){
  const height=device==='standalone'?844:offset?450:744;
  await p.evaluate(async({state,view,offset,height,waterMode})=>{
   const fs=await import('./js/fsedit.js');fs.close();document.getElementById('rd').hidden=true;delete document.body.dataset.reader;
   const b=document.body;b.dataset.agent='running';b.dataset.conn=state==='offline'?'off':'on';b.dataset.status=state==='question'?'waiting':state==='offline'?'unknown':state;b.dataset.kind=state==='question'?'question':'';
   const w=document.getElementById('water');w.dataset.known=waterMode==='unknown'?'0':'1';w.style.setProperty('--lvl',waterMode==='zero'?'0':'.4');
   window.__vv(height,offset);if(view==='editor')fs.open();if(view==='reader'){document.getElementById('rd').hidden=false;b.dataset.reader='1'}
   // Safari may pan the visual viewport AND scroll its document while focusing an input.
   let spacer=document.getElementById('safari-scroll-fixture');if(!spacer){spacer=document.createElement('div');spacer.id='safari-scroll-fixture';spacer.style.cssText='position:absolute;top:1800px;width:1px;height:1px;pointer-events:none';document.documentElement.append(spacer)}
   document.documentElement.style.setProperty('overflow','auto','important');window.scrollTo({left:0,top:offset?160:0,behavior:'instant'});
   const {applyViewport}=await import('./js/viewport.js');applyViewport();const {paintIOSChrome}=await import('./js/ios-chrome.js');paintIOSChrome();
  },{state,view,offset,height,waterMode});await p.waitForFunction(y=>Math.abs(window.scrollY-y)<1,offset?160:0,{timeout:5000});await p.waitForTimeout(110);
  const m=await p.evaluate(()=>{
   const cs=e=>getComputedStyle(e),rgb=e=>{const c=document.createElement('canvas').getContext('2d');c.fillStyle=cs(e).backgroundColor;c.fillRect(0,0,1,1);return [...c.getImageData(0,0,1,1).data].slice(0,3)};
   return{root:rgb(document.documentElement),body:rgb(document.body),water:rgb(document.getElementById('water')),page:rgb(document.getElementById('chromeProbe')),bottom:cs(document.getElementById('sabband')).backgroundColor,scrollY,offsetTop:visualViewport.offsetTop,height:visualViewport.height,bandTop:document.getElementById('satband').getBoundingClientRect().top,meta:[...document.querySelectorAll('meta[name="theme-color"]')].map(e=>e.content),composerBottom:document.querySelector('.composer').getBoundingClientRect().bottom};
  });
  const label=`${device}-${waterMode}-${state}-${view}-${offset}`,path=`${out}/${label}.png`;await p.screenshot({path});
  const raw=execFileSync('magick',[path,'-depth','8','rgb:-']);const pixel=(x,y)=>[...raw.slice((y*390+x)*3,(y*390+x)*3+3)],same=(a,b)=>a.length===3&&a.every((v,i)=>Math.abs(v-b[i])<=1);
  const target=view==='home'?(waterMode==='visible'?m.water:m.page):noir;
  const topBad=Array.from({length:47},(_,i)=>i).filter(i=>[2,195,387].some(x=>!same(pixel(x,offset+i),noir)));
  const exposed=pixel(2,offset+height+10);
  if(device==='safari')ok(same(m.root,target)&&same(m.body,target),label+' html/body root visible layer/Noir');
  const band=await p.evaluate(()=>{const c=document.createElement('canvas').getContext('2d');c.fillStyle=getComputedStyle(document.getElementById('sabband')).backgroundColor;c.fillRect(0,0,1,1);return [...c.getImageData(0,0,1,1).data].slice(0,3)});
  ok(same(band,target),label+' bottom band matches visible layer');
  if(device==='safari')ok(same(exposed,target),label+' exposed toolbar/keyboard canvas');
  const bottomPixel=pixel(2,device==='standalone'?830:offset+height+10);
  ok(same(bottomPixel,target),label+' bottom visible pixels');ok(!topBad.length,label+' visible top47 Noir');
  ok(m.meta.every(c=>c==='#2c2b26'),label+' theme-color Noir');
  if(offset)ok(m.scrollY>0&&m.offsetTop>0,label+' actual document scroll and visual pan exercised');
  if(view==='home')ok(Math.abs(m.composerBottom-offset-height)<1,label+' composer follows visual viewport');
  matrix.push({label,device,waterMode,state,view,offset,...m,band,bottomPixel,target,exposed,topBad,path});
 }
 await ctx.close();
 }
 assert.equal(execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim(),frozenHash,'source must remain frozen during qualification');
 const result={baseline,combined:frozenHash,matrix,failures,model:'WebKit iPhone; actual window.scrollY=160, independent synthetic visualViewport.offsetTop=90/220; root canvas sampled beyond body; not physical Safari UI.'};
 writeFileSync(`${out}/results.json`,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({cases:matrix.length,failures},null,2));if(!baseline)assert.equal(failures.length,0);
}finally{await browser.close();await fake.stop();await web.stop()}
