// P88 encrypted browser fixture: exact cached meters, expansion preserves the floor.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=new URL('../../../reports/qa/p88/web/',import.meta.url);mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();const results={};
try {
 for(const [label,width,height] of [['phone',390,844],['desktop',1280,900]]) {
  const p=await newPage(B,width,height,'light',{touch:true,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.send({t:'meter',h5:{pct:1},ctx:{pct:49},source_at:1000});
  await waitFor(p,`document.getElementById('m5h').textContent.includes('1%')`);
  assert.equal(await evaluate(p,`document.getElementById('m5h').hidden`),false);
  assert.equal(await evaluate(p,`document.getElementById('mWeek').hidden`),true);
  assert.ok(await evaluate(p,`document.querySelector('#m5h i').getBoundingClientRect().width>=6`));
  await navigate(p,web.url);await waitState(p,'ready');
  await waitFor(p,`document.getElementById('m5h').textContent.includes('1%')`);
  writeFileSync(new URL(label+'-cached-1pct.png',out),await shoot(p,''));
  await fake.send({t:'meter',h5:{pct:0},ctx:null});
  await waitFor(p,`document.getElementById('m5h').textContent.includes('0%')`);
  assert.equal(await evaluate(p,`document.getElementById('m5h').dataset.zero`),'1');
  writeFileSync(new URL(label+'-zero.png',out),await shoot(p,''));
  await fake.send({t:'meter',h5:null,ctx:null});
  await waitFor(p,`document.getElementById('m5h').textContent.includes('—')`);
  assert.equal(await evaluate(p,`document.getElementById('m5h').dataset.known`),'0');
  for(const [length,reply] of [['short','Short reply'],['long','A long reply with readable content. '.repeat(300)]]) {
   await fake.addTurn({k:'phone',text:'A long input which expands. '.repeat(80)},reply);
   await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(reply.slice(0,16))})`);
   const geom=()=>evaluate(p,`(()=>{const r=id=>document.getElementById(id).getBoundingClientRect();return {top:r('rm').top,bottom:r('rmbar').bottom,meter:r('m5h').top,input:r('composer').top,height:r('main').height};})()`);
   // Composer is <footer>, its id differs across older builds; the meter is the stable floor.
   const before=await evaluate(p,`({top:rm.getBoundingClientRect().top,bottom:rmbar.getBoundingClientRect().bottom,meter:m5h.getBoundingClientRect().top,height:main.getBoundingClientRect().height})`);
   await evaluate(p,`document.getElementById('omMore').click()`);await sleep(200);
   const after=await evaluate(p,`({top:rm.getBoundingClientRect().top,bottom:rmbar.getBoundingClientRect().bottom,meter:m5h.getBoundingClientRect().top,height:main.getBoundingClientRect().height,scroll:main.scrollHeight})`);
   assert.ok(after.top>before.top);assert.ok(after.height<before.height);
   assert.ok(Math.abs(after.bottom-before.bottom)<2,JSON.stringify({before,after}));
   assert.ok(after.bottom+10<after.meter);assert.equal(after.meter,before.meter);
   if(length==='long')assert.ok(after.scroll>after.height);
   writeFileSync(new URL(label+'-'+length+'-expanded.png',out),await shoot(p,''));
   await evaluate(p,`document.getElementById('omMore').click()`);await sleep(200);
   assert.ok(Math.abs(await evaluate(p,`main.getBoundingClientRect().height`)-before.height)<2);
   writeFileSync(new URL(label+'-'+length+'-collapsed.png',out),await shoot(p,''));
  }
  await fake.clearHistory();await waitFor(p,`document.getElementById('pg').textContent === '0 / 0'`);
  results[label]='pass';console.log('PASS '+label);
 }
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('results.json',out),JSON.stringify(results,null,2)+'\n');
if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p88.mjs',results}));
