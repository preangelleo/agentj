// P135: an open page the Agent J host stays quiet about says so after 30 s (stopped: at once) and offers a refresh;
// a failed head fetch retries with backoff from 5 s instead of leaving 「正在恢复…」 on screen forever.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createUnread} from '../public/js/unread.js';
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const words=src.slice(src.indexOf('const NO_ECHO_MS'),src.indexOf('function fill('));
const history=src.slice(src.indexOf('const hist ='),src.indexOf('function hhmm')).replaceAll('export function','function');

// Fake clock + timers inside the vm context.
function clock(){
  const c={now:0,timers:[],seq:0};
  c.setTimeout=(f,ms)=>{const id=++c.seq;c.timers.push({id,at:c.now+ms,f,ms});return id;};
  c.clearTimeout=id=>{c.timers=c.timers.filter(x=>x.id!==id);};
  c.Date={now:()=>c.now};
  c.advance=async ms=>{c.now+=ms;for(;;){const due=c.timers.filter(x=>x.at<=c.now).sort((a,b)=>a.at-b.at)[0];if(!due)break;c.clearTimeout(due.id);due.f();await new Promise(r=>setImmediate(r));}};
  return c;
}

test('P135 waitKey: stopped at once, idle/down only after 30 s, working / compacting / resuming win', ()=>{
  const ctx=vm.createContext({});
  vm.runInContext(words,ctx);
  const k=(s,sync,ms)=>vm.runInContext('waitKey',ctx)(s,sync,ms);
  assert.equal(k({status:'unknown',hostStatus:'stopped'},false,0),'r.replyStopped');
  for(const h of ['idle','down']){
    assert.equal(k({status:h==='idle'?'idle':'unknown',hostStatus:h},false,29999),'r.replyPending');
    assert.equal(k({status:h==='idle'?'idle':'unknown',hostStatus:h},false,30000),'r.replyNoEcho');
  }
  assert.equal(k({status:'working',hostStatus:'working'},false,99999),'r.replyWorking');
  assert.equal(k({status:'working',hostStatus:'compacting',compacting:true},false,99999),'r.replyCompacting');
  assert.equal(k({status:'idle',hostStatus:'idle'},true,99999),'st.resuming');
  assert.equal(k({status:'waiting',hostStatus:'waiting'},false,99999),'r.replyPending');
});

test('P135 open page: a timer redraws to 「没有回音」 after 30 s idle, back to normal on working, cleared on leaving', async()=>{
  const c=clock(),painted=[],w={dataset:{}};
  const ctx=vm.createContext({setTimeout:c.setTimeout,clearTimeout:c.clearTimeout,Date:c.Date,historySync:false,shownKey:'7|x',wordsSrc:null,cardReplyKey:null,refreshCards(){},
    cur:{status:'idle',hostStatus:'idle'},t:x=>x,el:()=>w,fill:(_,x)=>painted.push(x),afterWords(){},rdFollow(){}});
  vm.runInContext(words,ctx);
  ctx.showPage=()=>vm.runInContext(`renderWords('',true)`,ctx);
  ctx.showPage();
  assert.equal(painted.at(-1),'r.replyPending');
  assert.equal(c.timers.length,1);
  await c.advance(29000); assert.equal(painted.at(-1),'r.replyPending');
  await c.advance(1000);
  assert.equal(painted.at(-1),'r.replyNoEcho'); assert.equal(vm.runInContext('wordsSrc===NO_ECHO_SRC',ctx),true);
  assert.equal(c.timers.length,0);
  vm.runInContext(`cur={status:'working',hostStatus:'working'}`,ctx); ctx.showPage();
  assert.equal(painted.at(-1),'r.replyWorking'); assert.equal(vm.runInContext('wordsSrc===NO_ECHO_SRC',ctx),false);
  vm.runInContext(`cur={status:'unknown',hostStatus:'stopped'}`,ctx); ctx.showPage();
  assert.equal(painted.at(-1),'r.replyStopped');
  assert.equal(c.timers.length,1);
  vm.runInContext(`renderWords('a reply',false)`,ctx);   // left the open page
  assert.equal(c.timers.length,0);
});

test('P135 a failed head fetch retries at 5, 10, 20 s … one at a time, then clears historySync', async()=>{
  const c=clock();let calls=0;
  const answers=[()=>Promise.reject(new Error('timeout')),()=>Promise.reject(new Error('timeout')),()=>Promise.resolve(null),
    ()=>Promise.resolve({epoch:0,count:1,first_id:1,turns:[{id:1,reply:'done',end:'done',source:{k:'phone',text:'q'}}]})];
  const ctx=vm.createContext({unread:createUnread(),hostId:()=> 'test-host',setTimeout:c.setTimeout,clearTimeout:c.clearTimeout,
    C:{api:{history:()=>answers[calls++]()},myDev:()=>'mine'},showPage(){},announceTurn(){},el:()=>({hidden:true}),historySync:false,historyGeneration:0,wordsSrc:null});
  vm.runInContext(history,ctx);
  await vm.runInContext('refreshNewest()',ctx);
  assert.equal(calls,1); assert.equal(vm.runInContext('historySync',ctx),true);
  assert.deepEqual(c.timers.map(x=>x.ms),[5000]);
  await c.advance(4999); assert.equal(calls,1);
  await c.advance(1); assert.equal(calls,2); assert.deepEqual(c.timers.map(x=>x.ms),[10000]);
  await c.advance(10000); assert.equal(calls,3); assert.deepEqual(c.timers.map(x=>x.ms),[20000]);
  await c.advance(20000); assert.equal(calls,4);
  assert.equal(vm.runInContext('historySync',ctx),false);
  assert.equal(vm.runInContext('hist.turns[0].reply',ctx),'done');
  assert.equal(c.timers.length,0);
});

test('P135 backoff caps at 60 s and a newer generation (disconnect / fresh refresh) stops it', async()=>{
  const c=clock();let calls=0;
  const ctx=vm.createContext({unread:createUnread(),hostId:()=> 'test-host',setTimeout:c.setTimeout,clearTimeout:c.clearTimeout,
    C:{api:{history:()=>{calls++;return Promise.reject(new Error('x'));}},myDev:()=>'mine'},showPage(){},announceTurn(){},el:()=>({hidden:true}),historySync:false,historyGeneration:0,wordsSrc:null});
  vm.runInContext(history,ctx);
  await vm.runInContext('refreshNewest()',ctx);
  for(const ms of [5000,10000,20000,40000,60000,60000]){ assert.deepEqual(c.timers.map(x=>x.ms),[ms]); await c.advance(ms); }
  const n=calls;
  vm.runInContext('historyGeneration++',ctx);              // setConn(false)
  await c.advance(60000); assert.equal(calls,n);
  await vm.runInContext('refreshNewest()',ctx);            // a fresh refresh starts over at 5 s, never two timers
  assert.deepEqual(c.timers.map(x=>x.ms),[5000]);
});
