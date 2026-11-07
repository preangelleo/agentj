// #568: real encrypted client approval ownership and account/rescue isolation.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=process.env.P568_OUT||'reports/qa/codex-account-568-20261106/web';mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.P568_BASE?{publicDir:process.env.P568_BASE}:{}),fake=await startFakeHost(),B=await launch();
const $=id=>`document.getElementById('${id}')`, results=[];
try {for(const width of [390,1440])for(const theme of ['light','dark']){
 fake.reset();let flow=null, hold=null, initial=null, heldInitial=false, seen=[];
 const acct={codex:{active:'a1',items:[{id:'a1',name:'Main',method:'device',quota:null}]},claude:{active:null,items:[]},conflict:[]};
 const authView=()=>({harnesses:[],dot:false,flow});
 fake.st.onApp=async(c,m,send)=>{
  seen.push(m);
  if(m.t==='acct_get'){await send({t:'acct_res',r:m.r,ok:true,view:acct});return true;}
  if(m.t==='auth_get' && !heldInitial){heldInitial=true;initial={m,send};return true;}
  if(m.t==='auth_get'){await send({t:'auth_res',r:m.r,ok:true,view:authView()});return true;}
  if(m.t==='auth_start'){hold={m,send};return true;}
  return false;
 };
 const p=await newPage(B,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 const save=async n=>writeFileSync(`${out}/${theme}-${width}-${n}.png`,await shoot(p,''));
 const push=async f=>{flow=f;await fake.send({t:'auth_rescue',view:authView()});await sleep(160);};
 try{
  await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}';${$('setBtn')}.click();${$('set-accts-nav')}.click()`);
  await waitFor(p,`!!document.querySelector('#acct-list [data-kind=codex] .acctadd')`);
  await evaluate(p,`(()=>{document.querySelector('#acct-list [data-kind=codex] .acctadd').click();const f=document.querySelector('#acct-list .acctform');f.querySelector('input').value='Backup';f.querySelector('button').click()})()`);
  for(let i=0;i<30&&!hold;i++)await sleep(50);assert.ok(hold);
  await fake.send({t:'ask',id:'a'.repeat(32),tool:'AuthRescue',host_tool:true,summary:'Sign in to AI',ttl:120,passkey_required:true});await sleep(500);await save('approval');
  assert.equal(await evaluate(p,`${$('settings')}.hidden`),false,'account approval preserves Settings');
  assert.equal(await evaluate(p,`${$('set-accts')}.hidden`),false,'account approval preserves original page');
  await fake.send({t:'ask_done',id:'a'.repeat(32),result:'allow'});await evaluate(p,`${$('sheetClose')}.click()`);
  flow={kind:'codex',route:'device',account:'Backup',stage:'starting'};
  await hold.send({t:'auth_res',r:hold.m.r,ok:true,view:authView()});await sleep(150);
  const ownFlow={...flow};
  assert.ok(initial,'initial page auth_get is held independently of the add request');
  await initial.send({t:'auth_res',r:initial.m.r,ok:true,view:{harnesses:[],dot:false,flow:{kind:'codex',account:'Backup',stage:'done'}}});
  await sleep(180);
  assert.equal(await evaluate(p,`document.querySelector('#acct-list [data-kind=codex] .acctadd').disabled`),true,'old same-name initial auth_get must not finish accepted new add');
  await push({kind:'codex',stage:'done'});
  assert.equal(await evaluate(p,`document.querySelector('#acct-list [data-kind=codex] .acctadd').disabled`),true,'unrelated rescue cannot finish add');
  await push({...ownFlow,stage:'failed',why:'This Codex account is already registered.'});
  assert.equal(await evaluate(p,`${$('settings')}.hidden`),false);
  assert.equal(await evaluate(p,`${$('acct-flow')}.hidden`),true,'ended account flow closes per #456');
  assert.ok(await evaluate(p,`document.getElementById('toast').textContent.includes('already registered')`),'failure remains visible in the timed result notice');
  assert.equal(await evaluate(p,`${$('ar-flow')}.hidden`),true,'adding failure never enters rescue flow');await save('failure');
  for(const why of ['denied','timeout']){
   hold=null;
   await evaluate(p,`(()=>{document.querySelector('#acct-list [data-kind=codex] .acctadd').click();const f=document.querySelector('#acct-list .acctform');f.querySelector('input').value='Backup';f.querySelector('button').click()})()`);
   for(let i=0;i<30&&!hold;i++)await sleep(50);assert.ok(hold);
   await hold.send({t:'auth_res',r:hold.m.r,ok:false,why,view:authView()});
   await waitFor(p,`${$('acct-msg')}.classList.contains('ardanger')`);
   assert.equal(await evaluate(p,`${$('settings')}.hidden`),false,why+' stays on original page');
   assert.equal(await evaluate(p,`document.querySelector('#acct-list [data-kind=codex] .acctadd').disabled`),false,why+' allows retry');
   await save(why);
  }
  await evaluate(p,`import('./js/settings.js').then(s=>s.openAuth())`);await waitFor(p,`!${$('set-auth')}.hidden`);
  assert.equal(await evaluate(p,`${$('ar-flow')}.hidden`),true,'opening rescue does not resurrect account failure');await save('rescue');
  await push({kind:'codex',stage:'done'});
  assert.equal(await evaluate(p,`${$('acct-flow')}.hidden`),true,'rescue success never enters account flow');
  assert.equal(await evaluate(p,`${$('ar-flow')}.hidden`),true,'ended rescue flow also closes per #456');
  // Newer unrelated approval deliberately owns chat; a late add result cannot steal it back.
  await fake.send({t:'ask',id:'b'.repeat(32),tool:'SandboxOnce',summary:'fixture',ttl:120,passkey_required:true});await sleep(250);
  assert.equal(await evaluate(p,`${$('settings')}.hidden`),true);
  await push({kind:'codex',account:'Backup',stage:'done'});
  assert.equal(await evaluate(p,`${$('settings')}.hidden`),true,'late result cannot reopen Settings');
  assert.deepEqual(p.problems.filter(x=>!/favicon/.test(x)),[]);results.push({width,theme,pass:true});
 }finally{await p.dispose();}
}}finally{await B.close();await fake.stop();await web.stop();writeFileSync(`${out}/results.json`,JSON.stringify(results,null,2));}
console.log('PASS #568 '+results.length+' viewport/theme cases');
