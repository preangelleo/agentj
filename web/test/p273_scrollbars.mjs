// P273 (2026-10-14 19:10): 「在任何页面都不出现这种下拉状态条了」. Real Chromium with CLASSIC scrollbars switched on
// (no --hide-scrollbars; desktop 1280 and a 390 phone), a real Noise session against the fake host. On each surface — the
// chat, the opened source card, the reader, the shortcut sheet, search, Settings, the slash menu, an approval with a long
// command, the e-mail dialog, a Markdown preview — every element that actually overflows is checked: scrollbar-width
// none, no bar takes layout width, and it still scrolls (scrollTop moves). Results → reports/qa/p273/scrollbars.json.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {randomBytes,createHash} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const rec=parityRecorder('web/test/p273_scrollbars.mjs');let ok=false;
const {chromium}=await import(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright/index.mjs');
const OUT='reports/qa/p273';mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}), fake=await startFakeHost();
const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,ignoreDefaultArgs:['--hide-scrollbars']});
const LONG='长回复\n\n'+Array.from({length:60},(_,i)=>`第 ${i+1} 行：滚动但不画滚动条。`).join('\n\n');
const CMD=Array.from({length:40},(_,i)=>`echo "step ${i}: copy part-${i}.json"`).join('\n');
const MD=Buffer.from('# Preview\n\n'+Array.from({length:120},(_,i)=>`- line ${i+1}`).join('\n')+'\n'),mid='S'.repeat(22);
const file={mid,name:'NOTES.md',mime:'text/markdown',kind:'file',bytes:MD.length,sha256:createHash('sha256').update(MD).digest('hex'),ref:'out/NOTES.md'};
// every overflowing element on screen (the document included): its bar width, scrollbar-width, and whether it scrolls
const SCAN=`(() => { const out = [];
  for (const n of [document.documentElement, ...document.querySelectorAll('body *')]) {
    if (!(n.offsetParent || n === document.documentElement || getComputedStyle(n).position === 'fixed')) continue;
    const cs = getComputedStyle(n), ov = cs.overflowY + ' ' + cs.overflowX;
    if (!/(auto|scroll)/.test(ov) && n !== document.documentElement) continue;
    const vy = n.scrollHeight > n.clientHeight + 2, vx = n.scrollWidth > n.clientWidth + 2;
    if (!vy && !vx) continue;
    const bw = (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0), bh = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
    const barY = n === document.documentElement ? innerWidth - n.clientWidth : n.offsetWidth - n.clientWidth - bw;
    const barX = n === document.documentElement ? innerHeight - n.clientHeight : n.offsetHeight - n.clientHeight - bh;
    let moves = null;
    if (vy && /(auto|scroll)/.test(cs.overflowY)) { const t = n.scrollTop; n.scrollTop = t + 40; moves = n.scrollTop !== t; n.scrollTop = t; }
    out.push({ el: n.id ? '#' + n.id : n.tagName.toLowerCase() + (n.className && typeof n.className === 'string' ? '.' + n.className.trim().split(/\\s+/).join('.') : ''),
      sw: cs.scrollbarWidth, barY: vy ? barY : 0, barX: vx ? barX : 0, moves });
  } return out; })()`;
const results={};let checks=0;
try {
 for(const [tag,vp] of [['desktop1280',{width:1280,height:900,mobile:false}],['phone390',{width:390,height:844,mobile:true}]]){
  fake.reset();fake.st.extraCaps=['contacts','tgfile'];fake.st.autoReply=false;
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='ct_list'){await send({t:'ct_res',r:m.r,ok:true,contacts:Array.from({length:30},(_,i)=>({id:'c'+i,name:'Contact '+i,emails:['c'+i+'@example.com']})),me:['owner@example.com'],mail:{ready:true}});return true;}
   if(m.t==='media_get'){await send({t:'media_chunk',mid,o:0,d:MD.toString('base64url'),last:true});return true;}
   return false;
  };
  const ctx=await browser.newContext({viewport:{width:vp.width,height:vp.height},isMobile:vp.mobile,hasTouch:vp.mobile});
  const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(fake.newPairing(web.url+'?lang=zh'));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
  await page.evaluate(()=>document.querySelector('#phone-env-dismiss')?.click());
  await fake.send({t:'status',s:'idle',name:'Agent J',agent:'claude'});
  await fake.addTurn({k:'phone',dev:'other',text:Array.from({length:30},(_,i)=>'原话第 '+(i+1)+' 行').join('\n')},LONG);
  await fake.updateTurn(fake.st.turns.at(-1).id,{media:[file]});
  await page.waitForSelector('.mcard');
  const blur=()=>page.evaluate(()=>document.activeElement?.blur());
  const esc=async()=>{await page.keyboard.press('Escape');await page.waitForTimeout(250);};
  const surfaces=[
   ['chat',async()=>{}],
   ['source-open',async()=>{await blur();await page.keyboard.press('o');await page.waitForFunction(()=>document.getElementById('om').classList.contains('open'));},esc],
   ['reader',async()=>{await blur();await page.keyboard.press('f');await page.waitForSelector('#rd:not([hidden])');await page.waitForTimeout(450);},esc],
   ['keys',async()=>{await blur();await page.keyboard.press('?');await page.waitForSelector('#keys:not([hidden])');},esc],
   ['search',async()=>{await blur();await page.keyboard.press('Control+k');await page.waitForSelector('#srch:not([hidden])');await page.keyboard.type('行');await page.waitForTimeout(600);},esc],
   ['settings',async()=>{await blur();await page.keyboard.press('Control+Comma');await page.waitForSelector('#settings:not([hidden])');},esc],
   ['slash-menu',async()=>{await page.locator('#input').focus();await page.keyboard.type('/');await page.waitForTimeout(400);},async()=>{await esc();await page.locator('#input').fill('');await blur();}],
   ['mail-dialog',async()=>{await blur();await page.evaluate(()=>window.AgentJMailForward.open({turn:1,whole:true}));await page.waitForSelector('#ctFwd:not([hidden])');await page.waitForTimeout(400);},esc],
   ['md-preview',async()=>{await page.evaluate(()=>document.querySelector('.mcard .mact.pv, .mcard [class*="pv"]')?.click());await page.waitForSelector('.mview:not([hidden])',{timeout:8000});await page.waitForTimeout(800);},esc],
   ['approval',async()=>{await fake.send({t:'status',s:'waiting',agent:'claude',name:'Agent J'});await fake.ask({id:randomBytes(16).toString('hex'),tool:'Bash',summary:CMD,ttl:120,cat:['delete'],why:'rm -rf'});
     await page.waitForFunction(()=>document.body.textContent.includes('step 39'));await page.evaluate(()=>document.getElementById('cmdMore')?.click());await page.waitForTimeout(300);}],
  ];
  results[tag]={};
  for(const [name,open,close] of surfaces){
   await open();
   const found=await page.evaluate(SCAN);
   results[tag][name]=found;
   const bad=found.filter(f=>f.sw!=='none'||f.barY!==0||f.barX!==0||f.moves===false);
   assert.ok(!bad.length,`${tag} ${name}: a drawn / dead scroller ${JSON.stringify(bad)}`);checks++;
   assert.ok(found.length>0||['keys','search','slash-menu'].includes(name),`${tag} ${name}: something overflows here (else the case proves nothing)`);checks++;
   await page.screenshot({path:`${OUT}/scrollbars-${tag}-${name}.png`});
   if(close)await close();
  }
  // the frame inside the Markdown / HTML preview: srcdoc carries the no-bar style (checked statically in p273.test.mjs)
  assert.ok(errors.length===0,errors.join('\n'));checks++;
  await ctx.close();
 }
 const total=Object.values(results).reduce((n,r)=>n+Object.values(r).reduce((a,x)=>a+x.length,0),0);
 assert.ok(total>=15,'enough overflowing scrollers were checked: '+total);checks++;
 writeFileSync(OUT+'/scrollbars.json',JSON.stringify({rule:'no scrollbar drawn, every overflow still scrolls',checks,scrollers:total,results},null,1)+'\n');
 console.log('P273 scrollbar walk: '+checks+' checks over '+total+' overflowing scrollers');ok=true;
} finally {rec.record('noScrollbarsAnywhere',ok);rec.write();await browser.close();await fake.stop();await web.stop();}
