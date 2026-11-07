import { stepTitle } from './approval-layout0281.js';
// Dedicated secret/elevation cards keep their sealed-value protocols. Only safe metadata is shared.
import { approvalEnvelopeDigest } from '../proto/wire.js';
import { channel } from './session.js';
import { approvalReview } from './api.js';
import { t } from './t.js';
import { toast } from './ui.js';
import { setApprovalAnchor } from './approval-anchor541.js';
const decorations=new WeakMap();
const expired=new Map();
const records=new Map();let normal='idle';
const node=(tag,cls,tx)=>{const n=document.createElement(tag);n.className=cls;n.setAttribute('data-aj-owner',/special-(?:question|allow|deny)541/.test(cls)?'aj-action':'aj-auth-card');if(/special-(?:question|allow|deny)541/.test(cls))n.setAttribute('data-aj-action-context','dialog');n.textContent=tx||'';return n;};
export function attachSpecial(m,c,repaint){
 if(m.approval_v!==2&&m.v!==2)return;
 expired.delete(c.id);Object.assign(c,{review_kind:'special',approval_v:2,envelope:m.envelope,envelope_sha256:m.envelope_sha256,revision:m.revision,review_state:m.review_state||'pending',envelope_verified:false,pickup_uv_allowed:m.pickup_uv_allowed===true});
 c.collapsed=c.review_state==='questioned';records.set(c.id,{c,repaint});
 c.envelopeReady=Promise.resolve().then(async()=>{
  const e=c.envelope;if(!e||e.v!==2||e.request_id!==c.id||e.channel!==channel()||e.revision!==c.revision||!Number.isInteger(e.revision)||e.revision<1)throw new Error('invalid envelope');
  if(await approvalEnvelopeDigest(e)!==c.envelope_sha256)throw new Error('envelope mismatch');
  c.envelope_verified=true;repaint();
 }).catch(()=>{c.envelope_verified=false;repaint();});
}
export function canSpecialApprove(c){return c?.approval_v!==2||(c.envelope_verified===true&&c.review_state!=='questioned'&&(c.envelope?.source?.verified===true&&c.envelope.source.suspicious!==true||c.pickup_uv_allowed===true&&c.envelope?.special?.tool==='SecretPickup'&&c.envelope.special.pickup_uv_allowed===true&&c.envelope?.source?.channel==='unknown'&&!['unknown_external','external_advice'].includes(c.envelope.source.role)));}
export function forgetSpecial(id,result){const entry=records.get(id);if(entry&&['expired','timeout'].includes(result)){entry.c.state='expired';entry.c.collapsed=true;expired.set(id,entry);while(expired.size>64)expired.delete(expired.keys().next().value);}else expired.delete(id);records.delete(id);refreshSpecialVisibility();}
export const specialApprovalById=id=>(records.get(id)||expired.get(id))?.c;
export function clearSpecialApprovals(){records.clear();expired.clear();refreshSpecialVisibility();}
export function refreshSpecialVisibility(){
 let count=0,expanded=false,target;
 for(const entry of records.values()){
  count++;if(entry.c.collapsed)target||=entry;else if(entry.root&&!entry.root.hidden)expanded=true;
 }
 setApprovalAnchor('special',{count,collapsed:!!target,expanded,open:()=>target&&reviewSpecial(target.c,'reopen')});
 if(expanded){if(document.body.dataset.approvalSpecial!=='1'&&['idle','working'].includes(document.body.dataset.status))normal=document.body.dataset.status;document.body.dataset.approvalSpecial='1';document.body.dataset.status='waiting';document.body.dataset.kind='permission';}
 else if(document.body.dataset.approvalSpecial==='1'){delete document.body.dataset.approvalSpecial;document.body.dataset.status=normal;delete document.body.dataset.kind;}
}
export async function reviewSpecial(c,op){
 const entry=records.get(c.id)||expired.get(c.id);if(!entry)return;
 // A local fold changes no request authority or nonce. Showing the pending card
 // again must not depend on the Agent's current task or rotate its UV challenge.
 if(op==='reopen'&&records.has(c.id)&&c.review_state==='pending'&&c.deadline>Date.now()){
  c.collapsed=false;entry.repaint();refreshSpecialVisibility();return;
 }
 const r=await approvalReview(c,op,innerWidth<500?'Face ID':'Touch ID');
 if(!r.ok){toast(t('p541.reviewFail'),3500);return;}
 if(op==='deny')return;
 if(r.state==='restart_requested'){toast(t('p541.renewWaiting'),3500);return;}
 const fresh=records.get(c.id);if(!fresh)return;
 fresh.c.collapsed=op==='question';fresh.repaint();refreshSpecialVisibility();
}
export function specialQuestioned(m){const e=records.get(m.id);if(!e)return;if(m.ask)return; e.c.collapsed=true;e.c.review_state='questioned';e.repaint();refreshSpecialVisibility();}
export function restoreSpecial(root){
 const d=decorations.get(root);if(d){for(const [n,mark]of d.homes){mark.replaceWith(n);}d.ui.remove();d.actions.remove();d.allow.className=d.allowClass;d.deny.className=d.denyClass;decorations.delete(root);}
 root.classList.remove('approval-special541');
}
export function decorateSpecial(root,c,{title,metadata=[],allow,deny,fold,repaint,opened=false}){
 if(c?.approval_v!==2||opened){restoreSpecial(root);refreshSpecialVisibility();return;}
 const entry=records.get(c.id);if(entry)entry.root=root;
 root.classList.add('approval-special541');root.setAttribute('data-aj-owner','aj-auth-card');
 if(decorations.get(root)?.allow!==allow&&decorations.has(root))restoreSpecial(root);
 const box=root.querySelector('.box'),e=c.envelope||{},src=e.source||{};
 let ui=root.querySelector('.special-main541');
 if(!ui){
  ui=node('div','special-main541');if(title)title.after(ui);else box.insertBefore(ui,box.firstChild);
  const close=node('button','special-fold541',t('r.sheet.fold'));close.type='button';ui.append(close);
  ui.append(node('p','special-status0282',t('p541.wait')));
  const warning=node('p','auth-warn541');ui.append(warning);
  const source=node('button','auth-source541');source.type='button';ui.append(source);
  ui.append(node('p','auth-action541'));
  const details=node('details','auth-details541');const summary=node('summary','',t('p541.details'));details.append(summary);const full=node('button','auth-full541',t('p541.full'));full.type='button';details.append(full);ui.append(details);
  const doubt=node('button','special-deny541 special-question541',t('p541.question'));doubt.type='button';
  const actions=node('div','special-buttons541');const homes=new Map();for(const n of [allow,deny,...metadata])if(n){const mark=document.createComment('541 dedicated control home');n.before(mark);homes.set(n,mark);}
  decorations.set(root,{homes,ui,actions,allow,deny,allowClass:allow.className,denyClass:deny.className});
  actions.append(allow,doubt,deny);box.append(actions);
  allow.classList.add('special-allow541');deny.classList.add('special-deny541');for(const b of [allow,deny]){b.setAttribute('data-aj-owner','aj-action');b.setAttribute('data-aj-action-context','dialog');}
 }
 ui.hidden=false;box.querySelector('.special-buttons541').hidden=false;
 ui.querySelector('.special-fold541').onclick=()=>{c.collapsed=true;fold();refreshSpecialVisibility();};
 const warning=ui.querySelector('.auth-warn541');warning.hidden=src.verified===true&&!src.suspicious;warning.textContent=t(src.suspicious?'p541.suspicious':'p541.unknown');
 const source=ui.querySelector('.auth-source541');source.replaceChildren();
 const ch=t('p541.channel.'+src.channel),tm=Number(src.time),stamp=tm>0?new Date(tm<1e12?tm*1000:tm).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',hour12:false}):'';
 source.append(node('span','auth-meta541',src.history_outside_window?t('p541.initiator',{name:src.initiator||'Agent J'}):[ch.startsWith('p541.')?src.channel:ch,src.sender||t('p541.you'),stamp].filter(Boolean).join(' · ')),node('span','auth-text541',src.history_outside_window?t('p541.older'):src.text||t('p541.unknown')),node('span','auth-id541',src.history_outside_window?t('p541.task',{task:src.task_record||''}):'Message ID · '+(src.message_id||'—')));
 source.disabled=!src.message_id||src.history_outside_window===true;source.onclick=()=>{c.collapsed=true;fold();dispatchEvent(new CustomEvent('approval541-locate',{detail:src}));refreshSpecialVisibility();};
 ui.querySelector('.auth-action541').textContent=e.action||'';
 const details=ui.querySelector('details');for(const n of metadata)if(n)details.append(n);
 ui.querySelector('.auth-full541').onclick=()=>{let d=document.getElementById('auth-full541');if(!d){d=document.getElementById('full541').cloneNode(true);d.id='auth-full541';for(const n of [d,...d.querySelectorAll('*')]){if(n!==d)n.removeAttribute('id');n.setAttribute('data-aj-owner','aj-auth-card');}document.body.append(d);}d.querySelector('pre').textContent=[e.details||'',src.root_message_id?t('p541.rootRef',{id:src.root_message_id}):'',src.message_id?t('p541.triggerRef',{id:src.message_id}):'',src.task_record?t('p541.task',{task:src.task_record}):''].filter(Boolean).join('\n\n');c.collapsed=true;fold();d.addEventListener('close',()=>{if(records.get(c.id)?.c===c){c.collapsed=false;repaint();}},{once:true});d.showModal();d.querySelector('button').onclick=()=>d.close();};
 deny.textContent=t('r.appr.deny');
 const doubt=box.querySelector('.special-question541');doubt.onclick=()=>reviewSpecial(c,'question');doubt.disabled=!c.envelope_verified;
 if(title)title.textContent=stepTitle(e,title.textContent);
 if(!canSpecialApprove(c))allow.disabled=true;
 refreshSpecialVisibility();
}
