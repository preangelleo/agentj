// Real client screenshots of the owner's exact short/long text boundary.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
process.env.P541_LIBRARY='1';const H=await import('./p541.mjs');const {sleep,shoot}=await import('./browser.mjs');
const out=process.env.P0282_TOAST_OUT||'reports/qa/p0282-air-web/toast';mkdirSync(out,{recursive:true});const rows=[];
try{const p=await H.setup(390,844,'light');await H.fake.askDone(H.currentCard().id,'deny');
 for(const [language,text,shape] of [['zh','已经全部完成','square'],['zh','已完成发送','circle'],['en','Completed!','circle'],['en','Hello world','square']]){
  await H.ev(p,`import('./js/ui.js').then(m=>m.toast(${JSON.stringify(text)},120000))`);await sleep(180);
  const g=await H.ev(p,`(()=>{const t=document.getElementById('toast'),b=t.querySelector('.nt-body'),r=t.getBoundingClientRect(),s=getComputedStyle(b);return {circle:t.classList.contains('circle'),square:t.classList.contains('square'),nowrap:s.whiteSpace==='nowrap',center:r.x+r.width/2,width:r.width,height:r.height,text:b.textContent};})()`);
  assert.equal(g[shape],true);if(shape==='circle')assert.equal(g.nowrap,true);assert.ok(Math.abs(g.center-195)<2);const file=out+'/'+language+'-'+shape+'.png';writeFileSync(file,await shoot(p));rows.push({language,text,shape,file,...g});
 }await p.dispose();}finally{writeFileSync(out+'/result.json',JSON.stringify({rows},null,2));await H.B.close();await H.fake.stop();await H.web.stop();}
console.log('PASS p0282-toast 4 cases: Chinese five/six and half-width ten/eleven thresholds');
