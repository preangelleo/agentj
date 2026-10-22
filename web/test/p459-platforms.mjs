// P459: P458 before/candidate after on the SAME Android/desktop page: exact screenshot equality.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright'),out='reports/qa/p459/platforms';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),b=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});const results=[];
try{for(const device of ['android','desktop'])for(const scheme of ['light','dark']){
 const ctx=await b.newContext(device==='android'?{...pw.devices['Pixel 7'],deviceScaleFactor:1,colorScheme:scheme}:{viewport:{width:1280,height:900},colorScheme:scheme});
 const p=await ctx.newPage();let before=false;
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();if(before&&u.pathname.endsWith('.css'))return r.fulfill({contentType:'text/css',body:execFileSync('git',['show','d6545fb98:web/public'+u.pathname],{encoding:'utf8'})});return r.continue()});
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.getElementById('input').value='P459 stable fixture';});
 for(const editor of [false,true]){
  await p.evaluate(async open=>{const m=await import('./js/fsedit.js');open?m.open():m.close();document.activeElement?.blur()},editor);
  const imgs=[];
  for(const mode of ['before','after']){
   before=mode==='before';await p.evaluate(async mode=>{await Promise.all([...document.querySelectorAll('link[rel=stylesheet]')].map(e=>new Promise((resolve,reject)=>{e.onload=resolve;e.onerror=reject;const u=new URL(e.href);u.searchParams.set('p459',mode);e.href=u.href})));await document.fonts.ready;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);document.getElementById('toast').classList.remove('on');},mode);
   await p.mouse.move(200,300);await p.waitForTimeout(100);imgs.push(await p.screenshot({path:`${out}/${device}-${scheme}-${editor?'editor':'page'}-${mode}.png`}));
  }
  assert.ok(imgs[0].equals(imgs[1]),`${device}/${scheme}/${editor} byte-identical screenshot`);results.push({device,scheme,editor,pixelDifference:0});
 }
 assert.equal(await p.locator('#iosdiag').count(),0);await ctx.close();
}}finally{await b.close();await fake.stop();await web.stop()}
writeFileSync(`${out}/results.json`,JSON.stringify(results,null,2)+'\n');console.log('PASS Android/desktop: 8 byte-identical before/after screenshots; diagnostic absent');
