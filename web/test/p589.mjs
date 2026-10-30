import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitFor,waitState,shoot,key,sleep} from './browser.mjs';
const before=process.argv.includes('before');
const out=resolve(process.env.P589_OUT || 'reports/qa/p589',before?'before':'after');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,...(before?{publicDir:'/tmp/p589-baseline/web/public'}:{})});
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
  await key(p,'Escape');ok(await ev(p,`document.getElementById('usageDlg').hidden`),'Escape closes usage');
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
