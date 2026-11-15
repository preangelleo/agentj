// Real Noise requests and native WebAuthn; only cancellation timing is an explicit OS-UI test seam.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {randomBytes,generateKeyPairSync,webcrypto} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,tap,sleep,shoot} from './browser.mjs';
import {keysDigest,elevateChallenge} from '../public/js/faceid.js';
import {unb64u,elevateDigest,elevateFields,elevateMessage} from '../../protocol/wire.js';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),out=process.env.OLARES_RUN_OUT||'reports/qa/p0322-relay-settings';mkdirSync(out,{recursive:true});web.url=web.url.replace('127.0.0.1','localhost');
const names=['AGENTSRELAY_CLAUDE_KEY','AGENTSRELAY_OPENAI_KEY'],hex=()=>randomBytes(16).toString('hex'),py=new URL('../../host/.venv/bin/python',import.meta.url).pathname,results=[];
let registration,stored,current,reads,calls,mode,pending,currentCard;
function uv(fa,challenge){execFileSync(py,['-c',`import sys,json,base64;sys.path.insert(0,'agentjarvis/host');from agentj import passkey
x=json.load(sys.stdin);p=passkey.verify_registration(x['r'],bytes([1])*32);passkey.verify_assertion(p,passkey.approval_fields(x['fa']),base64.urlsafe_b64decode(x['ch']+'='*(-len(x['ch'])%4)))`],{input:JSON.stringify({r:registration,fa,ch:Buffer.from(challenge).toString('base64url')}),env:{...process.env,AGENTJ_TEST_PASSKEY_ORIGIN:new URL(web.url).origin}});}
function install(){fake.st.onApp=async(c,m,send)=>{
 if(m.t==='keys_get'){
  reads++;current={t:'keys',r:m.r,id:hex(),n:hex(),fa:registration.id,fa_s:'local',base:[{id:'agentsrelay',name:{zh:'Agents Relay',en:'Agents Relay'},rows:names.map(name=>({type:'key',name,set:stored.has(name)}))}],third:[]};
  if(mode==='missing-set')delete current.base[0].rows[0].set;if(mode==='invalid-set')current.base[0].rows[0].set='false';await send(current);return true;
 }
 if(m.t==='keys_op'){
  assert.equal(m.op,'delete');assert.equal(m.id,current.id);assert.equal(m.n,current.n);assert.ok(names.includes(m.name));uv(m.fa,await elevateChallenge(fake.channel,c.devId,m.id,'key_delete',m.n,await keysDigest('delete',m.name)));
  stored.delete(m.name);calls.push({op:'delete',name:m.name});const old=current;old.n=hex();const reply={t:'keys_res',r:m.r,ok:true,op:m.op,name:m.name,n:old.n};
  if(mode==='hold-delete'){pending={send,reply};return true;}await send(reply);return true;
 }
 if(m.t==='keys_set'){
  assert.equal('value' in m,false);assert.ok(names.includes(m.name));calls.push({op:'set',name:m.name});const pair=generateKeyPairSync('x25519');
  const card={t:'elev',kind:'secret',name:m.name,purpose:'Synthetic key',dest:'Private host fixture',verify:'',id:hex(),n:hex(),epk:pair.publicKey.export({format:'jwk'}).x,ttl:120,passkey_required:true,fa:registration.id,fa_s:'local'};
  const reply={t:'keys_res',r:m.r,ok:true,op:'set',name:m.name,card};if(mode==='hold-set'){pending={send,reply};return true;}currentCard=card;await send(reply);return true;
 }
 if(m.t==='elev_answer'){
  assert.equal(m.ok,false);assert.equal(m.id,currentCard.id);assert.equal(m.n,currentCard.n);const dev=fake.devs.find(d=>d.id===c.devId),pk=await webcrypto.subtle.importKey('raw',unb64u(dev.sk),'Ed25519',false,['verify']),digest=await elevateDigest('secret',elevateFields(currentCard));assert.equal(await webcrypto.subtle.verify('Ed25519',pk,unb64u(m.sig),elevateMessage(fake.channel,c.devId,m.id,'secret','deny',m.n,m.ts,digest,null)),true);await send({t:'elev_done',id:m.id,result:'denied'});return true;
 }
 if(m.t==='auth_get'){await send({t:'auth_res',r:m.r,ok:true,view:{harnesses:[],flow:null}});return true;}
 if(m.t==='acct_get'){await send({t:'acct_res',r:m.r,ok:true,view:{claude:{items:[]},codex:{items:[]}}});return true;}return false;
};}
async function click(p,selector){await waitFor(p,`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return false;e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect(),h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.width>0&&r.height>0&&(e===h||e.contains(h));})()`);const r=await evaluate(p,`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await tap(p,r.x,r.y);}
async function waitPending(){for(let i=0;i<100&&!pending;i++)await sleep(20);assert.ok(pending,'a real host request is pending');}
async function rows(p,which='ar'){await waitFor(p,`document.querySelectorAll('#${which}-relay-keys .krow').length===2`);await sleep(80);}
async function nextMount(p,kind){if(kind==='reload'){await evaluate(p,`import('./js/keys.js').then(m=>m.load())`);await rows(p);return'ar';}await click(p,'#set-back');await click(p,'#set-accts-nav');await rows(p,'acct');return'acct';}
async function deleteRow(p,root,name){await click(p,`#${root}-relay-keys [data-name="${name}"] [data-op="delete"]`);await waitFor(p,`!document.getElementById('confirm').hidden`);await click(p,'#confirm-yes');}
try{
 for(const scenario of ['delete-cross','delete-reload','set-cross','set-reload','uv-resolve','uv-reject','missing-set','invalid-set','delete-unknown']){
  if(process.env.AJ_RELAY_CONTINUATION_CASE&&process.env.AJ_RELAY_CONTINUATION_CASE!==scenario)continue;
  fake.reset();stored=new Set(scenario.startsWith('set-')?[]:names);registration=null;reads=0;calls=[];mode=scenario.endsWith('-set')?scenario:'';pending=null;currentCard=null;install();
  const p=await newPage(B,390,900,'dark',{touch:true,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`for(const id of ['confirm-no','a2hs-ok','phone-env-dismiss']){const e=document.getElementById(id);if(e&&e.offsetParent)e.click();}`);
   await p.send('WebAuthn.enable');await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});registration=await evaluate(p,`import('./js/faceid.js').then(f=>f.register(new Uint8Array(32).fill(1),{relayIndex:2,channel:'AAAAAAAAAAAAAAAAAAAAAA',hostPub:new Uint8Array(32)}))`);
   await click(p,'#setBtn');await click(p,'#set-auth-nav');
   if(scenario.endsWith('-set')){await waitFor(p,`document.querySelector('#ar-relay-keys .relay-key-section')?.textContent.includes('Agent J')`);assert.equal(await evaluate(p,`document.querySelectorAll('#ar-relay-keys .kbtn').length`),0);assert.equal(calls.length,0);results.push({scenario,result:'PASS'});continue;}
   await rows(p);
   if(scenario==='delete-unknown'){
    mode='hold-delete';await deleteRow(p,'ar',names[0]);await waitPending();assert.equal(stored.has(names[0]),false);await waitFor(p,`document.getElementById('toast').textContent.includes('不会自动重试')||document.getElementById('toast').textContent.includes('will not be retried automatically')`,35000);assert.equal(calls.filter(x=>x.op==='delete').length,1);await sleep(250);assert.equal(calls.filter(x=>x.op==='delete').length,1);writeFileSync(`${out}/delete-unknown.png`,await shoot(p));await nextMount(p,'cross');assert.equal(await evaluate(p,`document.querySelector('#acct-relay-keys [data-name="${names[0]}"] .kstate').dataset.on`),'0','fresh read discovers the actual applied deletion without replay');assert.equal(calls.filter(x=>x.op==='delete').length,1);
   }else if(scenario.startsWith('delete-')){
    mode='hold-delete';await deleteRow(p,'ar',names[0]);await waitPending();assert.equal(stored.has(names[0]),false,'the approved old mutation actually completed at the host seam');
    const root=await nextMount(p,scenario.endsWith('reload')?'reload':'cross');const readCount=reads;assert.deepEqual(await evaluate(p,`[...document.querySelectorAll('#${root}-relay-keys .kstate')].map(e=>e.dataset.on)`),['0','1']);mode='';await pending.send(pending.reply);await sleep(200);assert.equal(reads,readCount,'old result cannot refresh a different page snapshot');
    await deleteRow(p,root,names[1]);for(let i=0;i<100&&stored.has(names[1]);i++)await sleep(20);assert.equal(stored.has(names[1]),false,'the next real UV mutation uses the new page nonce');assert.deepEqual(calls.filter(x=>x.op==='delete').map(x=>x.name),names);
   }else if(scenario.startsWith('set-')){
    mode='hold-set';await click(p,`#ar-relay-keys [data-name="${names[0]}"] [data-op="set"]`);await waitPending();const root=await nextMount(p,scenario.endsWith('reload')?'reload':'cross');const readCount=reads;mode='';await pending.send(pending.reply);await sleep(200);assert.equal(await evaluate(p,`document.getElementById('elev')?.hidden!==false`),true,'a delayed acknowledgement cannot mount the old card');assert.equal(reads,readCount);
    await click(p,`#${root}-relay-keys [data-name="${names[1]}"] [data-op="set"]`);await waitFor(p,`document.getElementById('elev')?.hidden===false`);assert.ok(await evaluate(p,`document.getElementById('elev-title').textContent.includes('${names[1]}')`));await click(p,'#elev-deny');await click(p,'#elev-deny');await waitFor(p,`document.getElementById('elev').hidden`);assert.equal(stored.size,0);
   }else{
    await evaluate(p,`(()=>{const original=navigator.credentials.get.bind(navigator.credentials);window.__restoreUv=()=>navigator.credentials.get=original;navigator.credentials.get=(...args)=>new Promise((resolve,reject)=>{original(...args).then(credential=>{window.__lateUvReady=true;window.__releaseUv=()=>${scenario==='uv-resolve'?'resolve(credential)':"reject(new DOMException('Synthetic user cancellation','NotAllowedError'))"};},reject);});})()`);
    await deleteRow(p,'ar',names[0]);await waitFor(p,`window.__lateUvReady===true`);await nextMount(p,'cross');const readCount=reads;await evaluate(p,`window.__restoreUv();window.__releaseUv()`);await sleep(200);assert.equal(calls.filter(x=>x.op==='delete').length,0);assert.equal(reads,readCount);assert.equal(await evaluate(p,`document.querySelector('#acct-relay-keys #keys-pk')===null`),true,'no old UV error/retry paints into the new mount');assert.equal(stored.size,2);
   }
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);results.push({scenario,reads,calls,result:'PASS'});console.log('PASS',scenario);
  }finally{await p.dispose();}
 }
}finally{writeFileSync(`${out}/continuation-results.json`,JSON.stringify({physical:false,uvRejectSeam:'controlled standard cancellation after actual authenticator result',results},null,2));await B.close();await fake.stop();await web.stop();}
