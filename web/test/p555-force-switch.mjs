// #555: real encrypted fake host, signed switch requests, registry additions without web changes.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot, rect, sleep} from './browser.mjs';
const out = process.env.P555_FORCE_OUT || 'reports/qa/force-switch-555-20261106/web';
mkdirSync(out, {recursive:true});
const web = await startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'}), fake = await startFakeHost(), B = await launch();
// A synthetic future registration proves that the browser has no parallel harness list.
const registry = [{name:'claude',label:'Claude Code'}, {name:'codex',label:'Codex'}, {name:'opencode',label:'OpenCode'},
 {name:'kimi',label:'Kimi'}, {name:'qwen',label:'Qwen Code'}, {name:'dsh',label:'DSH'}, {name:'future_ai',label:'Future AI'}];
const names = ['doctor','status', ...registry.map(h=>'switch_'+h.name)];
let checks=0, calls=[], release;
const check=(x,n)=>{assert.ok(x,n);checks++;};
try {
 for(const lang of ['zh','en']) for(const theme of ['light','dark']) for(const width of [390,1440]) {
  fake.reset(); fake.setUp(true); fake.st.runCmds=names; fake.st.prefs={appearance:{language:lang,theme}};
  fake.st.onApp=async(c,m,send)=>{
   if(m.t!=='cmd_run')return false;
   check(await fake.verifyControl(c,'cmd_run',m,{cmd:m.cmd}),'device signature verified');calls.push(m.cmd);
   await send({t:'ctl_pending',r:m.r,action:'cmd_run',timeout_ms:150000});
   await new Promise(r=>release=r);
   await send({t:'ctl_res',r:m.r,action:'cmd_run',ok:true,code:0,out:'Synthetic command completed.'});return true;
  };
  const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click(); if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click(); import('./js/settings.js').then(m=>m.applyThemeLocal('${theme}'))`);
   await fake.send({t:'preferences',value:{appearance:{language:lang,theme}},host:{run_cmds:[...names,'rm_rf','switch_x;bad'],run_harnesses:registry}});
   const open=async()=>{await evaluate(p,`import('./js/settings.js').then(m=>{m.openRescue();document.getElementById('rs-cmds').open=true;})`);await waitFor(p,`!document.getElementById('settings').hidden`);};
   const click=async(id)=>{await evaluate(p,`document.getElementById('${id}').scrollIntoView({block:'center'})`);const r=await rect(p,'#'+id);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});};
   await open();
   const listed=await evaluate(p,`[...document.querySelectorAll('#rs-cmd-list li')].map(x=>x.dataset.cmd)`);
   assert.deepEqual(listed,names);checks++;
   check(await evaluate(p,`document.querySelector('[data-cmd="switch_future_ai"]').textContent.includes('Future AI')`),'future registry label shown');
   check(await evaluate(p,`document.querySelector('[data-cmd="switch_kimi"] small').textContent.includes(${JSON.stringify(lang==='zh'?'回到聊天':'return to chat')})`),'current language explanation');
   writeFileSync(out+'/'+[lang,theme,width,'list'].join('-')+'.png',await shoot(p,''));
   for(const h of registry) {
    const count=calls.length;release=null;await click('rs-cmd-switch_'+h.name);
    await waitFor(p,`document.getElementById('settings').hidden && document.body.dataset.view==='chat'`);
    for(let i=0;i<100&&(calls.length===count||!release);i++)await sleep(20);
    check(calls.length===count+1&&calls.at(-1)==='switch_'+h.name,'one signed switch and immediate chat '+h.name);
    release();await waitFor(p,`import('./js/cmdrun.js').then(m=>m._debug().R?.st==='done')`);
    await open();
   }
   for(const cmd of ['doctor','status']) {
    const before=calls.length;release=null;await click('rs-cmd-'+cmd);await waitFor(p,`import('./js/cmdrun.js').then(m=>m._debug().R?.st==='ask')`);
    for(let i=0;i<100&&(calls.length===before||!release);i++)await sleep(20);
    check(await evaluate(p,`!document.getElementById('settings').hidden`),'diagnostic stays in settings '+cmd);
    release();await waitFor(p,`import('./js/cmdrun.js').then(m=>m._debug().R?.st==='done')`);
   }
   check(await evaluate(p,`[...document.querySelectorAll('.rscmdgo svg')].every(s=>s.getBoundingClientRect().width>=18)`),'toolbar glyphs have visible width');
   check(await evaluate(p,`[...document.querySelectorAll('.rscmdgo')].every(b=>b.classList.contains('aj-btn')&&b.classList.contains('aj-btn--quiet')&&b.getBoundingClientRect().height>=44)`),'542 classes and touch height');
   check(await evaluate(p,`document.getElementById('set-main').scrollWidth<=document.getElementById('set-main').clientWidth+1`),'no horizontal overflow');
   release=null;await click('rs-cmd-switch_kimi');await waitFor(p,`document.getElementById('settings').hidden`);
   writeFileSync(out+'/'+[lang,theme,width,'chat'].join('-')+'.png',await shoot(p,''));for(let i=0;i<100&&!release;i++)await sleep(20);release();
   await waitFor(p,`import('./js/cmdrun.js').then(m=>m._debug().R?.st==='done')`);
   check(!p.problems.length,'no runtime/console errors');
  } finally {release?.();await p.dispose();}
 }
 writeFileSync(out+'/result.json',JSON.stringify({checks,calls,ok:true},null,2));console.log('#555 force switch PASS '+checks+' checks');
} finally {release?.();await B.close();await fake.stop();await web.stop();}
