// #511: real paired client, reversible fake host, EN/ZH and phone/desktop.
import assert from 'node:assert/strict';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch,newPage,evaluate,navigate,waitFor,waitState } from './browser.mjs';
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
let checks=0;
const ev=(p,s)=>evaluate(p,s),click=(p,id)=>ev(p,`document.getElementById('${id}').click()`);
try {
 for(const lang of ['zh','en']) for(const w of [390,1440]) {
  fake.reset();fake.st.prefs={appearance:{language:lang},agent:{isolation:true,session_mode:'independent',high_risk_warnings:false,allow_docker:false},approval:{goal_auto:true,jev_threshold:0.7},quota:{auto_switch:false}};
  const p=await newPage(B,w,900,'dark',{allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await click(p,'setBtn');await waitFor(p,"!document.getElementById('settings').hidden");
   assert.equal(await ev(p,"document.querySelectorAll('#set-risk-cmd,#set-jev-cmd,#set-goal-cmd,#set-qswitch-cmd,#set-mode-cmd,#set-iso-cmd,#set-docker-cmd').length"),0);checks++;
   for(const [id,k,v] of [['set-risk','agent.high_risk_warnings',true],['set-goal','approval.goal_auto',false],['set-qswitch','quota.auto_switch',true],['set-mode','agent.session_mode','shared'],['set-docker','agent.allow_docker',true],['set-iso','agent.isolation',false]]) {
    await click(p,id);
    // The fake host records the actual pref_set, not a DOM-only optimistic value.
    for(let n=0;n<80 && fake.st.prefSets.at(-1)?.key!==k;n++) await new Promise(r=>setTimeout(r,25));
    assert.deepEqual([fake.st.prefSets.at(-1)?.key,fake.st.prefSets.at(-1)?.value],[k,v],`${lang}/${w}/${id}`);checks++;
   }
   // Jev predates the generic fake-host enum; advertise and answer this existing host key explicitly.
   const prefs=fake.prefsMsg();prefs.host.settable.push('approval.jev_threshold');await fake.send(prefs);
   fake.st.onApp=async(c,m,send)=>{if(m.t!=='pref_set'||m.key!=='approval.jev_threshold')return false;fake.st.prefSets.push(m);fake.st.prefs.approval.jev_threshold=m.value;await send({t:'pref_res',r:m.r,ok:true,key:m.key});return true;};
   await ev(p, `import('./js/settings.js').then(m=>{m.render();return true})`);await click(p,'set-jev-minus');
   for(let n=0;n<80 && fake.st.prefSets.at(-1)?.key!=='approval.jev_threshold';n++) await new Promise(r=>setTimeout(r,25));
   assert.deepEqual([fake.st.prefSets.at(-1)?.key,fake.st.prefSets.at(-1)?.value],['approval.jev_threshold',0.65]);checks++;
   const terminal=await ev(p,`import('./js/termhook.js').then(m=>{m.render({terminal:{on:false,os:true,me:false,kind:'desktop',pk:true}});return {hidden:document.getElementById('set-term-cmdline').hidden,text:document.getElementById('set-term-cmd').textContent,enable:document.getElementById('set-term-en').hidden}})`);
   assert.deepEqual(terminal,{hidden:true,text:'',enable:false});checks++;
   const legacy=await ev(p,`import('./js/termhook.js').then(m=>{m.render({terminal:{on:false,os:true,me:false,kind:'unknown',pk:true}});return {hidden:document.getElementById('set-term-cmdline').hidden,text:document.getElementById('set-term-cmd').textContent}})`);
   assert.deepEqual(legacy,{hidden:false,text:'agentj terminal enable'});checks++;
   assert.deepEqual(p.problems,[]);console.log('PASS',lang,w);
  }finally{await p.dispose();}
 }
 console.log(`PASS ${checks} assertions`);
}finally{await B.close();await fake.stop();await web.stop();}
