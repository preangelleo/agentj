// Playwright + actual touch events/timing + real Noise session; fake Bot API and microphone.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p272.mjs');let ok=false;
import {mkdirSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {chromium}=await import(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright/index.mjs');
const OUT='reports/qa/p272';mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}), fake=await startFakeHost();
const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--autoplay-policy=no-user-gesture-required','--mute-audio']});
let checks=0;const check=(a,b)=>{assert.ok(a,b);checks++;};
const bytes=Buffer.from('# P272 file\nSafe fixture.\n'),mid='T'.repeat(22);
const file={mid,name:'REPORT.md',mime:'text/markdown',kind:'file',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),ref:'out/REPORT.md'};
let receipts=[],release=null,result={ok:true};
try {
 for(const lang of ['zh','en']){
  fake.reset();fake.st.extraCaps=['tgfile'];fake.st.tgfwd=true;fake.st.autoReply=false;
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='tg_fwd'){receipts.push(m);await new Promise(r=>release=r);await send({t:'tg_fwd_res',r:m.r,...result});return true;}
   if(m.t==='media_get'){await send({t:'media_chunk',mid,o:0,d:bytes.toString('base64url'),last:true});return true;}
   return false;
  };
  const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const page=await ctx.newPage(), errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(fake.newPairing(web.url+'?lang='+lang));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
  await page.evaluate(()=>document.querySelector('#phone-env-dismiss')?.click());
  await fake.send({t:'status',s:'idle',name:'Agent J',agent:'claude'});
  const add=async(text,media=[])=>{await fake.addTurn({k:'phone',dev:'other',text:'fixture'},text,{media});await page.waitForFunction(tx=>document.querySelector('#words').textContent.includes(tx),text.split('\n')[0]);};
  // Attach media after addTurn (its extra parameter isn't part of the public helper).
  await add('Reply one\n\n'+('Scrollable text.\n\n'.repeat(70)));
  await add('Reply two');await add('Reply three');
  await fake.updateTurn(fake.st.turns.at(-1).id,{media:[file]});
  await page.waitForSelector('.mcard .tg');
  await page.locator('.mcard .tg').tap();
  await page.waitForFunction(()=>document.querySelector('.mtg-status')?.textContent.includes(document.documentElement.lang==='en'?'Sending':'发送中'));
  check(await page.locator('.mcard .tg').isDisabled(),'disabled while sending');
  await page.screenshot({path:OUT+'/'+lang+'-sending.png'});
  release();await page.waitForFunction(()=>document.querySelector('.mcard .tg').dataset.state==='sent');
  await page.waitForTimeout(2200);check(await page.locator('.mtg-status').isVisible(),'persistent success');
  await page.screenshot({path:OUT+'/'+lang+'-sent.png'});
  result={ok:false,why:'not_configured'};await page.locator('.mcard .tg').tap();release();await page.waitForFunction(()=>document.querySelector('.mcard .tg').dataset.state==='failed');
  check(await page.locator('.mtg-status').textContent(),'setup failure visible');result={ok:true};
  const cdp=await ctx.newCDPSession(page);
  const touch=async(type,x,y)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'||type==='touchCancel'?[]:[{x,y}]});
  const box=async(sel)=>{const b=await page.locator(sel).boundingBox();return {x:b.x+b.width/2,y:b.y+b.height/2};};
  const tap=async(sel,hold=80)=>{const b=await box(sel);await touch('touchStart',b.x,b.y);await page.waitForTimeout(hold);await touch('touchEnd');};
  const debug=()=>page.evaluate(async()=> (await import('/js/relay.js')).debug());
  await tap('#mic');await page.waitForTimeout(100);check((await debug()).ptt?.locked,'single tap locks immediately');
  await tap('#mic');await page.waitForTimeout(150);check((await debug()).ptt?.locked,'double tap remains locked');
  await page.screenshot({path:OUT+'/'+lang+'-locked.png'});
  await page.waitForTimeout(650);await tap('#mic');await page.waitForFunction(()=>document.querySelector('#ptt').hidden);await page.waitForTimeout(800);
  check(!fake.log.some(m=>m.t==='say'),'locked finish stays draft');
  await page.evaluate(()=>{document.querySelector('#input').value='';document.querySelector('#input').dispatchEvent(new Event('input'));document.activeElement?.blur();});
  const n=fake.log.filter(m=>m.t==='say').length;
  await page.waitForTimeout(450);await tap('#mic',1100);
  await page.waitForFunction(()=>document.querySelector('#input').value==='');
  for(let i=0;i<100&&fake.log.filter(m=>m.t==='say').length===n;i++)await page.waitForTimeout(100);
  check(fake.log.filter(m=>m.t==='say').length===n+1,'hold releases and sends exactly once');
  await page.waitForTimeout(500);
  const b=await box('#mic');await touch('touchStart',b.x,b.y);await page.waitForTimeout(90);await touch('touchCancel');await page.waitForTimeout(250);check(!(await debug()).ptt,'cancelled short press does not lock');
  await page.evaluate(()=>document.activeElement?.blur());await page.keyboard.press('ArrowLeft');
  // A longer held recording travels as an attachment and still sends on release.
  const longBefore=fake.log.filter(m=>m.t==='say').length;
  const longPoint=await box('#mic');await touch('touchStart',longPoint.x,longPoint.y);await page.waitForTimeout(1100);
  await page.evaluate(()=>{window.realP272Now=performance.now.bind(performance);performance.now=()=>window.realP272Now()+35000;});
  await touch('touchEnd');await page.evaluate(()=>{performance.now=window.realP272Now;delete window.realP272Now;});
  for(let i=0;i<100&&fake.log.filter(m=>m.t==='say').length===longBefore;i++)await page.waitForTimeout(100);
  const longSay=fake.log.filter(m=>m.t==='say').at(-1);
  check(fake.log.filter(m=>m.t==='say').length===longBefore+1&&longSay.att?.length===1,'long held recording auto-sends as attachment');
  await page.evaluate(()=>document.activeElement?.blur());
  for(let i=0;i<5 && !(await page.locator('#readBtn').isEnabled());i++){await page.keyboard.press('ArrowLeft');await page.waitForTimeout(200);}
  await page.locator('#readBtn').tap();await page.waitForSelector('#rd:not([hidden])');await page.waitForTimeout(650);
  const swipe=async(dx,dy=0,x=190,hold=0)=>{const y=350;await touch('touchStart',x,y);if(hold)await page.waitForTimeout(hold);for(let i=1;i<=6;i++){await touch('touchMove',x+dx*i/6,y+dy*i/6);await page.waitForTimeout(20);}await touch('touchEnd');await page.waitForTimeout(250);};
  await swipe(140);check((await page.locator('#rdWords').textContent()).includes('Reply two'),'right swipe older');
  await swipe(-140);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'left swipe newer');
  await page.keyboard.press('ArrowLeft');await page.waitForTimeout(250);check((await page.locator('#rdWords').textContent()).includes('Reply two'),'desktop left older');
  await page.keyboard.press('ArrowRight');await page.waitForTimeout(250);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'desktop right newer');
  await swipe(140,0,10);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'edge back untouched');
  await swipe(140,0,190,400);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'long press selection untouched');
  await swipe(0,-140);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'vertical does not page');
  await swipe(-140);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'newest boundary');
  await page.evaluate(()=>{const n=document.querySelector('#rdWords').firstChild;const r=document.createRange();r.selectNodeContents(n);getSelection().removeAllRanges();getSelection().addRange(r);});
  await swipe(140);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'selected text does not page');
  await page.keyboard.press('Shift+ArrowLeft');await page.waitForTimeout(150);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'selection keyboard extension does not page');
  await page.keyboard.press('ArrowLeft');await page.waitForTimeout(150);check((await page.locator('#rdWords').textContent()).includes('Reply three'),'selection keyboard collapse does not page');
  await page.evaluate(()=>getSelection().removeAllRanges());
  await swipe(140);await swipe(140);check((await page.locator('#rdWords').textContent()).includes('Reply one'),'earliest reply');
  await swipe(140);check((await page.locator('#rdWords').textContent()).includes('Reply one'),'oldest boundary');
  await swipe(0,-150);check((await page.locator('#rdScroll').evaluate(n=>n.scrollTop))>0,'native vertical reader scroll works');

  await page.screenshot({path:OUT+'/'+lang+'-reader.png'});
  check(errors.length===0,errors.join('\n'));await ctx.close();
 }
 writeFileSync(OUT+'/browser.json',JSON.stringify({checks,ok:true,telegramRequests:receipts.length},null,2)+'\n');console.log('P272 Playwright checks:',checks);ok=true;
} finally {rec.record('runP272',ok);rec.write();await browser.close();await fake.stop();await web.stop();}
