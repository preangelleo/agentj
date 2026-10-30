// P260: actual Playwright mouse dblclick and 393px touch pairs against the real encrypted client + local fake host.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,mkdtempSync,rmSync,mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {setTimeout as sleep} from 'node:timers/promises';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const OUT=new URL('../../../reports/qa/p260/',import.meta.url);mkdirSync(OUT,{recursive:true});
const temp=mkdtempSync(join(tmpdir(),'p260-'));
let web,fake,browser;const results=[];
try {
 execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','color=c=blue:s=320x240:r=10','-t','12','-c:v','libvpx','-y',join(temp,'video.webm')]);
 execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','sine=frequency=440','-t','1','-y',join(temp,'audio.wav')]);
 const fix=n=>readFileSync(new URL('./fixtures/p183/'+n,import.meta.url));
 const cases=[['chart.png','image/png','image',Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=','base64'),'image'],
 ['video.webm','video/webm','video',readFileSync(join(temp,'video.webm')),'player'],
 ['report.pdf','application/pdf','pdf',fix('report.pdf'),'doc'],['plan.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document','file',fix('plan.docx'),'doc'],
 ['notes.md','text/markdown','file',Buffer.from('# P260\n\nFile full screen'),'doc'],['code.js','text/javascript','file',Buffer.from('const p260 = true;'),'doc'],
 ['text.txt','text/plain','file',Buffer.from('P260 text reader'),'doc'],['page.html','text/html','html',Buffer.from('<h1>P260 HTML</h1>'),'doc'],
 ['sales.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','file',fix('sales.xlsx'),'doc'],
 ['audio.wav','audio/wav','audio',readFileSync(join(temp,'audio.wav')),'audio']];
 web=await startWebServer({port:0});fake=await startFakeHost();browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--autoplay-policy=no-user-gesture-required']});
 for(const touch of [false,true]) {
  fake.reset();const bytes=new Map();
  fake.st.onApp=async(c,m,send)=>{if(m.t!=='media_get')return false;const b=bytes.get(m.mid);for(let i=0;i<8;i++){const o=m.o+i*45056;if(o>=b.length)break;const d=b.subarray(o,o+45056);await send({t:'media_chunk',mid:m.mid,o,d:d.toString('base64url'),last:o+d.length>=b.length});}return true;};
  const ctx=await browser.newContext({viewport:{width:touch?393:1440,height:852},hasTouch:touch,isMobile:touch});
  await ctx.addInitScript(() => { window.__p260Shared=[]; Object.defineProperty(navigator,'canShare',{value:()=>true,configurable:true}); Object.defineProperty(navigator,'share',{value:async data=>{window.__p260Shared.push(data.files.map(f=>f.name));},configurable:true}); });
  const p=await ctx.newPage(),errors=[];p.on('pageerror',e=>errors.push(String(e)));
  await p.goto(fake.newPairing(web.url+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
  await p.evaluate(()=>{for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click();}});
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  const pair=async(selector)=>{const loc=p.locator(selector);await loc.scrollIntoViewIfNeeded();if(!touch){await loc.dblclick({delay:80});return;}const b=await loc.boundingBox();await p.touchscreen.tap(b.x+b.width/2,b.y+Math.min(b.height/2,30));await sleep(80);await p.touchscreen.tap(b.x+b.width/2,b.y+Math.min(b.height/2,30));};
  for(let i=0;i<cases.length;i++) {
   const [name,mime,kind,data,mode]=cases[i],mid=String.fromCharCode(65+i).repeat(22);bytes.set(mid,data);
   const turn=await fake.addTurn({k:'phone',dev:null,text:'P260 '+name},'P260 reply text\n\n'+('Reading position '.repeat(80)),'done');
   await fake.updateTurn(turn.id,{media:[{mid,name,mime,kind,bytes:data.length,sha256:createHash('sha256').update(data).digest('hex'),ref:'out/'+name}]});
   const card=`#mstrip .mcard[data-mid="${mid}"]`;await p.locator(card).scrollIntoViewIfNeeded();await p.waitForFunction(s=>document.querySelector(s)?.dataset.state==='ready',card);
   await p.locator(card+(kind==='video'?' video':' .mname')).scrollIntoViewIfNeeded();
   const scroll=await p.locator('#main').evaluate(e=>e.scrollTop);
   if(kind==='video') {
    await p.locator(card+' video').evaluate(async v=>{v.currentTime=2;await v.play();v.dataset.p260='same-player';});
    await pair(card+' .mvideo-surface');
   } else await pair(card+' .mname');
   if(kind==='audio') {
    await p.waitForFunction(()=>!document.getElementById('rd').hidden);
    assert.equal(await p.locator('.mview').count()===0||await p.locator('.mview').isHidden(),true);
    await p.keyboard.press('Escape');await sleep(450);
   } else {
    await p.locator('.mview').waitFor({state:'visible'});assert.equal(await p.locator('.mview').getAttribute('data-mode'),mode);
    assert.equal(await p.locator('.mvtitle').textContent(),name);assert.equal(await p.locator('#rd').isHidden(),true);
    if(kind==='video') {const v=await p.locator('.mview video').evaluate(v=>({same:v.dataset.p260,paused:v.paused,time:v.currentTime}));assert.equal(v.same,'same-player');assert.equal(v.paused,false);assert.ok(v.time>=2);}
    if(kind==='image') {await sleep(450);await p.locator('.mzoom').dblclick({delay:70});assert.match(await p.locator('.mzoom').getAttribute('style'),/scale\(2.5\)/);}
    if(['file','pdf'].includes(kind))await p.waitForFunction(()=>document.querySelector('.mdoc')?.children.length>0);
    await p.screenshot({path:new URL(`${touch?'touch':'desktop'}-${name}.png`,OUT).pathname});
    if(i%2)await p.goBack();else await p.keyboard.press('Escape');
    await p.locator('.mview').waitFor({state:'hidden'});await sleep(450);
    assert.ok(Math.abs(await p.locator('#main').evaluate(e=>e.scrollTop)-scroll)<2,'reply scroll position retained: '+scroll+' -> '+await p.locator('#main').evaluate(e=>e.scrollTop));
    if(kind==='video') {
     assert.equal(await p.locator(card+' video').evaluate(v=>v.dataset.p260),'same-player');
     await p.locator(card+' video').evaluate(async v=>{v.currentTime=2;await v.play();});
     await sleep(450); // separate these single taps from the preceding double gesture
     const surface=p.locator(card+' .mvideo-surface');
     if(touch)await surface.tap();else await surface.click();
     await p.waitForFunction(s=>document.querySelector(s)?.paused,card+' video');
     await sleep(450); // two independent single taps, outside the shared double-tap window
     if(touch)await surface.tap();else await surface.click();
     await p.waitForFunction(s=>document.querySelector(s)&&!document.querySelector(s).paused,card+' video');
     assert.equal(await p.locator(card+' video').evaluate(v=>v.controls),true,'native controls, including full-screen, retained');
    }
    // Accessible visible entry; download remains an action and does not open the reply reader.
    const button=p.locator(card+' .pv');assert.equal(await button.innerText(),'全屏查看');
    await button.focus();await p.keyboard.press('Enter');await p.locator('.mview').waitFor({state:'visible'});await p.keyboard.press('Escape');await sleep(450);
    const dl=await Promise.all([p.waitForEvent('download'),p.locator(card+' .dl').click()]);assert.equal(dl[0].suggestedFilename(),name);
    assert.equal(await p.locator('#rd').isHidden(),true);assert.equal(await p.locator('.mview').isHidden(),true);
    await p.locator(card+' .sh').click();await p.waitForFunction(name=>window.__p260Shared.some(files=>files.includes(name)),name);
    assert.equal(await p.locator('#rd').isHidden(),true);assert.equal(await p.locator('.mview').isHidden(),true);
   }
   results.push({viewport:touch?393:1440,name,mode,pass:true});console.log('PASS',touch?'touch':'desktop',name);
  }
  await pair('#words p:first-child');await p.locator('#rd').waitFor({state:'visible'});await p.keyboard.press('Escape');await sleep(450);
  results.push({viewport:touch?393:1440,name:'reply text',pass:true});
  const blank=await p.locator('#rm').evaluate(e=>{const r=e.getBoundingClientRect();return {x:r.right-3,y:r.top+Math.min(30,r.height/2)};});
  if(touch){await p.touchscreen.tap(blank.x,blank.y);await sleep(80);await p.touchscreen.tap(blank.x,blank.y);}else await p.mouse.dblclick(blank.x,blank.y,{delay:80});
  await p.locator('#rd').waitFor({state:'visible'});await p.keyboard.press('Escape');await sleep(450);
  results.push({viewport:touch?393:1440,name:'reply blank edge',pass:true});
  const nestedMid='Z'.repeat(22),image=cases[0][3];bytes.set(nestedMid,image);
  const nested=await fake.addTurn({k:'phone',dev:null,text:'Nested file layer'},'![chart](out/chart.png)\n\nNested reply','done');
  await fake.updateTurn(nested.id,{media:[{mid:nestedMid,name:'chart.png',mime:'image/png',kind:'image',bytes:image.length,sha256:createHash('sha256').update(image).digest('hex'),ref:'out/chart.png'}]});
  await p.locator('#words .mcard').waitFor();await p.waitForFunction(()=>document.querySelector('#words .mcard')?.dataset.state==='ready');
  await p.locator('#readBtn').click();await p.locator('#rd').waitFor({state:'visible'});await sleep(450);
  await pair('#rdWords .mcard .mname');await p.locator('.mview').waitFor({state:'visible'});await p.goBack();await p.locator('.mview').waitFor({state:'hidden'});
  assert.equal(await p.locator('#rd').isVisible(),true,'Back closes the file without closing the reply reader');
  await sleep(450);await pair('#rdWords .mcard .mname');await p.locator('.mview').waitFor({state:'visible'});await p.keyboard.press('Escape');await p.locator('.mview').waitFor({state:'hidden'});
  assert.equal(await p.locator('#rd').isVisible(),true,'Escape closes the file layer first');await sleep(450);await p.keyboard.press('Escape');await p.locator('#rd').waitFor({state:'hidden'});await sleep(450);
  results.push({viewport:touch?393:1440,name:'nested file and reply history layers',pass:true});
assert.deepEqual(errors,[]);await ctx.close();
 }
 writeFileSync(new URL('browser.json',OUT),JSON.stringify(results,null,2)+'\n');
} finally {await browser?.close();await fake?.stop();await web?.stop();rmSync(temp,{recursive:true,force:true});}
