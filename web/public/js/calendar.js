// P413: no third-party code or network. Calendar content travels only over the paired channel.
import {ask1} from './session.js';
import {t,locale} from './t.js';
import {attach,setFlash,flashLine,syncStrip,conflictList,tags,undoText} from './calsync.js';
const n=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const b=(text,fn)=>{const e=n('button',text,'fp-action');e.type='button';e.onclick=fn;return e;};
export const dayKey=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
export function monthDays(year,month){const first=new Date(year,month,1,12);first.setDate(1-first.getDay());return Array.from({length:42},(_,i)=>{const d=new Date(first);d.setDate(d.getDate()+i);return d;});}
export function onDay(e,key){if(e.all_day)return e.start<=key&&key<e.end;const a=new Date(key+'T00:00:00'),z=new Date(a);z.setDate(z.getDate()+1);return new Date(e.start)<z&&new Date(e.end)>a;}
function localInput(value){const d=new Date(value);return dayKey(d)+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
const selected=new Map();
export function drawCalendar(box,m,hooks,reload){
 let key=selected.get('day')||dayKey(new Date()),month=new Date(key+'T12:00:00');
 const panel=n('div',undefined,'jc-calendar');box.append(panel);
 const error=n('p',undefined,'jc-note');error.setAttribute('role','status');box.append(error);
 async function modify(op,event,entry){
  if(!box.isConnected)return false;
  setFlash(null);const res=await ask1({t:'jcard_cal',id:'calendar',op,event,entry,revision:m.revision},'jcard_cal',15000);
  if(!box.isConnected)return false;
  if(res.ok){setFlash('p428.saved',true);reload();return true;}
  error.textContent=t(res.why==='conflict'?'p413.conflict':'p413.failed');
  return false;
 }
 attach(reload);
 const cal=(extra,ms)=>ask1({t:'jcard_cal',id:'calendar',...extra},'jcard_cal',ms||15000);
 async function undo(){setFlash(null);const res=await cal({op:'undo',revision:m.revision});setFlash(res.ok?'p428.undone':undoText(res.why));if(res.ok&&box.isConnected)reload();}
 async function keep(entry,side,acts){for(const x of acts.children)x.disabled=true;setFlash(null);const res=await cal({op:'resolve',revision:m.revision,entry,keep:side});
  if(res.ok){setFlash('p428.saved',true);if(box.isConnected)reload();}else{for(const x of acts.children)x.disabled=false;setFlash(res.why==='conflict'?'p413.conflict':'p428.resolveFailed');}}
 function editor(it,date){
  const dialog=n('dialog',undefined,'jc-dialog jc-cal-dialog'),form=n('form'),heading=n('h2',t(it?'p413.edit':'p413.add'));
  const fields={};
  const field=(name,type,value)=>{const label=n('label',t('p413.'+name)),input=n(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.name=name;if(type==='checkbox')input.checked=!!value;else input.value=value??'';label.append(input);form.append(label);fields[name]=input;return input;};
  form.append(heading);
  const title=field('title','text',it?.title||'');title.required=true;title.maxLength=300;
  const all=field('allDay','checkbox',it?.all_day??true);
  const start=field('start',all.checked?'date':'datetime-local',it?(all.checked?it.start:localInput(it.start)):date);
  const end=field('end',all.checked?'date':'datetime-local',it?(all.checked?it.end:localInput(it.end)):'');
  start.required=true;
  all.onchange=()=>{for(const input of [start,end]){const value=input.value;input.type=all.checked?'date':'datetime-local';input.value=all.checked?value.slice(0,10):(value.slice(0,10)||date)+'T09:00';}};
  const reminder=field('reminder','number',it?.reminder??'');reminder.min='0';reminder.max='40320';reminder.step='1';
  const note=field('note','textarea',it?.note||'');note.maxLength=1000;
  const message=n('p',undefined,'jc-note');message.setAttribute('role','status');form.append(message);
  const close=()=>{dialog.close();dialog.remove();},cancel=b(t('p413.cancel'),close),save=n('button',t('p413.save'),'fp-action');save.type='submit';
  const footer=n('div',undefined,'jc-cal-footer');footer.append(cancel,save);form.append(footer);dialog.append(form);document.body.append(dialog);
  dialog.addEventListener('cancel',e=>{e.preventDefault();close();});dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  form.onsubmit=async e=>{e.preventDefault();if(!form.reportValidity())return;
   const event={title:title.value,all_day:all.checked,start:all.checked?start.value:new Date(start.value).toISOString(),note:note.value,source:it?.source||'owner',reminder:reminder.value===''?null:Number(reminder.value)};
   if(end.value)event.end=all.checked?end.value:new Date(end.value).toISOString();
   save.disabled=true;
   const ok=await modify(it?'edit':'add',event,it?.id);
   if(ok)close();else{save.disabled=false;message.textContent=error.textContent;}
  };
  dialog.showModal();title.focus();
 }
 function paint(){
  panel.replaceChildren();
  const nav=n('div',undefined,'jc-cal-nav');
  const label=n('h2',month.toLocaleDateString(locale(),{year:'numeric',month:'long'}));
  const prev=b('‹',()=>{month=new Date(month.getFullYear(),month.getMonth()-1,1,12);paint();}),next=b('›',()=>{month=new Date(month.getFullYear(),month.getMonth()+1,1,12);paint();});
  prev.setAttribute('aria-label',t('p413.previous'));next.setAttribute('aria-label',t('p413.next'));
  nav.append(prev,label,next,b(t('p413.today'),()=>{key=dayKey(new Date());selected.set('day',key);month=new Date(key+'T12:00:00');paint();}));panel.append(nav);
  const strip=syncStrip(m.subscriptions,name=>cal({op:'sync',name},90000),paint);if(strip)panel.append(strip);
  const grid=n('div',undefined,'jc-cal-grid');grid.setAttribute('role','group');grid.setAttribute('aria-label',t('p413.month'));
  for(let i=0;i<7;i++)grid.append(n('span',new Date(2026,9,18+i).toLocaleDateString(locale(),{weekday:'short'}),'jc-cal-weekday'));
  for(const d of monthDays(month.getFullYear(),month.getMonth())){
   const day=dayKey(d),events=(m.events||[]).filter(e=>onDay(e,day));
   const cell=b(String(d.getDate())+(events.length?' ·':''),()=>{key=day;selected.set('day',key);paint();if(!events.length)editor(null,key);});
   cell.dataset.day=day;cell.dataset.out=String(d.getMonth()!==month.getMonth());cell.setAttribute('aria-pressed',String(day===key));cell.setAttribute('aria-label',day+' · '+events.length);grid.append(cell);
  }panel.append(grid);const clash=conflictList(m.conflicts,m.events,m.subscriptions,keep);if(clash)panel.append(clash);
  panel.append(flashLine(undo));   // in flow between the month / choices and the day's list it changed — never over the grid
  const head=n('div',undefined,'jc-cal-nav');head.append(n('h3',new Date(key+'T12:00:00').toLocaleDateString(locale(),{dateStyle:'full'})),b(t('p413.add'),()=>editor(null,key)));panel.append(head);
  const events=(m.events||[]).filter(e=>onDay(e,key));
  if(!events.length)panel.append(n('p',t('p413.empty'),'jc-note'));
  for(const it of events){
   const row=n('article',undefined,'jc-entry');row.dataset.item=it.id;
   row.append(n('h3',it.title),n('p',it.all_day?t('p413.allDay'):new Date(it.start).toLocaleString(locale())+' – '+new Date(it.end).toLocaleString(locale())));
   if(it.note)row.append(n('p',it.note));if(it.source)row.append(n('p',it.source,'fp-meta'));const tag=tags(it,m.subscriptions);if(tag)row.append(tag);
   if(it.reminder!==null&&it.reminder!==undefined)row.append(n('p',t('p413.reminderValue',{minutes:it.reminder}),'fp-meta'));
   if(it.readonly){row.append(n('p',t('p413.readonly'),'fp-meta'));if(it.source?.startsWith('card/todo#'))row.append(b(t('p413.openTodo'),()=>hooks.nav(it.source)));}
   else{const actions=n('div',undefined,'jc-entry-actions');actions.append(b(t('p413.edit'),()=>editor(it,key)),b(t('p413.delete'),()=>{
    const d=n('dialog',undefined,'jc-dialog');d.append(n('p',t('p413.delete')+' · '+it.title),b(t('p413.cancel'),()=>{d.close();d.remove();}),b(t('p413.delete'),async()=>{for(const x of d.querySelectorAll('button'))x.disabled=true;if(await modify('delete',{},it.id)){d.close();d.remove();}else{d.append(n('p',error.textContent));for(const x of d.querySelectorAll('button'))x.disabled=false;}}));d.addEventListener('cancel',()=>d.remove());document.body.append(d);d.showModal();
   }));row.append(actions);}panel.append(row);
  }
  panel.append(b(t('p413.collaborate'),()=>hooks.draft('/agentj-calendar ')));
 }
 paint();
}
