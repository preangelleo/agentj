// P273: the full-screen reader rebuilt for long reading — fixed header = the card's header (Agent + time · unread · page; no ×),
// fixed footer (type-size icon · the reply card's six icons), only the words scroll, no scrollbar, the dialogs' Noir.
// Playwright + real touch events (CDP) + a real Noise session against the fake host. Screenshots → reports/qa/p273/.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p273.mjs');let ok=false;
import {mkdirSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {chromium}=await import(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright/index.mjs');
const OUT='reports/qa/p273';mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}), fake=await startFakeHost();
const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--autoplay-policy=no-user-gesture-required','--mute-audio']});
let checks=0;const check=(a,b)=>{assert.ok(a,b);checks++;};
const bytes=Buffer.from('# P273 file\nSafe fixture.\n'),mid='R'.repeat(22);
const file={mid,name:'REPORT.md',mime:'text/markdown',kind:'file',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),ref:'out/REPORT.md'};
const LONG={zh:'长回复：周报全文\n\n'+Array.from({length:40},(_,i)=>`第 ${i+1} 段：这是一段用来测试长时间阅读的文字，滚动时页眉和页脚都不动。`).join('\n\n'),
            en:'Long reply: the weekly report\n\n'+Array.from({length:40},(_,i)=>`Paragraph ${i+1}: text for long reading; the header and the footer stay put while it scrolls.`).join('\n\n')};
const SHORT={zh:'短回复：好的，已经处理完。',en:'Short reply: done.'};
const ATT={zh:'带附件的回复：报告在下面。',en:'A reply with a file: the report is below.'};
const IDS=['rdReadBtn','rdSpeakBtn','rdFwdBtn','rdMailBtn','rdCopyBtn','rdReplyBtn'];
const shots=[];

async function setup(lang,{width,height,mobile,scheme}){
  fake.reset();fake.st.tgfwd=true;fake.st.extraCaps=['contacts','tgfile'];fake.st.autoReply=false;
  fake.st.onApp=async(c,m,send)=>{
    if(m.t==='ct_list'){await send({t:'ct_res',r:m.r,ok:true,contacts:[],me:['owner@example.com'],mail:{ready:true}});return true;}
    if(m.t==='media_get'){await send({t:'media_chunk',mid,o:0,d:bytes.toString('base64url'),last:true});return true;}
    return false;
  };
  const ctx=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,colorScheme:scheme,deviceScaleFactor:mobile?2:1});
  await ctx.grantPermissions(['clipboard-read','clipboard-write'],{origin:web.url.replace(/\/$/,'')}).catch(()=>{});
  const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(fake.newPairing(web.url+'?lang='+lang));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
  await page.evaluate(()=>document.querySelector('#phone-env-dismiss')?.click());
  await fake.send({t:'status',s:'idle',name:'Agent J',agent:'claude'});
  const add=async(text,q)=>{await fake.addTurn({k:'phone',dev:'other',text:q||'fixture'},text);await page.waitForFunction(tx=>document.querySelector('#words').textContent.includes(tx),text.split('\n')[0]);};
  await add(LONG[lang]);await add(SHORT[lang]);await add(ATT[lang]);
  await fake.updateTurn(fake.st.turns.at(-1).id,{media:[file]});
  await page.waitForSelector('.mcard');
  return {ctx,page,errors,add};
}
const words=page=>page.locator('#rdWords').textContent();
const rect=(page,sel)=>page.locator(sel).evaluate(n=>{const r=n.getBoundingClientRect();return {t:r.top,b:r.bottom,l:r.left,r:r.right,w:r.width,h:r.height};});
async function openReader(page){await page.waitForTimeout(400);await page.evaluate(()=>document.activeElement?.blur());if(await page.locator('#readBtn').isVisible())await page.locator('#readBtn').click();else await page.keyboard.press('f');await page.waitForSelector('#rd:not([hidden])');await page.waitForTimeout(450);}

try {
 // ---- behaviour: a 390 px phone, touch, both languages ---------------------------------------------------------------
 for(const lang of ['zh','en']){
  const {ctx,page,errors,add}=await setup(lang,{width:390,height:844,mobile:true,scheme:'dark'});
  const cdp=await ctx.newCDPSession(page);
  const touch=(type,x,y)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'||type==='touchCancel'?[]:[{x,y}]});
  const center=async sel=>{const b=await page.locator(sel).boundingBox();return {x:b.x+b.width/2,y:b.y+b.height/2};};
  const tap=async(sel,hold=60)=>{const c=await center(sel);await touch('touchStart',c.x,c.y);await page.waitForTimeout(hold);await touch('touchEnd');};
  const dtap=async sel=>{await tap(sel);await page.waitForTimeout(90);await tap(sel);await page.waitForTimeout(350);};
  const swipe=async(dx,dy=0)=>{const x=190,y=420;await touch('touchStart',x,y);for(let i=1;i<=6;i++){await touch('touchMove',x+dx*i/6,y+dy*i/6);await page.waitForTimeout(20);}await touch('touchEnd');await page.waitForTimeout(300);};

  await openReader(page);
  check((await words(page)).includes(ATT[lang]),'opens on the newest reply');
  check(await page.locator('#rdName').textContent()==='Agent J','header: Agent name');
  check(/^\d\d:\d\d$/.test(await page.locator('#rdTime').textContent()),'header: time');
  check((await page.locator('#rdPg').textContent()).replace(/\s/g,'')==='3/3','header: page number');
  // rework (Leo 23:1x): no ×, the page number in the top-right corner, the header as low as the card's (44 px, one row)
  const x=await rect(page,'#rdClose'),pg=await rect(page,'#rdPg'),hdr=await rect(page,'#rdHd'),who=await rect(page,'#rdWho'),tm=await rect(page,'#rdTime');
  const xs=await page.locator('#rdClose').evaluate(n=>{const c=getComputedStyle(n);return {o:c.opacity,pe:c.pointerEvents,svg:!!n.querySelector('svg')};});
  check(xs.o==='0'&&xs.pe==='none'&&!xs.svg,'no × drawn; the screen-reader exit is transparent and click-through '+JSON.stringify(xs));
  check(Math.round(hdr.h)===44,'header 44 px, like the card header ('+hdr.h+')');
  check(390-pg.r<=24+1&&pg.t>=hdr.t&&pg.b<=hdr.b,'page number in the top-right corner');
  check(Math.abs((tm.t+tm.b)/2-(pg.t+pg.b)/2)<=2&&who.h<=44,'name, time and page number on one row');
  const sr=await page.evaluate(()=>{const b=document.getElementById('rdClose');return {label:b.getAttribute('aria-label'),focusable:b.tabIndex>=0&&!b.disabled};});
  check(sr.focusable&&sr.label.length>2,'a readable "leave" control for screen readers: '+sr.label);
  check(!(await page.locator('#rdSlider').isVisible()),'the size slider is not in the header');
  // the Noir of the centred dialogs
  const bg=await page.evaluate(()=>{const p=document.createElement('div');p.style.background='var(--dlg-bg)';document.body.append(p);const v=getComputedStyle(p).backgroundColor;p.remove();return [v,getComputedStyle(document.getElementById('rd')).backgroundColor];});
  check(bg[0]===bg[1]&&bg[1]==='rgb(44, 43, 38)','reader background = --dlg-bg Noir '+bg.join(' / '));
  // footer: the reply card's six icons, same order, 回复 rightmost
  const ids=await page.$$eval('#rdActs .act',bs=>bs.filter(b=>!b.hidden).map(b=>b.id));
  check(JSON.stringify(ids)===JSON.stringify(IDS),'footer six icons in the card order: '+ids.join(','));
  const reply=await rect(page,'#rdReplyBtn'),fsb=await rect(page,'#rdFsBtn'),ft=await rect(page,'#rdFt');
  check(reply.r>=ft.r-24&&fsb.l<=ft.l+24,'size icon bottom-left, reply bottom-right');
  check(fsb.b<=844+1&&reply.b<=844+1,'footer on screen');
  // jump to the first (double tap the Agent / time): the long one
  await dtap('#rdWho');
  await page.waitForFunction(t=>document.querySelector('#rdWords').textContent.includes(t),LONG[lang].split('\n')[0]);
  check((await page.locator('#rdPg').textContent()).replace(/\s/g,'')==='1/3','double tap name → first, page 1 / 3');
  check(!(await page.locator('#rd').evaluate(n=>n.hidden)),'double tap on the header does not close');
  // only the words scroll; header and footer stay; no scrollbar drawn
  const hd0=await rect(page,'#rdHd'),ft0=await rect(page,'#rdFt'),sc=await rect(page,'#rdScroll');
  check(hd0.b<=sc.t+1&&ft0.t>=sc.b-1,'words sit between header and footer');
  await swipe(0,-400);await swipe(0,-400);
  const top=await page.locator('#rdScroll').evaluate(n=>n.scrollTop);
  check(top>200,'touch scroll moves the words ('+top+')');
  const hd1=await rect(page,'#rdHd'),ft1=await rect(page,'#rdFt');
  check(hd1.t===hd0.t&&ft1.t===ft0.t,'header / footer never move');
  check((await page.locator('#rdPg').textContent()).replace(/\s/g,'')==='1/3','vertical scroll does not page');
  const bar=await page.locator('#rdScroll').evaluate(n=>({w:n.offsetWidth-n.clientWidth,sw:getComputedStyle(n).scrollbarWidth}));
  check(bar.w===0&&bar.sw==='none','reader scrollbar hidden '+JSON.stringify(bar));
  // keys scroll it too
  await page.keyboard.press('ArrowDown');await page.waitForTimeout(250);
  check((await page.locator('#rdScroll').evaluate(n=>n.scrollTop))>top,'↓ scrolls');
  // double tap the page number → the newest, still full screen
  await dtap('#rdPgBtn');
  await page.waitForFunction(t=>document.querySelector('#rdWords').textContent.includes(t),ATT[lang]);
  check((await page.locator('#rdPg').textContent()).replace(/\s/g,'')==='3/3','double tap page → newest');
  check(!(await page.locator('#rd').evaluate(n=>n.hidden)),'… without leaving');
  // swipe right: header follows the page
  await swipe(140);check((await words(page)).includes(SHORT[lang]),'swipe right → older');
  check((await page.locator('#rdPg').textContent()).replace(/\s/g,'')==='2/3','page number follows the swipe');
  // type size: the quiet icon opens the slider; Esc closes the popover, not the reader; +/- and pinch work without it
  await tap('#rdFsBtn');await page.waitForTimeout(150);
  check(await page.locator('#rdSlider').isVisible(),'size icon opens the slider');
  const fs0=Number(await page.locator('#rdFsN').textContent());
  await tap('#rdPlus');await page.waitForTimeout(100);
  check(Number(await page.locator('#rdFsN').textContent())>fs0,'+ in the popover');
  await page.keyboard.press('Escape');await page.waitForTimeout(150);
  check(!(await page.locator('#rdSlider').isVisible())&&!(await page.locator('#rd').evaluate(n=>n.hidden)),'Esc closes the popover only');
  await tap('#rdFsBtn');await page.waitForTimeout(150);await tap('#rdScroll');await page.waitForTimeout(150);
  check(!(await page.locator('#rdSlider').isVisible()),'a tap outside closes the popover');
  await page.evaluate(()=>document.activeElement?.blur());
  await page.keyboard.press('-');await page.waitForTimeout(100);
  check(Number(await page.locator('#rdFsN').textContent())===fs0,'- key still works');
  // stay-open actions: Telegram, speak, e-mail
  // P536: the reader follows the explicit 「转发去向」 (default system share) — the owner picked Telegram
  check(await page.evaluate(()=>import('./js/forward-choice.js').then(m=>(m.select('tg'),m.destination())))==='tg','Telegram picked as the forward destination');
  const n0=(fake.st.tgFwds||[]).length;
  await tap('#rdFwdBtn');
  await page.waitForFunction(()=>document.querySelector('#rdFwdBtn').dataset.state==='sent');
  check((fake.st.tgFwds||[]).length===n0+1,'Telegram forward sent once');
  check(!(await page.locator('#rd').evaluate(n=>n.hidden)),'Telegram keeps the reader');
  check(await page.locator('#rdFwdBtn').evaluate(b=>b.dataset.tg==='1'),'paper plane shown');
  await tap('#rdSpeakBtn');await page.waitForTimeout(300);
  check(!(await page.locator('#rd').evaluate(n=>n.hidden)),'speak keeps the reader');
  if(await page.locator('#rdSpeakBtn').evaluate(b=>b.dataset.state!=='idle')){await tap('#rdSpeakBtn');await page.waitForTimeout(200);}
  await tap('#rdMailBtn');await page.waitForSelector('#ctFwd:not([hidden])');
  check(!(await page.locator('#rd').evaluate(n=>n.hidden)),'e-mail dialog opens over the reader');
  await page.keyboard.press('Escape');await page.waitForTimeout(200);
  check(await page.locator('#ctFwd').evaluate(n=>n.hidden),'Esc closes the e-mail dialog');
  check(!(await page.locator('#rd').evaluate(n=>n.hidden)),'… and the reader stays');
  // a newer reply while reading: the page number pulses; the unread badge jumps to it
  await fake.addTurn({k:'phone',dev:'other',text:'more'},lang==='zh'?'第四条：新来的回复':'Fourth: a new reply');
  await page.waitForFunction(()=>document.getElementById('rd').dataset.newreply==='1');
  check((await page.locator('#rdPg').textContent()).replace(/\s/g,'')==='2/4','still on my page, total grew');
  await page.waitForFunction(()=>+document.getElementById('rdUnread').dataset.n>0);
  check(await page.locator('#rdUnreadN').isVisible(),'unread badge in the header middle');
  await page.screenshot({path:`${OUT}/${lang}-390-dark-unread.png`});shots.push(`${lang}-390-dark-unread.png`);
  await tap('#rdUnread');
  await page.waitForFunction(()=>/第四条|Fourth/.test(document.querySelector('#rdWords').textContent));
  check((await page.locator('#rdPg').textContent()).replace(/\s/g,'')==='4/4','unread badge → the new reply');
  // copy: visible completed state, keeps reading
  await tap('#rdCopyBtn');await page.waitForFunction(()=>document.getElementById('rdCopyBtn').dataset.done==='1');
  check(await page.locator('#rd').isVisible(),'copy feedback stays visible in the reader');
  await page.keyboard.press('Escape');await page.waitForFunction(()=>document.getElementById('rd').hidden);
  const clip=await page.evaluate(()=>navigator.clipboard.readText().catch(()=>''));
  check(/第四条|Fourth/.test(clip),'copy put the reply on the clipboard');
  // P375: touch reply quotes this one and leaves without opening the keyboard
  await openReader(page);await swipe(140);check((await words(page)).includes(ATT[lang]),'older again');
  await tap('#rdReplyBtn');await page.waitForFunction(()=>document.getElementById('rd').hidden);
  await page.waitForTimeout(250);
  check(await page.locator('#qbar').isVisible(),'reply quotes the reader page');
  check(await page.evaluate(()=>document.activeElement?.id!=='input'),'touch reply keeps the keyboard closed');
  check((await page.locator('#qbText').textContent()).includes(ATT[lang].slice(0,6)),'the quote is the reader page');
  await page.keyboard.press('Escape');await page.evaluate(()=>document.activeElement?.blur());
  // the book in the footer leaves; double tap on the words leaves; the screen reader's exit leaves (shown while focused)
  await openReader(page);await tap('#rdReadBtn');await page.waitForFunction(()=>document.getElementById('rd').hidden);check(true,'book leaves');
  await openReader(page);await dtap('#rdScroll');await page.waitForFunction(()=>document.getElementById('rd').hidden);check(true,'double tap on the words leaves');
  await openReader(page);await page.locator('#rdPgBtn').focus();await page.keyboard.press('Tab');
  check(await page.evaluate(()=>document.activeElement?.id==='rdClose'),'Tab reaches the exit after the page number');
  const fx=await rect(page,'#rdClose'),fo=await page.locator('#rdClose').evaluate(n=>getComputedStyle(n).opacity);check(fo==='1'&&fx.h>=44,'the exit shows while it has the keyboard focus');
  await page.keyboard.press('Enter');await page.waitForFunction(()=>document.getElementById('rd').hidden);check(true,'the exit control leaves');
  await page.evaluate(()=>document.activeElement?.blur());
  // keys: G first, g newest, y copy + keep reading
  await openReader(page);await page.keyboard.press('Shift+G');
  await page.waitForFunction(t=>document.querySelector('#rdWords').textContent.includes(t),LONG[lang].split('\n')[0]);check(true,'G → first');
  await page.keyboard.press('g');await page.waitForFunction(()=>/第四条|Fourth/.test(document.querySelector('#rdWords').textContent));check(true,'g → newest');
  await page.keyboard.press('y');await page.waitForFunction(()=>document.getElementById('rdCopyBtn').dataset.done==='1');check(await page.locator('#rd').isVisible(),'y copies and keeps reading');
  await page.keyboard.press('Escape');await page.waitForFunction(()=>document.getElementById('rd').hidden);
  check(errors.length===0,errors.join('\n'));
  await ctx.close();
 }
 // ---- screenshots: phone 390 · fold outer 280 · desktop 1440 × light / dark × long / short / with a file -----------------
 const SIZES=[['390',{width:390,height:844,mobile:true}],['fold280',{width:280,height:653,mobile:true}],['1440',{width:1440,height:900,mobile:false}]];
 for(const [tag,size] of SIZES) for(const scheme of ['light','dark']){
  const {ctx,page,errors}=await setup('zh',{...size,scheme});
  await openReader(page);
  for(const [kind,go] of [['attach',null],['short','ArrowLeft'],['long','ArrowLeft']]){
   if(go){await page.keyboard.press(go);await page.waitForTimeout(350);}
   const W=await words(page);check(W.includes(kind==='attach'?ATT.zh:kind==='short'?SHORT.zh:'长回复'),`${tag} ${scheme} ${kind}`);
   const hd=await rect(page,'#rdHd'),ft=await rect(page,'#rdFt'),act=await rect(page,'#rdReplyBtn'),fsb=await rect(page,'#rdFsBtn');
   check(hd.t>=0&&ft.b<=size.height+1&&act.r<=size.width+1&&fsb.l>=0,`${tag} ${scheme} ${kind}: header + footer on screen`);
   const pr=await rect(page,'#rdPg'),wr=await rect(page,'#rdWho');
   check(Math.round(hd.h)===44&&pr.r<=size.width+1&&pr.r>=size.width-30&&wr.r<=pr.l+1,`${tag} ${scheme} ${kind}: 44 px header, page number top right, no overlap`);
   check(await page.$$eval('#rdActs .act',bs=>bs.filter(b=>!b.hidden).length)===6,`${tag}: six icons`);
   if(kind==='attach')check(await page.locator('#rdWords .rdstrip .mcard').count()===1,`${tag}: the file card under the words`);
   const ov=await page.$$eval('#rdFsBtn,#rdActs .act',bs=>{const r=bs.map(b=>b.getBoundingClientRect());return r.some((a,i)=>i&&a.left<r[i-1].right-0.5);});check(!ov,`${tag}: footer icons do not overlap`);
   if(kind==='long'){await page.locator('#rdScroll').evaluate(n=>n.scrollTop=n.scrollHeight/3);await page.waitForTimeout(100);}
   const name=`zh-${tag}-${scheme}-${kind}.png`;await page.screenshot({path:`${OUT}/${name}`});shots.push(name);
  }
  await page.locator('#rdFsBtn').click();await page.waitForTimeout(150);
  const name=`zh-${tag}-${scheme}-size.png`;await page.screenshot({path:`${OUT}/${name}`});shots.push(name);
  check(errors.length===0,errors.join('\n'));await ctx.close();
 }
 for(const scheme of ['light','dark']){
  const {ctx,page}=await setup('en',{width:390,height:844,mobile:true,scheme});
  await openReader(page);await page.keyboard.press('Shift+G');await page.waitForTimeout(500);
  const name=`en-390-${scheme}-long.png`;await page.screenshot({path:`${OUT}/${name}`});shots.push(name);await ctx.close();
 }
 writeFileSync(OUT+'/browser.json',JSON.stringify({checks,ok:true,shots},null,2)+'\n');console.log('P273 Playwright checks:',checks);ok=true;
} finally {rec.record('runP273',ok);rec.write();await browser.close();await fake.stop();await web.stop();}
