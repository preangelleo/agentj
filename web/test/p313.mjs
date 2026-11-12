// P313: two pages in one forward dialog, 55 synthetic contacts, real web client + fake Noise host.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, shoot, key} from './browser.mjs';

const before=process.env.P313_BEFORE||'';
const OUT=fileURLToPath(new URL(`../../../reports/qa/p313/${before?'before':'after'}/`,import.meta.url));
const BOOK=Array.from({length:55},(_,i)=>({id:'c'+String(i+1).padStart(13,'0'),name:'同事 '+String(i+1).padStart(2,'0'),nicks:[],emails:[{v:`colleague${i+1}@example.com`,label:''}],phones:[],letter:String.fromCharCode(65+Math.floor(i/4)),send_count:55-i}));
const J=JSON.stringify, ev=(p,s)=>evaluate(p,s), press=(p,sel)=>ev(p,`document.querySelector(${J(sel)}).click()`);
let checks=0;
const check=(ok,label)=>{assert.ok(ok,label);checks++;};
mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*',...(before?{publicDir:before}:{})});
const fake=await startFakeHost(), B=await launch();
try {
 for(const [size,w,h] of [['phone',390,844],['tablet',768,1024],['desktop',1440,900]]) for(const theme of ['light','dark']) {
  fake.reset();fake.st.caps=['contacts'];const sent=[];let saved=0;
  fake.st.onApp=async(c,m,reply)=>{
   if(m.t==='ct_list') {let list=[...BOOK,...(saved?[{...BOOK[0],id:'new',name:'新同事',emails:[{v:'new@example.com',label:''}]}]:[])];
    if(m.q) list=list.filter(x=>x.name.includes(m.q)||x.emails.some(e=>e.v.includes(m.q)));
    if(m.sort==='frequent') list.reverse();
    await reply({t:'ct_res',r:m.r,ok:true,contacts:list,count:list.length,me:['owner@example.com'],mail:{ready:true}});
   } else if(m.t==='ct_save'){saved++;await reply({t:'ct_res',r:m.r,ok:true});}
   else if(m.t==='mail_fwd'){sent.push(m);await reply({t:'mail_fwd_res',r:m.r,ok:true,sent:m.to.length});}
   else return false;
   return true;
  };
  const p=await newPage(B,w,h,theme,{touch:w<800,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await fake.addTurn({k:'phone',dev:'other',text:'整理文件'},'文件已准备好。');
   await waitFor(p,`window.AgentJMailForward?.available()`);
   await ev(p,`window.AgentJMailForward.open({turn:1,mid:'AAAAAAAAAAAAAAAAAAAAAA',name:'sample.pdf'})`);
   await waitFor(p,`document.querySelector(${J(before?'#ctFwd .ct-mode[data-mode=other]':'#ctFwd-other')}) && !document.getElementById('ctFwd-send').disabled`);
   const save=async(name)=>writeFileSync(join(OUT,`${size}-${theme}-${name}.png`),await shoot(p,''));
   await save('main');await press(p,before?'#ctFwd .ct-mode[data-mode=other]':'#ctFwd-other');
   if(before){await save('picker');continue;}
   check(await ev(p,`document.querySelectorAll('#ctFwd-list .ct-person').length===55`),'55 contacts '+size+theme);
   check(await ev(p,`document.querySelector('#ctFwd-title').textContent==='选择收件人'`),'second page title');
   check(await ev(p,`document.querySelector('#ctFwd .ct-mode')===null`),'first page not duplicated');
   const geom=await ev(p,`(()=>{let l=document.getElementById('ctFwd-list'),r=l.getBoundingClientRect(),f=document.getElementById('ctFwd-send').getBoundingClientRect();return {height:r.height,scroll:l.scrollHeight,foot:f.bottom,width:document.documentElement.scrollWidth}})()`);
   check(geom.height>120&&geom.scroll>geom.height*3&&geom.foot<=h&&geom.width<=w,`full scroll ${J(geom)}`);
   check(await ev(p,`[...document.querySelectorAll('#ctFwd button')].filter(x=>x.getClientRects().length).every(x=>x.getBoundingClientRect().height>=44)`),'touch targets');
   await save('picker');await ev(p,`document.getElementById('ctFwd-list').scrollTop=99999`);
   check(await ev(p,`document.getElementById('ctFwd-list').scrollTop>500`),'last contacts reachable');
   await press(p,'#ctFwd-list [data-addr="colleague55@example.com"]');
   await key(p,'Escape');
   check(await ev(p,`!document.getElementById('ctFwd').hidden && document.getElementById('ctFwd-other').textContent.includes('同事 55')`),'Escape back retains selection');
   await save('selected');await press(p,'#ctFwd-other');
   check(await ev(p,`document.querySelector('#ctFwd-list [data-addr="colleague55@example.com"]').getAttribute('aria-checked')==='true'`),'reopen retains selection');
   await ev(p,`(()=>{let q=document.getElementById('ctFwd-q');q.value='colleague1@';q.dispatchEvent(new Event('input'))})()`);
   await waitFor(p,`document.querySelectorAll('#ctFwd-list .ct-person').length===1`);
   await press(p,'#ctFwd-list .ct-person');await press(p,'#ctFwd-send');
   check(await ev(p,`document.getElementById('ctFwd-other').textContent.includes('已选 2 人') && !document.getElementById('ctFwd-send').disabled`),'Done returns to selected summary');
   await press(p,'#ctFwd-send');await waitFor(p,`!!document.getElementById('ctFwd-to')`);
   check(sent.length===1&&sent[0].to.length===2&&sent[0].to.some(x=>x.addr==='colleague55@example.com'),'exact recipients sent');
   check(p.problems.length===0,'no browser errors '+J(p.problems));
   if(size==='phone'&&theme==='light'){
    await key(p,'Escape');await ev(p,`window.AgentJMailForward.open({turn:1,mid:'AAAAAAAAAAAAAAAAAAAAAA',name:'sample.pdf'})`);
    await waitFor(p,`!!document.getElementById('ctFwd-other')`);await press(p,'#ctFwd-other');
    await press(p,'#ctFwd-list .ct-person');await press(p,'#ctFwd-back');
    check(await ev(p,`document.getElementById('ctFwd-other').textContent.includes('已选 1 人')`),'back button retains selection');
    await press(p,'#ctFwd-other');await press(p,'#ctFwd .ct-sorts [data-sort="name"]');
    await waitFor(p,`!!document.querySelector('#ctFwd-list .ct-letter')`);
    check(await ev(p,`document.querySelectorAll('#ctFwd-list .ct-letter').length>=10`),'A-Z groups');
    await ev(p,`(()=>{let q=document.getElementById('ctFwd-q');q.value='nobody';q.dispatchEvent(new Event('input'))})()`);
    await waitFor(p,`document.getElementById('ctFwd-add')?.open`);
    await ev(p,`document.getElementById('ctFwd-addr').value='new@example.com'`);
    await press(p,'#ctFwd-addgo');await waitFor(p,`document.querySelector('#ctFwd-list [data-addr="new@example.com"]')?.getAttribute('aria-checked')==='true'`);
    check(saved===1,'add contact in picker');
   }
  } finally {await p.dispose();}
 }
 console.log(`p313: ${checks} checks passed; screenshots ${OUT}`);
} finally {await B.close();await fake.stop();await web.stop();}
