// Only Host-generated system metadata offers the existing allowlisted repair control.
import {mk} from './ui.js';
import {t} from './t.js';
import {copyable} from './copycode.js';
export function repairOf(page) {
 const s=page?.source,c=page?.card;
 if(!s||s.quote||page.assist||page.end==='open')return false;
 return s.k==='sys'&&s.repair==='reconnect'||s.k==='cmd'&&c?.cmd==='update'&&['ok','error'].includes(c.kind)&&c.repair==='reconnect';
}
export const replyName=(page,normal)=>repairOf(page)?'AgentJ':normal;
export function paintRepair(box,page,ctx) {
 if(!repairOf(page))return false;
 const b=mk('button','cmdundo',t('p0284.repair.button'),'aj-reply');b.type='button';b.dataset.repair='reconnect';
 const blocked=!ctx.ready()?t('p0284.repair.offline'):!ctx.supported()?t('p0284.repair.unsupported'):'';
 b.disabled=!!blocked;
 b.addEventListener('click',()=>{if(ctx.ready()&&ctx.supported())ctx.run();});
 box.append(b);
 if(blocked)box.append(mk('p','small',blocked));
 const more=mk('details','cmdresume'),label=mk('summary','',t('p339.details'),'aj-reply');
 more.append(label,copyable(mk('code','','agentj doctor --reconnect')));box.append(more);return true;
}
// 0.30.7: a Codex sandbox refusal caused by the owner's own config.toml carries the host's typed card.fix — never inferred
// from the page's text (the notice also shows the terminal commands). One button: the signed cmd_run by NAME + Face ID.
export const FIX = 'codex_full_access';
export function fixOf(page) {
 const s=page?.source,c=page?.card;
 if(!s||s.quote||s.via||s.forward||page.assist||page.end==='open')return false;
 return s.k==='sys'&&!!c&&typeof c==='object'&&c.fix===FIX;
}
export function fixResultText(m) {
 if(m?.ok&&m.unchanged)return t('p0307.fix.already');
 if(m?.ok&&m.restart==='requested')return t('p0307.fix.restarting');
 if(m?.ok)return t('p0307.fix.manual');
 return t('p0307.fix.failed');
}
export function paintFix(box,page,ctx) {
 if(!fixOf(page))return false;
 const b=mk('button','cmdundo',t('p0307.fix.button'),'aj-reply');b.type='button';b.dataset.fix=FIX;
 const blocked=!ctx.ready()?t('p0284.repair.offline'):!ctx.supported()?t('p0307.fix.unsupported'):'';
 b.disabled=!!blocked;
 b.addEventListener('click',async()=>{
  if(!ctx.ready()||!ctx.supported()||b.dataset.busy)return;
  b.dataset.busy='1';b.disabled=true;
  try{const m=await ctx.run();if(m?.why!=='cancel')ctx.say(fixResultText(m));}
  finally{delete b.dataset.busy;b.disabled=!ctx.ready()||!ctx.supported();}
 });
 box.append(b);
 if(blocked)box.append(mk('p','small',blocked));
 return true;
}
