import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitFor,waitState,shoot,sleep} from './browser.mjs';
const out=process.env.CONTROLS_OUT||'reports/qa/p0285-controls-redo';mkdirSync(out,{recursive:true});
const B=await launch(),fake=await startFakeHost(),rows=[];
const initial={enabled:true,available:true,busy:true,owner_waiting:true,waiting_s:35,elapsed_s:181,can_interrupt:true,turn:42,session:'a'.repeat(24)};
const combos=process.env.CONTROLS_MATRIX==='1'?[390,1440].flatMap(width=>['zh','en'].flatMap(lang=>['light','dark'].map(theme=>({width,lang,theme})))):[{width:390,lang:'zh',theme:'light'}];
try{for(const {width,lang,theme} of combos)for(const phase of ['before','after']){
 const web=await startWebServer(phase==='before'?{publicDir:process.env.CONTROLS_BEFORE||'/tmp/agentj-0285-master-before'}:{});
 fake.reset();fake.st.prefs={appearance:{theme,language:lang}};
 const calls=[];fake.st.onApp=async(c,m,send)=>{if(m.t==='main_thread_interrupt'){assert.ok(await fake.verifyControl(c,m.t,m,{request:m.request}));calls.push(m.request);await send({t:'main_thread_interrupt_res',r:m.r,ok:true});return true;}return false;};
 const p=await newPage(B,width,844,theme,{allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/settings.js').then(m=>m.applyThemeLocal('${theme}'))`);
 await fake.addTurn({k:'phone',dev:fake.devs[0].id,text:'查看工作目录'},'可以查看当前目录：\n\n```sh\npwd\n```','done');
 await fake.send({t:'busy',tasks:[{id:'synthetic-worker',who:'前端工程师',label:'界面检查',c:1}],task_count:1});await fake.send({t:'status',s:'working',main_thread:initial});await sleep(600);
 await waitFor(p,`document.querySelector('#words .codeblock')`);
 const layout=await evaluate(p,`(()=>{const r=id=>{const e=document.getElementById(id),q=e?.getBoundingClientRect();return q?{x:q.x,y:q.y,w:q.width,h:q.height,visible:getComputedStyle(e).visibility!=='hidden'&&q.width>0}:null};return{report:r('webReportBtn'),stop:r('main-thread-interrupt'),header:r('meta'),plus:r('cardPlus'),label:!!document.getElementById('main-thread-label'),banner:!!document.getElementById('main-thread-status'),runs:[...document.querySelectorAll('#words .runbtn')].map(e=>({text:e.textContent,aria:e.getAttribute('aria-label'),path:e.querySelector('path')?.getAttribute('d'),fill:e.querySelector('svg')&&getComputedStyle(e.querySelector('svg')).fill,rect:{w:e.offsetWidth,h:e.offsetHeight}})),overflow:document.documentElement.scrollWidth>innerWidth}})()`);
 writeFileSync(`${out}/${phase}-${width}-${lang}-${theme}.png`,await shoot(p));
 if(phase==='after'){
 assert.equal(layout.banner,false);assert.equal(layout.label,false);assert.equal(layout.report.visible,false);assert.deepEqual({...layout.stop,visible:true},{...layout.report,visible:true});assert.equal(layout.overflow,false);for(const r of layout.runs){assert.equal(r.text,'');assert.equal(r.path,'m8 5 11 7-11 7Z');assert.equal(r.fill,'none');assert.ok(r.aria&&r.rect.w>=44&&r.rect.h>=44);}
 for(const delta of [{waiting_s:0},{can_interrupt:false},{available:false},{owner_waiting:false},{busy:false}]){await fake.send({t:'status',s:'working',main_thread:{...initial,...delta}});await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('webReportBtn')).visibility`),'visible');}
 await fake.send({t:'status',s:'working',main_thread:initial});await waitFor(p,`!document.getElementById('main-thread-interrupt').hidden`);await evaluate(p,`document.getElementById('main-thread-interrupt').click()`);await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);assert.deepEqual(calls,[{turn:42,session:initial.session}]);
 }
 rows.push({phase,width,lang,theme,layout,calls});console.log('PASS',phase,JSON.stringify(layout));
 }finally{await p.dispose();await web.stop();}
}}finally{writeFileSync(out+'/result.json',JSON.stringify(rows,null,2));await B.close();await fake.stop();}
