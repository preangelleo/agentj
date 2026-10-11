// Owned headless browser + encrypted fake host only, never shared Chrome or production relay.
import assert from 'node:assert/strict';
import {writeFileSync,mkdirSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,sleep} from './browser.mjs';
const out=new URL('../../../reports/qa/p67-web/disconnect-results.json',import.meta.url);mkdirSync(new URL('.',out),{recursive:true});
const tests=[];const ok=(v,n)=>{assert.ok(v,n);tests.push({test:n,ok:true});console.log('PASS '+n);writeFileSync(out,JSON.stringify({kind:'fault injection, owned headless fake host',tests},null,2)+'\n');};
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try{
 const p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 const before=fake.log.length;
 await evaluate(p,`import('./js/session.js').then(m=>m.checkConnection())`);
 ok(!fake.log.slice(before).some(m=>m.t==='ping'),'old host without heartbeat capability stays ready without unsupported probe');
 fake.st.heartbeat=true;
 // Only this fake transport reconnects; the paired device is retained.
 await evaluate(p,`import('./js/session.js').then(m=>{m.closeSession();m.scheduleReconnect()})`);await waitState(p,'ready');
 let silent=false;
 fake.st.onApp=async(_c,m,send)=>{if(m.t==='ping'){if(!silent)await send({t:'pong',r:m.r});return true;}if(silent&&m.t==='say')return true;};
 await evaluate(p,`import('./js/session.js').then(m=>m.checkConnection())`);
 ok(fake.log.some(m=>m.t==='ping'),'negotiated heartbeat uses encrypted request/response');
 silent=true;
 await evaluate(p,`window.p67Visibility='visible';Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>window.p67Visibility});import('./js/session.js').then(m=>m.checkConnection())`);
 await sleep(200);
 await evaluate(p,`window.p67Visibility='hidden';document.dispatchEvent(new Event('visibilitychange'))`);
 await sleep(10500);
 ok(await evaluate(p,`window.__ajState==='ready'`),'background deadline cannot falsely disconnect a sleeping iOS page');
 silent=false;
 const foregroundStart=fake.log.length;
 await evaluate(p,`window.p67Visibility='visible';document.dispatchEvent(new Event('visibilitychange'))`);
 await sleep(500);
 ok(fake.log.slice(foregroundStart).some(m=>m.t==='ping'),'foreground starts a fresh authenticated health check');
 silent=true;
 await evaluate(p,`document.getElementById('input').value='保留这条未确认消息';document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('send').click();window.p67Health=import('./js/session.js').then(m=>m.checkConnection())`);
 await waitFor(p,`window.__ajState!=='ready'`,26000);   // P129: two misses in a row (10 s + 3 s + 10 s); a quick resume stays quiet
 ok(await evaluate(p,`document.getElementById('input').value==='保留这条未确认消息'`),'half-open connection retains unacknowledged message text');
 ok(await evaluate(p,`window.__ajState!=='ready'`),'missing pongs end the session without waiting for socket close');
 silent=false;await waitState(p,'ready');
 ok(await evaluate(p,`document.getElementById('input').value==='保留这条未确认消息'`),'automatic resume keeps paired device and unsent draft');
 await evaluate(p,`document.getElementById('input').value='';document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true}))`);
 const prior=fake.st.says.length;
 await evaluate(p,`window.p67Sid=import('./js/api.js').then(async a=>{const sid=a.newSid();await a.say({sid,text:'唯一消息'});await a.say({sid,text:'唯一消息'});return sid})`);
 await waitFor(p,`!!window.p67Sid`);await sleep(500);
 const retries=fake.st.says.slice(prior);
 ok(retries.length===2 && retries[0].sid===retries[1].sid,'retry preserves send ID for host deduplication');
 ok(p.problems.length===0,'fault injection produces no browser exceptions');
}finally{await B.close();await fake.stop();await web.stop();}
