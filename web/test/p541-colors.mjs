#!/usr/bin/env node
// Real computed foreground/background samples; isolated signed synthetic card, no execution.
process.env.P541_LIBRARY='1';
import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
const H=await import('./p541.mjs'),{waitFor,sleep}=await import('./browser.mjs');
const samples=[];
async function sample(p,state,selectors){
 const rows=await H.ev(p,`(${function(selectors){
  const rgba=c=>(c.match(/[\d.]+/g)||[]).map(Number);
  const over=(a,b)=>[...a.slice(0,3).map((v,i)=>v*a[3]+b[i]*(1-a[3])),1];
  const luminance=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
  return selectors.map(selector=>{
   const n=document.querySelector(selector);if(!n||!n.getClientRects().length)throw Error('missing visible '+selector);
   const chain=[];for(let a=n;a;a=a.parentElement)chain.push(a);
   let bg=[255,255,255,1];for(const a of chain.reverse()){const c=rgba(getComputedStyle(a).backgroundColor);bg=over([c[0],c[1],c[2],c[3]??1],bg);}
   const css=getComputedStyle(n),fg=rgba(css.color),final=over([fg[0],fg[1],fg[2],fg[3]??1],bg),l=[luminance(final),luminance(bg)].sort((a,b)=>a-b);
   return {selector,color:css.color,background:bg,contrast:(l[1]+.05)/(l[0]+.05),font:css.fontSize};
  });
 }.toString()})(${JSON.stringify(selectors)})`);
 for(const row of rows){samples.push({state,...row});assert.ok(row.contrast>=4.5,`${state} ${row.selector} contrast ${row.contrast}`);}
}
try{
 for(const theme of ['light','dark']){
  const p=await H.setup(390,844,theme);
  await sample(p,theme+' expanded',['#source541','#action541','#details541 summary','#apprAllow','#doubt541c','#apprDeny']);
  await H.ev(p,`document.getElementById('details541').querySelector('summary').click()`);
  await sample(p,theme+' details',['#details541 .cmd']);
  await H.ev(p,`document.getElementById('sheetClose').click()`);await sleep(100);
  await sample(p,theme+' folded',['#omText']);
  await H.ev(p,`document.getElementById('pending541').click()`);await waitFor(p,`document.body.dataset.sheet==='1'`);
  await H.ev(p,`document.getElementById('source541').click()`);await sleep(200);
  await sample(p,theme+' history',['#omText','#pg']);
  await H.ev(p,`document.getElementById('pending541').click()`);await waitFor(p,`document.body.dataset.sheet==='1'`);
  await H.ev(p,`document.getElementById('doubt541c').click()`);await waitFor(p,`document.body.dataset.status==='working'`);await sleep(200);
  await sample(p,theme+' question',['#omText']);
  assert.equal(H.fake.log.filter(m=>m.t==='answer'&&m.ok).length,0,'color/navigation never approve');
  await p.dispose();
 }
}finally{await H.B.close();await H.fake.stop();await H.web.stop();writeFileSync(H.OUT+'/colors-validation.json',JSON.stringify({samples},null,2));}
console.log(JSON.stringify({samples:samples.length,minContrast:Math.min(...samples.map(x=>x.contrast))}));
