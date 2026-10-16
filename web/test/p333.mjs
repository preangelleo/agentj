// P333 (Leo 10-16 12:0x): two remotes — the Mac PWA on the newest page, the Chrome tab on an older one. A new reply
// arrives; the newest one reads it at once and its receipt reaches every remote. The older one must still pulse its page
// number and show 「1」 on the header badge until it goes to that reply itself — also after its window went behind another
// app and came back. zh + en, dark + light.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out='web/reports/qa/p333'; mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const pages=[];
const badge=`+document.getElementById('omUnread').dataset.n`, pulse=`document.getElementById('pgCorner').dataset.unread`;
const shown=`(()=>{const b=document.querySelector('#omUnread .unb');return !!b && getComputedStyle(b).display!=='none' && getComputedStyle(b).animationName}).call()`;
const pg=`document.getElementById('pg').textContent`;
try {
 for(const lang of ['zh','en']) for(const scheme of ['dark','light']) {
  fake.reset();fake.st.ownerRead=true;
  for(let i=1;i<=3;i++) await fake.addTurn({k:'agent'},`Reply ${i}`);
  fake.st.read=fake.st.turns.at(-1).id;
  const newest=await newPage(B,1280,860,scheme,{allow:[web.url,fake.relay+'/']}),old=await newPage(B,390,844,scheme,{allow:[web.url,fake.relay+'/']});
  pages.push(newest,old);
  for(const p of [newest,old]){await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('a2hs-ok').click()`);await waitFor(p,`${pg}==='3 / 3'`);}
  await evaluate(old,`document.getElementById('pgPrev').click()`);await waitFor(old,`${pg}==='2 / 3'`);
  for(const p of [newest,old]){assert.equal(await evaluate(p,badge),0);assert.equal(await evaluate(p,pulse),'0');}
  const readBefore=fake.st.read;
  await fake.addTurn({k:'agent'},lang==='zh'?'新回复到了':'A new reply');
  await waitFor(newest,`${pg}==='4 / 4'`);
  for(let i=0;i<50 && fake.st.read<=readBefore;i++) await sleep(100);
  assert.ok(fake.st.read>readBefore,'the newest remote read it and the host moved the shared mark');
  await sleep(600);                                   // the receipt has reached the older remote too
  assert.equal(await evaluate(newest,badge),0,'the reading remote: nothing new');
  assert.equal(await evaluate(old,pg),'2 / 4','the older remote stays where it was');
  assert.equal(await evaluate(old,pulse),'1','the older remote still pulses its page number');
  assert.equal(await evaluate(old,badge),1,'and still says one new message');
  assert.equal(await evaluate(old,`document.getElementById('om').dataset.unread`),'1');
  const anim=await evaluate(old,shown);
  assert.ok(anim && anim!=='none','the badge is on screen and pulsing: '+anim);
  writeFileSync(`${out}/${lang}-${scheme}-old-remote-alert.png`,await shoot(old,''));
  writeFileSync(`${out}/${lang}-${scheme}-newest-remote.png`,await shoot(newest,''));
  // Mac: the window goes behind another app and comes back (visibilitychange → head fetch) — the alert survives
  await evaluate(old,`Object.defineProperty(document,'visibilityState',{get:()=>'hidden',configurable:true});document.dispatchEvent(new Event('visibilitychange'))`);
  await sleep(200);
  await evaluate(old,`delete document.visibilityState;document.dispatchEvent(new Event('visibilitychange'))`);
  await sleep(800);
  assert.equal(await evaluate(old,pulse),'1','back in front: still pulsing');
  assert.equal(await evaluate(old,badge),1,'back in front: still one new message');
  // the older remote goes to it (tap the badge) → the alert goes away as before, and nothing comes back
  await evaluate(old,`document.getElementById('omUnread').click()`);
  await waitFor(old,`${pg}==='4 / 4'`);
  await waitFor(old,`${badge}===0 && ${pulse}==='0'`);
  await sleep(300);assert.equal(await evaluate(old,badge),0);
  writeFileSync(`${out}/${lang}-${scheme}-old-remote-after.png`,await shoot(old,''));
  for(const p of [newest,old]){assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);await p.dispose();pages.splice(pages.indexOf(p),1);}
  console.log(`PASS P333 ${lang}/${scheme}: other remote read it, the older remote still pulses + badge 1, clears on its own look`);
 }
}finally {for(const p of pages)await p.dispose();await B.close();await fake.stop();await web.stop();}
