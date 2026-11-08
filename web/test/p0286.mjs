#!/usr/bin/env node
// 0.28.6: real browser + existing Noise fixture; no production pairing, approvals or network.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {performance} from 'node:perf_hooks';
import {createServer} from 'node:http';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=resolve(process.env.P0286_OUT||'reports/qa/0286/web'),before=process.env.P0286_BEFORE,only=process.env.P0286_ONLY;
mkdirSync(out,{recursive:true});
const web=await startWebServer(before?{publicDir:resolve(before)}:{}),fake=await startFakeHost(),B=await launch();
let canaryHits=0;const canary=createServer((req,res)=>{canaryHits++;res.end('canary')});await new Promise(r=>canary.listen(0,'127.0.0.1',r));const canaryUrl=`http://127.0.0.1:${canary.address().port}/`;
const results=[],p95=xs=>[...xs].sort((a,b)=>a-b)[Math.ceil(xs.length*.95)-1];
const raw='备份任务 daily-backup：status=success\n3 个目录已备份。报告：reports/backup.md';
const answer='备份已完成，3 个目录都已保存。FYI，无需处理。';
async function start(width=390,theme='light',lang='zh'){
 fake.reset();fake.st.prefSet=true;fake.st.prefs={appearance:{language:lang,theme:'system'},voice:{speak_replies:false}};
 const p=await newPage(B,width,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
 await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`window.__historyCalls=[];window.__long=[];new PerformanceObserver(l=>window.__long.push(...l.getEntries().map(x=>x.duration))).observe({type:'longtask',buffered:true});`});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
 return p;
}
const get=(p,id)=>evaluate(p,`document.getElementById(${JSON.stringify(id)}).textContent`);
const snap=async(p,name)=>writeFileSync(join(out,name+'.png'),await shoot(p,''));
try{
 if(!['load','cold'].includes(only))for(const width of [390,1440])for(const theme of ['light','dark']){
  const p=await start(width,theme);const tag=`${width}-zh-${theme}`;
  try{
   // The same wire fixture and page state are used for BEFORE and AFTER.
   const sources=[['Webhook',{k:'agent',via:'hook',name:'自动化报告'}],['API',{k:'agent',via:'api',name:'报告接口'}],['automation',{k:'agent',via:'auto',name:'备份'}],['mail',{k:'email',name:'主人'}],['wechat',{k:'wechat',name:'微信'}],['telegram',{k:'telegram',name:'Telegram'}],['heartbeat',{k:'agent',via:'heartbeat',name:'心跳'}],['system',{k:'sys',name:'系统提示'}]];
   for(const [kind,src] of sources){
    const inputTurn=await fake.addTurn({...src,text:raw},answer);await fake.updateTurn(inputTurn.id,{ts:1762540200000});
    await waitFor(p,`document.getElementById('words').textContent.includes('FYI')`);
    assert.equal(await get(p,'omText'),raw);
    assert.ok((await get(p,'omTime')).trim());
    assert.equal(await evaluate(p,`document.getElementById('om').classList.contains('open')`),false);
    assert.equal(await evaluate(p,`document.getElementById('om').dataset.header`),'0');
    assert.ok(!(await get(p,'words')).includes('status=success'),'external body never renders as an Agent response');
    if(!before&&kind==='Webhook')assert.match(await get(p,'omLabel'),/自动化报告/);
    await snap(p,`${before?'before':'after'}-${tag}-${kind}-folded`);
    await evaluate(p,`document.getElementById('omMore').click()`);
    assert.ok(await evaluate(p,`document.getElementById('om').classList.contains('open')`));
    assert.equal(await get(p,'omText'),raw);
    await snap(p,`${before?'before':'after'}-${tag}-${kind}-expanded`);
   }
   if(!before){
    await fake.addTurn({k:'sys',text:''},'来自邮件：原始报错内容','done',{channel:{source:'mail',label:'来自邮件',text:'原始报错内容'}});
    await waitFor(p,`document.getElementById('pg').textContent.includes('/ 9')`);await evaluate(p,"document.getElementById('pgNext').click()");await waitFor(p,`document.getElementById('omText').textContent==='原始报错内容'`);
    assert.ok(!(await get(p,'words')).includes('原始报错内容'),'legacy channel fallback stays input');
    await fake.addTurn({k:'agent',via:'hook',text:'/update [执行](reply:派)'},'/update [执行](reply:派)');
    await waitFor(p,`document.getElementById('omText').textContent.includes('/update')`);
    assert.equal(await evaluate(p,`document.getElementById('words').querySelectorAll('button,a').length`),0,'exact external echo has no executable links');
    assert.equal(await evaluate(p,'document.documentElement.scrollWidth>innerWidth'),false);
   }
   results.push({kind:'source-cards',tag,sources:sources.length,result:'pass'});
  }finally{await p.dispose();}
 }
 if(!before&&only!=='cards'){
  const p=await start();
  try{
   const base=Date.now();fake.st.epoch++;
   fake.st.turns=Array.from({length:500},(_,i)=>({id:i+1,ts:base,src:{k:'phone',text:`第${i+1}轮：检查任务。`},reply:{text:`第${i+1}轮已完成。\n\n`+'普通负载消息。'.repeat(64)},end:'done'}));fake.st.nextId=501;
   const cold=[];
   if(process.env.P0286_TRACE==='1'){await p.send('Debugger.enable');const source=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8'),line=source.split('\n').findIndex(x=>x.includes('const task = historyReadTail.then'));await p.send('Debugger.setBreakpointByUrl',{urlRegex:'/js/relay\\.js',lineNumber:line,condition:"(window.__historyCalls.push({query,epoch:hist.epoch,last:hist.lastId,candidate:historyCandidate,stack:new Error().stack}),false)"});}
   for(let n=0;n<Number(process.env.P0286_COLD_RUNS||5);n++){
    let bytes=0,requests=0,wsBytes=0;const historyRequests=[];let sampleAt=performance.now();fake.st.onApp=async(c,m)=>{if(m.t==='hist_get')historyRequests.push({at:performance.now()-sampleAt,after:m.after,before:m.before,limit:m.limit});return false;};
    const listener=m=>{if(m.method==='Network.loadingFinished')bytes+=m.params.encodedDataLength||0;if(m.method==='Network.requestWillBeSent')requests++;if(m.method==='Network.webSocketFrameReceived')wsBytes+=Buffer.from(m.params.response.payloadData,m.params.response.opcode===2?'base64':'utf8').length;};
    p.on(listener);await p.send('Network.setCacheDisabled',{cacheDisabled:true});const at=performance.now();sampleAt=at;
    await navigate(p,web.url+'?lang=zh');await waitState(p,'ready');await waitFor(p,`document.body.dataset.sync==='ready'&&document.getElementById('words').textContent.includes('第500轮')`);
    cold.push({ms:performance.now()-at,bytes,requests,wsBytes,historyRequests,trace:process.env.P0286_TRACE==='1'?await evaluate(p,'window.__historyCalls'):undefined,dom:await evaluate(p,'document.getElementsByTagName("*").length'),longTasks:await evaluate(p,'window.__long')});
   }
   writeFileSync(join(out,'cold-measurements.json'),JSON.stringify({cold,p95Ms:p95(cold.map(x=>x.ms))},null,2));assert.ok(p95(cold.map(x=>x.ms))<=2000,'controlled loopback cold p95 <=2s');
   writeFileSync(join(out,'cold-measurements.json'),JSON.stringify({cold,p95Ms:p95(cold.map(x=>x.ms))},null,2));
   if(only==='cold')results.push({kind:'cold-budget',cold,p95Ms:p95(cold.map(x=>x.ms))});
   else {
   const loaded=await evaluate(p,`import('./js/relay.js').then(m=>m.debug().hist.length)`);assert.equal(loaded,50,'500 rounds load only 50');
   // An exceptionally long single paragraph is kept whole rather than advertised as a 2MiB hard bound.
   const longText='超长正文。'.repeat(12000)+'尾部完整';let at=performance.now();
   const lt=await fake.addTurn({k:'phone',text:'核对超长单轮'},longText);
   await waitFor(p,`document.getElementById('words').textContent.includes('尾部完整')`,15000);
   const long={ms:performance.now()-at,utf8:Buffer.byteLength(longText),dom:await evaluate(p,'document.getElementsByTagName("*").length'),longTasks:await evaluate(p,'window.__long')};
   // High-frequency growing Agent output, 200 encrypted updates, never tool output reclassified as a reply.
   let text='';const stream=await fake.addTurn({k:'phone',text:'连续输出验收'},'','open');at=performance.now();
   const paints=await evaluate(p,`(()=>{window.__paints=0;window.__mut=new MutationObserver(()=>window.__paints++);window.__mut.observe(document.getElementById('words'),{childList:true,subtree:true,characterData:true});return 0;})()`);
   for(let i=0;i<200;i++){text+=`片段${i} 完整。\n`;await fake.updateTurn(stream.id,{reply:{text},end:'open'});}
   await fake.updateTurn(stream.id,{reply:{text:text+'最终正文完整'},end:'done'});
   await waitFor(p,`document.getElementById('words').textContent.includes('最终正文完整')`,10000);
   assert.equal((await get(p,'words')).replaceAll('\n','').trim(),(text+'最终正文完整').replaceAll('\n','').trim());assert.equal(await evaluate(p,`import('./js/relay.js').then(m=>m.loadedTurns().find(x=>x.id===${stream.id}).reply)`),text+'最终正文完整','raw final reply byte-exact');
   const streaming={ms:performance.now()-at,chunks:200,utf8:Buffer.byteLength(text),paints:await evaluate(p,'window.__paints'),dom:await evaluate(p,'document.getElementsByTagName("*").length')};
   await evaluate(p,'window.__mut.disconnect()');
   // Foreground after five minutes uses a simulated visibility/clock change, NOT a physical phone lock.
   writeFileSync(join(out,'load-partial.json'),JSON.stringify({cold,long,streaming},null,2));
   const recovery=[];writeFileSync(join(out,'recovery-partial.json'),'[]');
   for(const scenario of ['five-minute-background-simulated','network-change-simulated','host-restart-simulated','half-open-data-delay-simulated']){
    const id=fake.st.nextId++;fake.st.turns.push({id,ts:Date.now(),src:{k:'agent',via:'hook',name:'恢复验收',text:scenario},reply:{text:`恢复完成 ${id}`},end:'done'});
    fake.st.historySnapshotDelay=150;const at=performance.now(),timeline=[];fake.st.onApp=async(c,m)=>{timeline.push({t:m.t,ms:performance.now()-at});return false;};
    if(scenario==='network-change-simulated'){fake.kick();await sleep(20);await evaluate(p,`window.dispatchEvent(new Event('online'))`);await waitState(p,'ready');}
    else if(scenario==='host-restart-simulated'){fake.swapHost();await waitState(p,'ready');}
    else await evaluate(p,`(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));window.__backgroundSimulatedMs=300000;Object.defineProperty(document,'hidden',{configurable:true,value:false});Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));})()`);
    await waitFor(p,`document.body.dataset.sync==='pending'`);assert.match(await get(p,'stale'),/已连接.*同步/);
    await waitFor(p,`document.body.dataset.sync==='ready'&&document.getElementById('words').textContent.includes('恢复完成 ${id}')`);
    const ms=performance.now()-at;recovery.push({scenario,ms,simulated:true,timeline});writeFileSync(join(out,'recovery-partial.json'),JSON.stringify(recovery,null,2));assert.ok(ms<=3000,`${scenario}: ${ms}ms <=3s`);fake.st.historySnapshotDelay=0;
   }
   await import('./p0286-preview.mjs');
   const preview=JSON.parse(readFileSync(resolve('reports/qa/0286/web/preview/results.json'),'utf8'));
   results.push({kind:'load-budget',condition:'Chromium,390x844,loopback Noise fixture,no CPU/network throttle; host is Node fixture, not production Host',cold,p95Ms:p95(cold.map(x=>x.ms)),loaded,long,streaming,recovery,preview});
   }
  }finally{await p.dispose();}
 }
 writeFileSync(join(out,before?'before-results.json':'results.json'),JSON.stringify({baseline:'45204cc0f6',mode:before?'before':'after',results},null,2)+'\n');
 console.log(JSON.stringify({mode:before?'before':'after',groups:results.length,result:'pass'}));
}finally{await B.close();await fake.stop();await web.stop();await new Promise(r=>canary.close(r));}
