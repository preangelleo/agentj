// #514: a grey page must explain recovery even when the last reply says upgrade succeeded.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/avon514-web/',import.meta.url));
mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),browser=await launch();
try {
 for(const width of [390,1440]) for(const theme of ['light','dark']) for(const lang of ['zh','en']){
  fake.reset();fake.setUp(true);
  const p=await newPage(browser,width,844,theme,{allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   assert.equal(await evaluate(p,"getComputedStyle(document.getElementById('connection-help')).display"),'none','positive: online hides recovery');
   await fake.say('Upgrade completed.','agent');fake.setUp(false);await waitState(p,'waiting-host');
   await waitFor(p,"document.body.dataset.conn==='off'");
   assert.equal(await evaluate(p,"getComputedStyle(document.getElementById('connection-help')).display"),'flex');
   assert.ok(await evaluate(p,`document.getElementById('connection-help').textContent.includes(${JSON.stringify(lang==='zh'?'请客服帮忙':'Ask support for help')})`));
   await evaluate(p,"document.getElementById('offLater').click()");
   writeFileSync(out+`${width}-${theme}-${lang}-offline.png`,await shoot(p));
   await evaluate(p,"document.getElementById('setBtn').click()");
   assert.ok(await evaluate(p,"!document.getElementById('set-refresh').disabled && document.getElementById('set-refresh').textContent.trim().length>0"),'settings refresh has a visible label and remains available offline');
   assert.ok(await evaluate(p,"document.getElementById('connection-help').getBoundingClientRect().bottom <= document.getElementById('input').getBoundingClientRect().top"),'guidance does not cover the input');
   await evaluate(p,"document.querySelector('[data-set-jump=set-s-app]').click();document.getElementById('set-refresh').scrollIntoView({block:'center'})");
   writeFileSync(out+`${width}-${theme}-${lang}-settings-after.png`,await shoot(p));
   await evaluate(p,"document.querySelector('#set-refresh span').remove()");
   writeFileSync(out+`${width}-${theme}-${lang}-settings-before.png`,await shoot(p));
   await evaluate(p,"const n=document.createElement('span');n.textContent=document.getElementById('set-refresh').getAttribute('aria-label');document.getElementById('set-refresh').append(n)");
   await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
   assert.ok(await evaluate(p,"document.getElementById('settings').hidden"),'Esc closes settings');
   await evaluate(p,"window.__beforeRefresh=1;document.getElementById('input').value='preserved draft';document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true}))");
   await evaluate(p,"document.getElementById('connection-refresh').click()");
   await waitFor(p,"!window.__beforeRefresh",15000);
   fake.setUp(true);await waitState(p,'ready');
   assert.equal(await evaluate(p,"getComputedStyle(document.getElementById('connection-help')).display"),'none','reconnect hides guidance');
   await waitFor(p,"document.getElementById('input').value==='preserved draft'",15000);
   assert.equal(p.problems.length,0,'no page errors');
  }finally{await p.dispose();}
 }
 console.log('PASS #514: 8 viewport/theme/language cases, offline refresh, Esc, reconnect');
}finally{await browser.close();await fake.stop();await web.stop();}
