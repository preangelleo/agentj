import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {evaluate} from './browser.mjs';
import {measurePage} from './p0285-rework-c-dom.mjs';
export async function capture(p,meta){
  const scene=meta.scene,config={scene};
  if(scene==='585-repair')config.pairs=[['#set-s-rescue>.setnote','#rs-repair',16]],config.groups=[['#rs-repair',8],['#rs-repair .rsacts',12]];
  if(scene.startsWith('ssh'))config.groups=[['#owner-hosts-list',12],['#owner-hosts-list .owner-hosts__item:not([data-ssh-group])',12]];
  if(scene==='menu')config.groups=[['#five-menu .fp-menu-card',8]];
  const result=await evaluate(p,`(${measurePage.toString()})(${JSON.stringify(config)})`);
  // Component-specific C boundaries: textual SSH summary and reader button runs.
  const extra=await evaluate(p,`(()=>{const rows=[],v=n=>!!n&&!n.closest('[hidden]')&&n.getBoundingClientRect().width>0,add=(name,pass,detail)=>rows.push({gate:1,name,pass,detail});
    if(${JSON.stringify(scene)}.startsWith('ssh')){const other=document.querySelector('.owner-hosts__others');if(v(other)){const prev=other.previousElementSibling;if(v(prev)){const a=prev.getBoundingClientRect(),b=other.getBoundingClientRect();add('SSH other hosts margin',b.top-a.bottom>=7.4,{value:b.top-a.bottom,min:8});}}for(const n of document.querySelectorAll('#owner-hosts-list button')){if(!v(n))continue;const r=document.createRange();r.selectNodeContents(n);const rs=[...r.getClientRects()].filter(x=>x.width>0),box=n.getBoundingClientRect(),lh=parseFloat(getComputedStyle(n).lineHeight)||20,lines=rs.length?1+Math.round((Math.max(...rs.map(x=>x.y))-Math.min(...rs.map(x=>x.y)))/lh):0;add('SSH long button single line',lines<=1,{lines,text:n.textContent.trim()});add('SSH button text not clipped',n.scrollWidth<=n.clientWidth+1&&rs.every(x=>x.left>=box.left-.5&&x.right<=box.right+.5),{scrollWidth:n.scrollWidth,clientWidth:n.clientWidth,box:{left:box.left,right:box.right},textRects:rs.map(x=>({left:x.left,right:x.right}))});}}
    if(${JSON.stringify(scene)}==='reader'){const n=document.querySelector('#rdWords'),bs=[...n.querySelectorAll('button.rbtn')];add('two actual reply buttons covered',bs.length===2,{count:bs.length});for(let i=1;i<bs.length;i++){const a=bs[i-1].getBoundingClientRect(),b=bs[i].getBoundingClientRect(),gap=Math.hypot(Math.max(a.left-b.right,b.left-a.right,0),Math.max(a.top-b.bottom,b.top-a.bottom,0));add('reader peer reply margin',gap>=11.4,{value:gap,min:12});let s=bs[i-1].nextSibling,text='';while(s&&s!==bs[i]){text+=s.textContent;s=s.nextSibling;}add('no punctuation between reply buttons',!text.trim(),{text});}}
    if(${JSON.stringify(scene)}==='menu'){for(const key of ['workflows','skills']){const n=document.querySelector('#five-menu [data-page="'+key+'"] .fp-current');rows.push({gate:1,name:'menu count '+key,pass:!!n&&!!n.textContent.trim(),detail:{text:n?.textContent}});}}
    return rows;})()`);
  result.checks.push(...extra);const dir=join(meta.out,'gates');mkdirSync(dir,{recursive:true});
  writeFileSync(join(dir,`c-${meta.phase}-${scene}-${meta.width}-${meta.lang}-${meta.theme}.json`),JSON.stringify({...meta,...result},null,2));
  return result;
}
