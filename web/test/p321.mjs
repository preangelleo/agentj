// P321: read status across three remotes (iPhone / Pad / Mac widths). The host's mark already covers a long history
// (seeded) → no remote paints 99+; any remote reading clears every count; a lost receipt is sent again by itself;
// a remote that was away aligns on return. zh + en.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out='agentjarvis/reports/qa/p321'; mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const pages=[];const badge=`+document.getElementById('omUnread').dataset.n`;
const shownN=`document.getElementById('omUnreadN').textContent`;
// P333: a remote on screen keeps its own alert for replies that arrived while it looked elsewhere; P321 is about the shared
// count, so the remotes that do not read sit in the background (a phone in the pocket) while the replies arrive.
const hide=`Object.defineProperty(document,'visibilityState',{get:()=>'hidden',configurable:true})`;
const unhide=`delete document.visibilityState;document.dispatchEvent(new Event('visibilitychange'))`;
try {
 for(const lang of ['zh','en']) {
  fake.reset();fake.st.ownerRead=true;
  for(let i=1;i<=130;i++) await fake.addTurn({k:'agent'},`Reply ${i}`);
  fake.st.read=fake.st.turns.at(-1).id;   // the host's first mark: the newest reply (P321 seed)
  const sizes={iphone:[390,844,'dark'],pad:[800,1180,'light'],mac:[1280,860,'dark']},R={};
  for(const [k,[w,h,sc]] of Object.entries(sizes)){
   const p=await newPage(B,w,h,sc,{allow:[web.url,fake.relay+'/']});pages.push(p);R[k]=p;
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('a2hs-ok').click()`);
   await waitFor(p,`document.getElementById('pg').textContent==='130 / 130'`);
  }
  for(const p of Object.values(R)) assert.equal(await evaluate(p,badge),0,'a seeded host mark: nothing unread, no 99+');
  // 120 new replies while every remote sits on an older page: 99+ is the honest count here.
  for(const p of Object.values(R)) await evaluate(p,`document.getElementById('pgPrev').click()`);
  for(const p of Object.values(R)) await evaluate(p,hide);
  for(let i=1;i<=120;i++) await fake.addTurn({k:'agent'},`New ${i}`);
  for(const p of Object.values(R)) await waitFor(p,`${badge}===120`);
  assert.equal(await evaluate(R.iphone,shownN),'99+');
  writeFileSync(`${out}/${lang}-iphone-before.png`,await shoot(R.iphone,''));
  // The Pad reads to the newest; its receipt is lost once — the Pad sends it again by itself and every count clears.
  const iphonePg=await evaluate(R.iphone,`document.getElementById('pg').textContent`);
  fake.st.ownerReadLose=1;
  await evaluate(R.pad,unhide);await sleep(300);
  await evaluate(R.pad,`document.getElementById('pgLatest').click()`);
  await waitFor(R.pad,`${badge}===0`);
  await waitFor(R.iphone,`${badge}===0`,15000);await waitFor(R.mac,`${badge}===0`,15000);
  assert.equal(fake.st.ownerReadLost,1,'a receipt was really lost');
  assert.equal(await evaluate(R.iphone,`document.getElementById('pg').textContent`),iphonePg,'a remote receipt does not move the iPhone page');
  writeFileSync(`${out}/${lang}-iphone-after-pad.png`,await shoot(R.iphone,''));
  // The Mac goes away; one more reply; the iPhone reads it; the Mac comes back already clear.
  await navigate(R.mac,'about:blank');
  await evaluate(R.pad,`document.getElementById('pgPrev').click()`);
  await evaluate(R.pad,hide);
  await fake.addTurn({k:'agent'},'Last');
  for(const k of ['iphone','pad']) await waitFor(R[k],`${badge}===1`);
  await evaluate(R.iphone,unhide);await sleep(300);
  await evaluate(R.iphone,`document.getElementById('pgLatest').click()`);
  for(const k of ['iphone','pad']) await waitFor(R[k],`${badge}===0`);
  await navigate(R.mac,web.url+'?lang='+lang);await waitState(R.mac,'ready');await waitFor(R.mac,`/^\\d+ \\/ \\d+$/.test(document.getElementById('pg').textContent)`);
  await sleep(300);assert.equal(await evaluate(R.mac,badge),0);
  writeFileSync(`${out}/${lang}-mac-return.png`,await shoot(R.mac,''));
  for(const p of Object.values(R)){assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);await p.dispose();pages.splice(pages.indexOf(p),1);}
  console.log('PASS P321 '+lang+': seeded host no 99+, Pad read → all (lost receipt retried), Mac aligns on return');
 }
}finally {for(const p of pages)await p.dispose();await B.close();await fake.stop();await web.stop();}
