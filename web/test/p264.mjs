// P264: real paired page, local fake host; no production or QA account touched.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,waitFor,key,shoot,sleep} from './browser.mjs';
const out = new URL('../../../reports/qa/p264/', import.meta.url); mkdirSync(out,{recursive:true});
let web, fake, B;
let checks=0;
const ev=(p,s)=>evaluate(p,s), click=(p,id)=>ev(p,`document.getElementById('${id}').click()`);
const hot=(p,code)=>ev(p,`document.dispatchEvent(new KeyboardEvent('keydown',{key:'${code.slice(3)}',code:'${code}',altKey:true,shiftKey:true,bubbles:true}))`);
try {
 web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});fake=await startFakeHost();B=await launch();
 for (const scheme of ['light','dark']) for (const lang of ['zh','en']) for (const w of [393,1440]) {
  fake.reset(); fake.st.prefs={appearance:{language:lang}};
  const p=await newPage(B,w,852,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  for(const [code,kind] of [['KeyG','friends'],['KeyB','bots']]){
   await hot(p,code); await waitFor(p,`document.body.dataset.view==='${kind}'`);
   assert.equal(await ev(p,`document.getElementById('assist-${kind}').getBoundingClientRect().height>=56`),true);
   await click(p,'setBtn');assert.ok(await ev(p,`['scanBtn','srchBtn','setBtn'].every(id=>document.getElementById(id).getClientRects().length>0)`));await click(p,'setBtn');checks++;
   await ev(p,`document.getElementById('input').value='MY IDEA'`);
   writeFileSync(new URL(`assist-${kind}-${scheme}-${lang}-${w}.png`,out),await shoot(p));
   await click(p,'assist-'+kind);assert.equal(await ev(p,`document.body.dataset.view`),'chat');
   const value=await ev(p,`document.getElementById('input').value`);
   assert.ok(value.startsWith('/agentj-'+kind));assert.ok(value.includes('MY IDEA')); assert.ok(value.includes(lang==='zh'?'来自设置页按钮':'From setup-page button')); checks+=3;
   await ev(p,`document.getElementById('input').value='';document.getElementById('input').dispatchEvent(new Event('input'))`);
  }
  await click(p,'setBtn');await waitFor(p,`!document.getElementById('settings').hidden`);
  const ys=await ev(p,`['scanBtn','srchBtn','setBtn'].map(id=>document.getElementById(id).getBoundingClientRect().top)`);
  await ev(p,`document.getElementById('set-main').scrollTop=10000`); await sleep(60);
  assert.deepEqual(await ev(p,`['scanBtn','srchBtn','setBtn'].map(id=>document.getElementById(id).getBoundingClientRect().top)`),ys);checks++;
  await ev(p,`document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',code:'KeyK',ctrlKey:true,bubbles:true}))`);await ev(p,`document.getElementById('set-find-input').value=${JSON.stringify(lang==='zh'?'语言':'language')};document.getElementById('set-find-input').dispatchEvent(new Event('input'))`);
  assert.ok(await ev(p,`document.querySelectorAll('#set-find-results button').length>0`));checks++;
  await ev(p,`document.querySelector('#set-find-results button').click()`);assert.ok(await ev(p,`!!document.querySelector('.set-found')`));checks++;
  writeFileSync(new URL(`settings-${scheme}-${lang}-${w}.png`,out),await shoot(p));
  // Raster and serialized privacy checks: even password values and titles do not reach the SVG.
  const shot=await ev(p,`(async()=>{
    const box=document.createElement('div');box.id='test-secret';box.dataset.screenshotPrivate='';box.textContent='PRIVATE-CARD';box.title='PRIVATE-TITLE';box.style.cssText='position:fixed;top:180px;left:20px;width:180px;height:40px;z-index:9999';document.body.append(box);
    const pw=document.createElement('input');pw.type='password';pw.value='PRIVATE-PASSWORD';pw.style.cssText='position:fixed;top:230px;left:20px;width:180px;height:40px;z-index:9999';document.body.append(pw);
    const m=await import('./js/pageshot.js');const snap=await m.snapshotSVG();const f=await m.capturePage();
    const img=new Image();img.src=URL.createObjectURL(f);await img.decode();const c=document.createElement('canvas');c.width=img.width;c.height=img.height;const ctx=c.getContext('2d');ctx.drawImage(img,0,0);const ratio=img.width/innerWidth;const pixel=[...ctx.getImageData(Math.round(25*ratio),Math.round(190*ratio),1,1).data];URL.revokeObjectURL(img.src);box.remove();pw.remove();
    const data=await new Promise(r=>{const x=new FileReader();x.onload=()=>r(x.result.split(',')[1]);x.readAsDataURL(f);});return {data,private:snap.svg.includes('PRIVATE-'),type:f.type,size:f.size,w:img.width,h:img.height,pixel};
  })()`);
  writeFileSync(new URL(`dom-${scheme}-${lang}-${w}.png`,out),Buffer.from(shot.data,'base64'));assert.equal(shot.private,false);assert.equal(shot.type,'image/png');assert.ok(shot.size>1000);assert.equal(shot.pixel[3],255);assert.ok(shot.pixel[0]<30);checks+=4;
  await hot(p,'KeyY');await waitFor(p,`document.getElementById('settings').hidden && document.body.dataset.view==='chat' && document.querySelectorAll('#tray .chip').length>0`);checks++;
  // Long press is capture, and the following click cannot also open Settings.
  await click(p,'setBtn');
  await ev(p,`document.getElementById('setBtn').dispatchEvent(new PointerEvent('pointerdown',{pointerId:1,isPrimary:true,button:0,clientX:20,clientY:20,bubbles:true}))`);
  await sleep(600);
  await ev(p,`document.getElementById('setBtn').dispatchEvent(new PointerEvent('pointerup',{pointerId:1,isPrimary:true,bubbles:true}));document.getElementById('setBtn').click()`);
  await waitFor(p,`document.getElementById('settings').hidden`);assert.ok(await ev(p,`document.querySelectorAll('#tray .chip').length===2`));checks++;
  assert.equal(fake.log.filter(m=>m.t==='say').length,0,'never auto-send');checks++;
  assert.equal(p.problems.length,0,p.problems.join('\n')); await p.dispose();
 }
 writeFileSync(new URL('result.json',out),JSON.stringify({ok:true,checks},null,2)+'\n');console.log('P264 PASS',checks);
} finally { await Promise.allSettled([B?.close(),fake?.stop(),web?.stop()]); }
