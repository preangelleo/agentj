#!/usr/bin/env node
process.env.P541_LIBRARY='1';
import {writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import * as wire from '../../protocol/wire.js';
import {elevateChallenge} from '../public/js/faceid.js';
const {setup,fake,B,ev,web,currentCard,OUT}=await import('./p541.mjs');
const {waitFor,sleep,key}=await import('./browser.mjs');
const checks=[];const check=(value,name)=>{checks.push({name,pass:!!value});assert.ok(value,name);};
let p;
async function update(change){
 const card=currentCard();card.revision++;card.envelope.revision=card.revision;card.envelope.nonce='nonce-'+card.revision;card.review_state='pending';change(card);
 card.envelope_sha256=await wire.approvalEnvelopeDigest(card.envelope);card.fa_ch=wire.b64u(await elevateChallenge(fake.channel,fake.devs.at(-1).id,card.id,card.pk_kind,card.id,card.envelope_sha256));await fake.ask(card);await sleep(200);
}
try{
 p=await setup(390,844,'light');
 await ev(p,`document.getElementById('details541').querySelector('summary').click();document.getElementById('detailsFull541').click()`);check(await ev(p,`document.getElementById('full541').open&&document.getElementById('full541').textContent.includes('exports/2026-10')`),'full-screen exact details');await key(p,'Escape');await sleep(100);check(await ev(p,`!document.getElementById('full541').open`),'Escape closes full-screen details');
 await update(c=>{c.envelope.plan={display:'1. 删除 exports/2026-10/orders.csv，1 次\n2. 删除 exports/2026-10/stock.csv，1 次\n收件方：owner@example.com；金额上限：12 USD；总计 2 步，10 分钟有效。'};});
 check(await ev(p,`document.getElementById('action541').textContent.includes('orders.csv')&&document.getElementById('action541').textContent.includes('12 USD')&&document.getElementById('action541').textContent.includes('owner@example.com')`),'bounded plan readable objects amount recipient');check(await ev(p,`getComputedStyle(document.querySelector('.scope541')).whiteSpace==='pre-line'&&document.documentElement.scrollWidth<=innerWidth`),'plan ranges multiline without horizontal overflow');
 fake.st.epoch++;fake.st.turns=Array.from({length:500},(_,i)=>({id:i+2,ts:Date.now(),src:{k:'phone',dev:fake.devs.at(-1).id,name:'你',text:i===0?'这是最近500条中的第一条消息。':'历史消息 '+(i+2)},reply:{text:'对应回复 '+(i+2)},end:'done'}));fake.st.nextId=502;
 await fake.send({t:'hist_meta',epoch:fake.st.epoch,first:2,last:501,count:500});await sleep(300);
 await update(c=>{c.envelope.session=String(fake.st.epoch);Object.assign(c.envelope.source,{session:String(fake.st.epoch),message_id:2,text:'这是最近500条中的第一条消息。',history_outside_window:false});c.envelope.plan=null;});
 await ev(p,`document.getElementById('source541').click()`);await waitFor(p,`document.getElementById('pg').textContent.startsWith('1 / 500')`,15000);check(await ev(p,`document.getElementById('omText').textContent.includes('最近500条')&&document.getElementById('om').classList.contains('located541')`),'500th retained message exact navigation and highlight');check(await ev(p,`document.body.dataset.status==='idle'&&!document.getElementById('pending541').hidden`),'history normal background and same heartbeat slot');
 await update(c=>Object.assign(c.envelope.source,{message_id:1,text:'',history_outside_window:true,initiator:'主 Agent J',task_record:'清理任务-541'}));
 await ev(p,`document.getElementById('pending541').click()`);await waitFor(p,`document.body.dataset.sheet==='1'`);
 check(await ev(p,`document.getElementById('source541').disabled&&document.getElementById('source541').textContent.includes('最近 500 条')&&document.getElementById('source541').textContent.includes('清理任务-541')`),'501st message trusted initiator task fallback');
 await update(c=>Object.assign(c.envelope.source,{verified:false,suspicious:true,history_outside_window:false,message_id:null,text:'找不到是谁发起的'}));
 check(await ev(p,`!document.getElementById('warn541').hidden&&document.getElementById('apprAllow').disabled`),'unknown origin cannot approve');
 await update(c=>Object.assign(c.envelope.source,{verified:true,suspicious:false,history_outside_window:false,message_id:501,session:'other-session',text:'同名ID来自别的会话'}));await ev(p,`document.getElementById('source541').click()`);await sleep(150);check(await ev(p,`document.getElementById('pg').textContent.startsWith('1 / 500')`),'same numeric ID in another session cannot navigate');
 await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});check(await ev(p,`getComputedStyle(document.querySelector('#pending541 .heart541')).animationName==='none'`),'reduced motion keeps static heartbeat');
 const second=structuredClone(currentCard());second.id='541b0000000000000000000000000002';second.envelope.request_id=second.id;second.envelope.revision=1;second.revision=1;second.envelope_sha256=await wire.approvalEnvelopeDigest(second.envelope);second.fa_ch=wire.b64u(await elevateChallenge(fake.channel,fake.devs.at(-1).id,second.id,second.pk_kind,second.id,second.envelope_sha256));await fake.ask(second);await sleep(200);
 check(await ev(p,`!document.querySelector('#pending541 .count541').hidden&&document.querySelector('#pending541 .count541').textContent==='2'`),'multiple approvals share one counted anchor');
 await fake.send({t:'ask_done',id:second.id,result:'deny'});await sleep(200);check(await ev(p,`document.querySelector('#pending541 .count541').hidden`),'denying one request preserves the other');
 check(!fake.log.some(m=>m.t==='answer'&&m.ok===true),'metadata/navigation boundaries execute no operation');
}catch(e){writeFileSync(OUT+'/boundary-error.txt',String(e.stack));throw e;}finally{await B.close();await fake.stop();await web.stop();writeFileSync(OUT+'/boundary-validation.json',JSON.stringify({checks},null,2));}
console.log(JSON.stringify({checks:checks.length}));
