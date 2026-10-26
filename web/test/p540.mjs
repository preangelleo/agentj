// P540 real Chromium + paired fake host: the WeChat panel shows the host's last avatar outcome (fixed words only).
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const out=process.env.P540_SHOTS||'reports/qa/p540/screens';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const checks=[];
const fail={ok:false,error:'harness',detail:'quota',kind:'codex',at:1761450000};
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();
  let state={t:'wechat_state',bound:true,pending:0,assistant:fail};
  fake.st.onApp=async(c,m,send)=>{if(m.t!=='wechat')return false;if(m.op==='status')await send(state);return true;};
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.documentElement.dataset.theme='${theme}';true`);
   await evaluate(p,`import('./js/wechat.js').then(m=>m.show())`);
   await waitFor(p,`document.getElementById('wechat-panel')?.hidden===false&&!document.getElementById('wc-assist').hidden`);
   const line=await evaluate(p,`document.getElementById('wc-assist').textContent`);
   assert.ok(line.includes('Codex')&&line.includes('harness · quota'),line);
   assert.equal(await evaluate(p,`document.querySelector('#wc-assist-hint code')?.textContent`),'agentj wechat status');
   assert.equal(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth+1`),true,'no horizontal overflow');
   writeFileSync(`${out}/${lang}-${theme}-${width}-fail.png`,await shoot(p,''));
   // Non-word detail from a broken host is dropped, never rendered.
   await fake.send({t:'wechat_state',bound:true,pending:0,assistant:{ok:false,error:'harness',detail:'<img src=x> /srv/me',kind:'codex'}});
   await waitFor(p,`!document.getElementById('wc-assist').textContent.includes('quota')`);
   const odd=await evaluate(p,`document.getElementById('wc-assist').textContent`);
   assert.ok(!odd.includes('/srv')&&!odd.includes('<img')&&odd.includes('harness'),odd);
   assert.equal(await evaluate(p,`document.querySelector('#wc-assist img')`),null);
   await fake.send({t:'wechat_state',bound:true,pending:0,assistant:{ok:true,error:null,detail:null,kind:'codex',at:1761450100}});
   await waitFor(p,`document.getElementById('wc-assist-hint').hidden`);
   assert.ok((await evaluate(p,`document.getElementById('wc-assist').textContent`)).includes('Codex'));
   writeFileSync(`${out}/${lang}-${theme}-${width}-ok.png`,await shoot(p,''));
   await fake.send({t:'wechat_state',bound:false,pending:0,assistant:fail});
   await waitFor(p,`document.getElementById('wc-assist').hidden`);
   assert.deepEqual(p.problems,[]);
   checks.push(`${lang}-${theme}-${width}: fail line + hint, sanitized, ok line, hidden when unbound`);
  }finally{await p.dispose();}
 }
}finally{await B.close();await fake.stop();await web.stop();}
mkdirSync('reports/qa/p540',{recursive:true});writeFileSync('reports/qa/p540/browser.json',JSON.stringify({ok:true,checks},null,2)+'\n');
const rec=parityRecorder('web/test/p540.mjs');rec.record('p540-wechat-assistant',true);rec.write();
console.log('P540 browser PASS',checks.length);
