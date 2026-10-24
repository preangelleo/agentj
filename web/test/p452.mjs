// P452: encrypted loopback phone, timer-scaled liveness (no public relay / owner state).
import assert from 'node:assert/strict';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,sleep} from './browser.mjs';
const web=await startWebServer(), fake=await startFakeHost(), B=await launch();
let p;
try {
  fake.st.heartbeat=true;
  p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url)); await waitState(p,'awaiting-approval');
  await fake.approve(); await waitState(p,'ready');
  // Scale only liveness timers after pairing: 30s -> 600ms, 3s retry -> 60ms.
  await evaluate(p,`window.p452timer=window.setTimeout; window.setTimeout=(f,ms,...a)=>window.p452timer(f,ms===30000?600:ms===3000?60:ms,...a)`);
  let delay=400;
  fake.st.onApp=async(_c,m,send)=>{if(m.t==='ping'){setTimeout(()=>send({t:'pong',r:m.r}).catch(()=>{}),delay);return true;}};
  let before=fake.log.length;
  await evaluate(p,`window.p452beat=import('./js/session.js').then(m=>m.checkConnection(true))`);
  await sleep(500);
  assert.equal(await evaluate(p,`window.__ajState`),'ready');
  assert.equal(fake.log.slice(before).filter(m=>m.t==='hello').length,0);
  console.log('PASS foreground pong arriving after old 10-second budget stays ready');
  // A foreground miss is followed by retry, never an immediate disconnect.
  fake.st.onApp=async(_c,m)=>m.t==='ping';
  before=fake.log.length;
  await evaluate(p,`import('./js/session.js').then(m=>m.checkConnection(true))`);
  await sleep(750);
  assert.equal(await evaluate(p,`window.__ajState`),'ready');
  assert.equal(fake.log.slice(before).filter(m=>m.t==='hello').length,0);
  console.log('PASS one silent foreground probe keeps session for load grace');
  await waitFor(p,`window.__ajState!=='ready'`,3000);
  await waitState(p,'ready');
  assert.equal(fake.log.slice(before).filter(m=>m.t==='ping').length,3);
  assert.ok(fake.log.slice(before).some(m=>m.t==='hello'));
  console.log('PASS three consecutive unanswered probes reconnect a truly silent socket');
} finally {if(p) await p.close(); await B.close(); await fake.stop(); await web.stop();}
