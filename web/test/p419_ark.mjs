// Local Noise + real Chromium; no upstream provider, production or physical-device claims.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p419_ark.mjs');let passed=false;
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,rect,tap} from './browser.mjs';
const out='reports/qa/precheck/p419/ark-browser';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const action='ark_fix_'+'a'.repeat(24);let offer=null,current=null,calls=0,pendingModel=null,modePending=false;
fake.st.onApp=async(c,m,send)=>{
 if(m.t==='ark_plan_get'){if(pendingModel){current=pendingModel;pendingModel=null;}await send({t:'ark_plan',r:m.r,offer,current,outcome:null});return true;}
 if(m.t!=='rescue_do')return false;
 assert.equal(m.action,action);assert.ok(await fake.verifyControl(c,m.t,m,{action}));calls++;
 if(modePending)pendingModel=offer.model;else current=offer.model;offer=null;
 await send({t:'reset_step',r:m.r,flow:action,step:1,state:'run',text:'Checking plan…'});
 await send({t:'reset_done',r:m.r,flow:action,ok:true,step:1,text:modePending?'Switching…':'Plan corrected.',pending:modePending});return true;
};
try{for(const plan of ['agent','coding'])for(const lang of ['zh','en'])for(const w of [390,1440]){
 fake.reset();fake.setUp(true);current=null;pendingModel=null;modePending=w===1440;offer={action,plan,model:(plan==='agent'?'ark-agent-plan/':'ark-coding-plan/')+'doubao-seed-2.1-pro'};
 const theme=w===390?'light':'dark';const p=await newPage(B,w,w===390?844:900,theme,{touch:w===390,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();true`);
  await waitFor(p,`!!document.getElementById('ark-plan-fix-go') && !document.getElementById('ark-plan-banner').hidden`);
  const label=lang==='zh'?`改用 ${plan==='agent'?'Agent':'Coding'} Plan 并重试`:`Use ${plan==='agent'?'Agent':'Coding'} Plan and retry`;
  assert.equal(await evaluate(p,`document.getElementById('ark-plan-fix-go').textContent`),label);
  assert.ok(await evaluate(p,`(()=>{const r=document.getElementById('ark-plan-fix-go').getBoundingClientRect();return r.height>=44&&document.documentElement.scrollWidth<=innerWidth})()`));
  const id=w===390?'ark-plan-fix-go':'rs-ark-fix-go';
  if(w===1440){await evaluate(p,`document.getElementById('setBtn').click();true`);await waitFor(p,`!document.getElementById('settings').hidden`);await evaluate(p,`document.getElementById('rs-ark-plan').scrollIntoView({block:'center'});true`);}
  const r=await rect(p,'#'+id);
  if(w===390)await tap(p,r.x,r.y);else{await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});}
  await waitFor(p,w===390?`document.getElementById('ark-plan-banner').textContent.includes('Plan corrected.')`:`document.getElementById('rs-ark-plan').textContent.includes(${JSON.stringify(lang==='zh'?'已改用火山':'Now using Volcano')})`);
  assert.equal(await evaluate(p,`document.querySelectorAll('#ark-plan-banner input,#rs-ark-plan input').length`),0);
  writeFileSync(`${out}/${plan}-${lang}-${w}.png`,await shoot(p,''));console.log('PASS',plan,lang,w);
 }finally{await p.dispose();}
}assert.equal(calls,8);passed=true;}finally{rec.record('both-plan-directions',passed);rec.write();await B.close();await fake.stop();await web.stop();}
