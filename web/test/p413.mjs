import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const OUT=fileURLToPath(new URL('../../../reports/qa/p413/',import.meta.url));mkdirSync(OUT,{recursive:true});
const ev=evaluate,q=JSON.stringify,click=(p,s)=>ev(p,`document.querySelector(${q(s)}).click()`);
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();let checks=0;
const check=(x,s)=>{assert.ok(x,s);checks++;};
try{
for(const width of [390,1440])for(const lang of ['zh','en'])for(const theme of ['light','dark']){
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 let events=[],revision=0,operations=[],conflict=false;
 const cards=[{id:'calendar',name:{zh:'日历',en:'Calendar'},desc:{zh:'共同管理日程',en:'Your shared schedule'},icon:'calendar',accent:'windstorm',official:true,on:true,plus:'agentj-calendar'}];
 fake.st.onApp=async(c,m,send)=>{
  const reply=async(t,v)=>{await send({t,r:m.r,...v});return true;};
  if(m.t==='jcard_list')return reply('jcard_items',{cards});
  if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:'calendar',template:'calendar',events,revision:String(revision),plus:'agentj-calendar',actions:[],anchors:[],links:[]});
  if(m.t==='jcard_cal'){
   operations.push(m);
   if(conflict){conflict=false;return reply(m.t,{ok:false,why:'conflict'});}
   if(m.revision!==String(revision))return reply(m.t,{ok:false,why:'conflict'});
   if(m.op==='add')events.push({...m.event,id:'event-'+revision,readonly:false,end:m.event.end||(m.event.all_day?m.event.start:'2026-10-20T10:00:00Z')});
   if(m.op==='edit')events=events.map(e=>e.id===m.entry?{...e,...m.event}:e);
   if(m.op==='delete')events=events.filter(e=>e.id!==m.entry);
   revision++;return reply(m.t,{ok:true});
  }
  return false;
 };
 const p=await newPage(B,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 const open=async()=>{await ev(p,"import('./js/five-pages.js').then(m=>m.openTarget('calendar'))");await waitFor(p,"document.querySelectorAll('.jc-cal-grid button').length===42");};
 const submit=()=>ev(p,"document.querySelector('.jc-cal-dialog form').requestSubmit()");
 const set=(name,value)=>ev(p,`document.querySelector('.jc-cal-dialog [name=${name}]').value=${q(value)}`);
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await fake.send({t:'status',s:'idle',agent:'codex',name:'Agent J'});await ev(p,"document.getElementById('phone-env-dismiss')?.click()");await sleep(250);await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");await open();
  const tag=`${width}-${lang}-${theme}`;
  check(await ev(p,`document.documentElement.dataset.theme===${q(theme)}`),'theme '+tag);
  check(await ev(p,"document.querySelector('.jc-cal-grid').scrollWidth<=document.querySelector('.jc-cal-grid').clientWidth"),'grid fits '+tag);
  check(await ev(p,"[...document.querySelectorAll('.jc-cal-grid button')].every(b=>b.getBoundingClientRect().height>=44)"),'touch targets '+tag);
  await click(p,'.jc-cal-grid button[aria-pressed=true]');await waitFor(p,"document.querySelector('.jc-cal-dialog[open]')");
  check(operations.length===0,'empty day opens without model '+tag);
  writeFileSync(join(OUT,'editor-'+tag+'.png'),await shoot(p));
  await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await waitFor(p,"!document.querySelector('.jc-cal-dialog')");
  check(operations.length===0,'ESC no save '+tag);
  await click(p,'.jc-cal-grid button[aria-pressed=true]');await waitFor(p,"document.querySelector('.jc-cal-dialog[open]')");await set('title',lang==='zh'?'与 Agent J 共同安排':'Plan with Agent J');
  const selected=await ev(p,"document.querySelector('.jc-cal-dialog [name=start]').value");
  const endDate=new Date(selected+'T12:00:00');endDate.setDate(endDate.getDate()+1);const end=endDate.toISOString().slice(0,10);await set('end',end);await set('note','Notes <script>alert(1)</script>');await set('reminder','15');await submit();await waitFor(p,"!document.querySelector('.jc-cal-dialog')&&document.querySelector('.jc-calendar .jc-entry')");
  check(operations.length===1&&operations[0].op==='add'&&operations[0].event.reminder===15,'direct save '+tag);
  check(await ev(p,"!document.querySelector('.jc-calendar script')"),'text only notes '+tag);
  writeFileSync(join(OUT,'calendar-'+tag+'.png'),await shoot(p));
  await click(p,'.jc-entry-actions button:first-child');await waitFor(p,"document.querySelector('.jc-cal-dialog[open]')");await set('title','Edited event');await submit();await waitFor(p,"!document.querySelector('.jc-cal-dialog')&&document.querySelector('.jc-entry h3')?.textContent==='Edited event'");check(operations.at(-1).op==='edit','edit '+tag);
  conflict=true;await click(p,'.jc-entry-actions button:first-child');await waitFor(p,"document.querySelector('.jc-cal-dialog[open]')");await set('title','Concurrent draft');await submit();await waitFor(p,"document.querySelector('.jc-cal-dialog [role=status]')?.textContent");check(events[0].title==='Edited event','conflict keeps entry '+tag);await ev(p,"document.querySelector('.jc-cal-dialog').dispatchEvent(new Event('cancel',{cancelable:true}))");
  await click(p,'.jc-entry-actions button:last-child');await waitFor(p,"document.querySelector('.jc-dialog[open]')");await ev(p,"[...document.querySelectorAll('.jc-dialog button')].at(-1).click()");await waitFor(p,"!document.querySelector('.jc-entry')&&!document.querySelector('.jc-dialog')&&document.querySelectorAll('.jc-cal-grid button').length===42");check(events.length===0,'delete '+tag);
  await click(p,'.jc-cal-grid button[aria-pressed=true]');await waitFor(p,"document.querySelector('.jc-cal-dialog[open]')");
  await set('title','Timed meeting');await ev(p,"document.querySelector('[name=allDay]').checked=false;document.querySelector('[name=allDay]').dispatchEvent(new Event('change'))");
  await set('start',selected+'T09:30');await set('end',selected+'T10:30');await submit();await waitFor(p,"!document.querySelector('.jc-cal-dialog')&&document.querySelector('.jc-entry')");
  check(operations.at(-1).event.all_day===false&&operations.at(-1).event.start.endsWith('Z'),'timed explicit offset '+tag);
  events.push({id:'readonly-feed',title:'Subscribed meeting',start:selected,end,all_day:true,readonly:true,source:'ICS: personal'}, {id:'readonly-todo',title:'Timed to-do',start:selected,end,all_day:true,readonly:true,source:'card/todo#t1'});await open();
  check(await ev(p,"!document.querySelector('[data-item=readonly-feed] .jc-entry-actions')&&!document.querySelector('[data-item=readonly-todo] .jc-entry-actions')"),'read-only source UI '+tag);
  check(await ev(p,"document.querySelector('[data-item=readonly-todo] button')?.textContent.length>0"),'to-do source link '+tag);
  const before=await ev(p,"document.querySelector('.jc-cal-nav h2').textContent");await click(p,'.jc-cal-nav button:last-child');await sleep(60); // today
  await click(p,'.jc-cal-nav button:nth-of-type(2)');check(await ev(p,`document.querySelector('.jc-cal-nav h2').textContent!==${q(before)}`),'month navigation '+tag);
  await ev(p,"[...document.querySelectorAll('.jc-calendar>.fp-action')].at(-1).click()");await waitFor(p,"document.querySelector('#input').value==='/agentj-calendar '");check(true,'collaborate draft trailing space '+tag);
  check(p.problems.length===0,'console '+tag+': '+p.problems.join('|'));
 }finally{await p.dispose();}
}
writeFileSync(join(OUT,'browser.json'),JSON.stringify({ok:true,checks,scope:'Chromium 390 touch / 1440 desktop, zh/en, light/dark; emulation only'},null,2));console.log('P413 PASS '+checks+' checks');
}finally{await B.close();await fake.stop();await web.stop();}
const rec=parityRecorder('web/test/p413.mjs');rec.record('p413-calendar',true);rec.write();
