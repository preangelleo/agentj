// #557: real client paired to a local fake host; no synthetic header markup.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,sleep,shoot} from './browser.mjs';
const out=process.env.P557_OUT || new URL('../../../reports/qa/robots-overlap-557-20261106/web/',import.meta.url).pathname;
mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*',...(process.env.P557_PUBLIC_DIR?{publicDir:process.env.P557_PUBLIC_DIR}:{})}),fake=await startFakeHost(),B=await launch();
const results=[];
try {
for(const lang of ['zh','en']) for(const theme of ['light','dark']){
 fake.reset();const p=await newPage(B,390,844,theme,{allow:[web.url,fake.relay+'/'],touch:true});
 try {
 await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('confirm-no')?.click();document.getElementById('a2hs-ok')?.click();`);
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await fake.addTurn({k:'phone',dev:'other',text:lang==='zh'?'检查窄屏名字行':'Check the narrow screen header'},lang==='zh'?'机器人会按剩余空间显示。':'Robots fit the space remaining after the name and time.');
 await waitFor(p,`document.getElementById('words').textContent.length>10`);
 for(const width of [280,320,360,390,1440]) for(const fz of [1,1.4]) for(const long of [false,true]) for(const count of [0,3,6,12]){
  await p.send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:1,mobile:false});
  const name=long?(lang==='zh'?'掌枢负责监控全部后台任务的长名字':'Agent J with a very long monitoring name'):'Agent J';
  await fake.send({t:'status',s:'idle',agent:'claude',name});
  await evaluate(p,`(async()=>{document.documentElement.style.setProperty('--fz',${JSON.stringify(String(fz))});document.documentElement.style.fontSize=${JSON.stringify((fz*100)+'%')};(await import('./js/settings.js')).setFontStep(${fz===1?2:5});})()`);
  await fake.send({t:'busy',cap:12,tasks:Array.from({length:count},(_,i)=>({id:'task'+i,who:'Agent '+i,label:'Background task '+i,c:i%9}))});
  await sleep(70);
  const a=await evaluate(p,`(()=>{const q=id=>document.getElementById(id),rect=e=>{const r=e.getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}};const nm=q('agent'),tm=q('rmTime'),b=q('busyDots'),hd=b.parentElement,items=[...b.querySelectorAll('i:not(.bye),b')].map(rect),name=rect(nm),time=rect(tm),box=rect(b),header=rect(hd),om=q('om'),fl=om.querySelector('.omfl'),pg=q('pg');return {name,time,box,header,items,hidden:b.hidden,more:b.querySelector('b')?.textContent||'',shown:b.querySelectorAll('i:not(.bye)').length,ellipsis:getComputedStyle(nm).textOverflow,overflow:hd.scrollWidth>hd.clientWidth,input:{first:rect(fl),page:rect(pg)},errors:window.__p557errors||[]};})()`);
  const tag=JSON.stringify({width,fz,long,count,lang,theme});
  assert.ok(a.name.r<=a.time.l+.5,'name/time '+tag);
  assert.equal(a.overflow,false,'header overflow '+tag);
  assert.equal(a.ellipsis,'ellipsis');
  if(!long)assert.ok(await evaluate(p,`(()=>{const e=document.getElementById('agent'),r=document.createRange();r.selectNodeContents(e);return r.getBoundingClientRect().width<=e.getBoundingClientRect().width+.5})()`),'short name complete '+tag);
  assert.ok(await evaluate(p,`document.getElementById('rmTime').scrollWidth<=document.getElementById('rmTime').clientWidth+1`),'time complete '+tag);
  assert.ok(a.input.first.r<=a.input.page.l+.5,'input/page '+tag);
  if(count){assert.equal(a.hidden,false);assert.ok(a.box.l>=a.time.r-.5,'time/button '+tag);assert.ok(a.box.r<=a.header.r+.5,'button/header '+tag);assert.equal(a.shown+(Number(a.more.slice(1))||0),count,'accounting '+tag);for(const r of a.items){assert.ok(r.l>=a.box.l-.5&&r.r<=a.box.r+.5,'robot/count overflow '+tag);assert.equal(r.h,18,'same height '+tag);}}
  else assert.ok(a.hidden || a.items.length===0,'zero tasks '+tag);
  results.push({width,fz,long,count,lang,theme,...a});
  if(!long&&count===12&&[280,390,1440].includes(width)&&fz===1.4)writeFileSync(out+`557-机器人-${lang}-${theme}-${width}-140.png`,await shoot(p,''));
 }
 // Resize and font changes alone must refit the existing list, without another busy frame.
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await evaluate(p,`document.documentElement.style.setProperty('--fz','1')`);
 await fake.send({t:'busy',cap:12,tasks:Array.from({length:12},(_,i)=>({id:'live'+i,who:'Agent '+i,c:i%9}))});
 await p.send('Emulation.setDeviceMetricsOverride',{width:280,height:844,deviceScaleFactor:1,mobile:false});await sleep(100);
 const narrow=await evaluate(p,`document.querySelectorAll('#busyDots i:not(.bye)').length`);
 await p.send('Emulation.setDeviceMetricsOverride',{width:1440,height:844,deviceScaleFactor:1,mobile:false});await sleep(100);
 const wide=await evaluate(p,`document.querySelectorAll('#busyDots i:not(.bye)').length`);
 assert.ok(wide>narrow,'ResizeObserver restores robots without busy frame');
 await p.send('Emulation.setDeviceMetricsOverride',{width:280,height:844,deviceScaleFactor:1,mobile:false});await sleep(100);
 await evaluate(p,`document.documentElement.style.setProperty('--fz','1.4')`);await sleep(100);
 const font=await evaluate(p,`document.querySelectorAll('#busyDots i:not(.bye)').length`);
 assert.ok(font<narrow,'ResizeObserver refits larger name/time fonts without busy frame');
 }catch(e){writeFileSync(out+`557-机器人-${lang}-${theme}-failed.png`,await shoot(p,''));throw e;}finally{await p.dispose();}
}
writeFileSync(out+'results.json',JSON.stringify(results,null,2));console.log('P557 PASS: '+results.length+' real-client viewport/font/name/task/language/theme cases');
}finally{await B.close();await fake.stop();await web.stop();}
