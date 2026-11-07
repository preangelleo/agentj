// One existing reply-action slot for all pending approvals. Never an execution control.
import { SVG } from './icons.js';
import { t } from './t.js';
const entries=new Map();
let anchor;
export function setApprovalAnchor(key,value){
 if(value?.count>0)entries.set(key,value);else entries.delete(key);
 paint();
}
export function refreshApprovalAnchor(){paint();}
function paint(){
 const original=document.getElementById('webReportBtn');if(!original)return;
 if(!anchor){anchor=original.cloneNode(false);anchor.id='pending541';anchor.setAttribute('data-aj-owner','aj-reply-action');anchor.removeAttribute('aria-haspopup');const svg=document.createElementNS(SVG,'svg');svg.setAttribute('viewBox','0 0 36 36');svg.setAttribute('aria-hidden','true');for(const [cls,r]of [['disc',18],['halo541',5],['heart541',4]]){const circle=document.createElementNS(svg.namespaceURI,'circle');circle.setAttribute('class',cls);for(const [key,v]of [['cx',18],['cy',18],['r',r]])circle.setAttribute(key,String(v));svg.append(circle);}const badge=document.createElement('span');badge.className='count541';anchor.append(svg,badge);original.after(anchor);}
 const count=[...entries.values()].reduce((n,e)=>n+e.count,0),target=[...entries.values()].find(e=>e.collapsed),expanded=[...entries.values()].some(e=>e.expanded);
 original.hidden=count>0;anchor.hidden=!count||!target||expanded;anchor.disabled=false;
 anchor.setAttribute('aria-label',t('p541.pending',{n:count}));anchor.title=t('p541.pending',{n:count});anchor.querySelector('.count541').hidden=count<2;anchor.querySelector('.count541').textContent=String(count);
 // @ix appr.heartbeat
 anchor.onclick=()=>target?.open?.();
 if(count&&document.getElementById('pendTag'))document.getElementById('pendTag').hidden=true;
}
