#!/usr/bin/env node
// Actual product DOM and Noise/device signatures; no owner device or production state.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync,mkdtempSync,readdirSync,symlinkSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {resolve,join} from 'node:path';
import {webcrypto} from 'node:crypto';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
import * as wire from '../../protocol/wire.js';
const out=resolve(process.env.UV_LIST_WEB_OUT||'reports/qa/uv-list/web');mkdirSync(out,{recursive:true});
const old=mkdtempSync('/var/tmp/aj-uv-web-');
for(const name of readdirSync(PUBLIC_DIR)){if(name==='js')continue;symlinkSync(join(PUBLIC_DIR,name),join(old,name));}
mkdirSync(join(old,'js'));
const changed=['approval541.js','approval-source572.js','api.js'];
for(const name of readdirSync(join(PUBLIC_DIR,'js'))){if(changed.includes(name))writeFileSync(join(old,'js',name),execFileSync('git',['show',`b8ad3d701ac635b258b8339e262f9e3287f18943:web/public/js/${name}`]));else symlinkSync(join(PUBLIC_DIR,'js',name),join(old,'js',name));}
const before=await startWebServer({publicDir:old}),after=await startWebServer(),fake=await startFakeHost(),B=await launch();
let serial=0,received=[];const checks=[];
const check=(v,name)=>{checks.push({name,pass:!!v});assert.ok(v,name);};
fake.st.onApp=async(c,m)=>{if(m.t==='answer'||m.t==='approval_review'){received.push({c,m});if(m.t==='approval_review')await fake.send({t:'approval_review_result',event_id:m.event_id,ok:true,state:'questioned'});return true;}return false;};
const mk=async(channel='unknown',uv=false)=>{const id=(++serial).toString(16).padStart(32,'0');const envelope={v:2,channel:fake.channel,session:'synthetic',request_id:id,revision:1,nonce:'uv-list-'+serial,details:'Synthetic test command',source:{channel,verified:false,suspicious:channel==='email'||channel==='webhook',text:'测试来源',message_id:1,root_message_id:1},action:'测试确认',title:'测试确认',expires_at:Math.ceil(Date.now()/1000)+600};return{t:'ask',id,tool:uv?'agentj fn run':'Bash',summary:envelope.details,ttl:600,approval_v:2,envelope,envelope_sha256:await wire.approvalEnvelopeDigest(envelope),revision:1,...(uv?{passkey_required:true,fa:'synthetic',fa_ch:'c3ludGhldGlj'}:{})};};
const open=async(p,card)=>{await fake.ask(card);await waitFor(p,`(async()=>{const {S}=await import('./js/snap.js');return S.asks.get('${card.id}')?.envelope_verified===true})()`);await sleep(80);};
const act=(p,id,action)=>evaluate(p,`(async()=>{const {S}=await import('./js/snap.js'),a=await import('./js/api.js');return a.approve(S.asks.get('${id}'),'${action}')})()`);
const done=card=>fake.send({t:'ask_done',id:card.id,result:'deny'});
try{
for(const width of [390,1440])for(const theme of ['light','dark']){
 for(const [stage,server] of [['before',before],['after',after]]){
  fake.reset();received=[];const p=await newPage(B,width,width===390?844:900,theme,{allow:[server.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(server.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click()}`);
  const card=await mk();await open(p,card);
  const disabled=await evaluate(p,`document.getElementById('apprAllow').disabled`);
  check(disabled===(stage==='before'),`${width}/${theme}/${stage}: unknown origin confirm ${stage==='before'?'blocked':'enabled'}`);
  writeFileSync(join(out,`${width}-${theme}-${stage}.png`),await shoot(p));
  if(stage==='before'){check((await act(p,card.id,'allow')).why==='unverified_trigger',`${width}/${theme}: original API reproduces source rejection`);await done(card);await p.dispose();continue;}
  check((await act(p,card.id,'allow')).ok,`${width}/${theme}: unknown source can sign ordinary confirmation`);await waitFor(p,`true`);await sleep(80);
  const answer=received.find(r=>r.m.t==='answer'),dev=fake.devs.find(d=>d.id===answer.c.devId),key=await webcrypto.subtle.importKey('raw',wire.unb64u(dev.sk),'Ed25519',false,['verify']);
  check(await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(answer.m.sig),await wire.approvalV2Message(fake.channel,dev.id,{...card,envelope_verified:true},'allow')),`${width}/${theme}: unchanged request-bound signature valid`);await done(card);
  for(const source of ['system','email','webhook']){const c=await mk(source);await open(p,c);check(!await evaluate(p,`document.getElementById('apprAllow').disabled`),`${width}/${theme}: ${source} confirm enabled`);check((await act(p,c.id,'deny')).ok,`${width}/${theme}: ${source} deny signed`);await done(c);}
  const uv=await mk('email',true);uv.pk_kind='approval';uv.fa_ch=await evaluate(p,`(async()=>{const {elevateChallenge}=await import('./js/faceid.js'),{b64u}=await import('./proto/wire.js');return b64u(await elevateChallenge('${fake.channel}','${dev.id}','${uv.id}','${uv.pk_kind}','${uv.id}','${uv.envelope_sha256}'))})()`);await open(p,uv);const n=received.length;check((await act(p,uv.id,'allow')).why==='passkey_required'&&received.length===n,`${width}/${theme}: external-source listed command cannot omit UV`);check((await act(p,uv.id,'deny')).ok,`${width}/${theme}: UV refusal needs no biometric`);await done(uv);
  const invalid=await mk();invalid.envelope.details+=' changed after digest';await fake.ask(invalid);await sleep(150);check(await evaluate(p,`document.getElementById('apprAllow').disabled`),`${width}/${theme}: invalid envelope confirm disabled`);check(!(await act(p,invalid.id,'allow')).ok,`${width}/${theme}: invalid envelope cannot sign`);await done(invalid);
  const q=await mk('webhook');await open(p,q);await evaluate(p,`document.getElementById('doubt541c').click()`);await sleep(100);const question=received.find(r=>r.m.t==='approval_review'&&r.m.id===q.id);check(question?.m.op==='question',`${width}/${theme}: doubt sends original review intent`);check(await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(question.m.sig),wire.approvalReviewMessage(fake.channel,dev.id,{...q,envelope_verified:true},'question',question.m.event_id)),`${width}/${theme}: doubt signature valid`);
  await evaluate(p,`(async()=>{const {S}=await import('./js/snap.js'),m=await import('./js/approval541.js');const q=S.asks.get('${q.id}');q.review_state='questioned';m.paint(q,{busy:false,fold(){},locate(){},open(){}})})()`);check(await evaluate(p,`document.getElementById('apprAllow').disabled`),`${width}/${theme}: questioned request remains disabled`);await done(q);await p.dispose();
 }
}
}finally{await B.close();await fake.stop();await before.stop();await after.stop();rmSync(old,{recursive:true,force:true});writeFileSync(join(out,'validation.json'),JSON.stringify({checks},null,2)+'\n');}
console.log(JSON.stringify({checks:checks.length,passed:checks.filter(c=>c.pass).length}));
