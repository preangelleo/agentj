// P428 item 4: the calendar card's two-way parts on the phone page — sync strip (busy + every result line), source /
// waiting tags, conflict choices (keep mine / keep theirs frames), the inline undo line (frame, failure text, timeout),
// read-only rows and P413's ESC / save footer unchanged. Real client + fake host; 390 touch / 1440 desktop × zh/en × light/dark.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const OUT=fileURLToPath(new URL('../../../reports/qa/p428/',import.meta.url));mkdirSync(OUT,{recursive:true});
const ev=evaluate,q=JSON.stringify,click=(p,s)=>ev(p,`document.querySelector(${q(s)}).click()`);
const DICT=(await import('../public/i18n.js')).default;
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();let checks=0;
const check=(x,s)=>{assert.ok(x,s);checks++;};
const today=new Date(),day=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
const next=new Date(today);next.setDate(next.getDate()+1);const end=`${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,'0')}-${String(next.getDate()).padStart(2,'0')}`;
const ev0=(id,title,more)=>({id,title,start:day,end,all_day:true,reminder:null,note:'',source:'owner',readonly:false,remote:null,pending:false,conflict:false,...more});
try{
for(const width of [390,1440])for(const lang of ['zh','en'])for(const theme of ['light','dark']){
 const T=(k,v)=>String(DICT[lang][k]).replace(/\{(\w+)\}/g,(m,x)=>v&&x in v?String(v[x]):m);
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 let revision=0,operations=[],syncReply=null,hold=null,undoWhy=null;
 let events=[ev0('g1',lang==='zh'?'团队周会':'Team weekly',{remote:'work',pending:true}),ev0('i1',lang==='zh'?'看牙医':'Dentist',{remote:'icloud',conflict:true}),
  ev0('w2',lang==='zh'?'交房租':'Pay rent',{remote:'work',conflict:true}),
  ev0('feed',lang==='zh'?'订阅的会议':'Subscribed meeting',{readonly:true,source:'ICS: personal'}),ev0('todo',lang==='zh'?'有时间的待办':'Timed to-do',{readonly:true,source:'card/todo#t1'})];
 let conflicts=[{id:'i1',remote:'icloud',at:'2026-10-20T08:00:00Z',remote_state:'changed',local:{title:events[1].title,start:day,end,all_day:true,reminder:null,note:'',source:'owner'}},
  {id:'w2',remote:'work',at:'2026-10-20T08:00:00Z',remote_state:'deleted',local:{title:events[2].title,start:day,end,all_day:true,reminder:null,note:'',source:'owner'}},
  {id:'gone',remote:'work',at:'2026-10-20T08:00:00Z',remote_state:'changed',local:null}];
 events.push(ev0('gone',lang==='zh'?'家长会':'School meeting',{remote:'work',conflict:true}));
 const subscriptions=()=>[{name:'work',kind:'google',enabled:true,last_sync:'2026-10-20T07:05:00Z',last_error:null,queued:events.filter(e=>e.pending).length,conflicts:2,push_new:true,auto:false},
  {name:'icloud',kind:'caldav',enabled:true,last_sync:null,last_error:null,queued:0,conflicts:1,push_new:false,auto:false},{name:'feed',kind:'ics',enabled:true,last_sync:null,last_error:null,queued:0,conflicts:0,push_new:false,auto:false}];
 const listing=()=>({id:'calendar',template:'calendar',events,revision:String(revision),subscriptions:subscriptions(),conflicts,undo:3,plus:'agentj-calendar',actions:[],anchors:[],links:[]});
 const cards=[{id:'calendar',name:{zh:'日历',en:'Calendar'},desc:{zh:'共同管理日程',en:'Your shared schedule'},icon:'calendar',accent:'windstorm',official:true,on:true,plus:'agentj-calendar'}];
 fake.st.onApp=async(c,m,send)=>{
  const reply=async(t,v)=>{await send({t,r:m.r,...v});return true;};
  if(m.t==='jcard_list')return reply('jcard_items',{cards});
  if(m.t==='jcard_get')return reply('jcard_get',{ok:true,...listing()});
  if(m.t==='jcard_cal'){
   operations.push(m);
   if(m.op==='sync'){if(hold)await hold;return reply(m.t,syncReply.top||{ok:true,...listing(),sync:{name:m.name,pulled:0,pushed:0,queued:0,conflicts:0,linked_duplicates:0,...syncReply}});}
   if(m.revision!==String(revision))return reply(m.t,{ok:false,why:'conflict'});
   if(m.op==='undo'){if(undoWhy)return reply(m.t,{ok:false,why:undoWhy,...listing()});revision++;return reply(m.t,{ok:true,...listing()});}
   if(m.op==='resolve'){conflicts=conflicts.filter(x=>x.id!==m.entry);events=events.map(e=>e.id===m.entry?{...e,conflict:false}:e);revision++;return reply(m.t,{ok:true,...listing()});}
   if(m.op==='edit')events=events.map(e=>e.id===m.entry?{...e,...m.event,pending:e.remote?true:e.pending}:e);
   if(m.op==='add')events.push(ev0('event-'+revision,m.event.title));
   if(m.op==='delete')events=events.filter(e=>e.id!==m.entry);
   revision++;return reply(m.t,{ok:true,...listing()});
  }
  return false;
 };
 const p=await newPage(B,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 const open=async()=>{await ev(p,"import('./js/five-pages.js').then(m=>m.openTarget('calendar'))");await waitFor(p,"document.querySelectorAll('.jc-cal-grid button').length===42");};
 const text=s=>ev(p,`document.querySelector(${q(s)})?.textContent||''`);
 const syncOnce=async(name,reply,want)=>{
  syncReply=reply;await click(p,`.jc-cal-sync-row[data-remote=${name}] button`);
  await waitFor(p,`document.querySelector('.jc-cal-sync-row[data-remote=${name}] .jc-cal-sync-note')?.textContent===${q(want)}`,10000);
  check(operations.at(-1).op==='sync'&&operations.at(-1).name===name&&!('revision' in operations.at(-1)),'sync frame '+name);
 };
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await fake.send({t:'status',s:'idle',agent:'codex',name:'Agent J'});await ev(p,"document.getElementById('phone-env-dismiss')?.click()");await sleep(250);await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");await open();
  const tag=`${width}-${lang}-${theme}`;
  // strip: two two-way rows (the ICS feed has none), label, last sync / never, waiting count, 44 px Sync
  check(await ev(p,"document.querySelectorAll('.jc-cal-sync-row').length===2&&!document.querySelector('.jc-cal-sync-row[data-remote=feed]')"),'strip rows '+tag);
  check(await ev(p,"document.querySelector('.jc-cal-sync').compareDocumentPosition(document.querySelector('.jc-cal-grid'))&Node.DOCUMENT_POSITION_FOLLOWING"),'strip above grid '+tag);
  check((await text('[data-remote=work] b'))==='Google'&&(await text('[data-remote=icloud] b'))==='iCloud','strip labels '+tag);
  check((await text('[data-remote=icloud] .jc-cal-sync-info'))==='iCloud'+T('p428.never'),'never synced '+tag);
  check((await text('[data-remote=work] .jc-cal-sync-info')).includes(T('p428.queued',{count:1})),'waiting count '+tag);
  check(!(await text('[data-remote=icloud] .jc-cal-sync-info')).includes(T('p428.queued',{count:0})),'no zero count '+tag);
  check(await ev(p,"[...document.querySelectorAll('.jc-cal-sync button, .jc-cal-conflict button, .jc-cal-grid button')].every(b=>b.getBoundingClientRect().height>=44)"),'touch targets '+tag);
  check(await ev(p,"[...document.querySelectorAll('.jc-cal-sync-row, .jc-cal-conflict')].every(r=>r.scrollWidth<=r.clientWidth+1&&r.getBoundingClientRect().right<=innerWidth)"),'no clipping '+tag);
  // tags: source + waiting on two-way rows, editable; read-only rows unchanged from P413
  check((await text('[data-item=g1] .jc-cal-tags'))==='Google'+T('p428.pending'),'google tag + waiting '+tag);
  check((await text('[data-item=i1] .jc-cal-tags'))==='iCloud'+T('p428.conflictTag'),'icloud tag '+tag);
  check(await ev(p,"!!document.querySelector('[data-item=g1] .jc-entry-actions')&&!!document.querySelector('[data-item=i1] .jc-entry-actions')"),'two-way editable '+tag);
  check(await ev(p,"!document.querySelector('[data-item=feed] .jc-entry-actions')&&!document.querySelector('[data-item=todo] .jc-entry-actions')&&!document.querySelector('[data-item=feed] .jc-cal-tags')"),'read-only rows '+tag);
  check((await text('[data-item=todo] button')).length>0,'to-do link kept '+tag);
  // conflicts: one row each, the right explanation and buttons
  check(await ev(p,"document.querySelectorAll('.jc-cal-conflict').length===3"),'conflict rows '+tag);
  check((await text('[data-conflict=i1] p'))===T('p428.changed',{remote:'iCloud'}),'changed text '+tag);
  check((await text('[data-conflict=w2] p'))===T('p428.deleted',{remote:'Google'}),'deleted text '+tag);
  check((await text('[data-conflict=gone] p'))===T('p428.localDeleted',{remote:'Google'})&&(await text('[data-conflict=gone] h3'))===events.at(-1).title,'local-deleted text + title '+tag);
  check((await text('[data-conflict=i1] .jc-entry-actions button:first-child'))===T('p428.keepMine')&&(await text('[data-conflict=i1] .jc-entry-actions button:last-child'))===T('p428.keepTheirs',{remote:'iCloud'}),'conflict buttons '+tag);
  // QA D1: deletion conflicts name what happens — deleted there: 「也删掉」 / 「留着」; deleted here: 「留着{remote}的」 / 「还是删掉」
  check((await text('[data-conflict=w2] .jc-entry-actions button:first-child'))===T('p428.deleteToo')&&(await text('[data-conflict=w2] .jc-entry-actions button:last-child'))===T('p428.keepIt'),'deleted-there buttons '+tag);
  check((await text('[data-conflict=gone] .jc-entry-actions button:first-child'))===T('p428.keepTheirCopy',{remote:'Google'})&&(await text('[data-conflict=gone] .jc-entry-actions button:last-child'))===T('p428.deleteAnyway'),'deleted-here buttons '+tag);
  // sync: busy while running, then one line per result
  let release;hold=new Promise(r=>{release=r;});syncReply={ok:true,pulled:3,pushed:1};
  await click(p,'.jc-cal-sync-row[data-remote=work] button');
  await waitFor(p,"document.querySelector('[data-remote=work] button').disabled");
  check(await ev(p,`document.querySelector('[data-remote=work] button').getAttribute('aria-busy')==='true'&&document.querySelector('[data-remote=work] button').textContent===${q(T('p428.syncing'))}`),'busy '+tag);
  release();hold=null;await waitFor(p,`document.querySelector('[data-remote=work] .jc-cal-sync-note')?.textContent===${q(T('p428.done',{pulled:3,pushed:1}))}`);
  check(await ev(p,"!document.querySelector('[data-remote=work] button').disabled"),'done + enabled again '+tag);
  if(lang==='zh')check((await text('[data-remote=work] .jc-cal-sync-note'))==='已同步：收到 3 条，发出 1 条','zh done wording '+tag);
  if(lang==='en')check((await text('[data-remote=work] .jc-cal-sync-note'))==='Synced: 3 in, 1 out','en done wording '+tag);
  await syncOnce('work',{ok:false,offline:true,why:'offline'},T('p428.offline'));
  await syncOnce('work',{ok:false,why:'auth'},T('p428.authGoogle'));
  await syncOnce('icloud',{ok:false,why:'auth'},T('p428.authCaldav'));
  await syncOnce('icloud',{ok:true,conflicts:1},T('p428.conflicts',{count:1}));
  await syncOnce('work',{ok:false,why:'failed'},T('p428.failed'));
  await syncOnce('work',{top:{ok:false,why:'not_two_way'}},T('p428.failed'));
  await syncOnce('work',{ok:true,pulled:0,pushed:1},T('p428.done',{pulled:0,pushed:1}));
  await ev(p,"document.querySelector('.jc-cal-conflicts').scrollIntoView({block:'center'})");
  writeFileSync(join(OUT,'conflicts-'+tag+'.png'),await shoot(p));
  // keep mine / keep theirs → resolve frames, then the undo line
  await click(p,'[data-conflict=i1] .jc-entry-actions button:first-child');await waitFor(p,"!document.querySelector('[data-conflict=i1]')&&document.querySelector('.jc-cal-flash button')");
  let o=operations.at(-1);check(o.op==='resolve'&&o.entry==='i1'&&o.keep==='local'&&o.revision==='0','keep mine frame '+tag);
  check((await text('.jc-cal-flash span'))===T('p428.saved')&&(await text('.jc-cal-flash button'))===T('p428.undo'),'undo line after resolve '+tag);
  // QA D5: the undo line sits in the page flow — it never overlaps the month, the strip or the choices, even with the
  // month's last row at the bottom of the screen (where the old sticky line covered a week)
  const apart=`(()=>{const f=document.querySelector('.jc-cal-flash').getBoundingClientRect();if(!f.height)return false;
   return getComputedStyle(document.querySelector('.jc-cal-flash')).position==='static'&&[...document.querySelectorAll('.jc-cal-grid, .jc-cal-grid button, .jc-cal-sync, .jc-cal-conflicts, .jc-entry')].every(e=>{const r=e.getBoundingClientRect();return r.bottom<=f.top+0.5||r.top>=f.bottom-0.5||r.right<=f.left||r.left>=f.right;});})()`;
  for(const block of ['end','center','start']){await ev(p,`document.querySelector('.jc-cal-grid').scrollIntoView({block:'${block}'})`);check(await ev(p,apart),'undo line apart from the month ('+block+') '+tag);}
  check(await ev(p,"document.querySelector('.jc-cal-grid').compareDocumentPosition(document.querySelector('.jc-cal-flash'))&Node.DOCUMENT_POSITION_FOLLOWING"),'undo line under the month '+tag);
  await click(p,'[data-conflict=w2] .jc-entry-actions button:first-child');await waitFor(p,"!document.querySelector('[data-conflict=w2]')&&document.querySelector('.jc-cal-conflicts')&&document.querySelector('.jc-cal-flash button')");
  o=operations.at(-1);check(o.op==='resolve'&&o.entry==='w2'&&o.keep==='remote'&&o.revision==='1','keep theirs frame '+tag);
  // undo after a save sends {op:'undo'} without entry
  await click(p,'.jc-cal-flash button');await waitFor(p,`document.querySelector('.jc-cal-flash span')?.textContent===${q(T('p428.undone'))}`);
  o=operations.at(-1);check(o.op==='undo'&&!('entry' in o)&&o.revision==='2','undo frame '+tag);
  check(await ev(p,"!document.querySelector('.jc-cal-flash button')"),'undone line has no button '+tag);
  // edit a two-way event (P413 editor + footer), ESC still closes without saving
  await click(p,'[data-item=i1] .jc-entry-actions button:first-child');await waitFor(p,"document.querySelector('.jc-cal-dialog[open] .jc-cal-footer button[type=submit]')");
  const n0=operations.length;await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await waitFor(p,"!document.querySelector('.jc-cal-dialog')");
  check(operations.length===n0,'ESC no save '+tag);
  await click(p,'[data-item=i1] .jc-entry-actions button:first-child');await waitFor(p,"document.querySelector('.jc-cal-dialog[open]')");
  await ev(p,"document.querySelector('.jc-cal-dialog [name=title]').value='Dentist 10:00'");await ev(p,"document.querySelector('.jc-cal-dialog form').requestSubmit()");
  await waitFor(p,`!document.querySelector('.jc-cal-dialog')&&document.querySelector('.jc-cal-flash button')&&document.querySelector('[data-item=i1] .jc-cal-tags')?.textContent.endsWith(${q(T('p428.pending'))})`);
  check(operations.at(-1).op==='edit'&&operations.at(-1).entry==='i1','edit two-way '+tag);
  check((await text('[data-item=i1] .jc-cal-tags'))==='iCloud'+T('p428.pending'),'edited row waits '+tag);
  await ev(p,"for(let e=document.querySelector('.jc-calendar');e;e=e.parentElement)e.scrollTop=0;scrollTo(0,0)");
  await ev(p,"document.querySelector('.jc-cal-flash').scrollIntoView({block:'center'})");   // the line is under the month: bring it on screen
  check(await ev(p,apart),'undo line apart after edit '+tag);
  writeFileSync(join(OUT,'strip-undo-'+tag+'.png'),await shoot(p));
  undoWhy='nothing_to_undo';await click(p,'.jc-cal-flash button');await waitFor(p,`document.querySelector('.jc-cal-flash span')?.textContent===${q(T('p428.nothingToUndo'))}`);
  check(operations.at(-1).op==='undo','failed undo frame '+tag);undoWhy=null;
  // conflict-on-save keeps P413's message
  const rev=revision;revision=99;await click(p,'[data-item=g1] .jc-entry-actions button:first-child');await waitFor(p,"document.querySelector('.jc-cal-dialog[open]')");await ev(p,"document.querySelector('.jc-cal-dialog form').requestSubmit()");
  await waitFor(p,`document.querySelector('.jc-cal-dialog [role=status]')?.textContent===${q(T('p413.conflict'))}`);check(true,'conflict on save '+tag);
  check(await ev(p,"!document.querySelector('.jc-cal-flash button')"),'failed save offers no undo '+tag);
  await ev(p,"document.querySelector('.jc-cal-dialog').dispatchEvent(new Event('cancel',{cancelable:true}))");revision=rev;await open();
  // the undo offer goes away by itself
  await ev(p,"import('./js/calsync.js').then(m=>{m.timing.undo=1200})");
  await click(p,'[data-item=g1] .jc-entry-actions button:first-child');await waitFor(p,"document.querySelector('.jc-cal-dialog[open]')");await ev(p,"document.querySelector('.jc-cal-dialog form').requestSubmit()");
  await waitFor(p,"document.querySelector('.jc-cal-flash button')");await waitFor(p,"document.querySelector('.jc-cal-flash').hidden",5000);check(true,'undo line times out '+tag);
  await ev(p,"import('./js/calsync.js').then(m=>{m.timing.undo=10000})");
  check(p.problems.length===0,'console '+tag+': '+p.problems.join('|'));
 }finally{await p.dispose();}
}
writeFileSync(join(OUT,'browser.json'),JSON.stringify({ok:true,checks,scope:'Chromium 390 touch / 1440 desktop, zh/en, light/dark; emulation only'},null,2));console.log('P428 PASS '+checks+' checks');
}finally{await B.close();await fake.stop();await web.stop();}
const rec=parityRecorder('web/test/p428.mjs');rec.record('p428-calendar-sync-phone',true);rec.write();
