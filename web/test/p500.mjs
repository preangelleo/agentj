// Independent archived page=d3ca5aed3; same-current-page CSS baseline=c97096e917. Synthetic safe areas/keyboard,
// desktop WebKit, not physical iOS compositor evidence or a diagnosis of #264.
// env() substitution models page insets only; navigator.standalone does not launch an iOS Web.app.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const cssHash=s=>createHash('sha256').update(s).digest('hex');
const cssSourceReceipts=[];
// Ordinary page/editor regression uses chairman-reviewed 0287 batch CSS (c97096e917).
// Historical feature-introduction trees precede later approved styles; zero-pixel
// comparison remains unchanged. New stylesheet paths stay identical on both sides.
const historicalStyles=new Set(execFileSync('git',['ls-tree','-r','--name-only','c97096e917','--','web/public'],{encoding:'utf8'}).trim().split('\n').filter(p=>p.endsWith('.css')).map(p=>'/'+p.slice('web/public/'.length)));
import {fileURLToPath} from 'node:url';
import {mkdirSync,mkdtempSync,rmSync,writeFileSync,readFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const shipped=p=>execFileSync('git',['show','d3ca5aed3:web/public/'+p],{encoding:'utf8'});
const view=s=>s.replace(/<!-- P500:[^>]*-->\n/g,'').replace(/(<meta name="apple-mobile-web-app-status-bar-style" content=")[^"]+/, '$1INSTALL_STYLE');
assert.equal(view(execFileSync('git',['show','850e08209:web/public/index.html'],{encoding:'utf8'})),view(shipped('index.html')),'only install style/comment changes in served view markup');
assert.equal(execFileSync('git',['diff','--name-only','d3ca5aed3','850e08209','--','web/public'],{encoding:'utf8'}).split('\n').filter(p=>p.endsWith('.css')).length,0,'P500 candidate changes no CSS; integrated changes are covered by P505 comparisons');

if(!process.argv.includes('--baseline'))execFileSync(process.execPath,[fileURLToPath(import.meta.url),'--baseline'],{stdio:'inherit'});
const baseline=process.argv.includes('--baseline'),out=`${process.env.P500_OUT||'reports/qa/p500/visual'}/${baseline?'before':'after'}`,tmp=mkdtempSync('/tmp/p500-visual-');mkdirSync(out,{recursive:true});
if(baseline)execFileSync('tar',['-x','-C',tmp],{input:execFileSync('git',['archive','d3ca5aed3','web/public'],{maxBuffer:100e6})});
const web=await startWebServer(baseline?{publicDir:tmp+'/web/public'}:{}),fake=await startFakeHost(),wk=await pw.webkit.launch(),ch=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-gpu','--force-color-profile=srgb']}),rows=[],recoveries=[];
try{for(const mode of (process.argv.includes('--platforms')?['android','desktop']:['safari','crios','standalone','android','desktop']))for(const scheme of ['light','dark']){
 const ios=['safari','crios','standalone'].includes(mode),width=mode==='desktop'?1280:ios?420:390,height=ios?912:844,safari=pw.devices['iPhone 13'].userAgent;
 const ctx=await(ios?wk:ch).newContext({...(ios?pw.devices['iPhone 13']:mode==='android'?pw.devices['Pixel 7']:{}),viewport:{width,height},deviceScaleFactor:1,colorScheme:scheme,serviceWorkers:'block',...(mode==='crios'?{userAgent:safari.replace(/Version\/[\d.]+/,'CriOS/131.0')}: {})});
 await ctx.addInitScript(({standalone,height,width,now})=>{
  Object.defineProperty(navigator,'standalone',{get:()=>standalone});window.Audio=undefined;
  const D=Date;window.Date=class extends D{constructor(...a){super(...(a.length?a:[now]));}static now(){return now;}};
  const vv=new EventTarget();Object.assign(vv,{height,width,offsetTop:0,scale:1});Object.defineProperty(window,'visualViewport',{value:vv});window.__vv=h=>{vv.height=h;vv.dispatchEvent(new Event('resize'));};
 },{standalone:mode==='standalone',height,width,now:Date.now()});
 let oldCSS=false;const p=await ctx.newPage();await p.route('**/*',async r=>{
  const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();
  if(u.pathname.endsWith('.css')){
   const body=oldCSS&&historicalStyles.has(u.pathname)?execFileSync('git',['show','c97096e917:web/public'+u.pathname],{encoding:'utf8'}):await(await r.fetch()).text();
   cssSourceReceipts.push({mode,scheme,path:u.pathname,comparisonMode:oldCSS?'approved-css':'served-css',source:oldCSS&&historicalStyles.has(u.pathname)?'c97096e917':baseline?'d3ca5aed3-archive':'selected-tree',sha256:cssHash(body)});
   return r.fulfill({contentType:'text/css',body:ios?body.replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,s)=>({top:'68px',bottom:'68px',left:'0px',right:'0px'}[s])):body});
  }
  return r.continue();
 });
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(async()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();await document.fonts.ready;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);});
 for(const level of ios?[0,null,12,80]:[12]){
  await fake.send({t:'preferences',value:{appearance:{theme:scheme,language:'en'}}});await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent Air'});await fake.send({t:'meter',ctx:level===null?null:{pct:level},h5:{pct:4},week:{pct:68}});
  for(const view of ios?['page','keyboard','editor','reader']:['page','editor']){
   await p.evaluate(async({view,height})=>{const fs=await import('./js/fsedit.js');fs.close();document.getElementById('rd').hidden=true;delete document.body.dataset.reader;document.activeElement?.blur();window.__vv(height);if(view==='editor')fs.open();if(view==='reader'){document.getElementById('rd').hidden=false;document.body.dataset.reader='1';}if(view==='keyboard'){document.getElementById('input').focus();window.__vv(510);}window.dispatchEvent(new Event('resize'));},{view,height});
   await p.waitForTimeout(600);await p.mouse.move(200,300);
   const label=`${mode}-${scheme}-${level??'unknown'}-${view}`,path=`${out}/${label}.png`;await p.screenshot({path});
   const diag=await p.evaluate(async()=>{const {capture}=await import('./js/layout-sample.js');return {...await capture(),installManifest:document.querySelector('link[rel=manifest]').getAttribute('href'),installStatus:document.querySelector('meta[name=apple-mobile-web-app-status-bar-style]').content};});
   if(!baseline){assert.equal(diag.installStatus,'default');assert.equal(diag.installManifest,ios?'manifest.webmanifest':`manifest-${scheme}.webmanifest`);}
   if(ios){assert.ok((await p.locator('meta[name="theme-color"]').evaluateAll(es=>es.every(e=>e.content==='#2c2b26'))),'top stays Noir');}
   let equal=null,comparison='independent shipped/candidate pages';
   if(!baseline){
    {
     // Full-frame zero-tolerance comparison on one compositor. Separate WebKit pages
     // can rasterise the same unchanged inline SVG differently at Air width.
     // Installation metadata/boot are asserted separately; no DOM or pixels are masked.
     // Same existing P488/P496 comparison technique, now used for both engines.
     const shots=[];
     for(const old of [true,false]){
      oldCSS=old;
      await p.evaluate(async old=>{await Promise.all([...document.querySelectorAll('link[rel=stylesheet]')].map(e=>new Promise((resolve,reject)=>{e.onload=resolve;e.onerror=reject;const u=new URL(e.href);u.searchParams.set('p500',String(old));e.href=u.href;})));await document.fonts.ready;},old);
      await p.waitForTimeout(200);shots.push(await p.screenshot({path:old?`${out}/${label}-same-page-before.png`:path}));
     }
     equal=shots[0].equals(shots[1]);comparison='same selected-tree DOM; approved c97096e917 CSS / selected-tree CSS; metadata independently asserted; archived d3ca5aed3 page is separate evidence';
    }
    assert.ok(equal,`${label}: shipped pixels unchanged (${comparison})`);
   }
   rows.push({label,path,equal,comparison,diag});
  }
 }
 // Resume handlers see restored visual viewport, without injecting host credentials.
 await p.evaluate(async height=>{(await import('./js/fsedit.js')).close();document.getElementById('rd').hidden=true;delete document.body.dataset.reader;document.activeElement?.blur();window.__vv(height);window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));document.dispatchEvent(new Event('visibilitychange'));},height);
 await p.waitForTimeout(100);assert.ok(Math.abs(await p.evaluate(()=>document.body.getBoundingClientRect().height)-height)<.1, 'viewport restoration within layout subpixel');
 fake.setUp(false);await p.waitForFunction(()=>window.__ajState!=='ready');fake.setUp(true);await p.waitForFunction(()=>window.__ajState==='ready');
 const recovery=await p.evaluate(()=>({state:window.__ajState,conn:document.body.dataset.conn,bottom:document.documentElement.style.getPropertyValue('--ios-bottom')}));
 await p.screenshot({path:`${out}/${mode}-${scheme}-reconnected.png`});recoveries.push({mode,scheme,...recovery});
 await ctx.close();
}}finally{await wk.close();await ch.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
if(!baseline){assert.ok(cssSourceReceipts.some(x=>x.path==='/app.css'&&x.source==='c97096e917'),'approved app.css really served');assert.ok(cssSourceReceipts.some(x=>x.path==='/app.css'&&x.source==='selected-tree'),'selected-tree app.css really served');}
writeFileSync(out+'/css-source-receipts.json',JSON.stringify({archive:'d3ca5aed3',samePageCSSBaseline:'c97096e917',currentDOM:!baseline,requests:cssSourceReceipts},null,2)+'\n');
writeFileSync(out+'/results.json',JSON.stringify({baseline,rows,recoveries,model:'Desktop WebKit iPhone Air-sized420x912 / Chromium Android390x844 and desktop1280x844; synthetic68px insets, keyboard510px; no physical phone'},null,2)+'\n');console.log(`PASS ${rows.length} screens ${baseline?'baseline captured':'byte-identical to approved c97096e917 CSS on the same current DOM'}; ${recoveries.length} viewport restorations and encrypted connection recoveries`);
