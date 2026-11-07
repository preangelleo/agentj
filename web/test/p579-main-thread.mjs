// Real paired client + Noise + Ed25519. Synthetic native halt/model response explicitly timed separately.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitFor,waitState,shoot,sleep} from './browser.mjs';
const out=process.env.P579_OUT||'reports/qa/p579-main-thread';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),rows=[];
const keys=['agent.main_thread_guard','agent.main_thread_work_calls','agent.main_thread_seconds'];
const initial={enabled:true,available:true,busy:true,elapsed_s:181,work_calls:8,warned:true,blocked:true,owner_waiting:true,waiting_s:30,can_interrupt:true,turn:42,session:'a'.repeat(24)};
try{for(const width of [390,600,1440])for(const theme of ['light','dark']){
 fake.reset();fake.st.prefs={appearance:{theme,language:'zh'},agent:{main_thread_guard:true,main_thread_work_calls:8,main_thread_seconds:180}};
 let current={...initial},calls=[],timing={};
 fake.st.onApp=async(c,m,send)=>{
  if(m.t==='main_thread_interrupt'){
   calls.push(m);assert.ok(await fake.verifyControl(c,'main_thread_interrupt',m,{request:m.request}),'current paired Ed25519 proof');
   if(m.request.turn!==current.turn||m.request.session!==current.session){await send({t:'main_thread_interrupt_res',r:m.r,ok:false,why:'stale'});return true;}
   timing.ack=Date.now();await send({t:'main_thread_interrupt_res',r:m.r,ok:true});
   setTimeout(async()=>{timing.model=Date.now();await fake.addTurn({k:'agent'},'Synthetic model response after native halt');current={...current,owner_waiting:false,busy:false};await fake.send({t:'status',s:'idle',main_thread:current});},1300);return true;
  }
  if(m.t==='pref_set'&&keys.includes(m.key)){calls.push(m);await send({t:'pref_res',r:m.r,ok:true});return true;}
  return false;
 };
 const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  await fake.addTurn({k:'agent'},'Synthetic previous reply');
  await fake.send({t:'busy',tasks:[{id:'worker-synthetic',who:'后台测试',label:'Synthetic background work',c:1}],task_count:1});
  timing.statusSent=Date.now();await fake.send({t:'status',s:'working',main_thread:current});
  await waitFor(p,`!document.getElementById('main-thread-interrupt').hidden`);timing.visible=Date.now();assert.ok(timing.visible-timing.statusSent<30000);

  const before=await evaluate(p,`(async()=>({banner:!!document.getElementById('main-thread-status'),label:!!document.getElementById('main-thread-label'),count:(await import('./js/busy.js')).currentCounts().task_count,overflow:document.documentElement.scrollWidth>innerWidth,button:(()=>{const r=document.getElementById('main-thread-interrupt').getBoundingClientRect();return {w:r.width,h:r.height,bottom:r.bottom}})(),footer:document.querySelector('footer')?.getBoundingClientRect().top,meter:document.getElementById('m5h').getBoundingClientRect().top}))()`);
  assert.equal(before.banner,false);assert.equal(before.label,false);assert.equal(before.count,1,'main native turn is not an extra background robot');assert.equal(before.overflow,false);assert.ok(before.button.h>=44&&before.button.w>=44);assert.ok(before.meter>=before.button.bottom);
  const file=out+`/${width}-${theme}-waiting.png`;writeFileSync(file,await shoot(p));
  timing.click=Date.now();await evaluate(p,`document.getElementById('main-thread-interrupt').click()`);
  await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);assert.ok(!timing.model,'ACK shown separately before any model response');
  await waitFor(p,`document.body.textContent.includes('Synthetic model response after native halt')`);assert.ok(timing.model-timing.click<10000,'synthetic model delivery <=10s');assert.ok(timing.model>timing.ack+1000);assert.equal(calls.filter(x=>x.t==='main_thread_interrupt').length,1);
  // Stale race: user saw old turn, Host now owns new turn. Signed old request must not stop it.
  await fake.send({t:'status',s:'working',main_thread:initial});current={...initial,turn:43};await waitFor(p,`!document.getElementById('main-thread-interrupt').hidden`);await evaluate(p,`document.getElementById('main-thread-interrupt').click()`);await waitFor(p,`document.getElementById('main-thread-interrupt').title.includes('当前回合已变化')`);
  // Existing native server: configured ON does not pretend runtime guard active.
  await fake.send({t:'status',s:'working',main_thread:{...initial,owner_waiting:false,busy:false,available:false,reason:'guard_pending'}});await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);
  const preferences=fake.prefsMsg();preferences.host.settable.push(...keys);await fake.send(preferences);await evaluate(p,`document.getElementById('setBtn').click()`);await waitFor(p,`!document.getElementById('set-main-thread-row').hidden`);
  await evaluate(p,`document.getElementById('set-main-thread-calls').value='9';document.getElementById('set-main-thread-calls').dispatchEvent(new Event('change'))`);await waitFor(p,`document.getElementById('set-main-thread-calls').value==='9'`);await sleep(150);assert.equal(calls.find(x=>x.key===keys[1])?.value,9);
  const n=calls.length;await evaluate(p,`document.getElementById('set-main-thread-seconds').value='29';document.getElementById('set-main-thread-seconds').dispatchEvent(new Event('change'))`);await sleep(100);assert.equal(calls.length,n,'invalid threshold never sent');
  await fake.send({t:'status',s:'idle'});await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);
  const sentBeforeRevoke=calls.length;await fake.revokeAll();await waitState(p,'revoked');await evaluate(p,`document.getElementById('main-thread-interrupt').click()`);await sleep(100);assert.equal(calls.length,sentBeforeRevoke,'unpaired browser cannot interrupt a newer native turn');assert.equal(await evaluate(p,`document.getElementById('main-thread-interrupt').hidden`),true);
  assert.deepEqual(p.offsite,[]);rows.push({width,theme,before,timing,file,sha256:createHash('sha256').update(readFileSync(file)).digest('hex')});console.log('PASS p579-current-turn-waiting-signed-interrupt',width,theme);
 }finally{await p.dispose();}
}}finally{writeFileSync(out+'/result.json',JSON.stringify({scope:'Synthetic native adapter; actual paired Noise and Ed25519 browser controls. ACK is not model reply.',rows},null,2));await B.close();await fake.stop();await web.stop();}
