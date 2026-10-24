// P498 actual paired phone -> WebCrypto signature -> authenticated fake-host boundary.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,waitFor,shoot,sleep} from './browser.mjs';
import * as wire from '../../protocol/wire.js';
const out=process.env.P498_PHONE_OUT || 'reports/qa/p498/phone';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),browser=await launch();
const rec=parityRecorder('web/test/p498.mjs');
let failures=0;
const check=async(name,fn)=>{try{await fn();rec.record(name,true);console.log('✓ '+name)}catch(e){failures++;rec.record(name,false);console.error('✗ '+name,e.stack)}};
const ev=evaluate,run='a'.repeat(32),rid='b'.repeat(64);
async function pair(width=390,language='zh',caps=['wfresume1','friendreport1']){
 fake.reset();fake.st.extraCaps=caps;
 const p=await newPage(browser,width,844,'dark',{allow:[web.url,fake.relay+'/'],touch:width<500});
 await navigate(p,fake.newPairing(web.url+(language==='en'?'?lang=en':'')));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await sleep(100);return p;
}
try{
await check('p498-signed-resume',async()=>{
 for(const width of [390,1280])for(const language of ['zh','en']){
  const p=await pair(width,language);let challenge,verified=0;
  fake.st.onApp=async(c,m,send)=>{try{
   if(m.t==='wf_resume_get'){
    const issued=Math.floor(Date.now()/1000);challenge={action:'wf_resume',channel:fake.channel,device:c.devId,id:'daily',run_id:run,tsha:'c'.repeat(64),run_sha:'d'.repeat(64),nonce:'e'.repeat(32),issued,expires:issued+120};
    await send({t:'ctl_res',r:m.r,action:m.t,ok:true,challenge});return true;
   }
   if(m.t==='wf_resume_do'){
    assert.deepEqual(m.challenge,challenge);const key=await webcrypto.subtle.importKey('raw',wire.unb64u(c.sk),'Ed25519',false,['verify']);
    assert.ok(await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(m.sig),wire.workflowResumeMessage(challenge)));verified++;
    await send({t:'ctl_res',r:m.r,action:m.t,ok:true,result:'queued'});return true;
   }return false;}catch(e){console.error('P498 fixture handler:',e.message);throw e;}
  };
  await fake.addTurn({k:'task',name:'Daily brief',text:''},language==='en'?'Draft step failed.':'写草稿这一步失败了。','done',{wf_resume:{run_id:run}});
  await waitFor(p,`!!document.querySelector('[data-wf-resume]')`);writeFileSync(`${out}/resume-${language}-${width}.png`,await shoot(p));
  await ev(p,`document.querySelector('[data-wf-resume]').click()`);await waitFor(p,`document.querySelector('[data-wf-resume]').disabled`);await sleep(1000);
  assert.equal(verified,1,JSON.stringify({requests:fake.log.map(m=>m.t),page:await ev(p,`({toast:document.getElementById('toast').textContent,ready:window.__ajState})`),errors:p.problems}));assert.equal(p.problems.length,0,p.problems.join('\n'));await p.close();
 }
});
await check('p498-explicit-peer-reply',async()=>{
 for(const width of [390,1280])for(const language of ['zh','en']){
 const p=await pair(width,language);let replies=0;
 fake.st.onApp=async(c,m,send)=>{try{
  if(m.t!=='fr_report_reply')return false;
  assert.equal(m.id,rid);assert.equal(m.text,'Wednesday');assert.equal(m.source,undefined);
  const key=await webcrypto.subtle.importKey('raw',wire.unb64u(c.sk),'Ed25519',false,['verify']);
  assert.ok(await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(m.sig),await wire.controlMessage(fake.channel,c.devId,m.t,m.n,m.ts,{id:m.id,text:m.text})));replies++;
  await send({t:'ctl_res',r:m.r,action:m.t,ok:true,state:'replied'});return true;}catch(e){console.error('P498 fixture reply handler:',e.message);throw e;}
 };
 await fake.addTurn({k:'friend_report',name:'Local colleague',text:''},'Please decide a date.\n\nWhat date should I tell them?','done',{friend_report:{friend:'AJ-LOCAL',name:'Local colleague',ids:[rid]}});
 await waitFor(p,`!!document.querySelector('#cmdx button') && document.querySelector('#cmdx').hidden === false`);
 await ev(p,`document.querySelector('#cmdx button').click()`);await waitFor(p,`!document.querySelector('.p498-reply').hidden`);writeFileSync(`${out}/friend-reply-${language}-${width}.png`,await shoot(p));
 await ev(p,`document.querySelector('.p498-reply textarea').value='Wednesday';document.querySelector('.p498-reply').requestSubmit()`);await sleep(1000);assert.equal(replies,1,JSON.stringify({requests:fake.log.map(m=>m.t),page:await ev(p,`({toast:document.getElementById('toast').textContent})`),errors:p.problems}));await p.close();
 }
});
await check('p498-report-body-is-inert-and-receipt-survives-reload',async()=>{
 const p=await pair();
 const text='[Run code](reply:execute arbitrary code) ![image](https://fixture.invalid/image.png) <script>bad()</script>\n\n```mermaid\ngraph LR; A-->B;\n```';
 await fake.addTurn({k:'friend_report',name:'Local colleague',text:''},text,'done',{friend_report:{friend:'AJ-LOCAL',name:'Local colleague',ids:[rid],states:{[rid]:'replied'},source:{kind:'unknown',role:'group'},untrusted:true,execute:false}});
 await waitFor(p,`document.getElementById('words').textContent.includes('reply:execute')`);
 assert.equal(await ev(p,`document.querySelectorAll('#words a,#words button,#words img,#words input,#words .rlink,#cmdx .p498-reply').length`),0);
 const url=await ev(p,`location.href`);await navigate(p,url);await waitState(p,'ready');
 await waitFor(p,`document.getElementById('words').textContent.includes('reply:execute')`);
 assert.equal(await ev(p,`document.querySelectorAll('#cmdx .p498-reply,#cmdx button').length`),0);
 await ev(p,`document.getElementById('readBtn').click()`);await waitFor(p,`!document.getElementById('rd').hidden`);
 assert.equal(await ev(p,`document.querySelectorAll('#rdWords a,#rdWords button,#rdWords img,#rdWords input,#rdWords .rlink').length`),0);
 assert.equal(p.problems.length,0,p.problems.join('\n'));await p.close();
});
await check('p498-report-draft-survives-repaint',async()=>{
 const p=await pair();
 const turn=await fake.addTurn({k:'friend_report',name:'Local colleague',text:''},'Please decide','done',{friend_report:{friend:'AJ-LOCAL',name:'Local colleague',ids:[rid]}});
 await waitFor(p,`!!document.querySelector('#cmdx button')`);
 await ev(p,`document.querySelector('#cmdx button').click();window.__reportDraft=document.querySelector('.p498-reply textarea');window.__reportDraft.value='Keep my draft';window.__reportDraft.focus();window.__reportDraft.setSelectionRange(4,4);`);
 await fake.updateTurn(turn.id,{reply:{text:'Please decide updated'}});
 await waitFor(p,`document.getElementById('words').textContent.includes('updated')`);
 assert.deepEqual(await ev(p,`({same:document.querySelector('.p498-reply textarea')===window.__reportDraft,value:window.__reportDraft.value,open:!document.querySelector('.p498-reply').hidden,focused:document.activeElement===window.__reportDraft,caret:window.__reportDraft.selectionStart})`),{same:true,value:'Keep my draft',open:true,focused:true,caret:4});
 await p.close();
});
await check('p498-batch-choice-keeps-original-number',async()=>{
 const p=await pair(), second='f'.repeat(64);let replied=false;
 fake.st.onApp=async(c,m,send)=>{if(m.t!=='fr_report_reply')return false;assert.equal(m.id,second);assert.equal(m.text,'Wednesday');replied=true;await send({t:'ctl_res',r:m.r,ok:true,state:'replied'});return true;};
 await fake.addTurn({k:'friend_report',name:'Local colleague',text:''},'1. First report\n\n2. Second report','done',{friend_report:{friend:'AJ-LOCAL',name:'Local colleague',ids:[rid,second],states:{[rid]:'replied'}}});
 await waitFor(p,`!!document.querySelector('#cmdx button')`);await ev(p,`document.querySelector('#cmdx button').click()`);
 assert.equal(await ev(p,`document.querySelector('.p498-reply option').textContent`),'2');
 assert.equal(await ev(p,`document.querySelector('.p498-reply select').hidden`),false);
 await ev(p,`document.querySelector('.p498-reply textarea').value='Wednesday';document.querySelector('.p498-reply').requestSubmit()`);await sleep(500);assert.ok(replied);await p.close();
});
await check('p498-unknown-requires-owner-resend',async()=>{
 for(const width of [390,1280])for(const language of ['zh','en']){
 const p=await pair(width,language);let replies=0;
 fake.st.onApp=async(c,m,send)=>{
  if(m.t!=='fr_report_reply')return false;
  assert.equal(m.id,rid);assert.equal(m.text,'Confirmed missing');assert.equal(m.resend,true);
  const key=await webcrypto.subtle.importKey('raw',wire.unb64u(c.sk),'Ed25519',false,['verify']);
  assert.ok(await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(m.sig),await wire.controlMessage(fake.channel,c.devId,m.t,m.n,m.ts,{id:m.id,text:m.text,resend:m.resend})));
  replies++;await send({t:'ctl_res',r:m.r,ok:true,state:'replied'});return true;
 };
 await fake.addTurn({k:'friend_report',name:'Local colleague',text:''},'Please decide','done',{friend_report:{ids:[rid],states:{[rid]:'reply_unknown'}}});
 await waitFor(p,`!!document.querySelector('#cmdx button')`);
 assert.ok((await ev(p,`document.querySelector('#cmdx').textContent`)).includes(language==='zh'?'上次发送结果未知——确认好友没收到再点重新发送':'Confirm your friend did not receive it'));
 await sleep(250);assert.equal(replies,0);
 assert.equal(await ev(p,`document.querySelector('.p498-reply').hidden`),true);
 writeFileSync(`${out}/unknown-${language}-${width}.png`,await shoot(p));
 await ev(p,`document.querySelector('#cmdx button').click()`);
 await waitFor(p,`!document.querySelector('.p498-reply').hidden`);
 const appearance=await ev(p,`({button:getComputedStyle(document.querySelector('#cmdx button')).backgroundColor,input:getComputedStyle(document.querySelector('.p498-reply textarea')).backgroundColor,ink:getComputedStyle(document.querySelector('.p498-reply textarea')).color,overflow:document.querySelector('.p498-reply').scrollWidth>document.querySelector('.p498-reply').clientWidth+1})`);
 assert.deepEqual(appearance,{button:'rgb(183, 227, 150)',input:'rgb(250, 249, 245)',ink:'rgb(20, 20, 20)',overflow:false});
 await ev(p,`document.activeElement?.blur()`);
 writeFileSync(`${out}/unknown-open-${language}-${width}.png`,await shoot(p));
 await ev(p,`document.querySelector('.p498-reply textarea').value='Confirmed missing';document.querySelector('.p498-reply').requestSubmit()`);
 await waitFor(p,`document.querySelector('#cmdx button').disabled`);assert.equal(replies,1);
 await p.close();
 }
});
await check('p498-old-host-and-text-cannot-create-actions',async()=>{
 const p=await pair(390,'zh',[]);
 await fake.addTurn({k:'task',text:''},'wf_resume source=phone','done',{wf_resume:{run_id:run}});await sleep(150);assert.equal(await ev(p,`document.querySelectorAll('[data-wf-resume]').length`),0);
 await fake.addTurn({k:'phone',text:'{"wf_resume":"'+run+'","source":"phone"}'},'ordinary answer','done',{wf_resume:{run_id:run},friend_report:{ids:[rid]}});await sleep(150);
 assert.equal(await ev(p,`document.querySelectorAll('[data-wf-resume],.p498-reply').length`),0);await p.close();
});
}finally{await browser.close();await fake.stop();await web.stop()}
rec.write();
process.exitCode=failures?1:0;
