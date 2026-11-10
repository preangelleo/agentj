// #541: presentation consumes the host-owned canonical envelope; it cannot grant authority.
import { t, lang } from './t.js';
import { el, toast } from './ui.js';
import { specialApprovalById, reviewSpecial } from './approval-special541.js';
import { setApprovalAnchor } from './approval-anchor541.js';
import { approvalReview } from './api.js';
import { stepTitle, stepReason } from './approval-layout0281.js';
const detailIds=['cmdBox','why','apprScope','cmdReason'];
const homes=new Map();
let key='', current=null, hooks=null, active=false;
const text=(tag,cls,value)=>{const n=document.createElement(tag);n.className=cls.replace(/\baj-btn(?=\b|--)/g,'aj-action');n.setAttribute('data-aj-owner',cls.includes('aj-btn')?'aj-action':'aj-approval');if(cls.includes('aj-btn'))n.setAttribute('data-aj-action-context','dialog');n.textContent=value||'';return n;};
export function safeDetails(e){
 const source=e?.source||{},chain=Array.isArray(source.chain)?source.chain.map(item=>typeof item==='string'?item:String(item?.agent||item?.unit||item?.task||[item?.kind,item?.id].filter(Boolean).join(' '))).filter(Boolean).join(' → '):'';
 return [String(e?.details||''),source.root_message_id?t('p541.rootRef',{id:source.root_message_id}):'',source.message_id?t('p541.triggerRef',{id:source.message_id}):'',source.root?.text?t('p541.rootText',{text:source.root.text}):'',chain?t('p541.chain',{chain}):'',source.task_record?t('p541.task',{task:source.task_record}):''].filter(Boolean).join('\n\n');
}
export const enabled=p=>p?.approval_v===2;
// #0304 A2①: the owner's own (verified) turn quoted outside material — say where the material came from, not 「外部消息」.
// Guest / group / unknown roots (root missing or not verified) keep #541's wording. Display only: Allow rules do not read this.
const QUOTE_CH=new Set(['email','wechat','telegram','friend_report','support']);
export const quotedMaterial=src=>src?.suspicious===true&&src?.root?.verified===true;
export function warnText(src){
 if(!quotedMaterial(src))return t(src?.suspicious?'p541.suspicious':'p541.unknown');
 return t('p0304.quote',{ch:t('p0304.qch.'+(QUOTE_CH.has(src.channel)?src.channel:'other'))});
}
export function reset(){
 active=false;current=null;el("sheet").removeAttribute("data-approval-v");
 for(const id of ['warn541','source541','action541','details541','doubt541c'])el(id).hidden=true;
 el('apprBtns').classList.remove('buttons541c');
 for(const [id,mark] of homes){const n=el(id);if(n.parentNode===el('details541'))mark.after(n);}
 const other=el('sheet').querySelector('.elsewhere');if(other)other.hidden=false;
}
export function paint(p, options){
 if(!enabled(p)){reset();return;}
 active=true;current=p;hooks=options;el("sheet").dataset.approvalV="2";
 const e=p.envelope||{},src=e.source||{},outside=src.history_outside_window===true;
 const suspect=src.suspicious===true||src.verified!==true,quoted=quotedMaterial(src);
 if(key!==p.id){key=p.id;el('details541').open=false;}
 for(const id of detailIds){const n=el(id);if(!homes.has(id)){const mark=document.createComment('541 details home');n.before(mark);homes.set(id,mark);}el('details541').append(n);}
 el('details541').hidden=false;
 const warn=el('warn541');warn.hidden=!suspect;warn.textContent=warnText(src);warn.dataset.quote=quoted?'1':'';
 const source=el('source541');source.hidden=false;source.replaceChildren();
 const channel=t('p541.channel.'+src.channel),label=channel.startsWith('p541.')?String(src.channel||''):channel;
 const tm=Number(src.time),time=Number.isFinite(tm)&&tm>0?new Date(tm<1e12?tm*1000:tm).toLocaleTimeString(lang()==='zh'?'zh-CN':'en-GB',{hour:'2-digit',minute:'2-digit',hour12:false}):'';
 const who=quoted?t('p541.you'):suspect?t('p541.external'):String(src.sender||t('p541.you'));   // #0304 A2①: the owner's own turn quoting material still started with the owner
 const meta=text('span','srcmeta541','');meta.append(text('span','srcsender541',outside?t('p541.initiator',{name:src.initiator||src.sender||'Agent J'}):[label,who].filter(Boolean).join(' · ')+(time?' · '+time:'')));
 if(!outside&&src.message_id)meta.append(text('span','',t('p541.source')));
 source.append(meta,text('span','srctext541',outside?t('p541.older'):src.text||t('p541.unknown')),text('span','srcid541',outside?t('p541.task',{task:src.task_record||''}):'Message ID · '+(src.message_id||'—')));
 // @ix appr.source
 source.disabled=outside||!src.message_id;
 source.onclick=async()=>{if(!source.disabled){options.fold();await options.locate(src);}};
 const action=el('action541');action.hidden=false;action.textContent=stepReason(e);
 if(e.plan?.display){action.append(text('span','scope541','\n'+((lang()==='en'&&e.plan.display_en)||e.plan.display)));}
 el('sheetTitle').textContent=stepTitle(e,t('p336.faTitle'));
 el('askText').textContent=t('p541.wait');el('askTags').hidden=true;el('cmdToggle').hidden=true;el('goalInfo').hidden=true;
 for(const id of ['apprBatch','apprGoalAdd','apprKbd','cmdCopy'])el(id).hidden=true;
 const task=el('apprTask');if(task)task.hidden=true;
 const other=el('sheet').querySelector('.elsewhere');if(other)other.hidden=true;
 el('apprBtns').classList.add('buttons541c');
 el('doubt541c').hidden=false;el('doubt541c').disabled=options.busy;
 // @ix appr.question
 el('doubt541c').onclick=()=>review(p,'question',options);
 if(p.envelope_verified!==true||p.review_state==='questioned')el('apprAllow').disabled=true;
 if(p.envelope_verified!==true){el('askState').textContent=t('p541.invalid');el('askState').hidden=false;}
 // @ix appr.details_full
 el('details541').ontoggle=()=>{}; // @ix appr.details
 el('detailsFull541').onclick=()=>{const d=el('full541');d.querySelector('pre').textContent=safeDetails(e);options.fold();d.addEventListener('close',()=>options.open(),{once:true});d.showModal();d.querySelector('button').focus();};
 el('full541').querySelector('button').onclick=()=>el('full541').close();
}
export function updateAnchor(count,folded,open,fallback){
 setApprovalAnchor('ordinary',{count,collapsed:folded,expanded:open,open:()=>active&&current?review(current,'reopen',hooks):fallback?.()});
}
export async function review(p,op,options=hooks){
 if(!p||!options)return;
 if(op==='reopen'&&p.review_state==='pending'&&p.state!=='expired'){
  options.open();return;
 }
 const result=await approvalReview(p,op,innerWidth<500?'Face ID':'Touch ID');
 if(!result.ok){toast(t('p541.reviewFail'),3500);return;}
 if(op==='question'){options.fold();options.questioned?.();}else if(result.state==='restart_requested'){toast(t('p541.renewWaiting'),3500);options.fold();}else options.open();
}
export function reopenButton(box,page,lookup,options){
 // Reply text can be cached while request lifecycle changes. Clear the previous control before all early returns.
 box.querySelectorAll('#reopen541').forEach(button=>button.remove());
 const ref=page.card?.approval_reopen,id=typeof ref==='string'?ref:ref?.id;
 if(!/^[a-f0-9]{32}$/.test(id||''))return false;
 const p=lookup(id)||specialApprovalById(id);if(!p||['denied','approved','terminal','stopped','ended'].includes(p.state))return false;
 if(box.id!=='words')box.replaceChildren();box.hidden=false;
 const b=text('button','aj-btn aj-btn--primary',t('p541.reopen'));b.type='button';b.id='reopen541';
 // @ix appr.reopen
 b.onclick=()=>p.review_kind==='special'?reviewSpecial(p,p.state==='expired'?'renew':'reopen'):review(p,p.state==='expired'?'renew':'reopen',options);box.append(b);return true;
}
