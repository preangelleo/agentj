// Real Chromium + real Noise to local fake host. TV and iOS are emulation, not hardware proof.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {i18nModule} from '../build.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,waitFor,key,sleep,shoot} from './browser.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/p387/web/',import.meta.url));mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p387-web-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[];
const ev=evaluate;
const tv='Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 Chrome/108.0.0.0 Safari/537.36';
// P434 removes the prose width cap: still overflow a 1920px reader to test remote paging.
const text='A long message. '.repeat(1600);
const before=process.env.AJ_P387_BEFORE==='1';
if(before)writeFileSync(join(tmp,'app.css'),execFileSync('git',['show','HEAD:web/public/app.css']));
async function pair(w,h,scheme,lang,ua){
 fake.reset();for(let i=0;i<3;i++)await fake.addTurn({k:'phone',dev:'X',name:'Remote',text:'Read '+i},'Message '+i+'\n\n'+text+'\n\n[Continue](reply:Continue) [Later](reply:Later)','done');
 const p=await newPage(B,w,h,scheme,{allow:[web.url,fake.relay+'/'],ua,touch:w<500});
 if(ua)await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`Object.defineProperty(window,'PublicKeyCredential',{value:undefined,configurable:true});Object.defineProperty(navigator,'mediaDevices',{value:undefined,configurable:true});`});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await waitFor(p,`document.querySelectorAll('#words .rbtn').length===2`);
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);return p;
}
const rgb=x=>x.replace(/\s/g,'');
try{
 for(const w of (process.env.AJ_P387_TV_ONLY?[]:[390,1440]))for(const scheme of ['light','dark'])for(const lang of ['zh','en']){
  const p=await pair(w,900,scheme,lang);
  if(before){await ev(p,`document.getElementById('main').scrollTop=1e8`);await sleep(80);writeFileSync(join(out,`before-options-${w}-${scheme}-${lang}.png`),await shoot(p));await p.dispose();continue;}
  const colors=[];
  for(const status of ['idle','working','waiting','unknown']){
   await ev(p,`document.body.dataset.status=${JSON.stringify(status)}`);
   const c=await ev(p,`(()=>{const b=document.querySelector('#words .rbtn'),s=getComputedStyle(b);return {bg:s.backgroundColor,color:s.color,border:s.borderTopWidth,h:b.getBoundingClientRect().height,r:s.borderRadius};})()`);
   assert.equal(rgb(c.bg),'rgb(44,43,38)');assert.equal(rgb(c.color),'rgb(250,249,245)');assert.equal(c.border,'0px');assert.ok(c.h>=44);colors.push(c);
  }
  await ev(p,`document.body.dataset.status='idle';document.getElementById('main').scrollTop=1e8`);await sleep(80);
  writeFileSync(join(out,`after-options-${w}-${scheme}-${lang}.png`),await shoot(p));
  const id=await ev(p,`(()=>{const n=document.querySelector('#words .rbtn');window.__option=n;return null;})()`);
  // Capture true native active state (not just the selected class).
  const r=await ev(p,`(()=>{const r=window.__option.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:r.x,y:r.y});
  assert.equal(rgb(await ev(p,`getComputedStyle(window.__option).backgroundColor`)),'rgb(20,20,20)');
  assert.equal(rgb(await ev(p,`getComputedStyle(window.__option).color`)),'rgb(250,249,245)');
  // Release away from the button: style verification must not send an answer.
  await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:0,y:0});
  // Draft survives an actual guarded hold and reload (the real IndexedDB sealed store).
  await ev(p,`document.getElementById('main').scrollTop=0;document.getElementById('input').value='P387 preserved draft';document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('input').blur()`);
  const hold=`(()=>{const n=document.getElementById('srchBtn');n.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,isPrimary:true,button:0,clientX:10,clientY:10}));})()`;
  await ev(p,`window.__beforeRefresh=1`);await ev(p,hold);await sleep(1400);
  await waitFor(p,`!window.__beforeRefresh&&document.getElementById('input').value==='P387 preserved draft'`,15000);
  assert.equal(await ev(p,`!document.getElementById('srch').hidden`),false);
  // Busy attachments block a hold without losing the current draft.
  await ev(p,`(async()=>{const r=await import('./js/relay.js');r.attachShot(new File(['x'],'p387.png',{type:'image/png'}),true);window.__blockedRefresh=1;})()`);
  await ev(p,hold);await sleep(800);assert.equal(await ev(p,'window.__blockedRefresh'),1);
  assert.match(await ev(p,`document.body.textContent`),lang==='zh'?/请先完成/:/Finish recording/);
  results.push({w,scheme,lang,colors,draftReload:true,busyBlocked:true});console.log(`matrix ${w}/${scheme}/${lang} PASS`);await p.dispose();
 }
 if(before){console.log('P387 baseline screenshots captured');process.exitCode=0;}else{
 const p=await pair(1920,1080,'dark','en',tv);
 await ev(p,`document.getElementById('input').blur();document.body.focus()`);
 assert.equal(await ev(p,`getComputedStyle(document.getElementById('mic')).display`),'none');
 await key(p,'ArrowLeft');await waitFor(p,`document.getElementById('pg').textContent.trim().endsWith('2 / 3')`);
 await key(p,'Enter');await sleep(100);await key(p,'Enter');await waitFor(p,`!document.getElementById('rd').hidden`);
 // An overlay above the reader owns remote Back; it must leave the reader intact.
 await sleep(400);await ev(p,`document.getElementById('rdFsBtn').click();document.getElementById('rd').focus()`);
 assert.equal(await ev(p,`document.getElementById('rdFsPop').hidden`),false);
 await key(p,'Unidentified',{keyCode:461});
 assert.equal(await ev(p,`document.getElementById('rdFsPop').hidden`),true);
 assert.equal(await ev(p,`document.getElementById('rd').hidden`),false);
 await key(p,'ArrowRight');await waitFor(p,`document.getElementById('rdWords').textContent.startsWith('Message 2')`);
 assert.ok(await ev(p,`document.getElementById('rdScroll').scrollHeight>document.getElementById('rdScroll').clientHeight`),'reader fixture genuinely overflows');
 await key(p,'PageDown');assert.ok(await ev(p,`document.getElementById('rdScroll').scrollTop>0`));
 await key(p,'PageUp');assert.equal(await ev(p,`document.getElementById('rdScroll').scrollTop`),0);
 for(const scheme of ['light','dark'])for(const lang of ['zh','en']){
  await ev(p,`document.documentElement.dataset.theme=${JSON.stringify(scheme)};window.AJLang.set(${JSON.stringify(lang)})`);
  await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:scheme}]});await sleep(80);
  writeFileSync(join(out,`after-tv-1920-${scheme}-${lang}-reader.png`),await shoot(p));
 }
 await key(p,'Backspace');await waitFor(p,`document.getElementById('rd').hidden`);

 await key(p,'Enter');await sleep(450);await key(p,'Enter');assert.equal(await ev(p,`document.getElementById('rd').hidden`),true);
 await sleep(450);await key(p,'Enter');
 await ev(p,`document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',repeat:true,bubbles:true}))`);
 assert.equal(await ev(p,`document.getElementById('rd').hidden`),true);
 await sleep(80);await key(p,'Enter');await waitFor(p,`!document.getElementById('rd').hidden`);
 await key(p,'Unidentified',{keyCode:461});await waitFor(p,`document.getElementById('rd').hidden`);
 await key(p,'ArrowDown');assert.equal(await ev(p,`document.body.dataset.fivePage`),'workflows');
 assert.equal(await ev(p,`document.activeElement.id`),'five-page');
 await key(p,'ArrowUp');assert.equal(await ev(p,`document.body.dataset.fivePage`),'chat');
 await ev(p,`document.getElementById('input').focus();document.getElementById('input').value='native field'`);await key(p,'ArrowDown');assert.equal(await ev(p,`document.body.dataset.fivePage`),'chat');
 await ev(p,`document.getElementById('input').blur();document.body.focus()`);
 assert.equal(await ev(p,`(async()=>{const {phoneConfirmation}=await import('./js/tvremote.js');return phoneConfirmation();})()`),true);
 assert.match(await ev(p,`document.body.textContent`),/Please confirm on your phone/);
 writeFileSync(join(out,'after-tv-1920-dark-en-chat.png'),await shoot(p));
 assert.equal(await ev(p,`document.documentElement.scrollWidth>innerWidth`),false);
 // Generic 1080p browser gets the same mapping and passkey-only fallback, even if the API exists.
 await p.send('Emulation.setUserAgentOverride',{userAgent:'Mozilla/5.0 Chrome/147.0.0.0 Safari/537.36'});
 await ev(p,`void Object.defineProperty(window,'PublicKeyCredential',{value:function PublicKeyCredential(){},configurable:true})`);
 assert.equal(await ev(p,`(async()=>{const m=await import('./js/faceid.js');return m.canAssert();})()`),true);
 assert.equal(await ev(p,`(async()=>{const m=await import('./js/tvremote.js');return m.isRemoteScreen()&&m.phoneConfirmation(true);})()`),true);
 await key(p,'ArrowDown');assert.equal(await ev(p,`document.body.dataset.fivePage`),'workflows');
 await key(p,'ArrowUp');assert.equal(await ev(p,`document.body.dataset.fivePage`),'chat');
 results.push({tv:true,readerPaging:true,channelScroll:true,back461:true,cardOrder:true,editingPreserved:true,phoneHint:true});await p.dispose();
 writeFileSync(join(out,'results.json'),JSON.stringify({scope:'Local Chromium, iOS/TV emulation; physical LG/Safari/HomeView not observed',cases:results},null,2)+'\n');console.log(`P387 browser PASS (${results.length} matrix cases + contract assertions)`);}
}finally{await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
