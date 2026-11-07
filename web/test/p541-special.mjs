#!/usr/bin/env node
process.env.P541_LIBRARY='1';
import {writeFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import assert from 'node:assert/strict';
import * as wire from '../../protocol/wire.js';
const {setup,fake,B,ev,web,OUT}=await import('./p541.mjs');
const {waitFor,sleep}=await import('./browser.mjs');
const checks=[];const check=(value,name)=>{checks.push({name,pass:!!value});assert.ok(value,name);};
try{
 const p=await setup(390,844,'light');await fake.send({t:'ask_done',id:'541b0000000000000000000000000001',result:'deny'});await sleep(200);
 const id='541a0000000000000000000000000001',e={v:2,channel:fake.channel,session:String(fake.st.epoch),request_id:id,revision:1,nonce:'nonce-1',title:'查看本次连接密钥',source:{message_id:1,root_message_id:1,session:String(fake.st.epoch),channel:'phone',time:Date.now(),sender:'你',text:'请在我确认的设备上显示连接密钥。',verified:true,suspicious:false,history_outside_window:false,initiator:'Agent J',task_record:'special-541',chain:[]},action:'仅在完成设备确认的遥控器上显示。',details:'受控秘密领取 · 原签名与UV协议',effects:['secret_display'],plan:null,expires_at:Math.floor(Date.now()/1000)+600};
 let card={t:'secret_out',id,n:'0'.repeat(32),kind:'text',name:'DEMO_KEY',purpose:'本次设备连接',filename:'',size:16,ttl:600,fa:'c3ludGhldGljLXJldmlldy1jcmVk',fa_s:'local',v:2,envelope:e,envelope_sha256:await wire.approvalEnvelopeDigest(e),revision:1,review_state:'pending'};
 fake.st.onApp=async(c,m,send)=>{
  if(m.t!=='approval_review')return false;
  const key=await webcrypto.subtle.importKey('raw',wire.unb64u(fake.devs.find(d=>d.id===c.devId).sk),'Ed25519',false,['verify']);
  check(await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(m.sig),wire.approvalReviewMessage(fake.channel,c.devId,{...card,approval_v:2,envelope_verified:true},m.op,m.event_id)),'dedicated review signature');
  card=structuredClone(card);card.revision++;card.envelope.revision=card.revision;card.envelope.nonce='nonce-'+card.revision;card.n=String(card.revision).padStart(32,'0');card.review_state=m.op==='question'?'questioned':'pending';card.envelope_sha256=await wire.approvalEnvelopeDigest(card.envelope);
  await send({t:'approval_review_result',id,op:m.op,event_id:m.event_id,ok:true,ask:card});return true;
 };
 await fake.send(card);await waitFor(p,`document.getElementById('sout')?.classList.contains('approval-special541')&&!document.getElementById('sout').hidden`);await sleep(250);
 check(await ev(p,`document.getElementById('sout').querySelector('.source541').textContent.includes('请在我确认的设备')`),'dedicated source displayed');
 check(await ev(p,`!document.getElementById('sout').querySelector('details').open`),'dedicated details default folded');
 await ev(p,`document.getElementById('sout').querySelector('.special-fold541').click()`);await waitFor(p,`!document.getElementById('pending541').hidden&&document.getElementById('sout').hidden`);
 check(await ev(p,`document.body.dataset.status==='idle'`),'dedicated fold normal background');
 await ev(p,`document.getElementById('pending541').click()`);await waitFor(p,`!document.getElementById('sout').hidden`);
 await ev(p,`document.getElementById('sout').querySelector('.special-question541').click()`);await waitFor(p,`document.getElementById('sout').hidden&&!document.getElementById('pending541').hidden`);
 check(!fake.log.some(m=>m.t==='secret_out_open'),'dedicated question never claims value');
 check(await ev(p,`!document.querySelector('#sout-value').textContent.includes('DEMO_SECRET_VALUE')`),'no value in metadata UI');
 await fake.send({t:'secret_out_done',id,result:'declined'});await sleep(200);check(await ev(p,`document.getElementById('pending541').hidden&&!document.getElementById('webReportBtn').hidden`),'dedicated terminal restores report slot');
 await p.dispose();
}finally{await B.close();await fake.stop();await web.stop();writeFileSync(OUT+'/special-validation.json',JSON.stringify({checks},null,2));}
console.log(JSON.stringify({checks:checks.length}));
