#!/usr/bin/env node
// Local successful question follows its new owner turn, in either history/result ordering.
process.env.P541_LIBRARY='1';
import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
import * as wire from '../../protocol/wire.js';
import {elevateChallenge} from '../public/js/faceid.js';
const H=await import('./p541.mjs'),{sleep,waitFor}=await import('./browser.mjs');
const checks=[];const check=(v,name)=>{checks.push({name,pass:!!v});assert.ok(v,name);};
try{
 for(const[w,theme]of[[390,'light'],[390,'dark'],[1440,'light'],[1440,'dark']])for(const order of['result-first','history-first']){
  const p=await H.setup(w,w===390?844:900,theme),label=w+' '+theme+' '+order;
  await H.ev(p,`document.getElementById('source541').click()`);await waitFor(p,`document.getElementById('pg').textContent.startsWith('1 /')&&document.body.dataset.sheet==='0'`);
  await H.fake.addTurn({k:'agent',text:'被动消息'},'被动回复不会把主人拉离历史页。');await sleep(120);
  check(await H.ev(p,`document.getElementById('pg').textContent.startsWith('1 /')`),label+' passive arrival preserves history');
  await H.ev(p,`document.getElementById('pending541').click()`);await waitFor(p,`document.body.dataset.sheet==='1'`);
  const original=H.fake.st.onApp;
  H.fake.st.onApp=async(c,m,send)=>{
   let held;const result=await original(c,m,async out=>{
    if(m.t==='approval_review'&&m.op==='question'&&out.t==='approval_review_result'){
     if(order==='history-first'){held=out;return;}
     await send(out);await sleep(180);return;
    }
    await send(out);
   });if(held)await send(held);return result;
  };
  await H.ev(p,`document.getElementById('doubt541c').click()`);
  await waitFor(p,`document.body.dataset.sheet==='0'&&document.getElementById('omText').textContent.includes('请给我解释一下。')`);
  check(await H.ev(p,`document.getElementById('pg').textContent.startsWith('4 /')&&document.getElementById('om').classList.contains('open')`),label+' active question displays new owner turn');
  check(await H.ev(p,`document.body.dataset.status==='working'&&!document.getElementById('pending541').hidden`),label+' question keeps blue working and collapsed anchor');
  await H.fake.updateTurn(H.currentQuestion().id,{reply:{text:'这是当前疑问的主线程解释。'},end:'done'});await H.fake.send({t:'status',s:'idle'});
  await waitFor(p,`document.getElementById('words').textContent.includes('当前疑问的主线程解释')&&!!document.getElementById('reopen541')`);
  check(await H.ev(p,`document.getElementById('pg').textContent.startsWith('4 /')&&document.body.dataset.status==='idle'`),label+' explanation remains current with standard reopen');
  const other=structuredClone(H.currentCard());other.id='541f0000000000000000000000000002';other.envelope.request_id=other.id;other.envelope_sha256=await wire.approvalEnvelopeDigest(other.envelope);other.fa_ch=wire.b64u(await elevateChallenge(H.fake.channel,H.fake.devs.at(-1).id,other.id,other.pk_kind,other.id,other.envelope_sha256));
  await H.fake.ask(other);await sleep(120);await H.fake.send({t:'ask_done',id:H.currentCard().id,result:order==='result-first'?'allow':'deny'});await sleep(120);await H.ev(p,`document.getElementById('sheetClose').click()`);
  check(await H.ev(p,`!document.getElementById('reopen541')&&document.getElementById('words').textContent.includes('当前疑问的主线程解释')`),label+' terminal request removes cached reopen and keeps explanation');
  check(await H.ev(p,`!document.getElementById('pending541').hidden&&document.querySelector('#pending541 .count541').hidden&&document.getElementById('webReportBtn').hidden`),label+' other pending card keeps its independent anchor');
  await H.fake.send({t:'ask_done',id:other.id,result:'deny'});await sleep(120);
  check(await H.ev(p,`!document.getElementById('reopen541')&&document.getElementById('pending541').hidden&&!document.getElementById('webReportBtn').hidden`),label+' absent lookup stays clean after final other card ends');
  await p.dispose();
 }
}finally{await H.B.close();await H.fake.stop();await H.web.stop();writeFileSync(H.OUT+'/review-follow-validation.json',JSON.stringify({checks},null,2));}
console.log(JSON.stringify({checks:checks.length}));
