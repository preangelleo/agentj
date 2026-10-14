// Leo 10-14 15:09: input cards keep their narrower 0.20.3 geometry, collapsed and expanded.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot } from './browser.mjs';
const web=await startWebServer(), fake=await startFakeHost(), browser=await launch();
const rows=[];
const out=process.env.AJ_SHOTS || 'reports/qa/p250-resume/input-width-shots';
mkdirSync(out,{recursive:true});
try {
  for (const width of [393,1440]) for(const theme of ['light','dark']) {
    fake.reset();
    const p=await newPage(browser,width,852,theme,{touch:width<600,allow:[web.url,fake.relay+'/']});
    await navigate(p,fake.newPairing(web.url)); await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
    await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
    const dev=await evaluate(p,`import('./js/api.js').then(api=>api.myDeviceId())`);
    await fake.addTurn({k:'phone',dev,text:'An input message with enough text to expand. '.repeat(25)},'The output is the focus.');
    await waitFor(p,`document.getElementById('pg').textContent==='1 / 1'`);
    for(const expanded of [false,true]) {
      if (expanded) await evaluate(p,`document.getElementById('omMore').click()`);
      await waitFor(p,`document.getElementById('omMore').getAttribute('aria-expanded')==='${expanded}'`);
      const g=await evaluate(p,`(()=>{const r=e=>e.getBoundingClientRect();const a=r(document.getElementById('om')),b=r(document.getElementById('rm'));return {input:a.width,output:b.width,left:a.left-b.left,right:b.right-a.right,padding:getComputedStyle(document.getElementById('om')).paddingBottom,overlap:getComputedStyle(document.getElementById('rm')).marginTop};})()`);
      assert.ok(g.output>0 && Math.abs(g.output-g.input-24)<1,JSON.stringify({width,theme,expanded,...g}));
      assert.ok(Math.abs(g.left-12)<1 && Math.abs(g.right-12)<1);
      assert.equal(g.padding,'20px'); assert.equal(g.overlap,'-12px');
      writeFileSync(out+`/${width}-${theme}-${expanded?'expanded':'collapsed'}.png`,await shoot(p,''));
      rows.push({width,theme,expanded,...g});
    }
    await p.dispose();
  }
} finally { await browser.close(); await fake.stop(); await web.stop(); }
mkdirSync('reports/qa/p250-resume',{recursive:true});
writeFileSync('reports/qa/p250-resume/input-width.json',JSON.stringify({passed:rows.length,rows},null,2)+'\n');
console.log('PASS input width: '+rows.length);
