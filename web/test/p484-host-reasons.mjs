// UI-only regression: real paired Noise and signed reset controls, synthetic Host results.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const out=process.env.OLARES_RUN_OUT||'reports/qa/p484-host-reasons';mkdirSync(out,{recursive:true});
const reasons=['quota','desktop_live','foreign','noack','not_stopped','failed','busy','offline'];
const hostSays=new Set(reasons.slice(0,5));let checks=0,signed=0;const rows=[];
let web,fake,B,reason='',message='';
try {
 fake=await startFakeHost();web=await startWebServer({port:0,relayCsp:fake.relay});B=await launch();
 fake.st.onApp=async(c,m,send)=>{
  if(m.t!=='reset')return false;
  assert.equal(await fake.verifyControl(c,'reset',m,{what:'connection'}),true);signed++;
  await send({t:'reset_done',r:m.r,flow:'reset',ok:false,why:reason,text:message});return true;
 };
 for(const width of[390,1440])for(const lang of['zh','en'])for(const theme of['light','dark']){
  fake.reset();fake.setUp(true);fake.st.prefs={appearance:{theme,language:lang}};
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay]});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,"document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();true");
   for(const why of reasons){
    reason=why;message=(lang==='zh'?'主机确认的原因：':'Host-confirmed reason: ')+why+' <b id="rescue-injection">literal</b>';
    await evaluate(p,"import('./js/rescue.js').then(m=>{m._reset();m.resetFromCommand();return true})");
    await waitFor(p,"document.getElementById('rs-reset')?.dataset.state==='fail'");
    const view=await evaluate(p,"import('./js/rescue.js').then(m=>({state:m._debug(),headline:document.getElementById('rs-reset-msg')?.textContent,highlight:document.getElementById('rs-help')?.hasAttribute('data-hl'),injected:!!document.getElementById('rescue-injection')}))");
    assert.equal(view.state.reset.why,why);checks++;
    assert.equal(view.state.failedOnce,['not_stopped','failed'].includes(why));checks++;
    assert.equal(view.highlight,['not_stopped','failed'].includes(why));checks++;
    if(hostSays.has(why)){assert.equal(view.headline,message);checks++;}
    assert.equal(view.injected,false);checks++;
    rows.push({width,lang,theme,reason:why,passed:true});
    if(why==='not_stopped')writeFileSync(resolve(out,`${width}-${lang}-${theme}.png`),await shoot(p));
   }
  } finally {await p.dispose();}
 }
 assert.equal(signed,rows.length);checks++;
 writeFileSync(resolve(out,'result.json'),JSON.stringify({checks,signed,cases:rows.length,rows},null,2));console.log(JSON.stringify({checks,signed,cases:rows.length,result:'PASS'}));
} finally {await Promise.allSettled([B?.close(),fake?.stop(),web?.stop()]);}
