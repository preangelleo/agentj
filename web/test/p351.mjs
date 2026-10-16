// P351 real browser, isolated encrypted fake host: a silent turn's process notes (reply.pre) stay out of the main flow
// and show only inside the expandable 「静默回合」 entry.
import assert from 'node:assert/strict';
import {writeFileSync,mkdirSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=new URL('../../../reports/qa/p351/web/',import.meta.url);mkdirSync(out,{recursive:true});
const M='〔不回群〕',NOTE='我检查一下是否有新的后台结果或未完成事项。';
const results={};const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await fake.addTurn({k:'host',text:'normal input'},'normal answer');
 await waitFor(p,`document.getElementById('words').textContent.includes('normal answer')`);
 const t=await fake.addTurn({k:'agent',via:'loop'},NOTE,'open');
 await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(NOTE)})`);
 await fake.updateTurn(t.id,{reply:{text:M,pre:NOTE},end:'done'});await sleep(500);
 const words=await evaluate(p,`document.getElementById('words').textContent`);
 assert.ok(!words.includes(NOTE)&&!words.includes(M),'the folded notes leave the main flow');
 assert.ok(words.includes('normal answer'),'the last real page is shown again');
 await evaluate(p,`document.getElementById('slashBtn').click();document.getElementById('silentEntry').click()`);
 await waitFor(p,`!!document.querySelector('#silentList details summary')`);
 await evaluate(p,`document.querySelector('#silentList summary').click()`);
 assert.equal(await evaluate(p,`document.querySelector('#silentList .rep.pre')?.textContent`),'过程说明：'+NOTE);
 assert.ok((await evaluate(p,`document.querySelector('#silentList .rep:not(.pre)').textContent`)).includes(M));
 writeFileSync(new URL('silent-notes.png',out),await shoot(p,''));
 assert.deepEqual(p.problems,[]);results['p351-silent-notes']='pass';console.log('PASS p351-silent-notes');
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('results.json',out),JSON.stringify(results,null,2)+'\n');
if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p351.mjs',results}));
