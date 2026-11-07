#!/usr/bin/env node
// Real Host frame → actual Noise client → native CDP WebAuthn → real Host admission.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';import {createInterface} from 'node:readline';
import {mkdirSync,writeFileSync} from 'node:fs';import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,sleep} from './browser.mjs';
const OUT=process.env.P572_OUT||process.cwd()+'/reports/qa/572/web/host-web-uv';mkdirSync(OUT,{recursive:true});
const checks=[],check=(v,n)=>{checks.push({name:n,pass:!!v});assert.ok(v,n);};
const py=spawn(process.env.AGENTJ_572_PYTHON||'python3',[fileURLToPath(new URL('./p572-host-bridge.py',import.meta.url))],{env:process.env,stdio:['pipe','pipe','pipe']});
let errors='',pending=[];py.stderr.on('data',b=>errors+=b);createInterface({input:py.stdout}).on('line',s=>{if(s.startsWith('572RPC:'))pending.shift()?.(JSON.parse(s.slice(7)));});
const rpc=async op=>{const result=await Promise.race([new Promise(r=>{pending.push(r);py.stdin.write(JSON.stringify(op)+'\n');}),new Promise((_,j)=>setTimeout(()=>j(Error('Host bridge timeout: '+errors.slice(-500))),15000))]);assert.ok(result.ok,(result.error||'')+' '+(result.trace||''));return result;};
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();const clientUrl=web.url.replace('127.0.0.1','localhost');let result,p;
try{
 p=await newPage(B,390,844,'dark',{allow:[clientUrl,fake.relay+'/']});await p.send('WebAuthn.enable');
 const {authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
 await navigate(p,fake.newPairing(clientUrl+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 const keys=await evaluate(p,`(async()=>{const s=await import('./js/store.js'),w=await import('./proto/wire.js');return {pub:w.b64u((await s.deviceKey()).pub),sk:w.b64u((await s.signKey()).pub)}})()`);
 const dev=fake.devs.at(-1),setup=await rpc({op:'setup',...keys,device:dev.id,channel:fake.channel});
 await p.send('WebAuthn.addCredential',{authenticatorId,credential:setup.credential});
 const {card}=await rpc({op:'card'});check(card.fa_ch!==card.envelope_fa_ch,'real Host frame retains distinct v1 and v2 UV challenges');
 // Tampered transport controls cannot replace the legacy challenge as fallback.
 for(const kind of ['missing','wrong']) {
  const bad=structuredClone(card);if(kind==='missing')delete bad.envelope_fa_ch;else bad.envelope_fa_ch=bad.fa_ch;
  await fake.ask(bad);await sleep(120);
  const refused=await evaluate(p,`(async()=>{const s=await import('./js/snap.js'),api=await import('./js/api.js');const a=s.S.asks.get('${card.id}');return {verified:a.envelope_verified,result:await api.approve(a,'allow')}})()`);
  check(!refused.verified&&!refused.result.ok,kind+' envelope_fa_ch refuses v2 instead of using legacy fallback');
  await fake.send({t:'ask_done',id:card.id,result:'deny'});await sleep(40);
 }
 // Positive path uses the unmodified Host._ask_msg output, never a reconstructed card.
 await fake.ask(card);
 await waitFor(p,`(async()=>{const s=await import('./js/snap.js');return s.S.asks.get('${card.id}')?.envelope_verified===true})()`);
 check(await evaluate(p,`(async()=>{const s=await import('./js/snap.js');return s.S.asks.get('${card.id}').fa_ch===${JSON.stringify(card.envelope_fa_ch)}})()`),'verified local card selects host envelope_fa_ch');
 check(await evaluate(p,`!document.getElementById('apprAllow').disabled&&document.getElementById('approvalSource572').textContent.includes('请整理旧导出')`),'actual real-host source shown and approval enabled');
 let resolve;const done=new Promise(r=>resolve=r);fake.st.onApp=async(c,m)=>{if(m.t!=='answer')return false;try{result=await rpc({op:'answer',answer:m});resolve();}catch(e){result={error:String(e)};resolve();}return true;};
 // The product approval API consumes the unmodified snapshot card; native credentials.get uses CDP's platform authenticator.
 const approval=await evaluate(p,`(async()=>{const s=await import('./js/snap.js'),api=await import('./js/api.js');return api.approve(s.S.asks.get('${card.id}'),'allow')})()`);check(approval.ok,'product API completed native WebAuthn and sent answer: '+JSON.stringify(approval));await Promise.race([done,new Promise((_,j)=>setTimeout(()=>j(Error('no actual webpage answer: '+JSON.stringify(p.problems))),12000))]);
 check(!result.error&&result.behavior==='allow'&&result.approval_v===2,'real webpage Ed25519 plus CDP WebAuthn answer accepted by Host._app');
 check(result.negative.includes('passkey_required')&&result.negative.includes('bad_signature'),'Host rejects same real answer without UV or with forged signature');
 check(result.audit==='ok','real host audit signature verifies');
 const credentials=await p.send('WebAuthn.getCredentials',{authenticatorId});check(credentials.credentials[0].signCount>0,'native virtual authenticator actually performed assertion');
 console.log(JSON.stringify({checks:checks.length,passed:checks.filter(x=>x.pass).length}));
}finally{try{await rpc({op:'stop'});}catch{}py.kill();await B.close();await fake.stop();await web.stop();writeFileSync(OUT+'/validation.json',JSON.stringify({checks,result,stderr:errors},null,2));}
