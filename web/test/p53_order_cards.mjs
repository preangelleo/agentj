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
   const path = resolve(root, '.' + u.pathname);
   if (!path.startsWith(root + '/')) return route.abort();
   try { return route.fulfill({ contentType: extname(path)==='.js'?'text/javascript':extname(path)==='.css'?'text/css':'text/plain', body:await readFile(path) }); }
   catch { return route.fulfill({ status:404, body:'' }); }
 });
 await page.goto('http://127.0.0.1:38471/');
 const result = await page.evaluate(async () => {
   const m = await import('/js/order-cards.js');
   const sent = [], main = document.querySelector('main');
   const order = { id:'o20261107-000001-abcd', title:'<img src=x onerror=alert(1)>', state:'stopped',
     origin:['Main','CEO'], head:{ done:'1/2',ready:'no',needs:'review',verdict:'attention' },
     gates:[{ question:'Reversible preference?', options:['A','B'], default:'B' }] };
   main.append(m.orderCard(order, { t:k=>k, input:async()=> 'extra', toMain:async x=>sent.push(x) }));
   const buttons = main.querySelectorAll('button');
   buttons[0].click(); await new Promise(r=>setTimeout(r,0));
   buttons[1].click(); await new Promise(r=>setTimeout(r,0));
   buttons[2].click(); await new Promise(r=>setTimeout(r,0));
   buttons[4].click(); await new Promise(r=>setTimeout(r,0));
   const group = m.groupOrders([{order_id:order.id},{order_id:order.id},{order_id:'../../state'}]);
   const text = main.textContent;
   const escaped = main.querySelectorAll('img').length === 0;
   const tap = [...buttons].every(b=>b.getBoundingClientRect().height >= 44);
   const classes = [...buttons].every(b=>b.classList.contains('aj-btn') && b.type==='button');
   main.replaceChildren(m.orderCard(order,{t:k=>k,input:async()=>'',toMain:async x=>sent.push(x),stopped:true}));
   const disabled = [...main.querySelectorAll('button')].every(b=>b.disabled);
   main.replaceChildren(m.orderCard({...order,state:'done',gates:[]},{t:k=>k,input:async()=>'',toMain:async x=>sent.push(x)}));
   const terminal = [...main.querySelectorAll('button')].every(b=>b.disabled);
   return { sent,text,escaped,classes,disabled,terminal,tap,groups:group.size,members:group.get(order.id).length };
 });
 for (const [name,condition] of Object.entries({
   actions: result.sent.map(x=>x.action).join(',')==='send,stop,resume,send',
   append: result.sent[0].text==='extra', gate: result.sent[3].text.endsWith('\nB'),
   origin: result.text.includes('Main › CEO'), default:result.text.includes('p53.default: B'),
   report:result.text.includes('p53.done: 1/2'), escaped:result.escaped, standardControls:result.classes,
   estop:result.disabled, terminal:result.terminal, tapTargets:result.tap, grouping:result.groups===1&&result.members===2
 })) { assert.ok(condition,name); count++; }
 console.log(`PASS ${count} DOM/browser assertions`);
} finally { await browser.close(); }
