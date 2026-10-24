// P502: portable runtime checks. Historical 0.25.5a1 pixel comparison lives in the private p502-visual gate.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitFor,waitState} from './browser.mjs';
const web=await startWebServer(),fake=await startFakeHost(),browser=await launch();
const rec=parityRecorder('web/test/p502.mjs'),rows=[];
const text=(p,id)=>evaluate(p,`document.getElementById(${JSON.stringify(id)}).textContent`);
try {
 for(const lang of ['zh','en'])for(const width of [390,1280]) {
  fake.reset();
  const page=await newPage(browser,width,844,'light',{allow:[web.url,fake.relay],touch:width<500});
  try {
   await navigate(page,fake.newPairing(web.url+'?lang='+lang));await waitState(page,'awaiting-approval');await fake.approve();await waitState(page,'ready');
   await evaluate(page,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();if(!document.getElementById('confirm').hidden)document.getElementById('confirm-no').click()`);
   await fake.addTurn({k:'phone',text:'Review the notes'},'The notes are ready.','done');
   await waitFor(page,`document.getElementById('words').textContent.includes('The notes are ready.')`);
   for(const name of ['Wren','小助手','贾维斯','Jarvis','Agent J-Wren','一二三四五六七八','<img src=x>','','Agent J','Agent J - Father@TB','Father@TB',null]) {
    const label=!name||name==='Agent J'?'Agent J':name.startsWith('Agent J - ')?name:'Agent J - '+name;
    await fake.send({t:'status',s:'idle',agent:'claude',name});
    await waitFor(page,`document.getElementById('agent').textContent===${JSON.stringify(label)}`);
    assert.equal(await text(page,'brand-name'),label);
    if(name)assert.equal(await evaluate(page,'document.title'),label);
    assert.equal(await evaluate(page,`document.querySelectorAll('#agent img,#brand-name img').length`),0);
    await waitFor(page,`(async()=>{const {dbGet}=await import('./js/store.js');return ((await dbGet('host'))?.name??null)===${JSON.stringify(name||null)}})()`);
    if(name) {
     await evaluate(page,`(async()=>{(await import('./js/agentname.js')).open()})()`);
     assert.equal(await evaluate(page,`document.getElementById('nameInput').value`),name,'rename edits raw owner data');
     await evaluate(page,`(async()=>{(await import('./js/agentname.js')).close()})()`);
    }
    await evaluate(page,`(async()=>{(await import('./js/relay.js')).remoteReader(true)})()`);
    await waitFor(page,`!document.getElementById('rd').hidden&&document.getElementById('rdName').textContent===${JSON.stringify(label)}`);
    await evaluate(page,`(async()=>{(await import('./js/relay.js')).remoteReader(false)})()`);
    rows.push({lang,width,name,label});
   }
   await evaluate(page,`(async()=>{(await import('./js/agentname.js')).open()})()`);
   await waitFor(page,`!document.getElementById('nameDlg').hidden&&!document.getElementById('nameInput').closest('[inert]')`);
   await evaluate(page,`const i=document.getElementById('nameInput');i.value='After';i.dispatchEvent(new Event('input'))`);
   await waitFor(page,`!document.getElementById('nameSave').disabled`);
   await evaluate(page,`document.getElementById('nameForm').requestSubmit()`);
   try{await waitFor(page,`document.getElementById('agent').textContent==='Agent J - After'`)}catch(e){console.log('P502 rename fixture',JSON.stringify({renames:fake.st.renames,ui:await evaluate(page,`({state:window.__ajState,header:document.getElementById('agent').textContent,name:document.getElementById('nameInput').value,hint:document.getElementById('nameHint').textContent,disabled:document.getElementById('nameSave').disabled,hidden:document.getElementById('nameDlg').hidden})`)}));throw e;}
   const saved=fake.st.renames.at(-1);assert.equal(saved.name,'After');assert.equal(saved.sigOk,true,'same paired signed rename path');
   await waitFor(page,`(async()=>{return(await(await import('./js/store.js')).dbGet('host')).name==='After'})()`);
   fake.setUp(false);await navigate(page,web.url+'?lang='+lang);await waitState(page,'waiting-host');
   assert.equal(await text(page,'agent'),'Agent J - After','offline cold start uses the raw cached name');
   fake.setUp(true);await waitState(page,'ready');
   assert.deepEqual(page.problems,[]);assert.deepEqual(page.offsite,[]);
  } finally {fake.setUp(true);await page.dispose();}
 }
 mkdirSync('reports/qa/p502',{recursive:true});writeFileSync('reports/qa/p502/runtime.json',JSON.stringify({ok:true,rows},null,2)+'\n');
 rec.record('seat-name-text-only',true);rec.write();console.log(`P502 runtime PASS: ${rows.length} cases, signed rename + offline cache in four contexts`);
} finally {await browser.close();await fake.stop();await web.stop();}
