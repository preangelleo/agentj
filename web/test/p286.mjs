// P286 (Leo 10-14): up to five quotes side by side above the field — each selection adds one (never replaces), 回复 adds the
// whole page, from the same or other reply cards (paging, the full-screen reader); the sixth is refused with a hint, the
// clipboard still gets the latest selection; each chip: source tag (jump + highlight) · first words (full text) · ×;
// one × / Esc clears all; the say carries reply_to / excerpt + quote_more in order; an older host gets 2…5 in the text.
// Playwright + a real Noise session against the fake host. Screenshots 390 / 1440 × 1–5 quotes → reports/qa/p286/.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p286.mjs');let ok=false;
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {chromium}=await import(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright/index.mjs');
const OUT='reports/qa/p286';mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}), fake=await startFakeHost();
const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true});
let checks=0;const check=(a,b)=>{assert.ok(a,b);checks++;};
const R={zh:['第一条回复：部署方案有三步，先备份数据库，再切换流量，最后观察十分钟。','第二条回复：预算还剩百分之四十，下个月要省着用。','第三条回复：测试全部通过，可以合并。'],
         en:['Reply one: the rollout has three steps, back up the database, switch traffic, then watch for ten minutes.','Reply two: forty percent of the budget is left; next month must be lean.','Reply three: all tests passed, ready to merge.']};
const shots=[];
const PICKS=[[0,6],[7,12],[0,6],[7,14],[8,16]];   // newest card ×2, the one before ×2, the first ×1

async function setup(lang,{width,height,mobile,scheme='light',mq=true}){
  fake.reset();fake.st.autoReply=false;fake.st.extraCaps=mq?['mq5']:[];
  const ctx=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,colorScheme:scheme,deviceScaleFactor:mobile?2:1});
  await ctx.grantPermissions(['clipboard-read','clipboard-write'],{origin:web.url.replace(/\/$/,'')}).catch(()=>{});
  const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(fake.newPairing(web.url+'?lang='+lang));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
  await page.evaluate(()=>document.querySelector('#phone-env-dismiss')?.click());
  await fake.send({t:'status',s:'idle',name:'Jarvis',agent:'claude'});
  for(const tx of R[lang]){await fake.addTurn({k:'phone',dev:'other',text:'q'},tx);await page.waitForFunction(t=>document.querySelector('#words').textContent.includes(t),tx.slice(0,8));}
  return {ctx,page,errors};
}
// a real selection inside the card (or the reader) — what a long press / drag leaves — then selectionchange
const select=(page,a,b,root='#words')=>page.evaluate(([a,b,root])=>{const p=document.querySelector(root+' p');const w=document.createTreeWalker(p,NodeFilter.SHOW_TEXT);let t=w.nextNode();while(t&&t.textContent.length<b)t=w.nextNode();t=t||p.firstChild;const r=document.createRange();r.setStart(t,a);r.setEnd(t,b);
  const s=getSelection();s.removeAllRanges();s.addRange(r);document.dispatchEvent(new Event('selectionchange'));},[a,b,root]);
const nq=page=>page.evaluate(()=>{const b=document.getElementById('qbar');return b.hidden?0:b.dataset.n?+b.dataset.n:1;});
const waitN=(page,n)=>page.waitForFunction(n=>{const b=document.getElementById('qbar');return (b.hidden?0:b.dataset.n?+b.dataset.n:1)===n;},n,{timeout:4000});
const unselect=page=>page.evaluate(()=>getSelection().removeAllRanges());
const back=async page=>{await unselect(page);await page.evaluate(()=>document.activeElement?.blur());await page.keyboard.press('ArrowLeft');await page.waitForTimeout(300);};
const T=(page,k,v)=>page.evaluate(([k,v])=>window.__ajT?window.__ajT(k,v):null,[k,v]);
const lastSay=()=>fake.st.says.filter(m=>m.t==='say').at(-1);
async function shot(page,name){await unselect(page);await page.evaluate(()=>document.activeElement?.blur());await page.waitForTimeout(250);await page.screenshot({path:`${OUT}/${name}`});shots.push(name);}

try {
 for(const lang of ['zh','en']){
  const {ctx,page,errors}=await setup(lang,{width:390,height:844,mobile:true});
  const [A,B,C]=R[lang];
  // 1 → one quote fills the row (the old strip)
  await select(page,0,6);await waitN(page,1);
  check(await page.locator('#qbJump').isVisible(),'one quote: the full-width row');
  // 2 → a second selection on the same card adds, does not replace
  await unselect(page);await select(page,8,14);await waitN(page,2);
  const w2=await page.$$eval('#qbar .qc',cs=>cs.map(c=>c.getBoundingClientRect().width));
  check(w2.length===2&&Math.abs(w2[0]-w2[1])<=1,'two quotes: halves '+w2.join(','));
  check(!(await page.locator('#qbJump').isVisible()),'the single row gives way to chips');
  check(await page.$$eval('#qbar .qs',ts=>ts.every(t=>/^\d\d:\d\d · Jarvis$/.test(t.textContent))),'two chips: time · Agent');
  // the same selection again: no duplicate
  await unselect(page);await select(page,8,14);await page.waitForTimeout(700);check(await nq(page)===2,'same selection twice = one quote');
  // 3 → another card, reached by paging
  await back(page);check((await page.locator('#words').textContent()).includes(B.slice(0,8)),'paged to the second reply');
  await select(page,0,5);await waitN(page,3);
  // 4 → 回复 on that card = the whole page, same strip
  await page.locator('#replyBtn').click();await waitN(page,4);
  check(await page.locator('#replyBtn').evaluate(b=>b.classList.contains('on')),'回复 lit for a quoted card');
  // 5 → in the full-screen reader, on the first card
  await back(page);await page.evaluate(()=>document.activeElement?.blur());await page.keyboard.press('f');await page.waitForSelector('#rd:not([hidden])');await page.waitForTimeout(700);
  await select(page,2,9,'#rdWords');await page.waitForFunction(()=>{const b=document.getElementById('qbar');return b.dataset.n==='5';},null,{timeout:4000});
  check(await page.evaluate(()=>!document.getElementById('rd').hidden),'selecting in the reader keeps the reader open');
  // 6 → refused with a hint, the clipboard still gets it
  await unselect(page);await select(page,10,16,'#rdWords');await page.waitForTimeout(900);
  check(await nq(page)===5,'the sixth is not added');
  const toastTx=await page.evaluate(()=>document.querySelector('.toast, #toast')?.textContent||'');
  check(/5/.test(toastTx),'a hint about the limit: '+toastTx);
  const clip=await page.evaluate(()=>navigator.clipboard.readText().catch(()=>null));
  if(clip!==null)check(clip===A.slice(10,16).trim(),'the clipboard holds the latest selection: '+clip);
  await unselect(page);await page.keyboard.press('Escape');await page.waitForSelector('#rd',{state:'hidden'});
  check(await nq(page)===5,'closing the reader keeps the quotes (Esc closed the reader first)');
  // five equal chips, each with its own ×, words cut with an ellipsis
  const g=await page.$$eval('#qbar .qc',cs=>cs.map(c=>{const r=c.getBoundingClientRect(),w=c.querySelector('.qw');return {w:r.width,l:r.left,r:r.right,x:!!c.querySelector('.qcx'),cut:w.scrollWidth>w.clientWidth};}));
  check(g.length===5&&Math.max(...g.map(x=>x.w))-Math.min(...g.map(x=>x.w))<=1,'five equal parts '+g.map(x=>x.w.toFixed(1)).join(','));
  check(g.every((x,i)=>x.x&&(!i||x.l>=g[i-1].r-0.5)),'each chip its own ×, no overlap');
  check(g.some(x=>x.cut)&&g.at(-1).r<=390,'narrow chips cut the words; the strip stays on screen');
  check(await page.$$eval('#qbar .qs',ts=>ts.every(t=>/^\d\d:\d\d$/.test(t.textContent))),'five chips: each tag = the time');
  await shot(page,`${lang}-390-5.png`);
  // the words → the full text; 跳到原文 → that card, the excerpt highlighted
  await page.locator('#qbar .qc').nth(2).locator('.qw').click();await page.waitForSelector('#quoteDlg:not([hidden])');
  check((await page.locator('#quoteDlgText').textContent())===B.slice(0,5).trim(),'the dialog shows the whole quote');
  if(lang==='zh')await shot(page,'zh-390-full.png');
  await page.locator('#quoteDlgJump').click();await page.waitForSelector('#quoteDlg',{state:'hidden'});
  await page.waitForFunction(b=>document.getElementById('words').textContent.includes(b),B.slice(0,8));
  check(await page.waitForFunction(b=>{const h=window.CSS&&CSS.highlights&&CSS.highlights.get('aj-quote');return !!h&&[...h][0]?.toString()===b;},B.slice(0,5).trim(),{timeout:2000}).then(()=>true,()=>false),'the excerpt is highlighted on its card');
  // the source tag jumps too
  await page.locator('#qbar .qc').nth(0).locator('.qs').click();
  await page.waitForFunction(c=>document.getElementById('words').textContent.includes(c),C.slice(0,8));
  check(true,'the source tag jumps back');
  // × removes one, order kept
  await page.locator('#qbar .qc').nth(3).locator('.qcx').click();await waitN(page,4);
  // send: reply_to / excerpt = the first, quote_more = the rest in order
  await page.locator('#input').fill(lang==='zh'?'这四段一起看':'look at these four');await page.locator('#send').click();
  await page.waitForFunction(()=>document.getElementById('qbar').hidden,null,{timeout:5000});
  const s=lastSay(),ids=fake.st.turns.map(t=>t.id);
  check(s.reply_to===ids[2]&&s.excerpt===C.slice(0,6).trim(),'first quote → reply_to + excerpt');
  check(JSON.stringify(s.quote_more)===JSON.stringify([{id:ids[2],ex:C.slice(8,14).trim()},{id:ids[1],ex:B.slice(0,5).trim()},{id:ids[0],ex:A.slice(2,9).trim()}]),'quote_more in order: '+JSON.stringify(s.quote_more));
  // the sent message's card shows all four quotes
  await page.waitForFunction(()=>!document.getElementById('omQuoteMore').hidden&&document.getElementById('omQuoteMore').children.length===3,null,{timeout:5000});
  check(true,'the source card shows quotes 2…4');
  // clear all: the strip's × and Esc
  await select(page,0,3);await waitN(page,1);await page.locator('#replyBtn').click();await waitN(page,2);
  check((await page.locator('#qbX').getAttribute('aria-label'))===(lang==='zh'?'清空全部引用（Esc）':'Clear all quotes (Esc)'),'× = clear all with two or more');
  await page.locator('#qbX').click();await waitN(page,0);
  check(await page.evaluate(()=>document.activeElement?.id!=='input'),'clear quotes keeps the touch keyboard closed');
  // P375 no longer focuses the composer: dispatch the collapse before starting a fresh selection gesture.
  await page.evaluate(()=>{getSelection().removeAllRanges();document.dispatchEvent(new Event('selectionchange'));});
  await select(page,0,3);await waitN(page,1);await unselect(page);await select(page,4,7);await waitN(page,2);
  await unselect(page);await page.evaluate(()=>document.activeElement?.blur());await page.keyboard.press('Escape');await waitN(page,0);
  check(true,'Esc clears all');
  check(errors.length===0,errors.join('\n'));await ctx.close();
 }
 // an older computer (no mq5): quotes 2…5 travel in the text, labelled; nothing is lost
 {
  const {ctx,page,errors}=await setup('zh',{width:390,height:844,mobile:true,mq:false});
  await select(page,0,6);await waitN(page,1);await unselect(page);await select(page,8,14);await waitN(page,2);
  await page.locator('#input').fill('旧Agent J 主机');await page.locator('#send').click();
  await page.waitForFunction(()=>document.getElementById('qbar').hidden,null,{timeout:5000});
  const s=lastSay();
  check(s.quote_more===undefined&&s.excerpt===R.zh[2].slice(0,6),'older host: the first as before, no quote_more');
  check(s.text.startsWith('旧Agent J 主机\n\n（下面是我另外引用的 1 段')&&s.text.includes('引用 2 · #')&&s.text.includes('> '+R.zh[2].slice(8,14)),'older host: the rest in the text '+JSON.stringify(s.text));
  check(errors.length===0,errors.join('\n'));await ctx.close();
 }
 // screenshots: 390 / 1440 × 1–5 quotes (light), 390 dark × 5
 for(const [tag,size] of [['390',{width:390,height:844,mobile:true}],['1440',{width:1440,height:900,mobile:false}]]){
  const {ctx,page,errors}=await setup('zh',{...size});
  for(let i=0;i<5;i++){await unselect(page);if(i===2||i===4)await back(page);await select(page,...PICKS[i]);await waitN(page,i+1);await shot(page,`zh-${tag}-${i+1}.png`);}
  check(errors.length===0,errors.join('\n'));await ctx.close();
 }
 {
  const {ctx,page}=await setup('zh',{width:390,height:844,mobile:true,scheme:'dark'});
  for(let i=0;i<5;i++){await unselect(page);if(i===2||i===4)await back(page);await select(page,...PICKS[i]);await waitN(page,i+1);}
  await shot(page,'zh-390-dark-5.png');await ctx.close();
 }
 writeFileSync(OUT+'/browser.json',JSON.stringify({checks,ok:true,shots},null,2)+'\n');console.log('P286 Playwright checks:',checks);ok=true;
} finally {rec.record('runP286',ok);rec.write();await browser.close();await fake.stop();await web.stop();}
