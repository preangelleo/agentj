// Real app screenshots through Playwright; synthetic data only. Never uses production/CDP :9222.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch } from './browser.mjs';
const { chromium } = await import(process.env.P427_PLAYWRIGHT || `${homedir()}/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs`);
const out=fileURLToPath(new URL('../../site/content/docs/57-scenes/',import.meta.url));
const evidence=fileURLToPath(new URL('../../../reports/qa/p427/',import.meta.url));
mkdirSync(evidence,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}), fake=await startFakeHost(), B=await launch();
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${B.port}`), records=[];
try {
for(const lang of ['zh','en']) for(const [name,width,height] of [['iphone',390,844],['android',412,915],['desktop',1440,900],['tv',1920,1080],['display',1024,600],['temporary',1280,800],['remotes',390,844]]) {
 fake.reset();
 const context=await browser.newContext({viewport:{width,height},locale:lang==='zh'?'zh-CN':'en-US'});
 const p=await context.newPage(), errors=[], external=[];
 p.on('pageerror',e=>errors.push(e.message));
 p.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 await p.route('**/*',route=>{const u=route.request().url(); if([web.url,fake.relay].some(x=>u.startsWith(x))||/^(blob:|data:)/.test(u))return route.continue(); external.push(u);return route.abort();});
 await p.addInitScript(()=>{navigator.storage.persisted=async()=>true; navigator.storage.persist=async()=>true;});
 const base=web.url+'?lang='+lang;
 if(name==='temporary') {
  await p.goto(base); await p.waitForFunction(()=>window.__ajState==='idle');
 } else {
  await p.goto(fake.newPairing(base)); await p.waitForFunction(()=>window.__ajState==='awaiting-approval'); await fake.approve(); await p.waitForFunction(()=>window.__ajState==='ready');
  await p.evaluate(()=>{for(const id of ['confirm-no','a2hs-ok','phone-env-dismiss']){const e=document.getElementById(id); if(e&&e.getBoundingClientRect().height)e.click();}});
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  await fake.addTurn({k:'phone',dev:'sample',name:'Phone',text:lang==='zh'?'给我今天的工作摘要':'Give me today’s work summary'},lang==='zh'?'## 今天的进展\n\n- 库存已核对\n- 周报草稿已备好\n- 等你确认补货清单\n\n| 商品 | 建议补货 |\n|---|---|\n| 马克杯 | 60 |\n| 棒球帽 | 40 |\n\n先看清单，再决定下一步。':'## Today’s progress\n\n- Stock checked\n- Weekly report drafted\n- Restock list ready for your review\n\n| Item | Restock |\n|---|---|\n| Mug | 60 |\n| Cap | 40 |\n\nReview the list before deciding what to do next.');
  await p.waitForFunction(()=>document.getElementById('pg').textContent==='1 / 1');
  if(name==='remotes') {
   const now=Math.floor(Date.now()/1000);
   fake.remotes({limit:10,items:[{id:'ME',me:true,name:lang==='zh'?'我的手机':'My phone',paired_at:now-3600,seen:now,online:true,source:'local'},{id:'Ab4Tq7Wz9Lm3Xc8P',name:lang==='zh'?'临时电脑':'Temporary computer',paired_at:now-600,seen:now,online:true,source:'phone'}]});
   await p.locator('#setBtn').click();await p.locator('#set-remotes-open').click();await p.waitForFunction(()=>document.querySelectorAll('#rm-list .rmitem').length===2);
   await p.locator('[data-id="Ab4Tq7Wz9Lm3Xc8P"] [data-act="unbind"]').click();await p.locator('#confirm-no').click();assert.equal(fake.st.remotes.items.length,2);
   // This screenshot shows the remote to remove, before approving its removal.
  }
 }
 await p.waitForTimeout(700);
 await p.screenshot({path:`${out}/${name}.${lang}.webp`,type:'webp',quality:76,mask:name==='temporary'?[p.locator('#rv-qr')]:[]});
 if(name==='remotes') {
  await p.locator('[data-id="Ab4Tq7Wz9Lm3Xc8P"] [data-act="unbind"]').click();await p.locator('#confirm-yes').click();await p.waitForFunction(()=>document.querySelectorAll('#rm-list .rmitem').length===1);
  assert.equal(fake.st.remotes.writes.at(-1).sigOk,true);assert.equal(fake.st.remotes.writes.at(-1).t,'dev_unbind');
 }
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 records.push({name,lang,width,height,errors,external,signedRemoval:name==='remotes'});
 await context.close();console.log(name,lang,'PASS');
}
writeFileSync(evidence+'/103-scenes.json',JSON.stringify({verdict:'PASS',capture:'Playwright / real app / fake host',records},null,2)+'\n');
} finally {await browser.close();await B.close();await fake.stop();await web.stop();}
