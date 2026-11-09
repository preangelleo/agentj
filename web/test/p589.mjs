import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitFor,waitState,shoot,key,sleep} from './browser.mjs';
const before=process.argv.includes('before');
const out=resolve(process.env.P589_OUT || 'reports/qa/p589',before?'before':'after');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,...(process.env.P589_PUBLIC?{publicDir:process.env.P589_PUBLIC}:before?{publicDir:'/tmp/p589-baseline/web/public'}:{})});
const fake=await startFakeHost(),B=await launch();let checks=0;
const ok=(v,m)=>{assert.ok(v,m);checks++;};
const click=(p,id)=>ev(p,`document.getElementById(${JSON.stringify(id)}).click()`);
const shot=async(p,n)=>writeFileSync(out+'/'+n+'.png',await shoot(p,''));
try{
for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const [w,h] of [[390,844],[1440,1000]]){
 fake.reset();fake.st.prefSet=true;fake.st.heartbeat=true;
 fake.st.prefs={appearance:{language:lang,theme},heartbeat:{enabled:true,interval_minutes:60,daily_at:'',quiet_enabled:true,quiet_start:'23:00',quiet_end:'07:00'}};
 fake.st.meter={model:'test',weekly_pct:20,heartbeat_usage:{available:true,input_tokens:12000,output_tokens:300,total_tokens:12300,turns:2,measured_turns:2,complete:true}};
 const p=await newPage(B,w,h,theme,{touch:w<500,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);await click(p,'setBtn');
 await ev(p,`document.getElementById('set-hb-row').scrollIntoView({block:'start'})`);await sleep(250);
 await shot(p,`${lang}-${theme}-${w}-settings`);
 if(!before){
  ok(await ev(p,`!document.getElementById('set-hb-quiet').hidden && document.getElementById('set-hb-quiet-start').value==='23:00'`),'quiet defaults');
  ok(await ev(p,`(()=>{const r=document.getElementById('set-hb-quiet-start').getBoundingClientRect();return r.height>=44&&r.left>=0&&r.right<=innerWidth})()`),'time target and viewport');
  await ev(p,`document.getElementById('set-hb-quiet-start').value='22:30';document.getElementById('set-hb-quiet-start').dispatchEvent(new Event('change'))`);
  await waitFor(p,`document.getElementById('set-hb-quiet-start').value==='22:30'`);await sleep(100);
  ok(fake.st.prefSets.some(m=>m.key==='heartbeat.quiet_start'&&m.value==='22:30'),'time preference sent');
  await click(p,'set-hb-quiet-enabled');await waitFor(p,`document.getElementById('set-hb-quiet-start').disabled`);
  ok(fake.st.prefSets.some(m=>m.key==='heartbeat.quiet_enabled'&&m.value===false),'quiet toggle saved');
 }
 await ev(p,`import('./js/settings.js').then(m=>m.close())`);await click(p,'mWeek');await waitFor(p,`!document.getElementById('usageDlg').hidden`);
 await shot(p,`${lang}-${theme}-${w}-usage`);
 if(!before){
  ok(await ev(p,`document.getElementById('usageList').textContent.includes('12,300')&&document.getElementById('usageList').textContent.includes('12,000')`),'real meter heartbeat usage survives snapshot');
  const escapeBefore=process.env.P589_DIAGNOSTIC==='1'?await ev(p,`(()=>{window.__p589keys=[];window.addEventListener('keydown',e=>window.__p589keys.push({key:e.key,code:e.code,trusted:e.isTrusted,composing:e.isComposing,focus:document.hasFocus()}),true);const n=document.getElementById('toast');return{toastClass:n.className,toastText:n.textContent}})()`):null;
  await key(p,'Escape');const usageClosed=await ev(p,`document.getElementById('usageDlg').hidden`);
  if(process.env.P589_DIAGNOSTIC==='1'){
   const capture=async label=>({label,usageClosed,escapeBefore,live:await ev(p,`(async()=>{const l=await import('./js/layers.js'),a=l.activeLayer();return {usageHidden:document.getElementById('usageDlg').hidden,activeLayer:a?.element()?.id,focus:document.activeElement?.id,pageFocus:document.hasFocus(),keys:window.__p589keys,toastClass:document.getElementById('toast').className,dialogs:[...document.querySelectorAll('[role=dialog],dialog,.ajmodal,#confirm')].filter(n=>!n.hidden&&(n.open||n.getClientRects().length)).map(n=>({id:n.id,covered:n.classList.contains('aj-layer-covered'),popup:n.dataset.popupSystem,card:n.dataset.card,tag:n.tagName})),keysHidden:document.getElementById('keys').hidden}})()`)});
   const diag=[await capture('after-original-check')];await sleep(150);diag.push(await capture('150ms'));await sleep(850);diag.push(await capture('1000ms'));if(!usageClosed){await key(p,'Escape');diag.push(await capture('second-Escape'));}writeFileSync(out+'/escape-diagnostic.json',JSON.stringify(diag,null,2));
  }
  ok(usageClosed,'Escape closes usage');
  await fake.send({t:'meter',model:'test',heartbeat_usage:{available:true,input_tokens:12000,output_tokens:300,total_tokens:12300,turns:3,measured_turns:2,complete:false}});await sleep(150);await click(p,'mWeek');
  ok(await ev(p,`document.getElementById('usageList').textContent.includes(${JSON.stringify(lang==='zh'?'总量不完整':'Total is incomplete')})&&document.getElementById('usageList').textContent.includes(${JSON.stringify(lang==='zh'?'2/3':'2 of 3')})`),'mixed measured and unknown usage marked incomplete');
  await shot(p,`${lang}-${theme}-${w}-usage-partial`);await key(p,'Escape');
  await fake.send({t:'meter',model:'test',heartbeat_usage:{available:false,input_tokens:0,output_tokens:0,total_tokens:0,turns:0}});await sleep(150);await click(p,'mWeek');
  ok(await ev(p,`document.getElementById('usageList').textContent.includes(${JSON.stringify(lang==='zh'?'未提供':'does not report')})`),'unknown usage never fabricates zero');
 }
 await p.close();
}
console.log(`P589 ${before?'baseline':checks+' checks PASS'} → ${out}`);
}finally{await B.close();await fake.stop();await web.stop();}
// Keep the existing phase's original heartbeat/usage assertions and add its
// Escape-ownership regression only after the first browser/transport is closed.
if(!before){process.env.P0300_ESCAPE_OUT ||= resolve(out,'escape-ownership');await import('./p0300-escape.mjs');}
