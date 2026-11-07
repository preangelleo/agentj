// Real product ES module, real isolated Chromium DOM. No network requests escape routing.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.AJ_PLAYWRIGHT_MODULE || 'playwright');
const root = resolve('web/public');
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM || undefined,
  headless: true, env: { ...process.env }
});
let count = 0;
try {
 const page = await browser.newPage({ viewport: { width: 390, height: 844 }});
 await page.route('**/*', async route => {
   const u = new URL(route.request().url());
   if (u.origin !== 'http://127.0.0.1:38471') return route.abort();
   if (u.pathname === '/') return route.fulfill({ contentType:'text/html', body:'<html><head><link rel="stylesheet" href="/brand/palette.css"><link rel="stylesheet" href="/brand/base.css"><link rel="stylesheet" href="/menu.css"></head><body><main class="menupage" id="test"></main></body></html>' });
   let path = resolve(root, '.' + u.pathname);
   if (!path.startsWith(root + '/')) return route.abort();
   try { return route.fulfill({ contentType: extname(path)==='.js'?'text/javascript':extname(path)==='.css'?'text/css':'text/plain', body:await readFile(path) }); }
   catch { return route.fulfill({ status:404, body:'' }); }
 });
 await page.goto('http://127.0.0.1:38471/');
 const result = await page.evaluate(async () => {
   const m = await import('/js/order-cards.js');
   const busy = await import('/js/busy.js');
   const oid='o20261107-000001-abcd';
   const items=busy.sanitize({tasks:[{id:'order:'+oid,label:'CEO'}, {id:'unrelated',label:'main'}, {id:'child',parent:oid,label:'sub'}]});
   const ordered=m.orderedBusy(items);
   const grouped=m.groupOrders(items);
   const crumb=m.originBreadcrumb(['Main','<script>forged</script>']); document.querySelector('main').append(crumb);
   const sent=[];
   const gate=m.gateCard(oid,{id:'g123456',question:'Preference',options:['A','B'],default:'B'}, {t:k=>k,toMain:async r=>sent.push(r)});
   document.querySelector('main').append(gate); gate.querySelector('button').click();
   await new Promise(r=>setTimeout(r,0));
   return {ids:ordered.map(x=>x.id), groups:grouped.size, members:grouped.get(oid).length,
       sourceOrder:items.map(x=>x.id), text:crumb.textContent, scripts:crumb.querySelectorAll('script').length,
       sent, invalid:m.orderId({parent:'../state'}), single:m.orderedBusy([items[0]]).length};
 });
 for (const [name,condition] of Object.entries({
   contiguous:result.ids.join(',')==='order:o20261107-000001-abcd,child,unrelated',
   originalList:result.sourceOrder[1]==='unrelated', oneMain:result.groups===1&&result.members===2,
   escaped:result.scripts===0&&result.text.includes('<script>'), gateId:result.sent[0].gate==='g123456',
   preferenceOnly:result.sent[0].action==='send'&&result.sent[0].answer==='A',
   badOrigin:result.invalid==='', single:result.single===1
 })) { assert.ok(condition,name); count++; }
 console.log(`PASS ${count} projection DOM assertions`);
} finally { await browser.close(); }
