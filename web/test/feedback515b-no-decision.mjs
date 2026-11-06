// Real product UI + signed cmd_run over local Noise fakehost; all doctor results are synthetic fixtures.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,copyFileSync,readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep,rect} from './browser.mjs';
const OUT=process.env.P515B_OUT||'reports/qa/515b-no-decision/web';mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*',...(process.env.P515B_PUBLIC_DIR?{publicDir:resolve(process.env.P515B_PUBLIC_DIR)}:{})});
const fake=await startFakeHost(),B=await launch();let checks=0;const rows=[];const check=(ok,msg)=>{assert.ok(ok,msg);checks++;};
const backup='~/.claude/.agentj-skill-backups';
const summaries=process.env.P515B_SUMMARIES?JSON.parse(readFileSync(process.env.P515B_SUMMARIES,'utf8')):{zh:`已更新 12 个内置技能，你改过的 2 个已另存备份，可在 ${backup} 找回。`,en:`Updated 12 built-in skills; your 2 modified copies were backed up and can be recovered from ${backup}.`};
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.setUp(true);fake.st.runCmds=['doctor','skill_replace_agentj-config','skill_keep_agentj-config'];fake.st.prefs={appearance:{language:lang,theme}};
  const H={calls:[]};
  fake.st.onApp=async(c,m,send)=>{
   if(m.t!=='cmd_run')return false;
   const valid=await fake.verifyControl(c,'cmd_run',m,{cmd:m.cmd});H.calls.push({cmd:m.cmd,valid});
   await send({t:'ctl_pending',r:m.r,action:'cmd_run',timeout_ms:150000});
   await send({t:'ctl_res',r:m.r,action:'cmd_run',cmd:m.cmd,ok:valid&&m.cmd==='doctor',code:0,out:summaries[lang],
    // Compatibility negative control: a stale host must not resurrect the retired decisions.
    skill_conflicts:[{name:'agentj-config',can_keep:true},{name:'agentj-update',can_keep:false}],ms:1});return true;
  };
  const p=await newPage(B,width,width===390?844:960,theme,{touch:false,allow:[web.url,fake.relay+'/']});
  const tag=`${lang}-${theme}-${width}`;
  const click=async(selector)=>{await evaluate(p,`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);await sleep(50);const r=await rect(p,selector);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});};
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.openRescue();return true})`);
   await evaluate(p,`document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();document.getElementById('rs-cmds').open=true;true`);
   check(await evaluate(p,`document.documentElement.getAttribute('data-theme')===${JSON.stringify(theme)}`),'requested theme active '+tag);
   await waitFor(p,`!!document.getElementById('rs-cmd-doctor')`);await click('#rs-cmd-doctor');await waitFor(p,`!!document.getElementById('rs-cmd-out-text')`);
   check(H.calls.length===1&&H.calls[0].valid&&H.calls[0].cmd==='doctor','real signed doctor '+tag);
   check(await evaluate(p,`document.getElementById('rs-cmd-out-text').textContent===${JSON.stringify(summaries[lang])}`),'one plain-language update/backup summary '+tag);
   check(await evaluate(p,`!document.getElementById('rs-skill-choices')&&!document.querySelector('[data-skill-action]')`),'no decisions even with legacy conflicts '+tag);
   check(await evaluate(p,`document.querySelectorAll('#rs-cmd-list [data-cmd]').length===1`),'retired actions not listed '+tag);
   for(const action of ['skill_replace_agentj-config','skill_keep_agentj-config'])await evaluate(p,`import('./js/cmdrun.js').then(m=>m.run(${JSON.stringify(action)}))`);
   check(H.calls.length===1,'retired actions cannot send '+tag);
   check(await evaluate(p,`document.getElementById('confirm').hidden`),'no decision dialog '+tag);
   await evaluate(p,`document.getElementById('rs-cmd-out').scrollIntoView({block:'center'})`);await sleep(80);
   check(await evaluate(p,`document.getElementById('set-main').scrollWidth<=document.getElementById('set-main').clientWidth+1`),'no horizontal overflow '+tag);
   const file=resolve(OUT,`${tag}.png`);writeFileSync(file,await shoot(p,''));
   if(lang==='zh'&&process.env.P515B_OUTBOX){const dest=resolve(process.env.P515B_OUTBOX,`515改后-自动更新与备份-${width===390?'手机':'电脑'}-${theme==='dark'?'暗色':'明色'}-中文.png`);copyFileSync(file,dest);}
   check(!p.problems.filter(x=>!/WebSocket|ERR_CONNECTION|net::/i.test(x)).length&&!p.offsite.length,'no errors/offsite '+tag);
   rows.push({tag,ok:true,file,theme:await evaluate(p,`document.documentElement.getAttribute('data-theme')`),settingsBackground:await evaluate(p,`getComputedStyle(document.getElementById('settings')).backgroundColor`)});console.log('PASS '+tag);
  }finally{await p.dispose();}
 }
 writeFileSync(resolve(OUT,'results.json'),JSON.stringify({fixture:true,summarySource:process.env.P515B_SUMMARIES||'synthetic UI fixture',source:resolve('web/public'),webPort:web.port,relay:fake.relay,checks,rows},null,2));console.log(`${checks} checks passed`);
}finally{await B.close();await fake.stop();await web.stop();}
