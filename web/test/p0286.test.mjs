import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import * as split from '../public/js/turnsplit.js';
import {createUnread} from '../public/js/unread.js';
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const history=src.slice(src.indexOf('const hist ='),src.indexOf('function hhmm')).replaceAll('export function','function');
function setup(historyFn){
 const node={hidden:true,classList:{toggle(){}},textContent:''};
 const ctx=vm.createContext({...split,search:{onEpoch(){}},unread:createUnread(),hostId:()=> 'fixture',setTimeout,clearTimeout,
 C:{api:{history:historyFn},connected:()=>true,myDev:()=> 'fixture'},document:{body:{dataset:{}}},t:x=>x,showPage(){},announceTurn(){},el:()=>node,chanCard:()=>null,acceptHistoryHead(){},historyGeneration:0,historySync:false,wordsSrc:null});
 vm.runInContext(history,ctx);return js=>vm.runInContext(js,ctx);
}
const turn=(id,text,end='done')=>({id,source:{k:'phone',text:'input'},reply:text,end});
const frame=turns=>({epoch:1,turns,count:500,first_id:1});
const next=()=>new Promise(r=>setImmediate(r));
test('0286 newest read coalesces, gap/older share one in-flight owner, live reply beats an older snapshot',async()=>{
 let active=0,max=0,resolve,calls=0;
 const run=setup(()=>{calls++;active++;max=Math.max(max,active);return new Promise(r=>{resolve=x=>{active--;r(x);};});});
 run('hist.epoch=1;hist.firstId=1;hist.lastId=500;hist.turns=[{id:500,reply:"old",source:{k:"phone",text:"input"},end:"open"}]');
 const a=run('refreshNewest()'),b=run('refreshNewest()');assert.equal(a,b);
 await next();run('upsertTurn({id:500,reply:"fresh live reply",source:{k:"phone",text:"input"},end:"done"})');
 resolve(frame([turn(500,'stale')]));await a;
 assert.equal(run('hist.turns[0].reply'),'fresh live reply');assert.equal(run('historySync'),false);assert.equal(max,1);assert.equal(calls,1);
});
test('0286 slow gap snapshot cannot replace a newer live update to the same turn',async()=>{
 let resolve;const run=setup(()=>new Promise(r=>resolve=r));
 run('hist.epoch=1;hist.firstId=1;hist.lastId=500;hist.turns=[{id:500,reply:"before",source:{k:"phone",text:"input"},end:"open"}]');
 const pending=run('syncHistory(501)');await next();
 run('upsertTurn({id:500,reply:"newest live",source:{k:"phone",text:"input"},end:"done"})');
 resolve(frame([turn(500,'old snapshot'),turn(501,'next')]));await pending;
 assert.equal(run('hist.turns.find(x=>x.id===500).reply'),'newest live');
});
test('0286 obsolete generation discards delayed older pages and gap reads',async()=>{
 for(const op of ['loadOlderBatch()','syncHistory(501)']){
 let resolve;const run=setup(()=>new Promise(r=>resolve=r));
 run('hist.epoch=1;hist.firstId=1;hist.lastId=500;hist.turns=[{id:500,reply:"current",source:{k:"phone",text:"input"},end:"done"}]');
 const pending=run(op);await next();run('historyGeneration++;resetHistory(2)');resolve(frame([turn(499,'old generation')]));await pending;
 assert.equal(run('hist.epoch'),2);assert.equal(run('hist.turns.length'),0);
 }
});
test('0286 background data read owner serializes gap and history head',async()=>{
 let active=0,max=0;const resolutions=[];
 const run=setup(()=>{active++;max=Math.max(max,active);return new Promise(r=>resolutions.push(x=>{active--;r(x)}));});
 run('hist.epoch=1');const gap=run('syncHistory(500)');await next();const head=run('refreshNewest()');
 assert.equal(active,1);resolutions.shift()(frame([turn(500,'gap')]));await next();
 resolutions.shift()(frame([turn(500,'head')]));await Promise.all([gap,head]);assert.equal(max,1);
});
test('0286 retained history refresh reads the tail turn inclusively and catches up bounded pages without refetching 50 large replies',async()=>{
 const calls=[],turns=Array.from({length:121},(_,i)=>turn(500+i,'answer '+i));
 const run=setup(q=>{calls.push(q);const remaining=turns.filter(x=>x.id>q.after);return Promise.resolve({...frame(remaining.slice(0,50)),more:remaining.length>50});});
 run('hist.epoch=1;hist.firstId=1;hist.lastId=500;hist.turns=[{id:500,reply:"before",source:{k:"phone",text:"input"},end:"open"}]');
 await run('refreshNewest()');assert.equal(calls.length,3);assert.equal(calls[0].after,499);
 assert.equal(run('hist.lastId'),620);assert.equal(run('new Set(hist.turns.map(x=>x.id)).size'),121);assert.equal(run('historySync'),false);
});
test('0286 same device after host epoch reset discards its old cursor and requests a fresh head',async()=>{
 const calls=[];const run=setup(q=>{calls.push(q);return Promise.resolve({epoch:2,first_id:1,count:1,turns:q.after?[]:[turn(1,'new epoch')]});});
 run('hist.epoch=1;hist.firstId=1;hist.lastId=500;hist.turns=[{id:500,reply:"old",source:{k:"phone",text:"input"},end:"done"}]');
 await run('refreshNewest()');assert.equal(calls.length,2);assert.equal(calls[0].after,499);assert.equal(calls[1].after,undefined);
 assert.equal(run('hist.epoch'),2);assert.equal(run('hist.turns.length'),1);assert.equal(run('hist.turns[0].reply'),'new epoch');
});
test('0286 foreground catches a prior open turn completed behind newer notifications, not only the last ID',async()=>{
 const calls=[];const run=setup(q=>{calls.push(q);return Promise.resolve(frame([turn(498,'late completed reply'),turn(499,'notice 499'),turn(500,'notice 500')]));});
 run('hist.epoch=1;hist.firstId=1;hist.lastId=500;hist.turns=[{id:498,reply:"partial",source:{k:"phone",text:"input"},end:"open"},{id:499,reply:"notice 499",source:{k:"auto",text:"notice"},end:"done"},{id:500,reply:"notice 500",source:{k:"auto",text:"notice"},end:"done"}]');
 await run('refreshNewest()');assert.equal(calls[0].after,497);assert.equal(run('hist.turns[0].reply'),'late completed reply');assert.equal(run('hist.turns[0].end'),'done');
});
test('0286 first retained open turn ID 1 uses cursor zero instead of falling back to a tail-only page',async()=>{
 const calls=[];const run=setup(q=>{calls.push(q);return Promise.resolve(frame([turn(1,'first finished'),turn(500,'newest')]));});
 run('hist.epoch=1;hist.firstId=1;hist.lastId=500;hist.turns=[{id:1,reply:"partial",source:{k:"phone",text:"input"},end:"open"},{id:500,reply:"notice",source:{k:"auto",text:"notice"},end:"done"}]');
 await run('refreshNewest()');assert.equal(calls[0].after,0);assert.equal(run('hist.turns[0].reply'),'first finished');
});
test('0286 stale status metadata cannot clear newer head data or reduce its count',async()=>{
 const run=setup(()=>Promise.reject(new Error('no reads expected')));
 run('hist.epoch=2;hist.firstId=1;hist.lastId=504;hist.count=504;hist.turns=[{id:504,reply:"new head",source:{k:"phone",text:"input"},end:"done"}]');
 run('noteHistory({history:{epoch:2,last_id:502,count:502}})');assert.equal(run('hist.lastId'),504);assert.equal(run('hist.count'),504);
 run('noteHistory({history:{epoch:1,last_id:500,count:500}})');assert.equal(run('hist.epoch'),2);assert.equal(run('hist.turns[0].reply'),'new head');
});
test('0286 legacy channel projection suppresses only its exact raw fallback, preserves a human receipt with the same prefix',()=>{
 const raw='外部服务器投递的原文',channel={source:'mail',label:'来自邮件',text:raw};
 const run=setup(()=>Promise.resolve(frame([])));
 run(`chanCard=p=>p.card?.channel||null;globalThis.c=${JSON.stringify(channel)}`);
 assert.equal(run(`displayTurn({id:1,source:{k:'sys',text:''},card:{channel:c},reply:'来自邮件：我已经核查，FYI，无需处理。'}).reply`),'来自邮件：我已经核查，FYI，无需处理。');
 assert.equal(run(`displayTurn({id:1,source:{k:'sys',text:''},card:{channel:c},reply:'来自邮件：外部服务器投递的原文'}).reply`),'');
 assert.equal(run(`displayTurn({id:1,source:{k:'sys',text:'完整原文'.repeat(2000)},card:{channel:c},reply:'真实人话'}).source.text.length`),8000);
});
test('0286 metadata with a different epoch never resets before fresh head confirmation in either numeric direction',()=>{
 const snap=readFileSync(new URL('../public/js/snap.js',import.meta.url),'utf8');
 const code=snap.slice(snap.indexOf('export function setMeta(m)'),snap.indexOf('export function pushTurn(m)')).replaceAll('export function','function');
 const calls=[],hints=[],ctx=vm.createContext({S:{hist:{epoch:2,first:1,last:504,count:504}},onHistoryHint:()=>hints.push(1),onChange:reset=>calls.push(reset)});vm.runInContext(code,ctx);
 vm.runInContext('setMeta({epoch:1,first:0,last:0,count:0});setMeta({epoch:2,first:0,last:502,count:502})',ctx);
 assert.equal(ctx.S.hist.epoch,2);assert.equal(ctx.S.hist.last,504);assert.deepEqual(calls,[]);
 vm.runInContext('setMeta({epoch:3,first:0,last:0,count:0})',ctx);assert.equal(ctx.S.hist.epoch,2);assert.equal(ctx.S.hist.last,504);assert.deepEqual(calls,[]);assert.equal(hints.length,3);
});
test('0286 fresh current-generation head may restore a numerically lower epoch after history off/on',async()=>{
 const calls=[];const run=setup(q=>{calls.push(q);return Promise.resolve({epoch:0,first_id:1,count:1,turns:q.after?[]:[turn(1,'persisted history restored')]});});
 run('hist.epoch=1794093888;hist.firstId=0;hist.lastId=0;hist.turns=[]');
 try{await run('refreshNewest()');assert.equal(run('hist.epoch'),0);assert.equal(run('hist.turns[0]?.reply'),'persisted history restored');assert.equal(run('historySync'),false);}
 finally{run('historyGeneration++;if(historyRetry)clearTimeout(historyRetry)');}
});
test('0286 a lower-epoch response to a retained cursor is confirmed by a fresh head, not adopted as an empty history',async()=>{
 const calls=[];const run=setup(q=>{calls.push(q);return Promise.resolve({epoch:0,first_id:1,count:1,turns:Object.hasOwn(q,'after')?[]:[turn(1,'restored from disk')]});});
 run('hist.epoch=1794093888;hist.firstId=500;hist.lastId=500;hist.turns=[{id:500,reply:"old ephemeral",source:{k:"phone",text:"input"},end:"done"}]');
 try{await run('refreshNewest()');assert.equal(calls.length,2);assert.equal(calls[1].after,undefined);assert.equal(run('hist.epoch'),0);assert.equal(run('hist.turns[0]?.reply'),'restored from disk');}
 finally{run('historyGeneration++;if(historyRetry)clearTimeout(historyRetry)');}
});
test('0286 hint arriving behind an in-flight head forces one later read and cannot settle from the earlier response',async()=>{
 const pending=[],calls=[];const run=setup(q=>{calls.push(q);return new Promise(resolve=>pending.push(resolve));});
 run('hist.epoch=1794093888;hist.firstId=0;hist.lastId=0;hist.turns=[]');
 const old=run('refreshNewest()');await next();run('historyHint();historyHint();historyHint()');
 assert.equal(calls.length,1,'hints coalesce while the single owner is in flight');
 pending.shift()({epoch:1794093888,first_id:0,count:0,turns:[]});await old;await next();
 assert.equal(calls.length,2,'one read issued after all hints');assert.equal(run('historySync'),true);assert.equal(run('hist.epoch'),1794093888,'old response was not used');
 pending.shift()({epoch:0,first_id:1,count:1,turns:[turn(1,'restored after hint')]});await run('historyHead');
 assert.equal(run('hist.epoch'),0);assert.equal(run('hist.turns[0].reply'),'restored after hint');assert.equal(run('historySync'),false);
});
test('0286 stale lower metadata hint plus a fresh current high-epoch head never rolls back newer state',async()=>{
 const run=setup(()=>Promise.resolve({epoch:2,first_id:1,count:504,turns:[turn(504,'newer current head')]}));
 run('hist.epoch=2;hist.firstId=1;hist.lastId=504;hist.count=504;hist.turns=[{id:504,reply:"latest",source:{k:"phone",text:"input"},end:"done"}]');
 run('historyHint()');await run('historyHead');assert.equal(run('hist.epoch'),2);assert.equal(run('hist.lastId'),504);assert.equal(run('hist.turns[0].reply'),'newer current head');
});
test('0286 unsolicited epoch reset invalidates a pending older head before it can lower the confirmed view',async()=>{
 const pending=[];const run=setup(()=>new Promise(resolve=>pending.push(resolve)));
 run('hist.epoch=1;hist.lastId=0');const old=run('refreshNewest()');await next();
 run('historyReset(2)');pending.shift()({epoch:0,first_id:1,count:1,turns:[turn(1,'obsolete response')]});await old;await next();
 assert.equal(run('hist.epoch'),2);assert.equal(run('hist.turns.length'),0);
 pending.shift()({epoch:2,first_id:1,count:1,turns:[turn(1,'confirmed epoch two')]});await run('historyHead');assert.equal(run('hist.turns[0].reply'),'confirmed epoch two');
});
test('0286 stale numerically larger metadata after legitimate lower store adoption is only a hint and fresh lower head remains visible',async()=>{
 const run=setup(()=>Promise.resolve({epoch:0,first_id:1,count:1,turns:[turn(1,'persistent history stays')]}));
 run('hist.epoch=0;hist.firstId=1;hist.lastId=1;hist.count=1;hist.turns=[{id:1,reply:"persistent history stays",source:{k:"phone",text:"input"},end:"done"}]');
 run('historyHint()');await run('historyHead');assert.equal(run('hist.epoch'),0);assert.equal(run('hist.turns[0].reply'),'persistent history stays');
});
test('0286 continuous same-candidate epoch stream confirms without starvation and one shared followup obtains the final in-flight push',async()=>{
 const pending=[];let reads=0;const run=setup(()=>{reads++;return new Promise(r=>pending.push(r));});
 run('hist.epoch=1;hist.lastId=1;hist.turns=[{id:1,reply:"old",source:{k:"phone",text:"input"},end:"done"}]');
 const old=run('refreshNewest()');await next();run('historyHint({epoch:2,kind:"turn"})');pending.shift()(frame([turn(1,'old response')]));await old;await next();
 for(let i=0;i<20;i++)run('historyHint({epoch:2,kind:"turn"})');
 const candidate=run('historyHead');pending.shift()({epoch:2,first_id:1,count:1,turns:[turn(1,'partial new epoch','open')]});await next();pending.shift()({epoch:2,first_id:1,count:1,turns:[turn(1,'partial new epoch','open')]});await candidate;await next();
 assert.equal(run('hist.epoch'),2,'new store is confirmed while the stream is still active');assert.equal(run('historySync'),true);
 run('historyHint({epoch:2,kind:"turn"})');const final=run('historyHead');pending.shift()({epoch:2,first_id:1,count:1,turns:[turn(1,'last in-flight final body')]});await final;
 assert.equal(reads,4);assert.equal(run('hist.turns[0].reply'),'last in-flight final body');assert.equal(run('historySync'),false);
});
test('0286 p33 initial head waits for ready metadata, then dispatches once after its hint',async()=>{
 const pending=[];let calls=0;const run=setup(()=>{calls++;return new Promise(r=>pending.push(r));});
 run('beginHistorySync(true)');await next();assert.equal(calls,0,'no pre-header head to discard');
 run('historyHint({epoch:2,kind:"meta"});historyMeta()');await next();assert.equal(calls,1);
 pending.shift()({epoch:2,first_id:1,count:1,turns:[turn(1,'initial body')]});await run('historyHead');
 assert.equal(calls,1);assert.equal(run('historySync'),false);assert.equal(run('historyRetry'),null);assert.equal(run('hist.turns[0].reply'),'initial body');
});
test('0286 delta reads encountering another opaque store only request authoritative head, never merge by epoch numeric order',async()=>{
 for(const epoch of [0,4]){
 const pending=[];const run=setup(()=>new Promise(r=>pending.push(r)));
 run('hist.epoch=2;hist.firstId=1;hist.lastId=1;hist.turns=[{id:1,reply:"current",source:{k:"phone",text:"input"},end:"done"}]');const gap=run('syncHistory(2)');await next();
 pending.shift()({epoch,first_id:1,count:1,turns:[turn(1,'different store')]});await gap;await next();assert.equal(run('hist.epoch'),2,'gap response is not authority');
 pending.shift()({epoch:2,first_id:1,count:1,turns:[turn(1,'confirmed current')]});await run('historyHead');assert.equal(run('hist.epoch'),2);
 }
});
test('0286 missing initial metadata uses the existing single timer fallback, then clears it on a valid head',async()=>{
 let calls=0;const run=setup(()=>{calls++;return Promise.resolve(frame([turn(1,'fallback recovered')]));});
 run('beginHistorySync(true)');await new Promise(r=>setTimeout(r,280));
 assert.equal(calls,1);assert.equal(run('hist.turns[0].reply'),'fallback recovered');assert.equal(run('historySync'),false);assert.equal(run('historyRetry'),null);
});
test('0286 disconnect cancels the initial metadata fallback before it can issue a read',async()=>{
 let calls=0;const run=setup(()=>{calls++;return Promise.resolve(frame([]));});
 const lost=src.slice(src.indexOf('export function connLost(){'),src.indexOf('/** on: a ready session',src.indexOf('export function connLost(){'))).replace('export function','function');
 run('globalThis.outbox={lost(){}};globalThis.quietLost=false');run(lost);run('beginHistorySync(true);connLost()');
 await new Promise(r=>setTimeout(r,280));assert.equal(calls,0);assert.equal(run('historyRetry'),null);assert.equal(run('historyAwaitingMeta'),false);
});
test('0286 a retained confirmed store resumes immediately without waiting for bootstrap metadata',async()=>{
 let calls=0;const run=setup(()=>{calls++;return Promise.resolve(frame([turn(500,'current tail')]));});
 run('historyConfirmedStore=true;hist.epoch=1;hist.firstId=1;hist.lastId=500;hist.turns=[{id:500,reply:"tail",source:{k:"phone",text:"input"},end:"done"}]');
 run('beginHistorySync(true)');await next();assert.equal(calls,1);assert.equal(run('historyRetry'),null);assert.equal(run('historyAwaitingMeta'),false);
});
