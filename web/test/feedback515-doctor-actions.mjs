import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep,rect} from './browser.mjs';
const OUT=process.env.P515_OUT||'reports/qa/skill-shadow-515-20261106/doctor-actions-web';mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*',...(process.env.P515_PUBLIC_DIR?{publicDir:resolve(process.env.P515_PUBLIC_DIR)}:{})});
const fake=await startFakeHost(),B=await launch();let checks=0;const rows=[];const check=(ok,msg)=>{assert.ok(ok,msg);checks++;};
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.setUp(true);fake.st.runCmds=['doctor'];fake.st.prefs={appearance:{language:lang,theme}};
  const H={calls:[],executed:[],conflicts:[{name:'agentj-config',can_keep:true},{name:'agentj-update',can_keep:true},{name:'agentj-add-memory',can_keep:false},{name:'../../unsafe',can_keep:true},{name:['agentj-browser'],can_keep:true},{name:42,can_keep:true}]};
  fake.st.onApp=async(c,m,send)=>{
   if(m.t!=='cmd_run')return false;
   const valid=await fake.verifyControl(c,'cmd_run',m,{cmd:m.cmd});H.calls.push({cmd:m.cmd,valid});
   if(!valid){await send({t:'ctl_res',r:m.r,action:'cmd_run',ok:false,why:'bad_signature'});return true;}
   const a=/^skill_(replace|keep)_(agentj-config|agentj-update|agentj-add-memory)$/.exec(m.cmd);
   if(m.cmd!=='doctor'&&!a){await send({t:'ctl_res',r:m.r,action:'cmd_run',ok:false,why:'shape'});return true;}
   H.executed.push(m.cmd);
   if(a)H.conflicts=H.conflicts.filter(r=>r.name!==a[2]);
   await send({t:'ctl_pending',r:m.r,action:'cmd_run',timeout_ms:150000});
   await send({t:'ctl_res',r:m.r,action:'cmd_run',cmd:m.cmd,ok:true,code:0,out:'Skills checked. <b>plain text</b>',skill_conflicts:H.conflicts,ms:1});return true;
  };
  const p=await newPage(B,width,width===390?844:960,theme,{touch:false,allow:[web.url,fake.relay+'/']});
  const tag=`${lang}-${theme}-${width}`;
  const click=async(selector)=>{await evaluate(p,`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);await sleep(50);const r=await rect(p,selector);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});};
  const choice=(n,a)=>`[data-skill="${n}"] [data-skill-action="${a}"]`;
  const save=async(label)=>writeFileSync(resolve(OUT,`${tag}-${label}.png`),await shoot(p,''));
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.openRescue();return true})`);
   await evaluate(p,`document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();document.getElementById('rs-cmds').open=true;true`);
   await waitFor(p,`!!document.getElementById('rs-cmd-doctor')`);await click('#rs-cmd-doctor');await waitFor(p,`!!document.getElementById('rs-cmd-out-text')`);
   check(H.executed.join()==='doctor'&&H.calls.every(c=>c.valid),'real signed doctor '+tag);
   if(process.env.P515_BEFORE){await save('before');check(await evaluate(p,`!!document.getElementById('rs-skill-choices')`),'baseline lacks requested clickable choices');}
   await waitFor(p,`!!document.getElementById('rs-skill-choices')`);
   check(await evaluate(p,`document.querySelectorAll('#rs-skill-choices [data-skill]').length===3`),'only safe names render '+tag);
   check(await evaluate(p,`document.querySelectorAll('#rs-cmd-list [data-cmd]').length===1`),'no skill actions added to general command list '+tag);
   check(await evaluate(p,`document.querySelector(${JSON.stringify(choice('agentj-add-memory','keep'))}).disabled`),'unreadable keep disabled '+tag);
   check(await evaluate(p,`document.getElementById('rs-skill-choices').textContent.includes(${JSON.stringify(lang==='zh'?'暂时无法读取':'cannot be read')})`),'unreadable reason visible '+tag);
   const audit=await evaluate(p,`(()=>{const x=document.getElementById('rs-skill-choices');return {small:[...x.querySelectorAll('button')].filter(b=>{const r=b.getBoundingClientRect();return r.width<44||r.height<44}).length,over:document.getElementById('set-main').scrollWidth>document.getElementById('set-main').clientWidth+1}})()`);
   check(!audit.small&&!audit.over,'44px targets/no overflow '+tag);
   await evaluate(p,`document.getElementById('rs-skill-choices').scrollIntoView({block:'center'})`);await save('choices');
   let count=H.calls.length;
   await evaluate(p,`import('./js/cmdrun.js').then(m=>m.run('skill_replace_agentj-unknown'))`);check(H.calls.length===count,'unknown skill not sent '+tag);
   await click(choice('agentj-config','keep'));await waitFor(p,`!document.getElementById('confirm').hidden`);check(H.calls.length===count,'no write before confirm '+tag);
   await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape'});await waitFor(p,`document.getElementById('confirm').hidden`);check(H.calls.length===count,'Esc cancels keep '+tag);
   await click(choice('agentj-config','keep'));await waitFor(p,`!document.getElementById('confirm').hidden`);await click('#confirm-yes');await waitFor(p,`document.querySelectorAll('#rs-skill-choices [data-skill]').length===2 && !!document.querySelector('[data-skill="agentj-update"] [data-skill-action="replace"]') && !document.querySelector('[data-skill="agentj-config"]') && !!document.getElementById('rs-cmd-out-text')`);check(H.executed.includes('skill_keep_agentj-config'),'keep actual click sends signed fixed name '+tag);
   await click(choice('agentj-update','replace'));await waitFor(p,`!document.getElementById('confirm').hidden`);check(await evaluate(p,`document.getElementById('confirm-text').textContent.includes(${JSON.stringify(lang==='zh'?'备份':'backed up')})`),'replace explains backup '+tag);await save('replace-confirm');
   await click('#confirm-yes');await waitFor(p,`document.querySelectorAll('#rs-skill-choices [data-skill]').length===1 && !document.querySelector('[data-skill="agentj-update"]') && !!document.getElementById('rs-cmd-out-text')`);check(H.executed.includes('skill_replace_agentj-update')&&H.calls.every(c=>c.valid),'replace actual click fixed signed name '+tag);
   check(await evaluate(p,`document.querySelectorAll('#rs-skill-choices [data-skill]').length===1`),'result list refreshed '+tag);
   await save('result');check(!p.problems.filter(x=>!/WebSocket|ERR_CONNECTION|net::/i.test(x)).length&&!p.offsite.length,'no errors/offsite '+tag);
   rows.push({tag,checks,ok:true});console.log('PASS '+tag);
  }finally{await p.dispose();}
 }
 writeFileSync(resolve(OUT,'results.json'),JSON.stringify({checks,rows},null,2));console.log(`${checks} checks passed`);
}finally{await B.close();await fake.stop();await web.stop();}
