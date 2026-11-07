// Only Host-generated system metadata offers the existing allowlisted repair control.
import {mk} from './ui.js';
import {t} from './t.js';
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
 more.append(label,mk('code','','agentj doctor --reconnect'));box.append(more);return true;
}
