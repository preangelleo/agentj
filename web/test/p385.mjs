// Synthetic local Noise host; desktop Chromium viewport simulation, not physical iOS.
import assert from 'node:assert/strict';import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out='reports/qa/p385/ui-restart';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();let calls=0;
fake.st.onApp=async(c,m,send)=>{if(m.t!=='rescue_do')return false;assert.equal(m.action,'restart');assert.ok(await fake.verifyControl(c,m.t,m,{action:'restart'}));calls++;await send({t:'reset_done',r:m.r,flow:'restart',ok:true,step:1,text:'Service restarted.'});return true;};
try{for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const w of [390,1440]){
 fake.reset();fake.setUp(true);const p=await newPage(B,w,w===390?844:900,theme,{touch:w===390,allow:[web.url,fake.relay+'/']});
 try{await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();document.getElementById('setBtn').click();true`);
 await waitFor(p,`!!document.getElementById('rs-restart-go')`);await evaluate(p,`document.getElementById('rs-restart-go').scrollIntoView({block:'center'});true`);
 assert.equal(await evaluate(p,`document.getElementById('rs-restart-go').textContent`),lang==='zh'?'重启服务':'Restart service');
 assert.ok(await evaluate(p,`(()=>{const r=document.getElementById('rs-restart-go').getBoundingClientRect();return r.height>=44&&r.width>=44&&document.documentElement.scrollWidth<=innerWidth})()`));
 await evaluate(p,`document.getElementById('rs-restart-go').click();true`);await waitFor(p,`document.getElementById('rs-restart').dataset.state==='ok'`);
 writeFileSync(`${out}/${lang}-${theme}-${w}.png`,await shoot(p,''));console.log('PASS restart',lang,theme,w);
 }finally{await p.dispose();}
}assert.equal(calls,8);}finally{await B.close();await fake.stop();await web.stop();}
