// P425 #6: the real question sheet + encrypted local host, with independent signature verification.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep} from './browser.mjs';
import {questionMessage,unb64u} from '../../protocol/wire.js';
import {parityRecorder} from '../../parity/lib.mjs';
const OUT='reports/qa/p425/free/';mkdirSync(OUT,{recursive:true});
const rec=parityRecorder('web/test/p425_free.mjs');
let web,fake,B,count=0,ok=false;
const ev=evaluate;
try{
 web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});fake=await startFakeHost();B=await launch();
 for(const language of ['zh','en'])for(const scheme of ['light','dark'])for(const width of [393,1440]){
  fake.reset();const p=await newPage(B,width,900,scheme,{allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await ev(p,`document.getElementById('a2hs-ok')?.click()`);
   const id=String(++count).padStart(32,'0');
   const qs=[{q:'Your answer?',h:'Title',m:false,o:[{l:'A',d:''},{l:'B',d:''}]},{q:'Many?',h:'Many',m:true,o:[{l:'One',d:''},{l:'Two',d:''}]}];
   await fake.send({t:'status',s:'waiting',kind:'question',agent:'codex',name:'Agent J'});
   await fake.send({t:'question',id,qs,text_max:2000,ttl:180});
   await waitFor(p,`document.querySelectorAll('#qs .qfree textarea').length===2 && document.body.dataset.sheet==='1'`);
   const type=async text=>ev(p,`(()=>{const f=document.querySelector('#qs textarea');f.value=${JSON.stringify(text)};f.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
   await type(' \n ');assert.ok(await ev(p,`document.getElementById('askSend').disabled`));
   await type('draft');await ev(p,`document.querySelector('#qs .opt').click()`);
   assert.equal(await ev(p,`document.querySelector('#qs textarea').value`),'');
   const text='我自己的答案\nUnicode 🐈 ;1,2';await type(text);
   await ev(p,`document.querySelector('#qs .opt[data-q="1"][data-n="2"]').click()`);
   assert.ok(await ev(p,`!document.getElementById('askSend').disabled`));
   assert.equal(await ev(p,`document.querySelector('#qs textarea').value`),text);
   const geometry=await ev(p,`(()=>{const f=document.querySelector('#qs textarea'),b=f.getBoundingClientRect();return {max:f.maxLength,w:b.width,h:b.height,over:f.scrollWidth-f.clientWidth}})()`);
   assert.equal(geometry.max,2000);assert.ok(geometry.w>200 && geometry.h>=80 && geometry.over<=1);
   writeFileSync(OUT+`${language}-${scheme}-${width}.png`,await shoot(p));
   await ev(p,`document.getElementById('askSend').click()`);
   for(let n=0;n<100&&!fake.st.qAnswers.length;n++)await sleep(20);
   const m=fake.st.qAnswers.at(-1);assert.ok(m);assert.deepEqual(m.pick,[text,[2]]);assert.equal(fake.st.says.length,0);
   const dev=fake.devs[0],key=await webcrypto.subtle.importKey('raw',unb64u(dev.sk),{name:'Ed25519'},false,['verify']);
   const msg=await questionMessage(fake.channel,dev.id,id,'answer',qs,m.pick);
   assert.ok(await webcrypto.subtle.verify({name:'Ed25519'},key,unb64u(m.sig),msg));
   const tampered=await questionMessage(fake.channel,dev.id,id,'answer',qs,[text+'x',[2]]);
   assert.equal(await webcrypto.subtle.verify({name:'Ed25519'},key,unb64u(m.sig),tampered),false);
   // A legacy host never offers a field it cannot receive.
   await ev(p,`document.getElementById('sheetClose').click()`);
   await fake.send({t:'question',id:'f'.repeat(32),qs,ttl:180});
   await waitFor(p,`document.querySelectorAll('#qs .qfree').length===0`);
  }finally{await p.dispose();}
 }
 ok=true;console.log('P425 free answers PASS',count,'zh/en light/dark narrow/wide cases, exact signatures and legacy host');
}finally{
 const clean=await Promise.allSettled([B?.close(),fake?.stop(),web?.stop()]);
 rec.record('free-answer',ok && clean.every(x=>x.status==='fulfilled'));rec.write();
 for(const x of clean)if(x.status==='rejected')throw x.reason;
}
