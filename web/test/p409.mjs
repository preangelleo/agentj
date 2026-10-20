// P409: real Chromium, local fake host, signed settings; no provider calls.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitFor,waitState,shoot,sleep} from './browser.mjs';
const out='reports/qa/p409/';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
let checks=0;
const click=(p,id)=>ev(p,`document.getElementById(${JSON.stringify(id)}).click()`);
try {
 for(const lang of ['zh','en'])for(const width of [390,1440])for(const theme of ['light','dark']){
  fake.reset();fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},jev:{mode:'openrouter'}};
  const p=await newPage(B,width,900,theme,{touch:width<500,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+(lang==='en'?'?lang=en':'')));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await click(p,'setBtn');await waitFor(p,`!document.getElementById('settings').hidden`);
   await ev(p,`document.getElementById('set-h-safety').scrollIntoView({block:'start'})`);await sleep(200);
   assert.equal(await ev(p,`document.getElementById('set-jev-mode-row').hidden`),false);checks++;
   assert.equal(await ev(p,`document.getElementById('set-jev-enabled').getAttribute('aria-checked')`),'true');checks++;
   assert.match(await ev(p,`document.getElementById('set-jev-current').textContent`),/OpenRouter/);checks++;
   await click(p,'set-jev-enabled');await waitFor(p,`!document.getElementById('confirm').hidden`);
   assert.equal(fake.st.approvalSets.length,0);checks++;
   assert.match(await ev(p,`document.getElementById('confirm-yes').textContent`),lang==='en'?/weaker/:/安全性会弱/);checks++;
   await click(p,'confirm-no');await sleep(150);assert.equal(fake.st.approvalSets.length,0);checks++;
   await click(p,'set-jev-enabled');await waitFor(p,`!document.getElementById('confirm').hidden`);await click(p,'confirm-yes');
   await waitFor(p,`document.getElementById('set-jev-enabled').getAttribute('aria-checked')==='false'`);
   assert.deepEqual(fake.st.approvalSets.map(x=>[x.key,x.value,x.sigOk]),[['jev.mode','off',true]]);checks++;
   await ev(p,`document.getElementById('set-jev-mode-row').scrollIntoView({block:'start'})`);await sleep(2200);
   writeFileSync(out+`settings-off-${lang}-${width}-${theme}.png`,await shoot(p,''));
   await click(p,'set-jev-enabled');await waitFor(p,`!document.getElementById('confirm').hidden`);await click(p,'confirm-yes');
   await waitFor(p,`document.getElementById('set-jev-enabled').getAttribute('aria-checked')==='true'`);
   assert.equal(fake.st.approvalSets.at(-1).value,'typesafe');checks++;
   await ev(p,`const s=document.getElementById('set-jev-source');s.value='openrouter';s.dispatchEvent(new Event('change'))`);
   await waitFor(p,`!document.getElementById('confirm').hidden`);await click(p,'confirm-yes');await sleep(200);
   assert.equal(fake.st.approvalSets.at(-1).value,'openrouter');checks++;
   assert.equal(await ev(p,`document.documentElement.scrollWidth>innerWidth`),false);checks++;
   await sleep(2200);
   writeFileSync(out+`settings-on-${lang}-${width}-${theme}.png`,await shoot(p,''));
  }finally{await p.dispose();}
 }
 writeFileSync(out+'browser.json',JSON.stringify({checks,views:8,upstreams:'fake only',result:'PASS'},null,2));console.log(`P409 Chromium: ${checks} checks PASS, 16 screenshots`);
}finally{await B.close();await fake.stop();await web.stop();}
