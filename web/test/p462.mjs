// P462: own local Chromium + fake paired host; 390 zh/en × light/dark baseline screenshots, no real account.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const out='reports/qa/p462',base='e96cc19ae',ids=['file','image','camera','mic','full'];
const dom={file:'tDoc',image:'tPhoto',camera:'tCamera',mic:'mic',full:'tFull'};
const legacy=['mic','camera','file','image'];
const perms=a=>a.length?a.flatMap((v,i)=>perms(a.filter((_,j)=>i!==j)).map(p=>[v,...p])):[[]];
const web=await startWebServer(),fake=await startFakeHost(),B=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--hide-scrollbars']});
const results=[];
const row=p=>p.locator('#tools > .tool').evaluateAll(ns=>ns.map(n=>n.id));
const chips=p=>p.locator('#set-tools button[data-tool]').evaluateAll(ns=>ns.map(n=>n.dataset.tool));
try{for(const lang of ['zh','en'])for(const theme of ['light','dark']){
 const evidence={lang,theme};let baseline;
 for(const which of ['before','after']){
  mkdirSync(`${out}/${which}`,{recursive:true});
  const ctx=await B.newContext({viewport:{width:390,height:844},colorScheme:theme,hasTouch:true});const p=await ctx.newPage();p.setDefaultTimeout(12000);
  try{
   await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();
    if(which==='before'&&['/js/toolorder.js','/js/toolsort.js','/i18n.js','/menu.css'].includes(u.pathname))return r.fulfill({status:200,contentType:u.pathname.endsWith('.css')?'text/css':'text/javascript',body:execFileSync('git',['show',base+':web/public'+u.pathname],{encoding:'utf8'})});return r.continue()});
   await p.addInitScript(v=>{if (!localStorage.getItem('aj.toolOrder')) localStorage.setItem('aj.toolOrder',JSON.stringify(v))},legacy);
   fake.reset();fake.setUp(true);fake.st.prefSet=true;fake.st.prefs={appearance:{language:lang,theme},ui:{composer_buttons:[...legacy]}};
   await p.goto(fake.newPairing(web.url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
   await p.evaluate(theme=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=theme;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)},theme);
   await p.evaluate(()=>document.fonts.ready);await p.waitForTimeout(450);
   assert.deepEqual(await row(p),[...legacy,'full'].map(id=>dom[id]));
   const prefix=`${out}/${which}/390-${lang}-${theme}`;
   await p.screenshot({path:prefix+'-toolbar.png'});
   const toolbar=await p.locator('#tools').screenshot({path:prefix+'-toolbar-crop.png'});
   const icons=await p.locator('#tools > .tool').evaluateAll(ns=>ns.map(n=>({id:n.id,svg:n.querySelector('svg').outerHTML,w:n.querySelector('svg').getBoundingClientRect().width,h:n.querySelector('svg').getBoundingClientRect().height,color:getComputedStyle(n).color})));
   await p.locator('#setBtn').click();await p.waitForFunction(()=>!document.getElementById('settings').hidden);await p.locator('#set-tools-row').scrollIntoViewIfNeeded();await p.waitForTimeout(350);
   assert.equal(await p.locator('#set-tools button').count(),which==='before'?4:5);
   await p.screenshot({path:prefix+'-settings.png'});
   if(which==='before'){baseline={toolbar,icons};continue}
   assert.deepEqual(icons,baseline.icons,'same icon SVG, size and colour');assert.equal(toolbar.equals(baseline.toolbar),true,'default migrated toolbar pixels unchanged');
   assert.deepEqual(await chips(p),[...legacy,'full']);
   // Actual held touch drag of the new fifth chip to the first slot.
   const full=p.locator('[data-tool="full"]'),first=p.locator('[data-tool="mic"]');
   const a=await full.boundingBox(),b=await first.boundingBox(),cdp=await ctx.newCDPSession(p);
   const pt=(r)=>({x:r.x+r.width/2,y:r.y+r.height/2});const from=pt(a),to=pt(b);
   await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[from]});await p.waitForTimeout(400);
   for(let i=1;i<=12;i++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:from.x+(to.x-from.x)*i/12,y:from.y+(to.y-from.y)*i/12}]});await p.waitForTimeout(20)}
   await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await p.waitForTimeout(850);
   assert.deepEqual(await chips(p),['full',...legacy]);assert.deepEqual(fake.st.prefs.ui.composer_buttons,['full',...legacy]);
   // All 120 permutations run against real DOM, retaining actual nodes and their click handlers.
   const permutations=perms(ids);await p.evaluate(async ps=>{const m=await import('./js/toolorder.js');for(const order of ps){m.set(order);const actual=[...document.getElementById('tools').children].map(n=>n.id);if(actual.join()!==order.map(id=>m.TOOL_DOM[id]).join())throw Error('bad order '+order)}},permutations);
   // Settings keyboard path puts full at every position, each save reaches host; reload uses host + storage.
   for(let at=0;at<5;at++){
    await full.focus();await full.press('End');await full.press('Home');for(let i=0;i<at;i++)await full.press('ArrowRight');await p.waitForTimeout(850);
    const expected=(await chips(p));assert.equal(expected[at],'full');assert.deepEqual(fake.st.prefs.ui.composer_buttons,expected);assert.deepEqual(await row(p),expected.map(id=>dom[id]));
   }
   // Save full first and verify a real refresh, with bootstrap initializer removed.
   await full.focus();await full.press('Home');await p.waitForTimeout(850);const saved=[...fake.st.prefs.ui.composer_buttons];
   await p.screenshot({path:prefix+'-settings-full-first.png'});await p.locator('#setBtn').click();await p.waitForTimeout(200);await p.screenshot({path:prefix+'-toolbar-full-first.png'});
   await p.reload();await p.waitForFunction(()=>window.__ajState==='ready');assert.deepEqual(await row(p),saved.map(id=>dom[id]));
   await p.locator('#tFull').click();await p.waitForFunction(()=>!document.getElementById('fsEd').hidden);await p.locator('#fsExit').click();
   evidence.passed=true;evidence.permutations=120;evidence.savedPositions=5;evidence.toolbarPixelIdentical=true;
   console.log('PASS',lang,theme,'120 permutations / 5 saves / reload / fullscreen / pixels');
  }finally{await ctx.close()}
 }
 results.push(evidence);
}}finally{await B.close();await fake.stop();await web.stop()}
writeFileSync(`${out}/results.json`,JSON.stringify({base,results,physicalDevice:false},null,2)+'\n');
