#!/usr/bin/env node
// Local-only candidate page + synthetic Noise host. No real seat, account, credential or main-session restart.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,rect,tap,sleep} from './browser.mjs';
const out=process.env.P0315_VISUAL_OUT || join(homedir(),'coding/.agentj/outbox/0315-int');
mkdirSync(out,{recursive:true});
const checks=[];
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 for(const width of [390,768,1280])for(const theme of ['light','dark']) {
  fake.reset();fake.st.prefs={appearance:{language:'zh',theme}};
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang=zh'));
   await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
   // The actual mic pointer gesture and Chromium's fake capture, not a DOM-only mock card.
   const mic=await rect(p,'#mic');await tap(p,mic.x,mic.y);await sleep(80);await tap(p,mic.x,mic.y);
   await waitFor(p,`document.getElementById('mic').classList.contains('lock') && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`,7000);
   const micState=await evaluate(p,`({hint:document.getElementById('pttHint').textContent,mode:document.getElementById('pttMode').textContent,seg:document.getElementById('pttSeg').textContent,media:window.__ajMediaDiagnostics,overflow:document.documentElement.scrollWidth>innerWidth})`);
   assert.ok(micState.hint && micState.mode && !micState.overflow && micState.media.some(x=>x.kind==='getUserMedia.ok'));
   writeFileSync(`${out}/mic-${width}-${theme}.png`,await shoot(p));
   await evaluate(p,`document.getElementById('pttCancel').click()`);
   await waitFor(p,`document.getElementById('ptt').hidden`);
   // Synthetic host state only: this is a UI screenshot, NOT a successful Touch ID assertion.
   await fake.send({t:'pk_state',s:'local',fa:'Y3JlZC0x',fa_ch:'A'.repeat(43),n:'B'.repeat(22)});
   await evaluate(p,`document.getElementById('setBtn').click()`);
   await waitFor(p,`!document.getElementById('settings').hidden && !document.getElementById('set-pk-test').hidden`);
   await evaluate(p,`document.getElementById('set-pk-row').scrollIntoView({block:'center'});const n=document.getElementById('toast');clearTimeout(n._t);n.classList.remove('on','act');n.textContent=''`);
   await sleep(700);
   const pkState=await evaluate(p,`({row:document.getElementById('set-pk-row').innerText,visible:document.getElementById('set-pk-test').getBoundingClientRect().width>0,overflow:document.documentElement.scrollWidth>innerWidth,theme:document.documentElement.dataset.theme,background:getComputedStyle(document.getElementById('settings')).backgroundColor,colorScheme:getComputedStyle(document.getElementById('settings')).colorScheme})`);
   assert.ok(pkState.visible && pkState.row.includes('测试一下') && !pkState.overflow);
   assert.equal(pkState.theme,theme);
   assert.equal(pkState.colorScheme,'dark');
   writeFileSync(`${out}/touchid-${width}-${theme}.png`,await shoot(p));
   checks.push({width,theme,mic:'fake Chromium audio capture OK',touchid:'synthetic ready state displayed; biometric untested',micHint:micState.hint,pkButton:pkState.visible,themeAttribute:pkState.theme,settingsBackground:pkState.background,settingsColorScheme:pkState.colorScheme,pageErrors:p.problems.length,offsite:p.offsite.length});
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
   console.log('PASS',width,theme);
  }finally{await p.dispose()}
 }
}finally{await B.close();await fake.stop();await web.stop();writeFileSync(`${out}/browser.json`,JSON.stringify({mode:'isolated fake-host; not installed, not physical iOS/Mac',checks},null,2))}
