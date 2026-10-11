// P132: every page has a header — pages without the owner's input (a check, a background task, the Agent reporting in)
// keep the source card's place and size, its page capsule, both corner double-taps and the new-message flash; on the newest
// page a new page takes the owner there, while paging history only the capsule flashes. Real page + local fake host.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const $ = id => `document.getElementById('${id}')`;
const pg = p => evaluate(p,`${$('pg')}.textContent`);
const box = (p,id) => evaluate(p,`(()=>{const r=${$(id)}.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})()`);
async function touch(p,id,count=1){
 const r=await evaluate(p,`(()=>{const r=${$(id)}.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 for(let i=0;i<count;i++){
  await p.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[r]});
  await p.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  if(i+1<count)await sleep(70);
 }
}
const L={
 zh:{ask:i=>`帮我看一下第 ${i} 件事。`,ans:i=>`第 ${i} 件事已经处理好了。`,loop:'定时检查：服务都正常，没有新的告警。',task:'后台任务跑完了：日志已整理到报告里。',
     report:'早上好，昨晚的三件事都做完了。',late:'又一条：备份也完成了。',labels:['定时检查','后台任务完成','Wren 主动汇报'],none:'这一页没有你的输入',flash:/有新消息/},
 en:{ask:i=>`Please check item ${i}.`,ans:i=>`Item ${i} is done.`,loop:'Scheduled check: all services are fine, no new alerts.',task:'The background task finished: logs are in the report.',
     report:'Good morning — all three things from last night are done.',late:'One more: the backup finished too.',labels:['Scheduled check','Background task finished','Wren reporting in'],none:'No message from you on this page',flash:/New message/},
};
export const CASES={a:'a) every page has a header card with page number and corner gestures',b:'b) on the newest page a new page takes the owner there',
 c:'c) paging history only flashes the capsule',d:'d) page numbers continuous across pages with and without input'};
export async function runP132({B,web,fake,out='reports/qa/p132',rec=parityRecorder('web/test/p132.mjs')}){
 mkdirSync(out,{recursive:true});
 const results={};
 const step=async(c,f)=>{try{await f();rec.record(CASES[c],true);}catch(e){rec.record(CASES[c],false);throw e;}};
 for(const lang of ['zh','en'])for(const theme of ['light','dark']){
  const s=L[lang];fake.reset();
  const p=await newPage(B,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
  const save=async name=>writeFileSync(resolve(out,`${lang}-${theme}-${name}.png`),await shoot(p,''));
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Wren'});
   const dev=await evaluate(p,`import('./js/api.js').then(api=>api.myDeviceId())`);
   let inputCard,seen;
   await step('d',async()=>{
   // d) mixed pages: two with the owner's input, then (a) three without — every page numbered, no gap
   for(let i=1;i<=2;i++)await fake.addTurn({k:'phone',dev,text:s.ask(i)},s.ans(i));
   await waitFor(p,`${$('pg')}.textContent==='2 / 2'`);
   inputCard=await box(p,'om');
   await fake.addTurn({k:'agent',text:'',via:'loop'},s.loop);
   await waitFor(p,`${$('pg')}.textContent==='3 / 3'`);           // b) on the newest page: the new page is shown
   await fake.addTurn({k:'agent',text:'',via:'task'},s.task);
   await waitFor(p,`${$('pg')}.textContent==='4 / 4'`);
   await fake.addTurn({k:'agent',text:''},s.report);
   await waitFor(p,`${$('pg')}.textContent==='5 / 5'`);
   await sleep(150);await save('header-newest');
   seen=[];
   for(let n=5;n>=1;n--){
    if(n<5){await evaluate(p,`${$('pgPrev')}.click()`);await waitFor(p,`${$('pg')}.textContent==='${n} / 5'`);}
    seen.push(await evaluate(p,`({pg:${$('pg')}.textContent,hidden:${$('om')}.hidden,header:${$('om')}.dataset.header,label:${$('omLabel')}.textContent,text:${$('omText')}.textContent,more:getComputedStyle(${$('omMore')}).visibility})`));
   }
   assert.deepEqual(seen.map(x=>x.pg),['5 / 5','4 / 5','3 / 5','2 / 5','1 / 5'],'page numbers are continuous across pages with and without input');
   for(const x of seen)assert.equal(x.hidden,false,'every page has its header card: '+x.pg);
   assert.deepEqual(seen.slice(0,3).map(x=>x.label),[...s.labels].reverse(),'source labels say where each page came from');
   for(const x of seen.slice(0,3)){assert.equal(x.header,'1');assert.equal(x.text,s.none);assert.equal(x.more,'hidden');}
   for(const x of seen.slice(3)){assert.equal(x.header,'0');assert.equal(x.more,'visible');}
   });
   await step('a',async()=>{
   // a) the header card is the input card's place and size; both corner gestures work on it
   for(const n of [2,3]){await evaluate(p,`${$('pgNext')}.click()`);await waitFor(p,`${$('pg')}.textContent==='${n} / 5'`);}
   assert.deepEqual(await box(p,'om'),inputCard,'header card = same place and size as an input card');
   for(const id of ['omFirst','pgLatest']){const b=await box(p,id);assert.ok(b.w>=44&&b.h>=44,id+' keeps a 44px target');}
   await touch(p,'omFirst',2);await waitFor(p,`${$('pg')}.textContent==='1 / 5'`);
   for(const n of [2,3]){await evaluate(p,`${$('pgNext')}.click()`);await waitFor(p,`${$('pg')}.textContent==='${n} / 5'`);}
   await touch(p,'pgLatest',2);await waitFor(p,`${$('pg')}.textContent==='5 / 5'`);
   await touch(p,'omFirst',2);await waitFor(p,`${$('pg')}.textContent==='1 / 5'`);
   for(const n of [2,3,4]){await evaluate(p,`${$('pgNext')}.click()`);await waitFor(p,`${$('pg')}.textContent==='${n} / 5'`);}
   });
   await step('c',async()=>{
   // c) paging history: a new page without input does not move him; the capsule flashes on a header page
   await fake.addTurn({k:'agent',text:'',via:'loop'},s.late);
   await waitFor(p,`${$('pgCorner')}.dataset.unread==='1' && ${$('pg')}.textContent==='4 / 6'`);
   assert.equal(await evaluate(p,`${$('om')}.dataset.header`),'1');
   assert.match(await evaluate(p,`${$('pgLatest')}.getAttribute('aria-label')`),s.flash);
   assert.equal(await evaluate(p,`getComputedStyle(${$('pg')}).animationName`),'new-message-pulse');
   assert.equal(await evaluate(p,`getComputedStyle(${$('pg')}).borderTopColor`),'rgba(250, 249, 245, 0.45)','P150: the flashing capsule ring is a light Lotion veil, not near-black');
   await sleep(150);await save('header-unread');
   await touch(p,'pgLatest');                                       // a single tap on the flashing capsule = to the newest
   await waitFor(p,`${$('pg')}.textContent==='6 / 6' && ${$('pgCorner')}.dataset.unread==='0'`);
   assert.equal(await evaluate(p,`${$('words')}.textContent.trim()`),s.late);
   });
   await step('b',async()=>{
   // b) on the newest page with a long reply scrolled half way: the next page still takes him there (P87 kept him behind)
   await fake.addTurn({k:'agent',text:''},Array.from({length:60},(_,i)=>`${s.report} ${i+1}`).join('\n\n'));
   await waitFor(p,`${$('pg')}.textContent==='7 / 7'`);
   await evaluate(p,`(()=>{const m=document.querySelector('#rm main');m.scrollTop=Math.floor((m.scrollHeight-m.clientHeight)/2);m.dispatchEvent(new Event('scroll'))})()`);
   await sleep(100);
   assert.ok(await evaluate(p,`(()=>{const m=document.querySelector('#rm main');return m.scrollHeight-m.scrollTop-m.clientHeight>100})()`),'mid-way through the reply');
   await fake.addTurn({k:'agent',text:'',via:'task'},s.task);
   await waitFor(p,`${$('pg')}.textContent==='8 / 8' && ${$('pgCorner')}.dataset.unread==='0'`);
   // b) a pending desktop input stays behind the reply, and its reply, when it lands, takes him there (no stuck page)
   const t=await fake.addTurn({k:'host',name:'电脑',text:s.ask(9)},'','open');
   await sleep(300);
   assert.equal(await pg(p),'8 / 9','pending input does not cover the reply');
   await fake.updateTurn(t.id,{reply:{text:s.ans(9)},end:'done'});
   await waitFor(p,`${$('pg')}.textContent==='9 / 9' && ${$('pgCorner')}.dataset.unread==='0'`);
   assert.equal(await evaluate(p,`${$('omLabel')}.textContent`),lang==='zh'?'电脑上的输入':'Typed on the computer');
   // silent turns stay silent: no page, no flash, no move
   await fake.addTurn({k:'agent',text:'',via:'loop'},'〔不回群〕');await sleep(300);
   assert.equal(await pg(p),'9 / 9');assert.equal(await evaluate(p,`${$('pgCorner')}.dataset.unread`),'0');
   });
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
   results[lang+'-'+theme]={result:'pass',pages:seen};console.log('PASS P132 '+lang+'-'+theme);
  }finally{await p.dispose();}
 }
 writeFileSync(resolve(out,'results.json'),JSON.stringify(results,null,2)+'\n');
 console.log('parity results → '+rec.write());
 return results;
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
 const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
 try{await runP132({B,web,fake});}finally{await B.close();await fake.stop();await web.stop();}
}
