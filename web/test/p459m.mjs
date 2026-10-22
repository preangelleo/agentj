// P459m final contract: shipped WebKit page, synthetic iPhone safe insets/keyboard.
// Browser-owned status bar and physical keyboard are not reproduced by WebKit emulation.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const baseline=process.argv.includes('--baseline'),out='reports/qa/p459m/'+(baseline?'final-red':'final');
mkdirSync(out,{recursive:true});
const failures=[],finalMatrix=[],snapshots={},fallbackChecks=[];let contrast;
const rgb=s=>s.startsWith('#')?[1,3,5].map(i=>parseInt(s.slice(i,i+2),16)):s.match(/[\d.]+/g).slice(0,3).map(v=>Math.round(Number(v)*(s.startsWith('color(srgb')?255:1)));
const noir=[44,43,38],ok=(v,s)=>{if(!v)failures.push(s)};
const web=await startWebServer(),fake=await startFakeHost(),browser=await pw.webkit.launch();
const safari=pw.devices['iPhone 13'].userAgent,crios=safari.replace(/Version\/[\d.]+/,'CriOS/131.0.6778.81');
try{
 for(const mode of ['safari','crios','standalone']){
  fake.reset();fake.setUp(true);
  const ctx=await browser.newContext({...pw.devices['iPhone 13'],userAgent:mode==='crios'?crios:safari,viewport:{width:390,height:844},deviceScaleFactor:1});
  await ctx.addInitScript(standalone=>{
   Object.defineProperty(navigator,'standalone',{get:()=>standalone});
   const vv=new EventTarget();Object.assign(vv,{height:844,width:390,offsetTop:0,offsetLeft:0,scale:1});Object.defineProperty(window,'visualViewport',{value:vv});
   window.__vv=h=>{vv.height=h;vv.dispatchEvent(new Event('resize'));vv.dispatchEvent(new Event('scroll'))};window.Audio=undefined;
  },mode==='standalone');
  const p=await ctx.newPage();p.setDefaultTimeout(10000);
  await p.route('**/*',async route=>{
   const u=new URL(route.request().url());if(!['localhost','127.0.0.1'].includes(u.hostname))return route.abort();
   if(baseline&&(u.pathname.endsWith('.js')||u.pathname.endsWith('.css')||u.pathname==='/')){
    const path='web/public'+(u.pathname==='/'?'/index.html':u.pathname);
    let body;try{body=execFileSync('git',['show','HEAD:'+path],{encoding:'utf8',stdio:['ignore','pipe','ignore']})}catch{}
    if(body!==undefined){if(u.pathname.endsWith('.css'))body=body.replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,s)=>({top:'47px',bottom:'34px',left:'0px',right:'0px'}[s]));return route.fulfill({contentType:u.pathname.endsWith('.js')?'text/javascript':u.pathname.endsWith('.css')?'text/css':'text/html',body})}
   }
   if(u.pathname.endsWith('.css')){const r=await route.fetch();return route.fulfill({response:r,body:(await r.text()).replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,s)=>({top:'47px',bottom:'34px',left:'0px',right:'0px'}[s]))})}return route.continue();
  });
  await p.goto(fake.newPairing(web.url+'?lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
  await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)});
  for(const state of ['idle','working','waiting','question','offline'])for(const view of ['home','editor','reader','record','settings'])for(const keyboard of [false,true]){
   await p.evaluate(async({state,view,keyboard})=>{
    const fs=await import('./js/fsedit.js');fs.close();await import('./js/settings.js').then(m=>m.close());
    const b=document.body;document.getElementById('ptt').hidden=true;b.classList.remove('recblur');document.getElementById('rd').hidden=true;delete b.dataset.reader;
    b.dataset.agent='running';b.dataset.conn=state==='offline'?'off':'on';b.dataset.status=state==='question'?'waiting':state==='offline'?'unknown':state;b.dataset.kind=state==='question'?'question':'';
    const w=document.getElementById('water');w.dataset.known='1';w.style.setProperty('--lvl','.4');window.__vv(keyboard?510:844);
    if(view==='editor')fs.open();if(view==='reader'){document.getElementById('rd').hidden=false;b.dataset.reader='1'}if(view==='record'){document.getElementById('ptt').hidden=false;b.classList.add('recblur')}if(view==='settings')await import('./js/settings.js').then(m=>m.open());
   },{state,view,keyboard});
   await p.waitForTimeout(90);
   const m=await p.evaluate(()=>{
    const cs=e=>getComputedStyle(e),rect=e=>{const r=e.getBoundingClientRect();return{left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height}};
    const w=document.getElementById('water'),b=document.body,ed=document.getElementById('fsEd');
    const canvas=document.createElement('canvas'),c=canvas.getContext('2d');c.fillStyle=cs(w).backgroundColor;c.fillRect(0,0,1,1);const waterColor='rgb('+[...c.getImageData(0,0,1,1).data].slice(0,3).join(', ')+')';
    return{userAgent:navigator.userAgent,waterColor,waterBackground:cs(w).backgroundColor,color:cs(document.getElementById('chromeProbe')).backgroundColor,meta:[...document.querySelectorAll('meta[name="theme-color"]')].map(m=>m.content),limit:visualViewport.height,composerGap:visualViewport.height-document.getElementById('input').closest('.field').getBoundingClientRect().bottom,composer:rect(document.querySelector('.composer')),body:rect(b),buttons:ed.hidden?[]:['fsSlash','fsSend'].map(id=>{const e=document.getElementById(id),r=rect(e),front=document.elementsFromPoint((r.left+r.right)/2,r.bottom-2)[0];return{id,...r,front:front===e||e.contains(front),occluded:['satband','sabband'].filter(id=>{const el=document.getElementById(id),q=rect(el),s=cs(el);return s.display!=='none'&&+s.zIndex>=+cs(ed).zIndex&&q.right>r.left&&q.left<r.right&&q.bottom>r.top&&q.top<r.bottom}),ink:cs(id==='fsSend'?e.querySelector('.rocket'):e).color}})};
   });
   const label=`${mode}-${state}-${view}-${keyboard?'keyboard':'full'}`,path=`${out}/${label}.png`;
   await p.screenshot({path});const raw=execFileSync('magick',[path,'-depth','8','rgb:-']);
   const pixel=(x,y)=>[...raw.slice((y*390+x)*3,(y*390+x)*3+3)],same=(a,b)=>a.every((v,i)=>Math.abs(v-b[i])<=1);
   const expectedBottom=['editor','reader'].includes(view)?'rgb(44, 43, 38)':m.waterColor,target=rgb(expectedBottom);
   const top_bad_rows=Array.from({length:47},(_,y)=>y).filter(y=>[2,195,387].some(x=>!same(noir,pixel(x,y))));
   const badRows=Array.from({length:34},(_,i)=>i).filter(i=>[2,387].some(x=>!same(target,pixel(x,m.limit-34+i))));
   for(const b of m.buttons){const ink=rgb(b.ink);let n=0;for(let y=Math.ceil((b.top+b.bottom)/2+3);y<Math.floor(b.bottom-5);y++)for(let x=Math.ceil(b.left);x<Math.floor(b.right);x++)if(same(ink,pixel(x,y)))n++;b.lower_ink_pixels=n;ok(b.top>=47&&b.bottom<=m.limit-10&&b.front&&!b.occluded.length&&n>0,label+' '+b.id+' complete lower glyph and unobstructed control')}
   ok(!top_bad_rows.length,label+' top 47px Noir');ok(!badRows.length,label+' bottom 34px '+expectedBottom);ok(m.meta.every(c=>same(rgb(c),noir)),label+' all theme-color Noir');
   if(view==='home')ok(Math.abs(m.composer.bottom-m.limit)<=1&&m.composerGap<=(mode==='standalone'&&!keyboard?46.5:12.5),label+' composer no extra bottom gap');
   const row={mode,state,view,keyboard,label,path,...m,standalone:mode==='standalone',designGap:12,topColor:'rgb('+pixel(2,0).join(', ')+')',expectedBottom,bottomColor:'rgb('+pixel(2,m.limit-1).join(', ')+')',top_bad_rows,bottom_bad_rows:keyboard?[]:badRows,keyboard_bad_rows:keyboard?badRows:[]};
   finalMatrix.push(row);snapshots[label]=row;
  }
  if(mode==='safari'){
   await p.evaluate(async()=>{await import('./js/settings.js').then(m=>m.close());document.body.classList.remove('recblur');document.getElementById('ptt').hidden=true;document.getElementById('rd').hidden=true;delete document.body.dataset.reader;window.__vv(844);const e=document.getElementById('rescue-banner');document.getElementById('reply-notices').append(e);e.hidden=false});
   const colors=await p.locator('#rescue-banner-go').evaluate(e=>{const s=getComputedStyle(e);return{fg:s.color,bg:s.backgroundColor}});
   const lum=s=>rgb(s).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0),a=lum(colors.fg),b=lum(colors.bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);contrast={...colors,ratio,inReply:true,visible:await p.locator('#rescue-banner-go').isVisible()};ok(ratio>=4.5,'rescue contrast >=4.5');
   await p.locator('#rescue-banner').screenshot({path:`${out}/rescue-after.png`});await p.addStyleTag({content:'.reply-notices .rsbanner__go{color:var(--dlg-fg)!important}'});await p.locator('#rescue-banner').screenshot({path:`${out}/rescue-before.png`});
  }
  const fallback=await p.evaluate(async()=>{const m=await import('./js/ios-chrome.js'),w=document.getElementById('water'),parent=w.parentNode,next=w.nextSibling;w.remove();m.paintIOSChrome();const color=getComputedStyle(document.documentElement).getPropertyValue('--ios-bottom').trim();parent.insertBefore(w,next);m.paintIOSChrome();return color});fallbackChecks.push({mode,color:fallback});ok(rgb(fallback).join()===noir.join(),mode+' missing water fallback Noir');
  await ctx.close();
 }
 for(const state of ['idle','working','waiting','question','offline'])for(const view of ['home','editor','reader','record','settings'])for(const keyboard of [false,true]){
  const rows=['safari','crios','standalone'].map(mode=>snapshots[`${mode}-${state}-${view}-${keyboard?'keyboard':'full'}`]);
  ok(rows[0].bottomColor===rows[1].bottomColor&&rows[0].topColor===rows[1].topColor,`${state}-${view}-${keyboard} Safari/CriOS edge parity`);
  execFileSync('magick',[...rows.map(r=>r.path),'+append',`${out}/compare-${state}-${view}-${keyboard?'keyboard':'full'}.png`]);
 }
 const result={combined:execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim(),baseline,contrast,fallbackChecks,checks:finalMatrix.filter(r=>r.mode!=='crios'&&r.view==='home'),browserChecks:finalMatrix.filter(r=>r.mode==='crios'&&r.view==='home'),overlays:finalMatrix.filter(r=>r.view!=='home'),finalMatrix,failures,emulation:'WebKit iPhone 13, synthetic 47/34 CSS safe insets and visualViewport 844/510 keyboard; real shipped page and local encrypted host. No physical iOS/browser status bar claim.'};
 writeFileSync(`${out}/results.json`,JSON.stringify(result,null,2)+'\n');if(!baseline){writeFileSync('reports/qa/p459m/ios-results.json',JSON.stringify(result,null,2)+'\n');writeFileSync('reports/qa/p459m/results.json',JSON.stringify(result,null,2)+'\n')}
 console.log(JSON.stringify({matrix:finalMatrix.length,failures},null,2));assert.equal(failures.length,0);
}finally{await browser.close();await fake.stop();await web.stop()}
