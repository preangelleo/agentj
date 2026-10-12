// P133: the grey 「正在恢复连接…」 page comes back fast — the retry ladder stops at 5 s, the network coming back retries at
// once, a resume the computer never answers is abandoned and retried, every resume tells the computer why it reconnected,
// and what was typed during the outage stays in the field and goes out on reconnect. Owned headless browser + fake host.
import assert from 'node:assert/strict';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,sleep} from './browser.mjs';
const ok=(v,n)=>{assert.ok(v,n);console.log('PASS '+n);};
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
let p;
try{
  p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  fake.st.heartbeat=true;
  await evaluate(p,`import('./js/session.js').then(m=>{m.closeSession();m.scheduleReconnect()})`);await waitState(p,'ready');

  // 1. the ladder: 250 ms doubling, never more than 5 s between tries
  const waits=await evaluate(p,`import('./js/session.js').then(m=>{const seen=[];const st=window.__ajSetStatus;
    const o=window.setTimeout;window.setTimeout=(f,ms,...a)=>{seen.push(ms);return o(f,ms,...a)};
    try{m.closeSession();for(let i=0;i<10;i++)m.scheduleReconnect();}finally{window.setTimeout=o}return seen;})`);
  const ladder=waits.filter(x=>x!==6000);   // 6000 = P129's quiet window, not a retry
  // P193 (B12): every later step is jittered into [½, 1] × 250·2ⁿ (capped at 5 s)
  const step=i=>Math.min(250*2**i,5000);
  ok(ladder.length===10&&ladder[0]===250&&Math.max(...ladder)<=5000&&ladder.every((x,i)=>x>=step(i)/2&&x<=step(i))&&ladder.slice(-4).every(x=>x>=2500),`retry waits ${JSON.stringify(waits)}: first 250 ms, then jittered, capped at 5 s (was 30 s)`);
  ok(new Set(ladder.slice(-4)).size>1,`capped waits are jittered, not all equal: ${JSON.stringify(ladder.slice(-4))}`);

  // 2. the network coming back retries at once (no waiting out the 5 s timer); the hello says rc why=offline
  let n0=fake.log.length,t0=Date.now();
  await evaluate(p,`dispatchEvent(new Event('offline'));dispatchEvent(new Event('online'))`);
  await waitState(p,'ready');
  const back=Date.now()-t0;
  ok(back<2500,`online event → reconnected in ${back} ms, not after the pending 5 s retry`);
  let hello=fake.log.slice(n0).find(m=>m.t==='hello');
  ok(hello&&hello.rc&&hello.rc.why==='offline','the resume hello says rc why=offline');

  // 3. a resume the computer never answers (socket open, no reply) is abandoned after 15 s and retried — rc why=stall
  await sleep(300);
  fake.st.muteResumes=1;
  n0=fake.log.length;t0=Date.now();
  fake.kick(1011);
  await sleep(1500);
  ok(await evaluate(p,`window.__ajState!=='ready'`),'the muted resume is not ready');
  await waitState(p,'ready',25000);
  const stall=Date.now()-t0;
  ok(stall>=14000&&stall<=22000,`a silent resume is retried after the 15 s watchdog (${stall} ms), not left grey`);
  hello=fake.log.slice(n0).filter(m=>m.t==='hello').pop();
  ok(hello&&hello.rc&&hello.rc.why==='stall'&&hello.rc.code===1011,'…and the next hello says rc why=stall (keeping the first close code 1011)');

  // 4. what is typed on the grey page stays in the field and sends once connected
  await sleep(300);
  fake.kick(1001);fake.setUp(false);
  await waitFor(p,`document.body.dataset.conn==='off'`,9000);
  const draft='灰屏时写的草稿 P133';
  await evaluate(p,`(()=>{const i=document.getElementById('input');i.focus();i.value=${JSON.stringify(draft)};i.dispatchEvent(new Event('input'));})()`);
  ok(await evaluate(p,`!document.getElementById('input').disabled&&!document.getElementById('input').readOnly`),'the field stays editable while offline');
  fake.setUp(true);await waitState(p,'ready');await sleep(300);
  ok(await evaluate(p,`document.getElementById('input').value`)===draft,'the draft is still in the field after reconnecting');
  const says0=fake.st.says.length;
  await evaluate(p,`document.getElementById('send').click()`);
  await waitFor(p,`document.getElementById('input').value===''`,5000);
  for(let i=0;i<50&&!fake.st.says.slice(says0).some(m=>m.text===draft);i++)await sleep(100);
  ok(fake.st.says.slice(says0).some(m=>m.text===draft),'…and it sends right away on the restored session');
  ok(p.problems.length===0,'no browser exceptions: '+JSON.stringify(p.problems.slice(0,3)));
}finally{await B.close();await fake.stop();await web.stop();}
