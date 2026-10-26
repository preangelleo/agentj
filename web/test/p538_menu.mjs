// P538 (#302 / #303): the shared-session model label says its version, and a Claude Code terminal menu reaches the phone
// as the host sends it — rows with their descriptions, or the terminal screen above an ↑ ↓ Enter Esc keypad — and a tap
// is a signed pick over exactly those cards (the screen text included). Real web client + encrypted fake host + Chromium.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep} from './browser.mjs';
import {questionMessage,unb64u} from '../../protocol/wire.js';
import {parityRecorder} from '../../parity/lib.mjs';
const OUT='reports/qa/p538/';mkdirSync(OUT,{recursive:true});
const CARDS=JSON.parse(readFileSync(new URL('./fixtures/p538-cards.json',import.meta.url),'utf8'));
const rec=parityRecorder('web/test/p538_menu.mjs');
const MODELS=[['opus-5.5','Opus 5.5'],['fable-5.1','Fable 5.1'],['sonnet-5.5','Sonnet 5.5'],['haiku-5.5','Haiku 5.5'],['opus-4.8','Opus 4.8']]
  .map(([id,name])=>({id,name,efforts:['low','medium','high','xhigh','max']}));
let web,fake,B,count=0,ok=false;
const ev=evaluate;
try{
 web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});fake=await startFakeHost();B=await launch();
 for(const language of ['zh','en'])for(const scheme of ['light','dark'])for(const width of [393,1440]){
  fake.reset();const p=await newPage(B,width,900,scheme,{allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await ev(p,`document.getElementById('a2hs-ok')?.click()`);
   // #302: the shared session's label = the terminal's status line name, version included
   await fake.send({t:'models',models:MODELS.map(m=>({...m,cur:m.id==='opus-4.8'})),effort:'medium',default:{model:null,effort:null}});
   await fake.send({t:'meter',model:'claude-opus-4-8',model_name:'Opus 4.8',effort:'medium',ctx:null,h5:null,week:null,at:Math.floor(Date.now()/1000)});
   await waitFor(p,`document.getElementById('metaModel').textContent==='Opus 4.8'`);
   await fake.send({t:'meter',model:'claude-opus-5-5',model_name:'Opus 5.5',effort:'medium',ctx:null,h5:null,week:null,at:Math.floor(Date.now()/1000)});
   await waitFor(p,`document.getElementById('metaModel').textContent==='Opus 5.5'`);
   if(width===393)writeFileSync(OUT+`pill-${language}-${scheme}.png`,await shoot(p));
   for(const name of ['model','flag','screen']){
    const qs=CARDS[name][language];
    const id=String(++count).padStart(32,'0');
    await fake.send({t:'status',s:'waiting',kind:'question',agent:'claude',name:'Agent J'});
    await fake.send({t:'question',id,qs,text_max:2000,ttl:180});
    await waitFor(p,`document.body.dataset.sheet==='1' && document.querySelectorAll('#qs .opt').length===${qs[0].o.length}`);
    const view=await ev(p,`(()=>{const pre=document.querySelector('#qs pre.qscreen'),h=document.querySelector('#qs h3');
      return {title:document.getElementById('sheetTitle').textContent,labels:[...document.querySelectorAll('#qs .opt .t > span:first-child')].map(x=>x.textContent),
       h:h?h.textContent:null,pre:pre?pre.textContent:null,ws:pre?getComputedStyle(pre).whiteSpace:null,font:pre?getComputedStyle(pre).fontFamily:null,
       preW:pre?pre.getBoundingClientRect().width:0,copy:!!(pre&&pre.closest('.cc')),sheetW:document.getElementById('sheet').getBoundingClientRect().width}})()`);
    assert.equal(view.title,qs[0].q);assert.deepEqual(view.labels,qs[0].o.map(o=>o.l));
    if(name==='screen'){
     assert.equal(view.pre,qs[0].h.replace(/\n+$/,''));assert.equal(view.h,null);assert.equal(view.ws,'pre');assert.match(view.font,/mono/i);
     assert.ok(view.preW>100 && view.preW<=view.sheetW,'the screen block stays inside the sheet');assert.ok(view.copy,'the screen text can be copied');
     assert.equal(qs[0].o.length,4);
    }else{assert.equal(view.pre,null);assert.equal(view.h,qs[0].h);}
    await waitFor(p,`!document.getElementById('toast').classList.contains('on')`);   // the previous card's result toast has gone
    writeFileSync(OUT+`${name}-${language}-${scheme}-${width}.png`,await shoot(p));
    const pickN=2;
    await ev(p,`document.querySelector('#qs .opt[data-q="0"][data-n="${pickN}"]').click()`);
    await ev(p,`document.getElementById('askSend').click()`);
    for(let n=0;n<100&&!fake.st.qAnswers.find(a=>a.id===id);n++)await sleep(20);
    const m=fake.st.qAnswers.find(a=>a.id===id);assert.ok(m,'signed pick sent');assert.deepEqual(m.pick,[[pickN]]);
    const dev=fake.devs[0],key=await webcrypto.subtle.importKey('raw',unb64u(dev.sk),{name:'Ed25519'},false,['verify']);
    assert.ok(await webcrypto.subtle.verify({name:'Ed25519'},key,unb64u(m.sig),await questionMessage(fake.channel,dev.id,id,'answer',qs,m.pick)));
    // the screen text is signed too: another screen with the same keys is a different card
    const other=[{...qs[0],h:qs[0].h+'x'}];
    assert.equal(await webcrypto.subtle.verify({name:'Ed25519'},key,unb64u(m.sig),await questionMessage(fake.channel,dev.id,id,'answer',other,m.pick)),false);
    await fake.send({t:'question_done',id,result:'answered'});
    await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
    await waitFor(p,`!document.querySelector('#qs .opt:not([disabled])')`);
    await ev(p,`document.getElementById('sheetClose')?.click()`);
   }
  }finally{await p.dispose();}
 }
 ok=true;console.log('P538 model label + terminal menu cards PASS',count,'cards, zh/en light/dark narrow/wide, signed picks');
}finally{
 const clean=await Promise.allSettled([B?.close(),fake?.stop(),web?.stop()]);
 rec.record('terminal-menu-card',ok && clean.every(x=>x.status==='fulfilled'));rec.write();
 for(const x of clean)if(x.status==='rejected')throw x.reason;
}
