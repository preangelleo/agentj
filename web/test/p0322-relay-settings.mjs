// Real client/Noise/key-card journey. All key material and accounts are isolated synthetic fixtures.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {randomBytes,generateKeyPairSync,createPublicKey,diffieHellman,hkdfSync,createDecipheriv,webcrypto} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep,key} from './browser.mjs';
import {elevateDigest,elevateFields,elevateMessage,sha256hex,unb64u} from '../../protocol/wire.js';
import {elevateChallenge,keysDigest} from '../public/js/faceid.js';
let absent=0;
const captureOnly=process.env.AJ_RELAY_SETTINGS_CAPTURE==='clean';
const before=process.env.AJ_RELAY_SETTINGS_PHASE==='before',out=process.env.OLARES_RUN_OUT||'reports/qa/p0322-relay-settings';mkdirSync(out,{recursive:true});
const names=['AGENTSRELAY_CLAUDE_KEY','AGENTSRELAY_OPENAI_KEY'],value='synthetic-relay-value-not-valid-for-any-provider';
const hex=()=>randomBytes(16).toString('hex'),web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch(),results=[];
web.url=web.url.replace('127.0.0.1','localhost');const py=new URL('../../host/.venv/bin/python',import.meta.url).pathname;
let stored=new Set(),registration=null,current=null,card=null,cardKey=null,mode='',calls=[],pending=[];
function verifyUV(fa,challenge){execFileSync(py,['-c',`import json,sys,base64;sys.path.insert(0,'agentjarvis/host');from agentj import passkey
v=json.load(sys.stdin);pk=passkey.verify_registration(v['registration'],bytes([1])*32);passkey.verify_assertion(pk,passkey.approval_fields(v['fa']),base64.urlsafe_b64decode(v['challenge']+'='*(-len(v['challenge'])%4)))`],{input:JSON.stringify({registration,fa,challenge:Buffer.from(challenge).toString('base64url')}),env:{...process.env,AGENTJ_TEST_PASSKEY_ORIGIN:new URL(web.url).origin}});}
function install(){fake.st.onApp=async(c,m,send)=>{
 if(m.t==='keys_get'){
  if(mode==='offline'){await send({t:'keys',r:m.r,id:'bad'});return true;}
  current={t:'keys',r:m.r,id:hex(),n:hex(),...(registration&&mode!=='no-passkey'?{fa:registration.id,fa_s:'local'}:{}),base:[{id:'agentsrelay',name:{zh:'Agents Relay',en:'Agents Relay'},rows:names.map(name=>({type:'key',name,set:stored.has(name),tail:'MASK',in_use:true,value:'untrusted-extra-field'}))},{id:'openai',name:{zh:'OpenAI',en:'OpenAI'},rows:[{type:'oauth',name:'codex',state:'in'}]}],third:[]};
  if(mode==='incomplete')current.base[0].rows.pop();await send(current);return true;
 }
 if(m.t==='keys_set'){
  assert.ok(names.includes(m.name));assert.equal('value' in m,false);calls.push({op:'set',name:m.name});
  if(mode==='timeout'){pending.push({m,send});return true;}
  if(mode==='deny-set'){await send({t:'keys_res',r:m.r,ok:false,why:'passkey'});return true;}
  cardKey=generateKeyPairSync('x25519');card={t:'elev',kind:'secret',name:m.name,purpose:'Synthetic Relay key',dest:'Host private key file',verify:'',id:hex(),n:hex(),epk:cardKey.publicKey.export({format:'jwk'}).x,ttl:120,passkey_required:true,fa:registration.id,fa_s:'local'};
  await send({t:'keys_res',r:m.r,ok:true,op:'set',name:m.name,card:{...card,...(mode==='wrong-card'?{name:'UNRELATED_KEY'}:{})}});return true;
 }
 if(m.t==='elev_answer'){
  assert.equal(m.id,card.id);assert.equal(m.n,card.n);const digest=await elevateDigest('secret',elevateFields(card)),dev=fake.devs.find(d=>d.id===c.devId),pk=await webcrypto.subtle.importKey('raw',unb64u(dev.sk),'Ed25519',false,['verify']);
  const ctSha=m.ok?await sha256hex(unb64u(m.ct)):null;
  assert.equal(await webcrypto.subtle.verify('Ed25519',pk,unb64u(m.sig),elevateMessage(fake.channel,c.devId,card.id,'secret',m.ok?'allow':'deny',card.n,m.ts,digest,ctSha)),true);
  calls.push({op:m.ok?'sealed-save':'cancel',name:card.name});
  if(m.ok){
   verifyUV(m.fa,await elevateChallenge(fake.channel,c.devId,card.id,'secret',card.n,digest));assert.equal('value' in m,false);
   const epk=unb64u(m.epk),shared=diffieHellman({privateKey:cardKey.privateKey,publicKey:createPublicKey({format:'jwk',key:{kty:'OKP',crv:'X25519',x:m.epk}})}),k=Buffer.from(hkdfSync('sha256',shared,Buffer.concat([Buffer.from(unb64u(card.epk)),Buffer.from(epk)]),Buffer.from('agentjarvis-seal-v1'),32)),ct=Buffer.from(unb64u(m.ct));
   const decipher=createDecipheriv('aes-256-gcm',k,ct.subarray(0,12));decipher.setAAD(Buffer.from(['agentjarvis-seal-v1',fake.channel,c.devId,card.id,'secret',card.n,digest].join('\n')));decipher.setAuthTag(ct.subarray(-16));
   const plain=Buffer.concat([decipher.update(ct.subarray(12,-16)),decipher.final()]);assert.equal(plain.toString(),value);plain.fill(0);shared.fill(0);k.fill(0);stored.add(card.name);
  }
  await send({t:'elev_done',id:card.id,result:m.ok?'saved':'denied'});return true;
 }
 if(m.t==='keys_op'){
  assert.equal(m.op,'delete');assert.ok(names.includes(m.name));assert.equal(m.id,current.id);assert.equal(m.n,current.n);assert.ok(m.fa);
  verifyUV(m.fa,await elevateChallenge(fake.channel,c.devId,m.id,'key_delete',m.n,await keysDigest('delete',m.name)));calls.push({op:'delete',name:m.name});current.n=hex();if(mode!=='deny-delete')stored.delete(m.name);
  await send({t:'keys_res',r:m.r,ok:mode!=='deny-delete',op:'delete',name:m.name,n:current.n,...(mode==='deny-delete'?{why:'passkey'}:{})});return true;
 }
 if(m.t==='auth_get'){await send({t:'auth_res',r:m.r,ok:true,view:{harnesses:[{kind:'claude',label:'Claude Code',installed:true,state:'ok'},{kind:'codex',label:'Codex',installed:true,state:'ok'}],flow:null}});return true;}
 if(m.t==='acct_get'){await send({t:'acct_res',r:m.r,ok:true,view:{claude:{items:[],active:null},codex:{items:[],active:null}}});return true;}
 if(m.t==='models_get'){await send({t:'models',r:m.r,models:[],harness:'Codex'});return true;}
 return false;
};}
let recentTaps=[];
async function tap(p,sel){
 const expression=`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2,hit=document.elementFromPoint(x,y);return{x,y,visible:r.width>0&&r.height>0,ready:!!hit&&(e===hit||e.contains(hit)),hit:hit?.id||hit?.className};})()`;
 let point;
 try{await waitFor(p,`(${expression})?.ready===true`,10000);point=await evaluate(p,expression);}catch(error){writeFileSync(`${out}/tap-failure.json`,JSON.stringify({sel,hit:await evaluate(p,expression),recentTaps,mode,calls},null,2));writeFileSync(`${out}/tap-failure.png`,await shoot(p));throw error;}
 recentTaps.push({sel,...point});recentTaps=recentTaps.slice(-8);
 await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:point.x,y:point.y});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:point.x,y:point.y});
}
const roots={models:'#keys-box',auth:'#ar-relay-keys',accts:'#acct-relay-keys'};
async function enter(p,entry,ready=true){if(entry==='models'){if(await evaluate(p,`document.getElementById('settings').hidden`))await tap(p,'#setBtn');await tap(p,'#open-models');await waitFor(p,`document.body.dataset.view==='models'`);}else{await tap(p,entry==='auth'?'#set-auth-nav':'#set-accts-nav');await waitFor(p,`!document.getElementById('set-${entry}').hidden`);}if(!before&&ready)await waitFor(p,`document.querySelectorAll('${roots[entry]} .relay-key-section .krow').length===2`);}
async function leave(p,entry){await tap(p,entry==='models'?'#models-back':'#set-back');}
async function shot(p,name,entry){if(!before)await evaluate(p,`document.querySelector('${roots[entry]} .relay-key-section').scrollIntoView({block:'center',behavior:'instant'})`);writeFileSync(`${out}/${name}.png`,await shoot(p));}
async function save(p,name,entry){const count=calls.filter(x=>x.op==='sealed-save').length;await tap(p,`${roots[entry]} [data-name="${name}"] [data-op="${stored.has(name)?'edit':'set'}"]`);await waitFor(p,`document.getElementById('elev')?.hidden===false`);await evaluate(p,`(()=>{const e=document.getElementById('elev-input');e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));})()`);await tap(p,'#elev-allow');await waitFor(p,`document.getElementById('elev').hidden`);assert.equal(calls.filter(x=>x.op==='sealed-save').length,count+1);await waitFor(p,`document.querySelector('${roots[entry]} [data-name="${name}"] .kstate')?.dataset.on==='1'`);assert.equal(stored.has(name),true);}
try{
 for(const language of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.setUp(true);fake.st.prefSet=true;fake.st.prefs={appearance:{language,theme}};stored=new Set();registration=null;mode='';calls=[];install();
  const p=await newPage(B,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`for(const id of ['confirm-no','a2hs-ok','phone-env-dismiss']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click();}`);
   await p.send('WebAuthn.enable');await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
   registration=await evaluate(p,`import('./js/faceid.js').then(f=>f.register(new Uint8Array(32).fill(1),{relayIndex:2,channel:'AAAAAAAAAAAAAAAAAAAAAA',hostPub:new Uint8Array(32)}))`);
   for(const entry of ['models','auth','accts']){
    if(captureOnly){
     for(const [state,values] of [['empty',[]],['one',[names[0]]],['both',names]]){
      stored=new Set(values);await enter(p,entry);await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,10000);await shot(p,`${language}-${theme}-${width}-${entry}-${state}`,entry);
      const panel=await evaluate(p,`({theme:document.documentElement.dataset.theme,scheme:matchMedia('(prefers-color-scheme:light)').matches?'light':'dark',background:getComputedStyle(document.getElementById('${entry==='models'?'models-view':'settings'}')).backgroundColor,ink:getComputedStyle(document.querySelector('${roots[entry]} .relay-key-section')).color,labels:[...document.querySelectorAll('${roots[entry]} .relay-key-section .kname')].map(e=>e.textContent),states:[...document.querySelectorAll('${roots[entry]} .relay-key-section .kstate')].map(e=>e.dataset.on)})`);
      assert.equal(panel.theme,theme);assert.deepEqual(panel.states,values.length===0?['0','0']:values.length===1?['1','0']:['1','1']);results.push({language,theme,width,entry,state,panel});await leave(p,entry);
     }
     continue;
    }
    stored.clear();await enter(p,entry);const prefix=`${language}-${theme}-${width}-${entry}`;await shot(p,prefix+'-empty',entry);
    if(before){const count=await evaluate(p,`document.querySelectorAll('${roots[entry]} .krow[data-name="AGENTSRELAY_CLAUDE_KEY"],${roots[entry]} .krow[data-name="AGENTSRELAY_OPENAI_KEY"]').length`);if(count!==2)absent++;results.push({language,theme,width,entry,relayComponents:count});await leave(p,entry);continue;}
    assert.equal(await evaluate(p,`document.querySelectorAll('${roots[entry]} .relay-key-section input').length`),0,'only the existing sealed card receives a value');
    assert.equal(await evaluate(p,`document.querySelector('${roots[entry]} .keys-help').href`),'https://agentj.app/docs/agents-relay/');
    const style=await evaluate(p,`({theme:document.documentElement.dataset.theme,scheme:matchMedia('(prefers-color-scheme:light)').matches?'light':'dark',surface:getComputedStyle(document.querySelector('${roots[entry]} .relay-key-section')).backgroundColor,ink:getComputedStyle(document.querySelector('${roots[entry]} .relay-key-section')).color})`);assert.equal(style.theme,theme);
    for(const name of names){await save(p,name,entry);await shot(p,prefix+(name===names[0]?'-one':'-both'),entry);}
    assert.ok(await evaluate(p,`document.querySelector('${roots[entry]} .relay-key-section').textContent.includes(${JSON.stringify(language==='en'?'connection not verified':'尚未验证连通')})`));
    assert.equal(await evaluate(p,`document.querySelector('${roots[entry]} .relay-key-section').textContent.includes('MASK')`),false,'no key tails');
    await save(p,names[0],entry);assert.equal(calls.filter(x=>x.op==='sealed-save'&&x.name===names[0]).length>=2,true,'replacement uses the same sealed route');
    await sleep(100);await tap(p,`${roots[entry]} [data-name="${names[0]}"] [data-op="delete"]`);await waitFor(p,`!document.getElementById('confirm').hidden`);const count=calls.filter(x=>x.op==='delete').length;await tap(p,'#confirm-no');assert.equal(calls.filter(x=>x.op==='delete').length,count,'cancel sends no mutation');
    mode='deny-delete';await tap(p,`${roots[entry]} [data-name="${names[0]}"] [data-op="delete"]`);await waitFor(p,`!document.getElementById('confirm').hidden`);await tap(p,'#confirm-yes');await sleep(300);assert.ok(stored.has(names[0]));await shot(p,prefix+'-refused',entry);
    mode='';await sleep(100);await tap(p,`${roots[entry]} [data-name="${names[0]}"] [data-op="delete"]`);await waitFor(p,`!document.getElementById('confirm').hidden`);await tap(p,'#confirm-yes');await waitFor(p,`document.querySelector('${roots[entry]} [data-name="${names[0]}"] .kstate')?.dataset.on==='0'`);assert.equal(stored.has(names[0]),false);
    const exposure=await evaluate(p,`({body:document.body.textContent.includes(${JSON.stringify(value)}),fields:[...document.querySelectorAll('input,textarea')].some(e=>e.value.includes(${JSON.stringify(value)})),storage:[...Object.values(localStorage),...Object.values(sessionStorage)].some(v=>String(v).includes(${JSON.stringify(value)})),targets:[...document.querySelectorAll('${roots[entry]} .kbtn')].map(e=>e.getBoundingClientRect().height)})`);assert.equal(exposure.body,false);assert.equal(exposure.fields,false);assert.equal(exposure.storage,false);assert.ok(exposure.targets.every(h=>h>=44));
    if(language==='zh'&&theme==='light'&&width===390){
     const savedCount=calls.filter(x=>x.op==='sealed-save').length;
     for(const failure of ['deny-set','wrong-card']){mode=failure;await tap(p,`${roots[entry]} [data-name="${names[0]}"] [data-op="set"]`);await sleep(200);assert.equal(await evaluate(p,`document.getElementById('elev').hidden`),true);assert.equal(calls.filter(x=>x.op==='sealed-save').length,savedCount);}
     mode='';await tap(p,`${roots[entry]} [data-name="${names[0]}"] [data-op="set"]`);await waitFor(p,`document.getElementById('elev')?.hidden===false`);await tap(p,'#elev-deny');await tap(p,'#elev-deny');await waitFor(p,`document.getElementById('elev').hidden`);assert.equal(stored.has(names[0]),false,'cancelled sealed card saves nothing');
     await leave(p,entry);mode='no-passkey';await enter(p,entry);const deletes=calls.filter(x=>x.op==='delete').length;await tap(p,`${roots[entry]} [data-name="${names[1]}"] [data-op="delete"]`);await sleep(200);assert.equal(calls.filter(x=>x.op==='delete').length,deletes);await shot(p,prefix+'-no-passkey',entry);
     await leave(p,entry);mode='incomplete';await enter(p,entry,false);await waitFor(p,`document.querySelector('${roots[entry]} .relay-key-section')?.textContent.includes('Agent J')`);assert.equal(await evaluate(p,`document.querySelectorAll('${roots[entry]} .relay-key-section .kbtn').length`),0,'unknown row state never guessed as empty');await shot(p,prefix+'-incomplete',entry);
     await leave(p,entry);mode='offline';await enter(p,entry,false);await sleep(100);assert.equal(await evaluate(p,`document.querySelectorAll('${roots[entry]} .relay-key-section .kbtn').length`),0,'failed read clears stale actions');writeFileSync(`${out}/${prefix}-offline.png`,await shoot(p));
     await leave(p,entry);mode='';await enter(p,entry);
     if(entry==='models'){await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,10000);mode='timeout';const sent=calls.filter(x=>x.op==='set').length;await tap(p,`${roots[entry]} [data-name="${names[0]}"] [data-op="set"]`);await waitFor(p,`document.getElementById('toast').textContent==='Agent J 主机没回应，什么也没做。'`,18000);assert.equal(calls.filter(x=>x.op==='set').length,sent+1);assert.equal(calls.filter(x=>x.op==='sealed-save').length,savedCount);mode='';}
    }
    results.push({language,theme,width,entry,style,sets:calls.filter(x=>x.op==='sealed-save').length,exposure});await leave(p,entry);
   }
   if(before)continue;
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);await key(p,'Escape');
  }finally{await p.dispose();}
 }
 if(before)assert.equal(absent,0,'All three entries must mount the shared editable Relay component; absent scenes');
 console.log(captureOnly?'PASS Relay settings clean capture: 72 stored-state scenes': 'PASS Relay settings: 24 actual entry journeys, sealed add/replace, UV delete/refusal/cancel, two-key states, EN/ZH light/dark 390/1440');
}finally{writeFileSync(`${out}/results.json`,JSON.stringify({before,results},null,2));await B.close();await fake.stop();await web.stop();}
