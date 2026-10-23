// P481 real phone page, local fake host, real Noise; no customer machine or credentials.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
const rec = process.env.AJ_P481_BEFORE === '1' ? null : parityRecorder('web/test/p481.mjs');
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep} from './browser.mjs';
const before = process.env.AJ_P481_BEFORE === '1';
const web = await startWebServer({port:0, relayCsp:'ws://127.0.0.1:*', ...(process.env.AJ_P481_PUBLIC ? {publicDir:process.env.AJ_P481_PUBLIC} : {})});
const fake = await startFakeHost(), B = await launch();
const out = 'reports/qa/p481/'; mkdirSync(out,{recursive:true});
try {
 for (const lang of ['zh','en']) for(const w of [390,412]){
  fake.reset(); fake.st.extraCaps=['ss1']; fake.st.sayMode='queued';
  const prev = await fake.addTurn({k:'phone',text:lang==='zh'?'上一件事':'Previous task'},'', 'open');
  const p = await newPage(B,w,844,'light',{allow:[web.url,fake.relay+'/'],touch:true});
  try {
   await navigate(p,fake.newPairing(web.url+(lang==='en'?'?lang=en':'')));
   await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
   await evaluate(p,`(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();return true})()`);
   await fake.send({t:'status',s:'working',agent:'codex',name:'Agent J'});
   await evaluate(p,`(()=>{const i=document.getElementById('input');i.value=${JSON.stringify(lang==='zh'?'继续下一件事':'Continue with the next task')};i.dispatchEvent(new Event('input'));document.getElementById('send').click();return true})()`);
   await waitFor(p,`document.body.dataset.pageQueued==='1'`); await sleep(1600);
   const stop = `document.querySelector('[data-queue-stop]')`;
   assert.equal(await evaluate(p,`!!${stop}`),!before);
   writeFileSync(`${out}${lang}-${w}-${before?'before':'after'}.png`,await shoot(p,''));
   if(!before){
    const geo=await evaluate(p,`(()=>{const r=${stop}.getBoundingClientRect();return {w:r.width,h:r.height,x:r.x,right:r.right}})()`);
    assert.ok(geo.h>=44 && geo.w>=56 && geo.x>=0 && geo.right<=w,JSON.stringify(geo));
    await evaluate(p,`(()=>{const real=Date.now;Date.now=()=>real()+181000;return true})()`);
    await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(lang==='zh'?'3 分钟':'3 minutes')})`,12000);
    writeFileSync(`${out}${lang}-${w}-stuck.png`,await shoot(p,''));
    // An earlier turn's real output resets the quiet window.
    await fake.updateTurn(prev.id,{reply:{text:'Working on it'}});
    await waitFor(p,`!document.getElementById('words').textContent.includes(${JSON.stringify(lang==='zh'?'3 分钟':'3 minutes')})`);
    await evaluate(p,`${stop}.click()`); await sleep(200);
    assert.equal(fake.st.slashes.filter(m=>m.cmd==='stop').length,1);
   }
   assert.equal(p.problems.length,0,p.problems.join('\n'));
   console.log(`PASS ${lang} ${w} ${before?'before':'after'} queue + stop + silent threshold + output reset`);
  } finally {await p.dispose();}
 }
 if(rec){ rec.record('P481 queue stop and silence', true); rec.write(); }
} finally {await B.close();await fake.stop();await web.stop();}
