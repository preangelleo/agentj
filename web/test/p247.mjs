import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out='agentjarvis/reports/qa/p247'; mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const pages=[];const results=[];
const badge=`+document.getElementById('omUnread').dataset.n`;
try {
 for(const lang of ['zh','en']) {
  fake.reset();fake.st.ownerRead=true;
  const a=await newPage(B,390,844,'dark',{allow:[web.url,fake.relay+'/']}),b=await newPage(B,720,1000,'light',{allow:[web.url,fake.relay+'/']});pages.push(a,b);
  for(const p of [a,b]) {await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('a2hs-ok').click()`);}
  await fake.addTurn({k:'agent'},'First');await fake.addTurn({k:'agent'},'Second');
  for(const p of [a,b]) {await waitFor(p,`document.getElementById('pg').textContent==='2 / 2'`);await evaluate(p,`document.getElementById('pgPrev').click()`);}
  await fake.addTurn({k:'agent'},'Third');await fake.addTurn({k:'agent'},'Fourth');
  for(const p of [a,b]) await waitFor(p,`${badge}===2`);
  writeFileSync(out+'/'+lang+'-B-unread.png',await shoot(b,''));
  await evaluate(a,`document.getElementById('pgLatest').click()`);
  await waitFor(a,`${badge}===0`);await sleep(300);
  // P333: B was on screen when Third / Fourth arrived — A's receipt clears the shared count, not B's own alert
  assert.equal(await evaluate(b,badge),2,'B still says two new messages');
  assert.equal(await evaluate(b,`document.getElementById('pgCorner').dataset.unread`),'1','B still pulses its page number');
  assert.equal(await evaluate(b,`document.getElementById('pg').textContent`),'1 / 4','remote receipt does not move B reading page');
  writeFileSync(out+'/'+lang+'-B-read.png',await shoot(b,''));
  await navigate(b,'about:blank');
  await evaluate(a,`document.getElementById('pgPrev').click()`);await fake.addTurn({k:'agent'},'Fifth');await waitFor(a,`${badge}===1`);
  await evaluate(a,`document.getElementById('pgLatest').click()`);await waitFor(a,`${badge}===0`);
  // Reload in a hidden tab: it cannot clear unread itself. Observe every painted positive badge.
  await b.send('Page.addScriptToEvaluateOnNewDocument',{source:`Object.defineProperty(document,'visibilityState',{get:()=>'hidden',configurable:true});window.flashes=[];new MutationObserver(()=>{const e=document.getElementById('omUnread');if(e && +e.dataset.n>0)window.flashes.push(+e.dataset.n)}).observe(document,{subtree:true,attributes:true,childList:true});`});
  await navigate(b,web.url+'?lang='+lang);await waitState(b,'ready');await waitFor(b,`document.getElementById('pg').textContent==='5 / 5'`);await sleep(200);
  assert.equal(await evaluate(b,badge),0);assert.deepEqual(await evaluate(b,'window.flashes'),[],'offline return has no stale unread flash');
  // A newer turn was missed while hidden with the socket still alive; foreground refetch catches the owner receipt.
  await evaluate(a,`document.getElementById('pgPrev').click()`);await fake.addTurn({k:'agent'},'Sixth');await waitFor(a,`${badge}===1`);await waitFor(b,`${badge}===1`);
  fake.st.ownerReadDropTo=await evaluate(b,`import('./js/api.js').then(api=>api.myDeviceId())`);
  await evaluate(a,`document.getElementById('pgLatest').click()`);await waitFor(a,`${badge}===0`);await sleep(100);
  assert.equal(await evaluate(b,badge),1,'the frozen background tab missed the read frame');
  fake.st.ownerReadDropTo=null;fake.st.ownerReadHeadDelay=500;
  await evaluate(b,`window.flashes=[];delete document.visibilityState;document.dispatchEvent(new Event('visibilitychange'))`);
  assert.equal(await evaluate(b,badge),0,'foreground suppresses stale hints before the delayed head returns');
  await sleep(650);assert.equal(await evaluate(b,badge),0);assert.deepEqual(await evaluate(b,'window.flashes'),[]);
  fake.st.ownerReadHeadDelay=0;
  for(const p of [a,b]) await evaluate(p,`document.getElementById('pgPrev').click()`);
  // P333: B in the background when Seventh arrives (phone in the pocket) — only the shared mark decides for it
  await evaluate(b,`Object.defineProperty(document,'visibilityState',{get:()=>'hidden',configurable:true})`);
  await fake.addTurn({k:'agent'},'Seventh');for(const p of [a,b])await waitFor(p,`${badge}===1`);
  fake.st.ownerReadMute=true;await evaluate(a,`document.getElementById('pgLatest').click()`);await waitFor(a,`${badge}===0`);await sleep(100);
  assert.equal(await evaluate(b,badge),1,'lost receipt does not reach B yet');
  fake.st.ownerReadMute=false;await navigate(a,web.url+'?lang='+lang);await waitState(a,'ready');await waitFor(b,`${badge}===0`);
  // Geometry uses the live DOM/CSS at phone/fold widths and enlarged copy.
  for(const p of [a,b]) for(const fz of [1,1.4]) {
   await evaluate(p,`document.documentElement.style.setProperty('--fz','${fz}');document.getElementById('ptt').hidden=false;document.getElementById('pttSeg').hidden=false;document.getElementById('pttSeg').textContent='1 / 10';document.getElementById('pttAct').hidden=false;document.getElementById('pttHint').textContent=${JSON.stringify(lang==='zh'?'再点一下结束 · 上滑取消':'Tap again to end · Slide up to cancel')}`);
   const g=await evaluate(p,`(()=>{const r=s=>document.querySelector(s).getBoundingClientRect(),c=r('.pttc'),m=r('.pttmic'),v=r('.pttring'),t=r('#pttTime'),h=r('#pttHint'),seg=r('#pttSeg'),act=r('#pttAct');return {dx:m.x+m.width/2-c.x-c.width/2,dy:m.y+m.height/2-c.y-c.height/2,rx:v.x+v.width/2-c.x-c.width/2,ry:v.y+v.height/2-c.y-c.height/2,copyOutside:t.y>=c.bottom,noOverlap:h.y>=t.bottom && seg.y>=h.bottom && act.y>=seg.bottom}})()`);
   assert.ok(Math.abs(g.dx)<1&&Math.abs(g.dy)<1,JSON.stringify(g));assert.ok(Math.abs(g.rx)<1&&Math.abs(g.ry)<1,JSON.stringify(g));assert.equal(g.copyOutside,true);assert.equal(g.noOverlap,true);
   results.push({lang,fz,geometry:g});writeFileSync(out+'/'+lang+'-mic-'+(p===a?'phone':'fold')+'-'+fz+'.png',await shoot(p,''));
   await evaluate(p,`document.getElementById('ptt').hidden=true`);
  }
  for(const p of [a,b]){assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);await p.dispose();pages.splice(pages.indexOf(p),1);}
  console.log('PASS P247 '+lang+': A→B live, offline no flash, foreground, mic centre');
 }
 writeFileSync(out+'/results.json',JSON.stringify(results,null,2)+'\n');
}finally {for(const p of pages)await p.dispose();await B.close();await fake.stop();await web.stop();}
