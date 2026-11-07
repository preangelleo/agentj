import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,shoot} from './browser.mjs';
const out=process.env.P0284_TOAST_OUT||'reports/qa/p0284-toast-fit';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),b=await launch(),rows=[];
const samples=[...Array.from({length:5},(_,i)=>({lang:'zh',text:'通知已发送完成'.slice(0,i+3)})),...['n','M','i'].flatMap(c=>Array.from({length:9},(_,i)=>({lang:c,text:c.repeat(i+6)}))),{lang:'emoji',text:'👨‍👩‍👧‍👦é👨‍👩‍👧‍👦'},{lang:'longzh',text:'这是一条较长的通知，应该完整显示并自然换行。'},{lang:'longen',text:'This longer notification must wrap without hiding or clipping its content.'}];
try {for(const width of [390,1440])for(const theme of ['light','dark']) {
 fake.reset();const p=await newPage(b,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try {await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.fonts.ready.then(()=>{document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();const s=document.createElement('style');s.textContent='.toast{transition:none!important}';document.head.append(s);})`);
 for(const scale of [1,1.25,1.6])for(const [index,sample] of samples.entries()) {
  const row=await evaluate(p,`import('./js/ui.js').then(m=>{document.documentElement.style.setProperty('--fz',${scale});const box=m.notify({text:${JSON.stringify(sample.text)},ms:0});const n=box.querySelector('.nt-body'), col=box.querySelector('.nt-text'),cs=getComputedStyle(box),f=getComputedStyle(n),range=document.createRange();range.selectNodeContents(n);const rects=[...range.getClientRects()];return {shape:box.classList.contains('circle')?'circle':'square',width:box.clientWidth,height:box.clientHeight,textWidth:Math.max(0,...rects.map(r=>r.width)),contentWidth:box.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight),textHeight:n.scrollHeight,lineHeight:parseFloat(f.lineHeight),scroll:box.scrollHeight,font:f.font,fontReady:document.fonts.status,lines:rects.length,zoom:devicePixelRatio};})`);
  if(row.shape==='circle'){assert.equal(row.width,row.height,'circle keeps equal diameter');assert.ok(row.textWidth<=row.contentWidth+0.01,JSON.stringify({sample,row}));assert.ok(row.textHeight<=Math.ceil(row.lineHeight));assert.ok(row.scroll<=row.height,'circle must never clip vertically');}
  else assert.ok(row.scroll<=row.height+1,'square content is visible and wraps');
  if(sample.lang==='M'&&sample.text.length===14&&scale===1)assert.equal(row.shape,'square');
  if(sample.lang==='i'&&sample.text.length===14&&scale===1)assert.equal(row.shape,'circle');
  let file=null;if(width===390&&theme==='dark'&&scale===1){file=`${out}/${sample.lang}-${sample.text.length}-${index}.png`;writeFileSync(file,await shoot(p));}
  rows.push({viewport:width,theme,scale,index,...sample,...row,file});
 }
 const negative=await evaluate(p,`import('./js/ui.js').then(m=>{Object.defineProperty(document.fonts,'status',{configurable:true,get:()=> 'loading'});const loading=m.notify({text:'已完成',ms:0}).classList.contains('square');delete document.fonts.status;const box=m.notify({text:'已完成',ms:0});box.style.fontSize='96px';dispatchEvent(new Event('resize'));const huge=box.classList.contains('square');box.style.fontSize='';return {loading,huge};})`);
 assert.deepEqual(negative,{loading:true,huge:true});console.log('PASS p0284-toast-measured-fit',width,theme,samples.length*3,'font-not-ready/large-font negative');
 } finally {await p.dispose();}
}} finally {writeFileSync(`${out}/measurements.json`,JSON.stringify({physical:false,source:'local 0284 preview; approval to publish still pending',rows},null,2));await b.close();await fake.stop();await web.stop();}
