// P488: approved 0287 CSS / selected-tree CSS on the SAME current page: exact screenshot equality.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
// Ordinary page/editor regression uses chairman-reviewed 0287 batch CSS (c97096e917).
// Historical feature-introduction trees precede later approved styles; zero-pixel
// comparison remains unchanged. New stylesheet paths stay identical on both sides.
const cssSourceReceipts=[];
const cssHash=s=>createHash('sha256').update(s).digest('hex');
const historicalStyles=new Set(execFileSync('git',['ls-tree','-r','--name-only','c97096e917','--','web/public'],{encoding:'utf8'}).trim().split('\n').filter(p=>p.endsWith('.css')).map(p=>'/'+p.slice('web/public/'.length)));
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
const frozenHash=execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim();
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright'),out=process.env.P488_PLATFORM_OUT||'reports/qa/p488/platforms';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),b=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});const wk=await pw.webkit.launch();const results=[];
try{for(const device of ['android','desktop'])for(const scheme of ['light','dark']){
 const ios=['safari','crios','standalone'].includes(device);
 const safari=pw.devices['iPhone 13'].userAgent;
 const options=ios?{...pw.devices['iPhone 13'],deviceScaleFactor:1,colorScheme:scheme,userAgent:device==='crios'?safari.replace(/Version\/[\d.]+/,'CriOS/131.0.6778.81'):safari}:device==='android'?{...pw.devices['Pixel 7'],deviceScaleFactor:1,colorScheme:scheme}:{viewport:{width:1280,height:900},colorScheme:scheme};
 const ctx=await (ios?wk:b).newContext({...options,serviceWorkers:'block'});
 await ctx.addInitScript(s=>Object.defineProperty(navigator,'standalone',{get:()=>s}),device==='standalone');
 const p=await ctx.newPage();let before=false;
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();if(u.pathname.endsWith('.css')){const historical=before&&historicalStyles.has(u.pathname),response=historical?null:await r.fetch(),body=historical?execFileSync('git',['show','c97096e917:web/public'+u.pathname],{encoding:'utf8'}):await response.text();cssSourceReceipts.push({device,scheme,mode:before?'before':'after',path:u.pathname,source:historical?'c97096e917':'selected-tree',sha256:cssHash(body)});return historical?r.fulfill({contentType:'text/css',body}):r.fulfill({response,body});}return r.continue()});
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.getElementById('input').value='P488 stable fixture';});
 for(const editor of [false,true]){
  await p.evaluate(async open=>{const m=await import('./js/fsedit.js');open?m.open():m.close();document.activeElement?.blur()},editor);
  const imgs=[];
  for(const mode of ['before','after']){
   before=mode==='before';await p.evaluate(async mode=>{await Promise.all([...document.querySelectorAll('link[rel=stylesheet]')].map(e=>new Promise((resolve,reject)=>{e.onload=resolve;e.onerror=reject;const u=new URL(e.href);u.searchParams.set('p459',mode);e.href=u.href})));await document.fonts.ready;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);document.getElementById('toast').classList.remove('on');},mode);
   const repairCSS=await p.evaluate(()=>[...document.styleSheets].filter(s=>s.href?.includes('/app.css')).some(s=>[...s.cssRules].some(r=>r.selectorText==='html[data-ios="1"] .water .wave')));
   assert.equal(repairCSS,true,'approved visual baseline and candidate both retain the shipped iOS repair');
   await p.mouse.move(200,300);await p.waitForTimeout(100);imgs.push(await p.screenshot({path:`${out}/${device}-${scheme}-${editor?'editor':'page'}-${mode}.png`}));
  }
  assert.ok(imgs[0].equals(imgs[1]),`${device}/${scheme}/${editor} byte-identical screenshot`);results.push({device,scheme,editor,pixelDifference:0});
 }
 assert.equal(await p.locator('#iosdiag').count(),0);await ctx.close();
}}finally{await b.close();await wk.close();await fake.stop();await web.stop()}
assert.equal(execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim(),frozenHash,'source must remain frozen during qualification');
for(const row of results)row.combined=frozenHash;
assert.ok(cssSourceReceipts.some(x=>x.path==='/app.css'&&x.mode==='before'&&x.source==='c97096e917'),'approved app.css was actually requested and served');
assert.ok(cssSourceReceipts.some(x=>x.path==='/app.css'&&x.mode==='after'&&x.source==='selected-tree'),'current app.css was actually requested and served');
writeFileSync(`${out}/css-source-receipts.json`,JSON.stringify({baseline:'c97096e917',comparison:'same current DOM; historical CSS for existing paths; new styles identical on both sides',requests:cssSourceReceipts},null,2)+'\n');
writeFileSync(`${out}/results.json`,JSON.stringify(results,null,2)+'\n');console.log('PASS Android/desktop: 8 byte-identical before/after screenshots; diagnostic absent');
