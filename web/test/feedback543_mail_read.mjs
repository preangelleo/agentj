// #543: processed-read is mandatory, not an owner setting. Real paired page, synthetic mailbox only.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {chromium}=await import(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright/index.mjs');
const out=resolve(process.env.P543_OUT||'reports/qa/mail-mark-read-543-20261106/ui');mkdirSync(out,{recursive:true});
const phase=process.env.P543_PHASE||'after';
const web=await startWebServer(process.env.P543_PUBLIC?{publicDir:resolve(process.env.P543_PUBLIC)}:{}),fake=await startFakeHost();
const browser=await chromium.launch({executablePath:process.env.CHROMIUM||'/usr/bin/chromium'});
const results=[];
try {
 for(const lang of ['zh','en'])for(const width of [390,1440])for(const theme of ['light','dark']){
  fake.reset();fake.st.mail=null;fake.st.autoReply=false;fake.st.prefs={appearance:{language:lang,theme}};
  const page=await browser.newPage({viewport:{width,height:width===390?844:1000},colorScheme:theme});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try {
   await page.goto(fake.newPairing(web.url+'?lang='+lang));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
   await page.evaluate(()=>{document.querySelector('#phone-env-dismiss')?.click();document.querySelector('#a2hs-ok')?.click();});
   await page.evaluate(async()=>{await import('./js/settings.js').then(m=>m.open());document.getElementById('set-mail-nav').click();});
   await page.waitForFunction(()=>!document.getElementById('set-mail').hidden&&document.getElementById('mail-blocked').children.length);
   const absent=await page.locator('#mail-mark-read').count()===0;
   await page.locator('#mail-blocked button').click();
   await page.waitForFunction(()=>document.getElementById('mail-allow').children.length);
   const saves=fake.st.mailMsgs.filter(x=>x.t==='mail_set');
   const saveWorks=saves.some(x=>x.cfg.allow?.[0]?.role==='guest')&&saves.every(x=>!Object.hasOwn(x.cfg,'mark_processed_read'));
   await page.evaluate(()=>{document.querySelectorAll('#mail-allow li>span,#mail-blocked li>span').forEach(e=>e.textContent='');}); // no mailbox accounts in screenshots
   await page.locator('#mail-poll').evaluate(e=>e.scrollIntoView({block:'center'}));
   await page.screenshot({path:resolve(out,`${phase}-${lang}-${theme}-${width}.png`)});
   const r={lang,width,theme,absent,saveWorks,errors};results.push(r);console.log(JSON.stringify(r));
  } finally {await page.close();}
 }
 writeFileSync(resolve(out,phase+'-results.json'),JSON.stringify(results,null,2)+'\n');
 assert.ok(results.every(x=>x.absent&&x.saveWorks&&x.errors.length===0),'toggle absent, ordinary save works without optional field, no page errors');
} finally {await browser.close();await fake.stop();await web.stop();}
