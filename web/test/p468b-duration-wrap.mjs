import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
import {orphanLineAuditSource} from '../../brand/test/orphan-line-audit.mjs';
const out=resolve(process.env.P468B_OUT||'reports/qa/remote-assist-468b-20261106');mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),measurements=[];let checks=0;
const check=(v,k)=>{assert.ok(v,k);checks++;};
try{
for(const width of [390,1440])for(const theme of ['light','dark']){
 fake.reset();fake.setUp(true);fake.st.extraCaps=['assist_term1'];fake.st.prefs={appearance:{language:'zh',theme}};
 const p=await newPage(B,width,844,theme,{touch:width<500,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.openRescue();return true})`);await waitFor(p,`!!document.getElementById('ast-min-15')`);
  await evaluate(p,`document.getElementById('ast-min-30').scrollIntoView({block:'center'})`);
  const geometry=await evaluate(p,`[...document.querySelectorAll('.ast__durations button')].map(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e),t=e.firstChild,range=document.createRange();range.selectNodeContents(e);return {id:e.id,text:e.textContent,whiteSpace:s.whiteSpace,width:r.width,height:r.height,rects:[...range.getClientRects()].map(r=>({x:r.x,y:r.y,width:r.width,height:r.height})),fits:e.scrollWidth<=e.clientWidth}})`);
  measurements.push({width,theme,geometry});writeFileSync(resolve(out,`geometry-${width}-${theme}.json`),JSON.stringify(geometry,null,2));
  for(const b of geometry){check(b.whiteSpace==='nowrap',b.id+' nowrap');check(b.rects.length===1,b.id+' single rendered line');check(b.fits,b.id+' fits button');}
  const findings=await evaluate(p,orphanLineAuditSource);writeFileSync(resolve(out,`orphans-${width}-${theme}.json`),JSON.stringify(findings,null,2));
  check(!findings.some(x=>x.element.startsWith('#ast-')), 'assist card has no orphan label');
  writeFileSync(resolve(out,`consent-zh-${theme}-${width}.png`),await shoot(p,''));
  // Red control on the real duration buttons: exactly the reported 15 分 / 钟 layout.
  await evaluate(p,`document.querySelectorAll('.ast__durations button').forEach(e=>{e.style.whiteSpace='normal';e.style.width='70px';e.style.maxWidth='70px';e.style.flex='none';e.style.padding='10px 16px'})`);
  check((await evaluate(p,orphanLineAuditSource)).some(x=>x.element==='#ast-min-15'&&x.lines.at(-1)==='钟'),'detector rejects actual wrapped minute button');
  // Nested spans + explicit br, short single-line labels, hidden labels, punctuation/digits.
  await evaluate(p,`(()=>{const f=document.createElement('div');f.id='orphan-fixture';f.innerHTML='<button id="orphan-nested"><span>远程协助</span><br><span>中</span><span>文</span></button><button id="orphan-short">好</button><button id="orphan-inline-atoms" style="display:block;width:42px;padding:0;white-space:normal"><span style="display:inline-block">15 分</span> <span style="display:inline-block">钟</span></button><button id="orphan-clamp"><span style="display:block;line-height:20px;height:20px;overflow:hidden">正常标签<br>字</span></button><button id="orphan-blocks"><b style="display:block">记忆</b><small style="display:block">记录所有资料</small></button><button id="orphan-hidden" hidden>远程协助<br>字</button><div class="card-title" id="orphan-card">远程协助<br><span>1</span><span>。</span></div>';document.body.append(f)})()`);
  const controls=await evaluate(p,orphanLineAuditSource);
  check(controls.some(x=>x.element==='#orphan-nested'&&x.lines.at(-1)==='中文'),'nested nodes and br measured together');
  check(controls.some(x=>x.element==='#orphan-inline-atoms'&&x.lines.at(-1)==='钟'),'inline-block atoms remain one label');
  check(controls.some(x=>x.element==='#orphan-card'&&x.lines.at(-1)==='1。'),'card label digits and punctuation counted');
  check(!controls.some(x=>['#orphan-short','#orphan-hidden','#orphan-blocks','#orphan-clamp'].includes(x.element)),'single-line short and hidden labels are valid');
  check(!p.offsite.length,'no offsite');console.log('PASS',width,theme);
 }finally{await p.dispose();}
}
writeFileSync(resolve(out,'result.json'),JSON.stringify({checks,measurements},null,2)+'\n');console.log('checks',checks);
}finally{await B.close();await fake.stop();await web.stop();}
