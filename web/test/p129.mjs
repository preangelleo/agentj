// P129: phone reconnects quietly, a busy host is not a dead one, the page tells the host why it reconnected, and the
// host's Telegram outage is one status line. Owned headless browser + encrypted fake host only (never the production relay).
import assert from 'node:assert/strict';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,sleep} from './browser.mjs';
const ok=(v,n)=>{assert.ok(v,n);console.log('PASS '+n);};
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const sampler=`window.p129=[];clearInterval(window.p129t);window.p129t=setInterval(()=>window.p129.push(document.body.dataset.conn+'|'+getComputedStyle(document.getElementById('stale')).display+'|'+window.__ajState),25)`;
let p;
try{
  p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  fake.st.heartbeat=true;
  await evaluate(p,`import('./js/session.js').then(m=>{m.closeSession();m.scheduleReconnect()})`);await waitState(p,'ready');

  // 1. a relay / network blink: the socket closes, the page is back within a second and the screen never went grey
  await evaluate(p,sampler);
  let n0=fake.log.length;const t0=Date.now();
  fake.kick(1001);
  await sleep(150);await waitState(p,'ready');
  const back=Date.now()-t0;
  await sleep(100);
  let s=await evaluate(p,`window.p129`);
  ok(back<2000,`quiet reconnect is back in ${back} ms (first retry ${250} ms, not 1 s)`);
  ok(s.some(x=>!x.endsWith('|ready')),'the session really dropped and resumed');
  ok(s.every(x=>x.startsWith('on|none')),'no grey page and no 「最后同步于」 line during a sub-second reconnect');
  const hello=fake.log.slice(n0).find(m=>m.t==='hello');
  ok(hello&&hello.rc&&hello.rc.why==='close'&&hello.rc.code===1001&&Number.isInteger(hello.rc.down),'the resume hello carries why it reconnected (rc: close 1001, ms offline)');

  // 2. a busy host (pong late, but frames flowing) is not a dead one
  let pongDelay=12500,beat=null;
  fake.st.onApp=async(_c,m,send)=>{if(m.t==='ping'){setTimeout(()=>send({t:'pong',r:m.r}).catch(()=>{}),pongDelay);return true;}};
  beat=setInterval(()=>fake.send({t:'status',s:'working',agent:'claude',name:null}).catch(()=>{}),2000);
  n0=fake.log.length;
  await evaluate(p,`window.p129busy=import('./js/session.js').then(m=>m.checkConnection())`);
  await sleep(13000);
  clearInterval(beat);
  ok(await evaluate(p,`window.__ajState==='ready'`),'a pong later than the deadline with other frames arriving keeps the session');
  ok(!fake.log.slice(n0).some(m=>m.t==='hello'),'…and no resume happened');

  // 3. a silent host: one miss is retried; only the second miss in a row reconnects — and quietly
  fake.st.onApp=async(_c,m)=>m.t==='ping';
  await evaluate(p,sampler);
  await evaluate(p,`import('./js/session.js').then(m=>m.checkConnection())`);
  await sleep(11000);
  ok(await evaluate(p,`window.__ajState==='ready'`),'one missed pong alone does not drop the session');
  n0=fake.log.length;
  await waitFor(p,`window.p129.some(x=>!x.endsWith('|ready'))`,16000);
  await waitState(p,'ready');
  const h2=fake.log.slice(n0).find(m=>m.t==='hello');
  ok(h2&&h2.rc&&h2.rc.why==='hb','two misses in a row reconnect; the hello says rc why=hb');
  s=await evaluate(p,`window.p129`);
  ok(s.every(x=>x.startsWith('on|')),'the heartbeat reconnect was quiet too');
  fake.st.onApp=null;

  // 4. the computer going offline on a steady session is news: shown at once (as before)
  fake.setUp(false);
  await waitFor(p,`document.body.dataset.conn==='off'`,2000);
  ok(true,'host down on a steady session → shown at once');
  fake.setUp(true);await waitState(p,'ready');

  // 5. a relay deploy / restart drops both ends: the page is back first, hears "computer offline", the computer follows a
  //    second later — all inside the quiet window, nothing shown
  await sleep(300);
  await evaluate(p,sampler);
  fake.kick(1001);fake.setUp(false);
  await sleep(1500);
  fake.setUp(true);await waitState(p,'ready');await sleep(100);
  s=await evaluate(p,`window.p129`);
  ok(s.some(x=>!x.endsWith('|ready'))&&s.every(x=>x.startsWith('on|none')),'relay deploy style double drop stays quiet');

  // 6. a longer outage right after a drop is still shown, after the quiet window
  await sleep(300);
  const t1=Date.now();
  fake.kick(1001);fake.setUp(false);
  await sleep(4000);
  ok(await evaluate(p,`document.body.dataset.conn==='on'`),'first seconds of an outage stay quiet');
  await waitFor(p,`document.body.dataset.conn==='off'`,6000);
  const shown=Date.now()-t1;
  ok(shown>=5500&&shown<=9000,`a longer outage is shown after the quiet window (${shown} ms)`);
  fake.setUp(true);await waitState(p,'ready');
  ok(await evaluate(p,`document.body.dataset.conn==='on'`),'and clears when it is back');

  // 7. Telegram down: one status line in the page's language, gone on the next plain status
  await fake.send({t:'status',s:'idle',agent:'claude',name:null,tg:'down'});
  await waitFor(p,`!document.getElementById('tgdown').hidden`,3000);
  const txt=await evaluate(p,`document.getElementById('tgdown').textContent`);
  ok(/^Telegram 暂时连不上/.test(txt)&&!/ \/ /.test(txt),'Telegram line shown, one language (no 「A / B」 bilingual)');
  ok(await evaluate(p,`!document.querySelector('.words')||!/Telegram unavailable/.test(document.body.innerText)`),'no Telegram history page');
  await fake.send({t:'status',s:'idle',agent:'claude',name:null});
  await waitFor(p,`document.getElementById('tgdown').hidden`,3000);
  ok(true,'Telegram line disappears by itself on recovery');
  ok(p.problems.length===0,'no browser exceptions: '+JSON.stringify(p.problems.slice(0,3)));
}finally{await B.close();await fake.stop();await web.stop();}
