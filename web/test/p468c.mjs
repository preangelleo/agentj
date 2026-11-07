import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,shoot} from './browser.mjs';
import {orphanLineAuditSource} from '../../brand/test/orphan-line-audit.mjs';
const mode=process.env.P468C_MODE||'after',out=resolve(process.env.P468C_OUT||'reports/qa/orphan-468c-20261106/engineering'),shots=resolve(process.env.HOME,'coding/.agentj/outbox');
mkdirSync(out,{recursive:true});mkdirSync(shots,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),entries=[];let checks=0;
const ids=(process.env.P468C_IDS||'setup-faceid,set-mail-nav,set-contacts-nav,set-notify-nav').split(',');
const originalKeys={'setup-faceid':'pkp.rowNote','set-mail-nav':'mail.navNote','set-contacts-nav':'p207set.ct.navNote','set-notify-nav':'p365.navNote'};
const check=(v,msg)=>{checks++;assert.ok(v,msg);};
try{for(const width of [320,360,390,430,1440])for(const theme of ['light','dark'])for(const language of ['zh','en']){
 fake.reset();fake.setUp(true);fake.st.extraCaps=['contacts'];fake.st.prefs={appearance:{language,theme}};
 const p=await newPage(B,width,1000,theme,{touch:width<500,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await fake.send({t:'preferences',v:{appearance:{language,theme}},host:{version:'0.26.12',platform:'macos',settable:['notify.enabled']}});
 await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.open();return true})`);await evaluate(p,'document.fonts.ready.then(()=>true)');
 for(const id of ids){
 await evaluate(p,`document.getElementById('${id}').scrollIntoView({block:'center'});true`);
 const geometry=await evaluate(p,`(()=>{const e=document.querySelector('#${id} small'),b=e.getBoundingClientRect(),lines=[],walk=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);let n;while(n=walk.nextNode())for(let i=0;i<n.length;i++){const c=n.data[i];if(!c.trim())continue;const r=document.createRange();r.setStart(n,i);r.setEnd(n,i+1);const q=r.getBoundingClientRect();let l=lines.find(l=>Math.abs(l.y-(q.top+q.bottom)/2)<3);if(!l){l={y:(q.top+q.bottom)/2,text:'',left:q.left,right:q.right};lines.push(l)}l.text+=c;l.left=Math.min(l.left,q.left);l.right=Math.max(l.right,q.right)}return{visible:!e.closest('[hidden]')&&!!e.getClientRects().length,text:e.textContent,font:getComputedStyle(e).fontSize,box:{left:b.left,right:b.right},lines,overflow:e.scrollWidth>e.clientWidth+1}})()`);
 const findings=(await evaluate(p,orphanLineAuditSource)).filter(x=>x.element==='#'+id);
 if(mode!=='before'&&language==='en') {
 const gaps=await evaluate(p,`(()=>{const a=[...document.querySelectorAll('#${id} small>.setnote-part')].filter(e=>e.textContent.trim());const edge=(e,last)=>{const walk=document.createTreeWalker(e,NodeFilter.SHOW_TEXT),chars=[];let n;while(n=walk.nextNode())for(let i=0;i<n.length;i++)if(n.data[i].trim()){const r=document.createRange();r.setStart(n,i);r.setEnd(n,i+1);chars.push(r.getBoundingClientRect())}return last?chars.at(-1):chars[0]};return a.slice(1).flatMap((e,i)=>{const prev=a[i];if(!/\\s$/.test(prev.textContent)&&!/^\\s/.test(e.textContent))return[];const x=edge(prev,true),y=edge(e,false);return Math.abs(x.top-y.top)<3?[{previous:prev.textContent,next:e.textContent,gap:y.left-x.right}]:[]})})()`);
 if(width===1440&&id==='setup-faceid')check(gaps.length>0,id+' desktop measures an English group boundary');
 check(gaps.every(g=>g.gap>0),id+' visible English word gaps '+JSON.stringify(gaps));
 }
 entries.push({mode,width,theme,language,id,geometry,findings});
 if(language==='zh'&&((id==='setup-faceid'&&width===360)||(id!=='setup-faceid'&&width===390)||width===1440))writeFileSync(resolve(shots,`468c-孤字-${mode==='before'?'改前':'改后'}-${id}-${language}-${theme}-${width}.png`),await shoot(p,''));
 if(mode!=='before'){check(geometry.text===await evaluate(p,`import('./js/t.js').then(m=>m.t('${originalKeys[id]}'))`),id+' keeps complete original wording');check(geometry.visible,id+' visible');check(!findings.length,`${width}/${theme}/${language}/${id}: ${JSON.stringify(findings)}`);check(!geometry.overflow,id+' no overflow');check(geometry.lines.every(l=>l.left>=geometry.box.left-1&&l.right<=geometry.box.right+1),id+' no clipping');}
 }
 if(mode!=='before'){check(!p.problems.length,'console/CSP '+p.problems.join('|'));check(!p.offsite.length,'no offsite');}
 }finally{await p.dispose();}
}
writeFileSync(resolve(out,mode+'.json'),JSON.stringify({mode,checks,entries},null,2)+'\n');console.log(JSON.stringify({mode,checks,entries:entries.length,findings:entries.filter(e=>e.findings.length).map(({width,theme,language,id,findings})=>({width,theme,language,id,findings}))}));
}finally{await B.close();await fake.stop();await web.stop();}
