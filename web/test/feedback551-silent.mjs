// Real client + encrypted local fake host; synthetic final replies only.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=new URL('../../../reports/qa/p551/web/',import.meta.url);mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),results={};
const old='\u3014\u4e0d\u56de\u7fa4\u3015';
try{
 for(const width of [390,1440])for(const theme of ['light','dark']){
  await fake.clearHistory();
  const p=await newPage(B,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.addTurn({k:'host',text:'normal'},'Visible answer');
  await waitFor(p,`document.getElementById('words').textContent.includes('Visible answer')`);
  await fake.addTurn({k:'host',text:'second'},'Second answer');
  await waitFor(p,`document.getElementById('words').textContent.includes('Second answer')`);
  await evaluate(p,`document.getElementById('pgPrev').click()`);
  const before=await evaluate(p,`document.getElementById('pg').textContent`);
  for(const marker of ['〔静默消息〕','[silent]',old])for(const reply of [marker,marker+'body','body'+marker])await fake.addTurn({k:'agent',text:'routine'},reply);
  const wireTurn=await fake.addTurn({k:'agent',text:'wire-silent'},'〔静默消息〕');
  await fake.updateTurn(wireTurn.id,{silent:true,reply:{text:'',pre:'Recorded silent body'}});
  await sleep(400);
  assert.equal(await evaluate(p,`document.getElementById('pg').textContent`),before);
  assert.equal(await evaluate(p,`document.getElementById('words').textContent`),'Visible answer');
  assert.equal(await evaluate(p,`document.getElementById('pgCorner').dataset.unread`),'0');
  await evaluate(p,`document.getElementById('slashBtn').click();document.getElementById('silentEntry').click()`);
  await waitFor(p,`document.querySelectorAll('#silentList details').length===10`);
  assert.match(await evaluate(p,`document.getElementById('silentList').textContent`),/Recorded silent body/);
  assert.doesNotMatch(await evaluate(p,`document.getElementById('silentList').textContent`),/〔静默消息〕|\[silent\]|\u3014\u4e0d\u56de\u7fa4\u3015/);
  await evaluate(p,`document.getElementById('silentClose').click()`);
  await evaluate(p,`document.getElementById('pgNext').click()`);
  await fake.addTurn({k:'host',text:'middle'},'Before 〔静默消息〕 after [silent] end '+old+' ok');
  await waitFor(p,`document.getElementById('words').textContent.includes('Before  after  end  ok')`);
  assert.doesNotMatch(await evaluate(p,`document.getElementById('words').textContent`),/〔静默消息〕|\[silent\]|\u3014\u4e0d\u56de\u7fa4\u3015/);
  writeFileSync(new URL(`${width}-${theme}.png`,out),await shoot(p,''));
  assert.deepEqual(p.problems,[]);results[`${width}-${theme}`]='pass';console.log('PASS '+width+' '+theme);
 }
}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('results.json',out),JSON.stringify(results,null,2)+'\n');
