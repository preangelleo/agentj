import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out='reports/qa/p392';mkdirSync(out,{recursive:true});
const fake=await startFakeHost(), web=await startWebServer(), B=await launch();
let count=0;
async function visibleToast(p){
 await waitFor(p,`(()=>{const n=document.getElementById('toast');return n.classList.contains('on') && +getComputedStyle(n).opacity>0.9})()`);
 assert.ok(await evaluate(p,`(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect();n.style.pointerEvents='auto';const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);n.style.pointerEvents='';return n.contains(hit)})()`),'toast paints above the full-screen Settings page');
}

try{
for(const lang of ['zh','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
 fake.reset();fake.setUp(true);
 const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click(); if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click(); true`);
 await evaluate(p,`document.documentElement.dataset.theme='${theme}'; import('./js/settings.js').then(s=>s.openRescue())`);
 assert.equal(await evaluate(p,`document.documentElement.dataset.theme`),theme);
 assert.equal(await evaluate(p,`document.getElementById('set-h-rescue').textContent`),lang==='zh'?'紧急修复':'Emergency repair');
 await waitFor(p,`!document.getElementById('settings').hidden`);
 fake.setUp(false); await waitState(p,'waiting-host');
 await waitFor(p,`document.getElementById('set-connection').dataset.online==='false'`);
 await evaluate(p,`document.getElementById('rs-reset-go').click()`);
 await waitFor(p,`document.getElementById('rs-reset').dataset.state==='fail'`);
 assert.equal(await evaluate(p,`document.getElementById('rs-reset-msg').classList.contains('rsfail')`),true);
 assert.match(await evaluate(p,`document.getElementById('toast').textContent`),lang==='zh'?/连接已断开/:/Connection lost/);
 assert.equal(await evaluate(p,`document.getElementById('set-connection').dataset.online`),'false');
 await visibleToast(p);writeFileSync(`${out}/web-${lang}-${theme}-${width}-offline.png`,await shoot(p,''));
 fake.setUp(true); await waitState(p,'ready');
 await waitFor(p,`document.getElementById('rs-reset').dataset.state==='ok'`);
 assert.match(await evaluate(p,`document.getElementById('rs-reset-body').textContent`),lang==='zh'?/已恢复连接/:/Connection restored/);
 assert.match(await evaluate(p,`document.getElementById('toast').textContent`),lang==='zh'?/已恢复连接/:/Connection restored/);
 const rect=await evaluate(p,`(()=>{const r=document.getElementById('set-connection').getBoundingClientRect();return {top:r.top,bottom:r.bottom}})()`);
 assert.ok(rect.top>=0 && rect.bottom<100);
 assert.equal(await evaluate(p,`document.documentElement.dataset.theme`),theme);
 assert.equal(await evaluate(p,`document.getElementById('set-connection').textContent`),lang==='zh'?'已恢复连接':'Connection restored');
 await visibleToast(p);writeFileSync(`${out}/web-${lang}-${theme}-${width}-restored.png`,await shoot(p,''));
 await evaluate(p,`import('./js/settings.js').then(s=>s.close())`);
 // Real owner tap signs and sends through the active Noise session, without a password fixture.
 const tapId='d'.repeat(32);
 await evaluate(p,`Promise.all([import('./js/elevate.js'),import('./js/api.js')]).then(async([e,a])=>e.add({id:'${tapId}',kind:'sudo',n:'${'b'.repeat(32)}',epk:'${'A'.repeat(43)}',ttl:10,cmd:'sleep 90',why:'Check connection',effect:'',helper:[await a.myDeviceId()]}))`);
 await evaluate(p,`document.getElementById('elev-allow').click()`);
 await waitFor(p,`document.getElementById('elev-allow').textContent===${JSON.stringify(lang==='zh'?'执行中…':'Executing…')}`);
 await evaluate(p,`import('./js/elevate.js').then(e=>{e.refused({id:'${tapId}'});})`);
 assert.equal(await evaluate(p,`document.getElementById('elev-allow').disabled`),false);
 await evaluate(p,`import('./js/elevate.js').then(e=>e.done({id:'${tapId}',result:'failed'}))`);
 assert.equal(await evaluate(p,`document.getElementById('elev').hidden`),true);
 const id='a'.repeat(32);
 await evaluate(p,`import('./js/elevate.js').then(e=>e.add({id:'${id}',kind:'sudo',n:null,epk:'${'A'.repeat(43)}',ttl:1,cmd:'sleep 90',why:'Check connection',effect:'',state:'running'}))`);
 assert.equal(await evaluate(p,`document.getElementById('elev-left').textContent`),lang==='zh'?'执行中…':'Executing…');
 await sleep(1100);
 assert.equal(await evaluate(p,`document.getElementById('elev-left').textContent`),lang==='zh'?'执行中…':'Executing…');
 await evaluate(p,`import('./js/elevate.js').then(e=>e.done({id:'${id}',result:'done',code:0}))`);
 assert.equal(await evaluate(p,`document.getElementById('elev').hidden`),true);
 // Lost receipt: an authoritative absence closes running cards without inventing success.
 await evaluate(p,`import('./js/elevate.js').then(e=>{e.add({id:'${'c'.repeat(32)}',kind:'sudo',n:null,epk:'${'A'.repeat(43)}',cmd:'sleep 90',why:'Check connection',effect:'',state:'running'});e.prune(()=>true)})`);
 assert.equal(await evaluate(p,`document.getElementById('elev').hidden`),true);
 await evaluate(p,`import('./js/elevate.js').then(e=>e.add({id:'${id}',kind:'sudo',n:null,epk:'${'A'.repeat(43)}',cmd:'sleep 90',why:'Check connection',state:'running'}))`);
 assert.equal(await evaluate(p,`document.getElementById('elev').hidden`),true);
 // Historical terminal cache is deduplicated quietly on a cold remote.
 const toastBefore=await evaluate(p,`document.getElementById('toast').textContent`);
 await evaluate(p,`import('./js/elevate.js').then(e=>e.done({id:'${'f'.repeat(32)}',result:'done',code:0}))`);
 assert.equal(await evaluate(p,`document.getElementById('toast').textContent`),toastBefore);
 if(count===0){
   fake.st.cards=true;
   await evaluate(p,`import('./js/session.js').then(s=>{s.peer.cards1=true})`);
   const recoveryId='e'.repeat(32);
   await evaluate(p,`import('./js/elevate.js').then(e=>e.add({id:'${recoveryId}',kind:'sudo',n:null,epk:'${'A'.repeat(43)}',cmd:'sleep 90',why:'Check connection',state:'running'}))`);
   await waitFor(p,`document.getElementById('elev').hidden`,20000);
   assert.ok(fake.st.cardGets.some(x=>x.sigOk && x.have.includes(recoveryId)),'running timer sends signed cards_get and closes from authoritative absence');
 }
 count++;
 }finally{await p.close();}
}
writeFileSync(`${out}/web-result.json`,JSON.stringify({pass:true,combos:count,screenshots:16},null,2));
console.log('PASS P392 phone',count,'combinations');
}finally{await B.close();await fake.stop();await web.stop();}
