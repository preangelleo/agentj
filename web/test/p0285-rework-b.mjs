// B track presentation only: actual paired page, eight viewport/language/theme combinations.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitFor,waitState,shoot} from './browser.mjs';
const out=process.env.P0285_B_OUT||'reports/qa/p0285-b-final/converged';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),rows=[];
const initial={enabled:true,available:true,busy:true,elapsed_s:900,owner_waiting:true,waiting_s:31,can_interrupt:true,turn:42,session:'a'.repeat(24)};
try{for(const width of [390,1440])for(const theme of ['light','dark'])for(const lang of ['zh','en']){
 fake.reset();fake.st.caps=['wf1','jcards1'];fake.st.prefs={appearance:{theme,language:lang}};let calls=0;
 fake.st.onApp=async(c,m,send)=>{if(m.t!=='main_thread_interrupt')return false;assert.ok(await fake.verifyControl(c,m.t,m,{request:m.request}));calls++;await send({t:'main_thread_interrupt_res',r:m.r,ok:true});return true;};
 const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  await fake.addTurn({k:'phone',text:'Synthetic owner question'},'```sh\nls -la\n```');
  await waitFor(p,`document.querySelector('#words .runbtn')`);
  const run=await evaluate(p,`(()=>{const b=document.querySelector('#words .runbtn'),r=b.getBoundingClientRect();return {text:b.textContent,path:b.querySelector('path')?.getAttribute('d'),w:r.width,h:r.height,gap:b.nextElementSibling.getBoundingClientRect().left-r.right,aria:b.getAttribute('aria-label')}})()`);
  assert.equal(run.text,'');assert.equal(run.path,'m8 5 11 7-11 7Z');assert.ok(run.w>=44&&run.h>=44&&run.aria);assert.ok(run.gap>=8);
  await fake.send({t:'status',s:'working',main_thread:{...initial,owner_waiting:false}});
  await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);
  await fake.send({t:'status',s:'working',main_thread:{...initial,waiting_s:0}});
  await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);
  for(const delta of [{busy:false},{available:false},{can_interrupt:false},{session:''},{turn:0}]){await fake.send({t:'status',s:'working',main_thread:initial});await waitFor(p,`!document.getElementById('main-thread-interrupt').hidden`);await fake.send({t:'status',s:'working',main_thread:{...initial,...delta}});await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('webReportBtn')).visibility`),'visible');}
  await fake.send({t:'status',s:'working',main_thread:initial});
  await waitFor(p,`!document.getElementById('main-thread-interrupt').hidden`);
  const stop=await evaluate(p,`(()=>{const b=document.getElementById('main-thread-interrupt'),r=b.getBoundingClientRect(),svg=b.querySelector('svg'),disc=b.querySelector('circle'),rect=b.querySelector('rect');return {w:r.width,h:r.height,text:b.textContent.trim(),disc:getComputedStyle(disc).fill,stop:getComputedStyle(rect).fill,parent:b.closest('#rmbar')?.id,report:getComputedStyle(document.getElementById('webReportBtn')).visibility,headerRows:!!document.getElementById('main-thread-label')||!!document.getElementById('main-thread-status')}})()`);
  assert.equal(stop.w,44);assert.equal(stop.h,44);assert.equal(stop.text,'');assert.equal(stop.disc,'rgb(250, 249, 245)');assert.equal(stop.stop,'rgb(44, 43, 38)');assert.equal(stop.parent,'rmbar');assert.equal(stop.report,'hidden');assert.equal(stop.headerRows,false);
  writeFileSync(`${out}/${width}-${lang}-${theme}-stop.png`,await shoot(p));
  await evaluate(p,`document.getElementById('main-thread-interrupt').click()`);await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);assert.equal(calls,1);
  await fake.send({t:'status',s:'idle'});await waitFor(p,`document.getElementById('main-thread-interrupt').hidden`);
  const plus=await evaluate(p,`(()=>{const b=document.getElementById('cardPlus'),s=b.querySelector('svg'),r=s.getBoundingClientRect(),path=s.querySelector('path').getBBox();return {hidden:b.hidden,w:r.width,h:r.height,pathWidth:path.width,pathHeight:path.height}})()`);
  assert.equal(plus.hidden,false);assert.ok(plus.w>=20&&plus.h>=20&&plus.pathWidth>0&&plus.pathHeight>0);
  assert.equal(await evaluate(p,`document.documentElement.scrollWidth>innerWidth`),false);
  rows.push({width,theme,lang,run,stop,plus});console.log('PASS B presentation',width,theme,lang);
 }finally{await p.dispose();}
}}finally{writeFileSync(out+'/regression.json',JSON.stringify({rows},null,2));await B.close();await fake.stop();await web.stop();}
