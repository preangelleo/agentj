// P471: real phone client, isolated encrypted fake host. No production or shared browser.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out=new URL('../../../reports/qa/p471/phone/',import.meta.url);mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 for (const language of ['zh','en']) {
  fake.reset();fake.st.prefs={appearance:{language},voice:{speak_replies:false}};
  const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  for (const [name,agent,model,max,pct] of [
   ['native','opencode','opencode/deepseek-v4.1-flash-free',80000,40],
   ['free','opencode','opencode/deepseek-v4.1-flash-free',200000,16],
   ['unknown','opencode','custom/unknown',200000,16],
   ['paid','opencode','deepseek/deepseek-v4-flash',1048576,3],
   ['claude','claude','claude-opus-5-5',100000,32],
   ['codex','codex','gpt-6.1-sol',272000,12],
  ]) {
   await fake.send({t:'status',s:'idle',agent,name:'Agent J'});
   await fake.send({t:'meter',model,ctx:{used:32000,max}});
   await waitFor(p,`document.getElementById('water').dataset.known==='1' && document.getElementById('water').dataset.lvl==='${pct}'`);
   const water=await evaluate(p,`(()=>{const w=document.getElementById('water');return {title:w.title,level:w.style.getPropertyValue('--lvl'),display:getComputedStyle(w).display};})()`);
   assert.equal(water.title,`32000 / ${max}`);assert.equal(Number(water.level),pct/100);assert.notEqual(water.display,'none');
   writeFileSync(new URL(`${name}-${language}.png`,out),await shoot(p,''));
   console.log(`PASS ${language} ${name}: visible ${pct}%`);
  }
  // Missing token measurements remain unknown; only missing capacity gets the OpenCode fallback.
  await fake.send({t:'meter',model:'custom/unknown',ctx:null});
  await waitFor(p,`document.getElementById('water').dataset.known==='0'`);
  assert.deepEqual(p.problems,[]);
 }
} finally {await B.close();await fake.stop();await web.stop();}
