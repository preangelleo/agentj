// Shipped paired page against local Noise host. Navigation captured; no Olares server/PTTY claim.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startWebServer, PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,shoot,rect} from './browser.mjs';
const baseline=process.argv.includes('--baseline');
const out='reports/qa/olares-web-terminal-20261103/web/'+(baseline?'before':'after');mkdirSync(out,{recursive:true});
const temp=baseline?mkdtempSync(join(tmpdir(),'aj-olares-web-')):null;
if(temp){cpSync(PUBLIC_DIR,temp,{recursive:true});writeFileSync(join(temp,'js/termhook.js'),execFileSync('git',['show','c86c7fe:web/public/js/termhook.js']));}
const web=await startWebServer({publicDir:temp||PUBLIC_DIR}),fake=await startFakeHost(),B=await launch();
let checks=0;
const origin='https://terminal.owner.private.example';
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme}};
  fake.st.terminal={on:false,os:false,me:false,kind:'desktop',pk:false,external_url:origin};
  const messages=[];fake.st.onApp=async(c,m)=>{if(m.t.startsWith('term_'))messages.push(m.t);return false;};
  const p=await newPage(B,width,width===390?844:1000,theme,{touch:false,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();true`);
   await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.open('safety');return true})`);
   await evaluate(p,`document.getElementById('set-term-row').scrollIntoView({block:'center'});window.__nav=[];window.open=(...a)=>window.__nav.push(a);true`);
   const shot=join(out,`${lang}-${theme}-${width}.png`);writeFileSync(shot,await shoot(p,''));
   if(baseline){assert.equal(await evaluate(p,`import('./js/termhook.js').then(m=>m.state())`),'os');continue;}
   assert.equal(await evaluate(p,`import('./js/termhook.js').then(m=>m.state())`),'external');
   const r=await rect(p,'#set-term-external-open');assert.ok(r.w>=44&&r.h>=44,JSON.stringify(r));
   await evaluate(p,`document.getElementById('set-term-external-open').click();true`);
   assert.deepEqual(await evaluate(p,'window.__nav'),[[origin,'_blank','noopener,noreferrer']]);
   assert.equal(messages.length,0);assert.equal(await evaluate(p,`import('./js/termhook.js').then(m=>m.usable())`),false);
   assert.equal(await evaluate(p,'document.documentElement.scrollWidth>innerWidth+1'),false);
   assert.equal(p.offsite.length,0);checks+=6;
   // Invalid or absent configuration reverts to the prior platform behavior, with no external entry.
   for(const external_url of ['https://terminal.example/path',null]){
    await evaluate(p,`import('./js/termhook.js').then(m=>{m.render({terminal:{on:false,os:false,external_url:${JSON.stringify(external_url)}}});return true})`);
    assert.equal(await evaluate(p,`document.getElementById('set-term-external').hidden`),true);checks++;
   }
  }finally{await p.dispose();}
 }
 writeFileSync(join(out,'results.json'),JSON.stringify({baseline,checks,scope:'Local paired Noise page. Olares navigation captured, not live Olares authentication or PTY.'},null,2));
 console.log(JSON.stringify({baseline,checks,out}));
}finally{await B.close();await fake.stop();await web.stop();if(temp)rmSync(temp,{recursive:true,force:true});}
