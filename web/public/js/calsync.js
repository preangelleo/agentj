// P428: the calendar card's two-way parts — source tags, the per-calendar sync strip, conflict choices and the inline
// undo line. Text only (textContent), no network of its own: every request goes through the paired channel (ask1).
import {t,locale} from './t.js';
const n=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const b=(text,fn)=>{const e=n('button',text,'fp-action');e.type='button';e.onclick=fn;return e;};
export const timing={undo:10000,note:120000};
const TWO=['google','caldav'];
export const twoWay=subs=>(subs||[]).filter(s=>TWO.includes(s.kind)&&s.enabled!==false);
const kindLabel=(s,name)=>s?.kind==='google'?'Google':/icloud/i.test(name||'')?'iCloud':(name||'');
/** The tag a two-way event / strip row shows: Google, iCloud, or the calendar's own name — and the own name whenever
 * two enabled two-way calendars would otherwise both say 「Google」 (or 「iCloud」). */
export function remoteLabel(name,subs){
 const s=(subs||[]).find(x=>x.name===name),label=kindLabel(s,name);
 return label!==name&&twoWay(subs).filter(x=>kindLabel(x,x.name)===label).length>1?name:label;
}
/** One status line from a sync reply → [key, vars]. */
export function syncStatus(res,kind){
 const s=res?.sync;
 if(!res?.ok||!s)return ['p428.failed'];
 if(s.offline||s.why==='offline')return ['p428.offline'];
 if(s.why==='too_soon')return ['p428.tooSoon'];
 if(s.why==='auth')return [kind==='google'?'p428.authGoogle':'p428.authCaldav'];
 const clash=s.conflicts||(s.why==='conflict'?(res.conflicts||[]).filter(c=>c.remote===s.name).length:0);
 if(clash>0)return ['p428.conflicts',{count:clash}];
 if(!s.ok)return ['p428.failed'];
 return ['p428.done',{pulled:s.pulled||0,pushed:s.pushed||0}];
}
/** The one-line explanation of a conflict → [key, vars]. */
export function conflictText(c,label){return [c.local===null?'p428.localDeleted':c.remote_state==='deleted'?'p428.deleted':'p428.changed',{remote:label}];}
/** The two choices of a conflict, each [key, vars, side] — the label says what happens to the event.
 * Deleted there: 「也删掉」 (side remote) / 「留着」 (local). Deleted here: 「留着{remote}的」 (remote) / 「还是删掉」 (local). */
export function conflictChoices(c,label){
 if(c.local===null)return [['p428.keepTheirCopy',{remote:label},'remote'],['p428.deleteAnyway',{},'local']];
 if(c.remote_state==='deleted')return [['p428.deleteToo',{},'remote'],['p428.keepIt',{},'local']];
 return [['p428.keepMine',{},'local'],['p428.keepTheirs',{remote:label},'remote']];
}
export const undoText=why=>why==='nothing_to_undo'?'p428.nothingToUndo':'p428.undoFailed';
export function tags(it,subs){
 if(!it.remote&&!it.pending&&!it.conflict)return null;
 const p=n('p',undefined,'jc-cal-tags');
 if(it.remote)p.append(n('span',remoteLabel(it.remote,subs),'jc-cal-tag'));
 if(it.pending)p.append(n('span',t('p428.pending'),'jc-cal-tag jc-cal-wait'));
 if(it.conflict)p.append(n('span',t('p428.conflictTag'),'jc-cal-tag jc-cal-wait'));
 return p;
}
// Module state survives the card's re-render after every change: which calendar is syncing, its last result, the undo offer.
const busy=new Set(),notes=new Map(),live={};let flash=null,timer=0;
export function setFlash(key,undo){clearTimeout(timer);flash=key?{key,undo}:null;if(flash)timer=setTimeout(()=>setFlash(null),undo?timing.undo:4000);live.flash?.();}
/** The inline 「撤销」 line under the calendar; `onUndo` is the current listing's undo request. */
export function flashLine(onUndo){
 const line=n('div',undefined,'jc-cal-flash');line.setAttribute('role','status');
 const draw=()=>{line.replaceChildren();line.hidden=!flash;if(!flash)return;line.append(n('span',t(flash.key)));if(flash.undo)line.append(b(t('p428.undo'),onUndo));};
 live.flash=draw;draw();return line;
}
/** The newest drawn calendar's re-fetch (a sync can outlive the view that started it). */
export const attach=reload=>{live.reload=reload;};
/** The sync strip: one row per two-way calendar. `ask(name)` resolves to the reply; `redraw` repaints for the busy state. */
export function syncStrip(subs,ask,redraw){
 const rows=twoWay(subs);if(!rows.length)return null;
 const strip=n('section',undefined,'jc-cal-sync');strip.setAttribute('aria-label',t('p428.strip'));
 for(const s of rows){
  const row=n('div',undefined,'jc-cal-sync-row');row.dataset.remote=s.name;
  const info=n('div',undefined,'jc-cal-sync-info');
  info.append(n('b',remoteLabel(s.name,subs)),n('span',s.last_sync?t('p428.lastSync',{time:new Date(s.last_sync).toLocaleString(locale(),{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}):t('p428.never'),'fp-meta'));
  if(s.queued>0)info.append(n('span',t('p428.queued',{count:s.queued}),'fp-meta'));
  const go=b(t(busy.has(s.name)?'p428.syncing':'p428.sync'),async()=>{
   busy.add(s.name);notes.delete(s.name);setFlash(null);redraw();
   const res=await ask(s.name);busy.delete(s.name);
   const [key,vars]=syncStatus(res,s.kind);notes.set(s.name,{key,vars,at:Date.now()});live.reload?.();
  });
  go.disabled=busy.has(s.name);go.setAttribute('aria-busy',String(busy.has(s.name)));
  const note=notes.get(s.name),status=n('p',note&&Date.now()-note.at<timing.note?t(note.key,note.vars):'','jc-note jc-cal-sync-note');status.setAttribute('role','status');
  row.append(info,go,status);strip.append(row);
 }
 return strip;
}
/** Conflict choices: title, one line of why, two buttons from conflictChoices. `keep(id, 'local'|'remote')`. */
export function conflictList(conflicts,events,subs,keep){
 if(!conflicts?.length)return null;
 const box=n('section',undefined,'jc-cal-conflicts');box.append(n('h3',t('p428.conflictsTitle')));
 for(const c of conflicts){
  const label=remoteLabel(c.remote,subs),row=n('article',undefined,'jc-entry jc-cal-conflict');row.dataset.conflict=c.id;
  const [key,vars]=conflictText(c,label);
  row.append(n('h3',c.local?.title||(events||[]).find(e=>e.id===c.id)?.title||t('p428.untitled')),n('p',t(key,vars)));
  const acts=n('div',undefined,'jc-entry-actions');
  for(const [k,v,side] of conflictChoices(c,label))acts.append(b(t(k,v),()=>keep(c.id,side,acts)));
  row.append(acts);box.append(row);
 }
 return box;
}
