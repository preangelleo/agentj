import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
test('P123 refreshed pending page waits for fresh host history, then renders offline completion',async()=>{
  let resolve,queries=[],painted=[];
  const ctx=vm.createContext({C:{api:{history:q=>{queries.push(q);return new Promise(r=>resolve=r);}},myDev:()=> 'mine'},showPage(){},announceTurn(){},el:()=>({hidden:true}),cur:{status:'idle'},t:x=>x,fill:(w,x)=>painted.push(x),afterWords(){},rdFollow(){}});
  vm.runInContext(src.slice(src.indexOf('let wordsSrc ='),src.indexOf('function fill(')),ctx);
  vm.runInContext(src.slice(src.indexOf('const hist ='),src.indexOf('function hhmm')).replaceAll('export function','function'),ctx);
  vm.runInContext(`hist.turns=[{id:1,reply:'',end:'open',source:{k:'phone',text:'question'}}];hist.lastId=1;renderWords('',true)`,ctx);
  assert.equal(painted.at(-1),'st.resuming');
  const job=vm.runInContext('refreshNewest()',ctx);
  assert.equal(queries[0].limit,50);assert.equal(queries[0].after,undefined);
  resolve({epoch:0,count:1,first_id:1,turns:[{id:1,reply:'completed while offline',end:'done',source:{k:'phone',text:'question'}}]});
  await job;
  assert.equal(vm.runInContext('hist.turns[0].reply',ctx),'completed while offline');
  assert.equal(vm.runInContext('historySync',ctx),false);
  vm.runInContext(`renderWords('',true)`,ctx);
  assert.equal(painted.at(-1),'r.replyPending');
});

test('P123 an in-flight head refresh cannot overwrite a newer live reply',async()=>{
 let resolve;
 const ctx=vm.createContext({C:{api:{history:()=>new Promise(r=>resolve=r)},myDev:()=> 'mine'},showPage(){},announceTurn(){},el:()=>({hidden:true}),historySync:false,historyGeneration:0,wordsSrc:null});
 vm.runInContext(src.slice(src.indexOf('const hist ='),src.indexOf('function hhmm')).replaceAll('export function','function'),ctx);
 vm.runInContext(`hist.turns=[{id:1,reply:'',end:'open',source:{k:'phone',text:'question'}}];hist.lastId=1`,ctx);
 const job=vm.runInContext('refreshNewest()',ctx);
 vm.runInContext(`upsertTurn({id:1,reply:'new live reply',end:'done',source:{k:'phone',text:'question'}})`,ctx);
 resolve({epoch:0,count:1,first_id:1,turns:[{id:1,reply:'',end:'open',source:{k:'phone',text:'question'}}]});
 await job;assert.equal(vm.runInContext('hist.turns[0].reply',ctx),'new live reply');
});

test('P123 local metadata CSP names the exact origin used by localhost WebAuthn fixtures',async()=>{
 const {startWebServer}=await import('./serve.mjs');
 const web=await startWebServer();
 try{
  for(const origin of [web.url,web.url.replace('127.0.0.1','localhost')]){
   const response=await fetch(origin);
   const csp=response.headers.get('content-security-policy');
   assert.ok(csp.includes(origin+'version.json'),csp);
   assert.ok(!csp.includes('http://localhost:*') && !csp.includes('http://127.0.0.1:*'));
  }
 }finally{await web.stop();}
});
