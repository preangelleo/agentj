// Synthetic Noise-paired host; real confirmation + Ed25519 task_set action, never an OS scheduler.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep,rect,tap} from './browser.mjs';
const out=resolve(process.env.P369_OUT||'reports/qa/p369-agentj-task');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*',...(process.env.P369_PUBLIC?{publicDir:process.env.P369_PUBLIC}:{})}),fake=await startFakeHost(),B=await launch();
let checks=0,configs=0;
const check=(x,message)=>{assert.ok(x,message);checks++;};
try{
 for(const width of [390,1440])for(const theme of ['light','dark']){
  if(process.env.P369_ONLY && process.env.P369_ONLY!==`${width}-${theme}`)continue;configs++;
  fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:'en',theme},voice:{speak_replies:false,wake_enabled:false}};
  const states={daily:true,tail:false},writes=[],reads=[];
  const target=id=>({kind:'agentj-task',id,tsha:'a'.repeat(64),enabled:states[id]});
  const row=(id,t=target(id),source='agentj')=>({id,name:id,source,schedule:'0 9 * * *',mode:'research',control:{supported:true,target:t}});
  fake.st.onApp=async(c,m,send)=>{
   const reply=(t,v)=>send({t,r:m.r,...v});
   if(m.t==='jcard_list'){await reply('jcard_items',{cards:[{id:'automation',name:{en:'Automation'},on:true,icon:'calendar',accent:'magic-carpet'}]});return true;}
   if(m.t==='jcard_get'){
    const page=m.automation_page||0;reads.push(page);
    const items=page===0?[row('daily'),row('foreign',target('daily'),'external'),row('bad-sha',{...target('daily'),tsha:'bad'}),row('extra',{...target('daily'),scope:'system'}),row('bad-kind',{...target('daily'),kind:'other'}),{...row('no-control'),control:undefined},row('bad-id',{...target('daily'),id:123})]:[row('tail')];
    await reply('jcard_get',{ok:true,id:'automation',template:'automation',items,pagination:{page,total:8,page_size:7,pages:2,scan_limited:true}});return true;
   }
   if(m.t==='task_list'){await reply('tasks',{items:[]});return true;}
   if(m.t==='native_scheduler_control'){throw Error('Agent J task reached native validator');}
   if(m.t==='task_set'){
    check(await fake.verifyControl(c,m.on?'task_on':'task_off',m,{id:m.id,tsha:m.tsha}),'real Ed25519 signature binds exact task');
    check(m.id in states && m.tsha==='a'.repeat(64) && !('kind'in m) && !('enabled'in m),'only existing task_set wire fields');
    writes.push(m);states[m.id]=m.on;await reply('ctl_res',{action:m.t,ok:true});return true;
   }return false;
  };
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  const click=async sel=>{
   await evaluate(p,`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`);await sleep(100);
   const r=await rect(p,sel);check(await evaluate(p,`document.querySelector(${JSON.stringify(sel)}).contains(document.elementFromPoint(${r.x},${r.y}))`),'real hit target '+sel);
   if(width===390)await tap(p,r.x,r.y);else{await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});}
  };
  try{
   await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,"document.getElementById('phone-env-dismiss')?.click()");await evaluate(p,"import('./js/five-pages.js').then(m=>m.openTarget('automation'))");await waitFor(p,"document.querySelector('[data-item=daily]')");
   if(process.env.P369_BEFORE==='1'||process.env.P369_CAPTURE_ONLY==='1'){writeFileSync(resolve(out,`${width}-${theme}-${process.env.P369_BEFORE==='1'?'before':'after-overview'}.png`),await shoot(p));continue;}
   await evaluate(p,"window.__p369Stale=import('./js/controls.js').then(m=>m.setTask({id:'daily',tsha:'"+'a'.repeat(64)+"',schedule:'0 9 * * *'},false,'daily',()=>false));void 0");
   await waitFor(p,"!document.getElementById('confirm').hidden");await click('#confirm-yes');
   check((await evaluate(p,'window.__p369Stale')).why==='changed' && writes.length===0,'discarded row after confirmation cannot dispatch');
   check(await evaluate(p,"['foreign','bad-sha','extra','bad-kind','no-control','bad-id'].every(id=>!document.querySelector('[data-item='+id+'] button:not(.jc-auto-at)' ))"),'foreign/tampered task descriptors have no control');
   check(await evaluate(p,"document.querySelector('.jc-body').innerText.includes('8 items') && document.querySelector('.jc-body').innerText.includes('may be incomplete')"),'count and scan limit are explicit');
   await click('[data-item=daily] button:not(.jc-auto-at)');await waitFor(p,"!document.getElementById('confirm').hidden");check(writes.length===0,'confirmation before dispatch');await click('#confirm-no');await sleep(100);check(writes.length===0,'cancel never dispatches');
   for(const enabled of [false,true]){
    await click('[data-item=daily] button:not(.jc-auto-at)');await waitFor(p,"!document.getElementById('confirm').hidden");await click('#confirm-yes');
    await waitFor(p,`document.querySelector('[data-item=daily] button:not(.jc-auto-at)')?.textContent===${JSON.stringify(enabled?'Turn off':'Turn on')}`);check(states.daily===enabled,'disable/enable exact real task');await sleep(3300);
   }
   await click('.jc-body > .fp-actions button:last-child');await waitFor(p,"!!document.querySelector('[data-item=tail]')");check(reads.includes(1),'page request reaches host');
   await click('[data-item=tail] button:not(.jc-auto-at)');await waitFor(p,"!document.getElementById('confirm').hidden");await click('#confirm-yes');await waitFor(p,"document.querySelector('[data-item=tail] button:not(.jc-auto-at)')?.textContent==='Turn off'");check(reads.at(-1)===1&&states.tail,'successful action preserves current page');await sleep(3300);
   check(writes.length===3,'only three intended task writes');
   writeFileSync(resolve(out,`${width}-${theme}-after.png`),await shoot(p));
  }finally{await p.dispose();}
 }
 console.log(JSON.stringify({checks,configs,beforeOnly:process.env.P369_BEFORE==='1',out}));
}finally{await B.close();await fake.stop();await web.stop();}
