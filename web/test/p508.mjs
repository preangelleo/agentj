// P508b: real page, isolated fake host and Chromium; bilingual 390/1440 provenance and removal.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/p508/screens/',import.meta.url));mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const rec=parityRecorder('web/test/p508.mjs');
const countFixtures=JSON.parse(readFileSync(new URL('../../host/tests/fixtures/p508-counts.json',import.meta.url),'utf8'));
let checks=0;const check=(ok,message)=>{assert.ok(ok,message);checks++;};
try{
 for(const lang of ['zh','en'])for(const width of [390,1440]){
  fake.reset();fake.st.busyget=true;
  const p=await newPage(B,width,900,'light',{touch:width===390,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
   const now=Math.floor(Date.now()/1000);
   const tasks=[{id:'parent',who:'Relay',kind:'ceo',label:lang==='zh'?'核对本次发布的完整任务标题与回归证据':'Review complete release title and regression evidence',since:now-754,c:0,
    detected:{harness:'codex',model:'gpt-test',effort:'xhigh',account:'acct:1234abcd',subscription:'chatgpt'},reported:{harness:'claude',model:'claimed'},conflict:true},
    {id:'child-one',who:'reviewer',kind:'temp',label:lang==='zh'?'同进程内的第一个子任务':'First child sharing the process',since:now-180,c:1,
     detected:{harness:'codex',model:'child-model',account:'acct:1234abcd'}},
    {id:'child-two',who:null,kind:'temp',label:lang==='zh'?'同进程内的第二个子任务':'Second child sharing the process',since:now-120,c:2,
     detected:{harness:'codex',model:null,account:null}}];
   await fake.send({t:'busy',by:'Agent J',tasks,task_count:3,live_process_count:1});
   await waitFor(p,`document.getElementById('busyDots').dataset.n==='3'`);
   await evaluate(p,`document.getElementById('busyDots').click()`);await waitFor(p,`document.querySelectorAll('#busyList li').length===3`);
   const text=await evaluate(p,`document.getElementById('busyList').textContent`);
   for(const value of ['gpt-test',lang==='zh'?'超高档':'Extra high','child-model','claimed',lang==='zh'?'自报与实际不一致':'Reported details differ'])check(text.includes(value),'visible '+value);
   check((await evaluate(p,`document.getElementById('busySub').textContent`)).includes(lang==='zh'?'存活进程 1 个':'Live processes: 1'),'independent process count');
   check(await evaluate(p,`[...document.querySelectorAll('#busyList li>span')].every(e=>e.textContent.length>10&&e.getBoundingClientRect().width>60)`),'titles retain a column');
   check(await evaluate(p,`[...document.querySelectorAll('#busyList .busy-identity')].every(e=>e.getBoundingClientRect().right<=innerWidth&&getComputedStyle(e).backgroundColor==='rgba(0, 0, 0, 0)')`),'secondary provenance text fits viewport');
   check(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth`),'no horizontal overflow');
   writeFileSync(out+lang+'-'+width+'.png',await shoot(p,''));
   await fake.send({t:'busy',by:'Agent J',tasks:tasks.slice(1),task_count:2,live_process_count:1});
   await waitFor(p,`document.querySelectorAll('#busyList li').length===2`);
   check(!(await evaluate(p,`document.getElementById('busyList').textContent`)).includes('claimed'),'lost source card removed');
   const unknownTask={...tasks[2],status:'unknown'};
   await fake.send({t:'busy',by:'Agent J',tasks:[unknownTask],task_count:0,live_process_count:1});
   await waitFor(p,`document.getElementById('busyDots').dataset.n==='0'`);
   await waitFor(p,`document.getElementById('busyList').textContent.includes(${JSON.stringify(lang==='zh'?'状态未知':'Status unknown')})`);
   check((await evaluate(p,`document.getElementById('busyTitle').textContent`)).includes('0'),'unknown is not counted as running');
   writeFileSync(out+lang+'-'+width+'-unknown.png',await shoot(p,''));
   for(const fixture of countFixtures){
    const rows=fixture.statuses.map((status,i)=>({...tasks[i],status,live_pids:fixture.pids[i]}));
    const counts=Object.fromEntries(['task_count','unknown_count','live_process_count'].map(k=>[k,fixture[k]]));
    await fake.send({t:'busy',by:'Agent J',tasks:rows,...counts});
    await waitFor(p,`document.getElementById('busyDots').dataset.n===${JSON.stringify(String(counts.task_count))}`);
    // Read the same counters consumed by the visible title and process subtitle.
    await waitFor(p,`import('./js/busy.js').then(m=>JSON.stringify(m.currentCounts())===${JSON.stringify(JSON.stringify(counts))})`);
    const actual=await evaluate(p,`import('./js/busy.js').then(m=>m.currentCounts())`);
    check(JSON.stringify(actual)===JSON.stringify(counts),'shared host/CLI/phone count fixture '+JSON.stringify(counts));
   }
   // Native child state and row presentation may differ; the host owns the total.
   await fake.send({t:'busy',tasks:[{...tasks[0],status:'unknown'}],task_count:7,unknown_count:2,live_process_count:3});
   await waitFor(p,`document.getElementById('busyDots').dataset.n==='7'`);
   check(true,'phone consumes host totals rather than recounting rows');
   await fake.send({t:'busy',by:'Agent J',tasks:[],task_count:0,live_process_count:0});
   await waitFor(p,`document.getElementById('busyDlg').hidden`);check(true,'last source closes dialog');
   writeFileSync(out+lang+'-'+width+'-removed.png',await shoot(p,''));
   check(p.offsite.length===0,'no offsite requests');
  }finally{await p.dispose();}
 }
 rec.record('verified-provenance-bilingual',true);
 console.log('P508 phone PASS '+checks+' checks; 390/1440 × zh/en');
}finally{await B.close();await fake.stop();await web.stop();rec.write();}
