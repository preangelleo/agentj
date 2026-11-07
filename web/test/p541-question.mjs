#!/usr/bin/env node
// Pending questions share the reply-action anchor; question payloads/signatures stay unchanged.
process.env.P541_LIBRARY='1';
import {writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const H=await import('./p541.mjs'),{sleep,waitFor}=await import('./browser.mjs');
const checks=[];const check=(value,name)=>{checks.push({name,pass:!!value});assert.ok(value,name);};
try{
 for(const[w,theme]of[[390,'light'],[390,'dark'],[1440,'light'],[1440,'dark']]){
  const p=await H.setup(w,w===390?844:900,theme);await H.fake.send({t:'ask_done',id:H.currentCard().id,result:'deny'});await sleep(100);
  const qid='541c0000000000000000000000000001',second='541c0000000000000000000000000002';
  const question=id=>({t:'question',id,ttl:180,qs:[{q:'选择报告格式？',h:'格式',m:false,o:[{l:'PDF',d:'仅选择报告格式'},{l:'Markdown',d:'保持原问题'}]}]});
  await H.fake.send(question(qid));await waitFor(p,`document.body.dataset.sheet==='1'&&document.getElementById('qs').textContent.includes('PDF')`);
  await H.fake.send(question(second));await H.ev(p,`document.getElementById('sheetClose').click()`);await sleep(100);
  check(await H.ev(p,`!document.getElementById('pending541').hidden&&document.getElementById('webReportBtn').hidden&&document.getElementById('pendTag').hidden`),w+theme+' question shared anchor');
  check(await H.ev(p,`document.querySelector('#pending541 .count541').textContent==='2'&&!document.querySelector('#pending541 .count541').hidden`),w+theme+' question count is exact');
  const before=H.fake.log.filter(m=>m.t==='approval_review').length;
  await H.ev(p,`document.getElementById('pending541').click()`);await waitFor(p,`document.body.dataset.sheet==='1'`);
  check(H.fake.log.filter(m=>m.t==='approval_review').length===before,w+theme+' question reopening is only visibility');
  check(await H.ev(p,`document.getElementById('sheet').textContent.includes('选择报告格式？')&&document.querySelectorAll('#qs .opt').length===2`),w+theme+' original question remains intact');
  await H.fake.send({t:'question_done',id:qid,result:'answered'});await sleep(100);await H.ev(p,`document.getElementById('sheetClose').click()`);
  check(await H.ev(p,`document.querySelector('#pending541 .count541').hidden&&!document.getElementById('pending541').hidden`),w+theme+' one question remains after other ends');
  await H.fake.send({t:'question_done',id:second,result:'cancelled'});await sleep(150);
  check(await H.ev(p,`document.getElementById('pending541').hidden&&!document.getElementById('webReportBtn').hidden`),w+theme+' final question restores report slot');
  await p.dispose();
  const mixed=await H.setup(w,w===390?844:900,theme);await H.fake.send(question(qid));await sleep(150);await H.ev(mixed,`document.getElementById('sheetClose').click()`);await sleep(100);
  check(await H.ev(mixed,`!document.getElementById('pending541').hidden&&document.querySelector('#pending541 .count541').textContent==='2'&&!document.querySelector('#pending541 .count541').hidden`),w+theme+' mixed question and permission count exactly two');
  await H.fake.send({t:'question_done',id:qid,result:'cancelled'});await sleep(100);await H.ev(mixed,`document.getElementById('sheetClose').click()`);
  check(await H.ev(mixed,`!document.getElementById('pending541').hidden&&document.querySelector('#pending541 .count541').hidden&&document.getElementById('webReportBtn').hidden`),w+theme+' ending question preserves permission anchor');
  await H.fake.send({t:'ask_done',id:H.currentCard().id,result:'deny'});await sleep(150);
  check(await H.ev(mixed,`document.getElementById('pending541').hidden&&!document.getElementById('webReportBtn').hidden`),w+theme+' mixed final permission restores report');
  await mixed.dispose();
 }
}finally{await H.B.close();await H.fake.stop();await H.web.stop();writeFileSync(H.OUT+'/question-validation.json',JSON.stringify({checks},null,2));}
console.log(JSON.stringify({checks:checks.length}));
