// Real phone page, encrypted local fake peer; all processes are owned and reaped.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,shoot,sleep} from './browser.mjs';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const out='reports/qa/p249/phone';mkdirSync(out,{recursive:true});
try{
 for(const lang of ['zh','en']){
  fake.reset();const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,"if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()");
  const dev=await evaluate(p,"import('./js/api.js').then(api=>api.myDeviceId())");
  const text=lang==='zh'?'正在完成定时任务 日报，压缩尚未发送。':'Finishing scheduled task Daily report; compaction has not been sent.';
  const turn=await fake.addTurn({k:'cmd',dev,text:'/compact'},text,'open',{cmd:'compact',cancel_wait:true});
  await waitFor(p,"document.querySelector('[data-cmd-cancel]') && !document.getElementById('cmdx').hidden");
  assert.ok(await evaluate(p,`document.getElementById('words').textContent.includes(${JSON.stringify(text)})`));
  writeFileSync(out+'/'+lang+'-waiting.png',await shoot(p,''));
  await evaluate(p,"document.querySelector('[data-cmd-cancel]').click()");await sleep(200);
  assert.ok(fake.log.some(m=>m.t==='cmd_cancel'&&m.turn===turn.id));
  await fake.updateTurn(turn.id,{end:'stopped',reply:{text:lang==='zh'?'已取消，未发送控制。':'Cancelled; control not sent.'},card:{}});
  await waitFor(p,"!document.querySelector('[data-cmd-cancel]')");
  writeFileSync(out+'/'+lang+'-cancelled.png',await shoot(p,''));
  const other=await fake.addTurn({k:'cmd',dev:'another-device',text:'/compact'},text,'open',{cmd:'compact',cancel_wait:true});
  await waitFor(p,`document.getElementById('pg').textContent==='2 / 2'`);
  assert.equal(await evaluate(p,"!!document.querySelector('[data-cmd-cancel]')"),false);
  await fake.updateTurn(other.id,{end:'open',card:{}});
  assert.equal(await evaluate(p,"!!document.querySelector('[data-cmd-cancel]')"),false);
  console.log('PASS '+lang+' waiting, encrypted cancel request, stopped page and device isolation');
 }
}finally{await B.close();await fake.stop();await web.stop();}
