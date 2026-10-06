// P68 real browser, isolated encrypted fake host. No native desktop-app claim.
import assert from 'node:assert/strict';
import {writeFileSync,mkdirSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=new URL('../../../reports/qa/p68/web/',import.meta.url);mkdirSync(out,{recursive:true});
const results={};const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await fake.addTurn({k:'host',text:'normal input'},'normal answer');
 await waitFor(p,`document.getElementById('words').textContent.includes('normal answer')`);
 const silent=await fake.addTurn({k:'agent'},'〔不回群〕');await sleep(500);
 assert.equal(await evaluate(p,`document.getElementById('words').textContent.includes('normal answer')`),true);
 assert.equal(await evaluate(p,`document.getElementById('words').textContent.includes('〔不回群〕')`),false);
 await evaluate(p,`document.getElementById('pgNext').click()`);
 await waitFor(p,`!!document.querySelector('#words details summary')`);
 assert.equal(await evaluate(p,`document.querySelector('#words details').open`),false);
 assert.match(await evaluate(p,`document.querySelector('#words summary').textContent`),/(静默回合|Silent turn) \d\d:\d\d/);
 await evaluate(p,`document.querySelector('#words summary').click()`);
 assert.equal(await evaluate(p,`document.querySelector('#words details').open && document.querySelector('#words pre').textContent.includes('〔不回群〕')`),true);
 writeFileSync(new URL('silent-expanded.png',out),await shoot(p,''));
 await fake.addTurn({k:'host',text:'mixed'},'answer 〔不回群〕');
 await waitFor(p,`document.getElementById('words').textContent.includes('answer 〔不回群〕')`);
 await fake.clearHistory();await sleep(150);await fake.addTurn({k:'agent'},'〔不回群〕');await sleep(350);
 assert.equal(await evaluate(p,`document.getElementById('words').textContent.includes('〔不回群〕')`),false);
 assert.deepEqual(p.problems,[]);results['silent-history']='pass';console.log('PASS silent-history');
 await fake.send({t:'meter',model:'gpt-6-astra',model_name:'gpt-6-astra',shared_status:'desktop_writer'});
 await waitFor(p,`document.getElementById('metaModel').textContent==='gpt-6-astra' && !document.getElementById('shared-status').hidden`);
 assert.match(await evaluate(p,`document.getElementById('shared-status').textContent`),/read-only|只读|只能看/);
 await navigate(p,web.url);await waitState(p,'ready');
 await waitFor(p,`!document.getElementById('shared-status').hidden`);
 assert.equal(await evaluate(p,`document.getElementById('metaModel').textContent`),'gpt-6-astra');
 await fake.send({t:'meter',model:'gpt-6-astra',shared_status:null});
 await waitFor(p,`document.getElementById('shared-status').hidden`);
 await fake.send({t:'meter',model:'gpt-6-astra',shared_status:'unknown-state'});
 assert.equal(await evaluate(p,`document.getElementById('shared-status').hidden`),true);
 assert.deepEqual(p.problems,[]);results['desktop-writer-banner']='pass';console.log('PASS desktop-writer-banner');

} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('results.json',out),JSON.stringify(results,null,2)+'\n');
if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p68.mjs',results}));
