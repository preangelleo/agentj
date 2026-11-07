// Feedback #562 (not historical P562): automatic approvals are audit-only.
// Real client + encrypted fake host; no live credentials, service or owner device.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const before=process.env.FEEDBACK562_BEFORE === '1';
const out=process.env.FEEDBACK562_OUT || `reports/qa/feedback562/${before?'before':'after'}`;
mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost();
let B, checks=0;
const failures=[];
const check=(v,msg)=>{checks++;if(before){if(!v) failures.push(msg);}else assert.ok(v,msg);};
const state=`JSON.stringify({unread:document.getElementById('om').dataset.unread,badge:document.getElementById('om').getAttribute('aria-label'),corner:document.getElementById('pgCorner').dataset.unread,pages:document.getElementById('pg').textContent,sheet:document.body.dataset.sheet})`;
try {
 B=await launch();
 for(const width of [390,1440])for(const theme of ['light','dark'])for(const lang of ['zh','en']){
  fake.reset();
  fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false}};
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  const tag=`${width}-${theme}-${lang}`;
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()`);
   const dev=await evaluate(p,`import('./js/api.js').then(m=>m.myDeviceId())`);
   await fake.addTurn({k:'phone',dev,text:lang==='zh'?'检查后台活':'Check the background job'},lang==='zh'?'后台活已重新派出。':'The background job has been dispatched again.');
   await waitFor(p,`document.getElementById('pg').textContent==='1 / 1'`);
   await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);
   await sleep(200);
   const initial=await evaluate(p,state);
   await evaluate(p,`(()=>{window.feedback562Alerts={notice:0,vibrate:0};const a=window.feedback562Alerts;Object.defineProperty(navigator,'vibrate',{configurable:true,value:()=>{a.vibrate++;return true}});window.Notification=class{static permission='granted';constructor(){a.notice++}};window.feedback562Mutations=[];window.feedback562Observer=new MutationObserver(()=>{const toast=document.getElementById('toast');if(toast.classList.contains('on'))window.feedback562Mutations.push(toast.textContent);});window.feedback562Observer.observe(document.getElementById('toast'),{attributes:true,childList:true,subtree:true});})()`);
   for(const by of [undefined,'jev','goal','task','full','background']){
    await fake.send({t:'auto',id:'a'.repeat(32),tool:'Bash',summary:'重新派出后台活',by});
   }
   for(const [id,result,code] of [['b','done',0],['c','done',1],['d','timeout',undefined],['b','done',0]])
    await fake.send({t:'elev_auto',id:id.repeat(32),exe:'gzip',result,code});
   for(const why of ['limit','expired','turn_end']){
    await fake.send({t:'grant',id:'f'.repeat(32),scope:'Bash',left:1,secs:600});
    await fake.send({t:'auto',id:'a'.repeat(32),tool:'Bash',summary:'执行普通命令',grant:'f'.repeat(32)});
    await fake.send({t:'grant_end',id:'f'.repeat(32),why});
   }
   await sleep(350);
   check((await evaluate(p,'window.feedback562Mutations')).length===0,`${tag}: no transient or persistent approval notice`);
   check(await evaluate(p,`!document.getElementById('toast').classList.contains('on')`),`${tag}: toast stays hidden`);
   check(await evaluate(p,`![...document.querySelectorAll('#reply-notices > *')].some(e=>!e.hidden&&e.getClientRects().length)`),`${tag}: no reply banner`);
   check((await evaluate(p,state))===initial,`${tag}: no unread, history, sheet changes`);
   check(await evaluate(p,`window.feedback562Alerts.notice===0&&window.feedback562Alerts.vibrate===0`),`${tag}: no notification or vibration`);
   writeFileSync(`${out}/${tag}-chat.png`,await shoot(p,''));
   await evaluate(p,'window.feedback562Observer.disconnect()');
   // Suppression must not dismiss or replace a real owner-facing error.
   await evaluate(p,`import('./js/ui.js').then(m=>m.notify({kind:'error',text:'Owner action required',ms:60000}))`);
   await fake.send({t:'auto',tool:'Bash',summary:'停止后台活',by:'full'});
   await sleep(100);
   check(await evaluate(p,`document.getElementById('toast').classList.contains('on')&&document.getElementById('toast').textContent.includes('Owner action required')`),`${tag}: owner error preserved`);
   await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);
   const effect=lang==='zh'?'重新派出后台活':'Dispatch the background job again';
   fake.answer('act_list',()=>({t:'act_page',items:[{k:'auto',tool:'Bash',summary:effect,by:'level_full',ts:Date.now()}],more:false,next:null}));
   await evaluate(p,`import('./js/controls.js').then(m=>m.openPanel('act'))`);
   await waitFor(p,`document.querySelectorAll('#act-list [data-k="auto"]').length===1`);
   check(await evaluate(p,`document.getElementById('act-list').textContent.includes(${JSON.stringify(effect)})`),`${tag}: audit record remains readable`);
   writeFileSync(`${out}/${tag}-activity.png`,await shoot(p,''));
   await evaluate(p,`import('./js/controls.js').then(m=>m.openPanel('chat'))`);
   await fake.ask({id:'e'.repeat(32),tool:'Bash',summary:lang==='zh'?'需要你批准的密钥外发':'Key delivery requires your approval',ttl:120,cat:['credentials'],passkey_required:true,cred:'change'});
   await waitFor(p,`document.body.dataset.sheet==='1'`);
   check(await evaluate(p,`document.getElementById('sheet').getClientRects().length>0 && document.getElementById('apprAllowLbl').textContent!==${JSON.stringify(lang==='en'?'Approve':'批准')}`),`${tag}: real UV approval and biometric label still appear`);
   writeFileSync(`${out}/${tag}-uv.png`,await shoot(p,''));
   check(p.problems.length===0,`${tag}: no console or external network errors ${p.problems.join('|')}`);
  }finally{await p.dispose();}
 }
 writeFileSync(`${out}/result.json`,JSON.stringify({before,checks,failures},null,2));
 console.log(`${before?'BASELINE':'PASS'} feedback562 ${checks} checks, ${failures.length} baseline failures; 24 screenshots`);
}finally{if(B)await B.close();await fake.stop();await web.stop();}
