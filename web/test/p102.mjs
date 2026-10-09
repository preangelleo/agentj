// P102 real page + encrypted local fake host; no production or shared browser.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const $ = id => `document.getElementById('${id}')`;
const state = p => evaluate(p,`({page:${$('pg')}.textContent,unread:${$('pgCorner')}.dataset.unread==='1',words:${$('words')}.textContent})`);
async function mouse(p,id,count=1){
 const r=await evaluate(p,`(()=>{const r=${$(id)}.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 for(let i=1;i<=count;i++){
  await p.send('Input.dispatchMouseEvent',{type:'mousePressed',...r,button:'left',clickCount:i});
  await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',...r,button:'left',clickCount:i});
 }
}
async function touch(p,id,count=1){
 const r=await evaluate(p,`(()=>{const r=${$(id)}.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 for(let i=0;i<count;i++){
  await p.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[r]});
  await p.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  if(i+1<count)await sleep(70);
 }
}
export async function runP102Screens({B,web,fake,preview=false,out='reports/qa/p102/screens',previewOut=process.env.P102_PREVIEW_DIR}){
 mkdirSync(out,{recursive:true});if(previewOut)mkdirSync(previewOut,{recursive:true});
 const results={};
 for(const variant of ['final'])for(const lang of ['zh','en'])for(const theme of ['light','dark']){
  if(preview && lang==='en')continue;
  fake.reset();
  const p=await newPage(B,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   const dev=await evaluate(p,`import('./js/api.js').then(api=>api.myDeviceId())`);
   for(let i=1;i<=6;i++)await fake.addTurn({k:'phone',dev,text:lang==='zh'?`请帮我整理第 ${i} 份周报。`:`Please review weekly report ${i}.`},lang==='zh'?`第 ${i} 份周报已经整理好了。\n\n重点事项与下一步安排都在这份记录里。`:`Weekly report ${i} is ready.\n\nThe key points and next steps are in this record.`);
   await waitFor(p,`${$('pg')}.textContent==='6 / 6'`);
   await sleep(200);
   const geometry=await evaluate(p,`(()=>{const ids=['om','omFirst','pgLatest','pgCorner'];return Object.fromEntries(ids.map(id=>{const r=document.getElementById(id).getBoundingClientRect();return[id,{x:r.x,y:r.y,w:r.width,h:r.height}]}))})()`);
   const save=async name=>{const b=await shoot(p,'');writeFileSync(resolve(out,`${variant}-${lang}-${theme}-${name}.png`),b);if(previewOut && lang==='zh' && ['normal','unread'].includes(name))writeFileSync(resolve(previewOut,`P102-preview-${variant}-${theme}-${name}.png`),b);};
   await save('normal');
   await evaluate(p,`${$('pgPrev')}.click()`);await waitFor(p,`${$('pg')}.textContent==='5 / 6'`);
   await fake.addTurn({k:'phone',dev,text:lang==='zh'?'再补上最新的一份。':'Add the latest report.'},lang==='zh'?'新的周报已完成，请查看。':'The latest report is ready.');
   await waitFor(p,`${$('pgCorner')}.dataset.unread==='1' && ${$('pg')}.textContent==='5 / 7'`);
   await sleep(150);await save('unread');
   const facts=await evaluate(p,`(()=>{const n=${$('pgLatest')},r=n.getBoundingClientRect(),c=${$('pgCorner')}.getBoundingClientRect(),o=${$('om')}.getBoundingClientRect();return {text:n.textContent.trim(),label:n.getAttribute('aria-label'),title:n.title,bg:getComputedStyle(n).backgroundColor,w:r.width,h:r.height,cover:r.x===c.x&&r.y===c.y&&r.width===c.width&&r.height===c.height,om:{x:o.x,y:o.y,w:o.width,h:o.height},pager:getComputedStyle(${$('pgLatest')}).visibility,bodySelect:getComputedStyle(${$('omText')}).userSelect,headerSelect:getComputedStyle(${$('omFirst')}).userSelect,touch:getComputedStyle(${$('pgLatest')}).touchAction,overflow:document.documentElement.scrollWidth>390}})()`);
   assert.equal(facts.text,'5 / 7');assert.equal(await evaluate(p,`${$('om')}.dataset.src`),'leo');assert.deepEqual(facts.om,geometry.om);assert.equal(facts.pager,'visible');assert.equal(facts.overflow,false);assert.notEqual(facts.bodySelect,'none');assert.equal(facts.headerSelect,'none');assert.equal(facts.touch,'manipulation');assert.match(facts.label,lang==='zh'?/有新消息/:/New message/);assert.equal(facts.label,facts.title);
   assert.ok(geometry.omFirst.w>=44&&geometry.omFirst.h>=44&&geometry.pgLatest.w>=44&&geometry.pgLatest.h>=44);
   {
    const pulse=await evaluate(p,`getComputedStyle(${$('pg')}).animationDuration`);assert.equal(pulse,'1.2s');
    await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'},{name:'prefers-color-scheme',value:theme}]});
    assert.equal(await evaluate(p,`getComputedStyle(${$('pg')}).animationName`),'none');
    assert.equal(await evaluate(p,`getComputedStyle(${$('pg')}).backgroundColor`),'rgb(183, 227, 150)');
    await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'},{name:'prefers-color-scheme',value:theme}]});
   }
   // Sample exact endpoints and intermediate frames on both real source-card colors.
   const colors={};
   for(const color of ['blue','yellow']){
    await evaluate(p,`${$('om')}.dataset.src='${color==='blue'?'host':'leo'}';${$('pg')}.style.animationPlayState='paused'`);
    const samples=[];
    for(let i=0;i<4;i++){
     await evaluate(p,`${$('pg')}.style.animationDelay='-${i*.3}s'`);await sleep(50);
     samples.push(await evaluate(p,`(()=>{const e=${$('pg')},r=e.getBoundingClientRect(),s=getComputedStyle(e);return {bg:s.backgroundColor,ink:s.color,opacity:s.opacity,x:r.x,y:r.y,w:r.width,h:r.height}})()`));
     if(preview && previewOut)writeFileSync(resolve(previewOut,`P102-preview-final-${theme}-${color}-${i}.png`),await shoot(p,''));
    }
    assert.equal(samples[0].bg,'rgb(183, 227, 150)');assert.equal(samples[2].bg,'rgb(252, 190, 124)');
    for(const f of samples){assert.equal(f.ink,'rgb(20, 20, 20)');assert.equal(f.opacity,'1');for(const k of ['x','y','w','h'])assert.equal(f[k],samples[0][k]);}
    colors[color]=samples;
   }
   facts.colors=colors;
   await evaluate(p,`${$('om')}.dataset.src='leo';${$('pg')}.style.animationPlayState='';${$('pg')}.style.animationDelay=''`);
   if(preview){
    results[variant+'-'+lang+'-'+theme]={geometry,unread:facts};continue;
   }
   await touch(p,'pgLatest');await waitFor(p,`${$('pg')}.textContent==='7 / 7' && ${$('pgCorner')}.dataset.unread==='0'`);
   assert.equal(await evaluate(p,`getComputedStyle(${$('pgLatest')}).visibility`),'visible');
   assert.equal(await evaluate(p,`getComputedStyle(${$('pg')}).animationName`),'none');
   assert.equal(await evaluate(p,`getComputedStyle(${$('pg')}).backgroundColor`),'rgba(0, 0, 0, 0)');
   // Single mouse click / single phone tap do nothing on either shortcut.
   for(const id of ['pgLatest','omFirst']){
    const before=await state(p);await mouse(p,id);await sleep(400);assert.deepEqual(await state(p),before);
    await touch(p,id);await sleep(450);assert.deepEqual(await state(p),before);
   }
   await touch(p,'omFirst',2);await waitFor(p,`${$('pg')}.textContent==='1 / 7'`);await save('touch-first');
   await touch(p,'pgLatest',2);await waitFor(p,`${$('pg')}.textContent==='7 / 7'`);await save('touch-latest');
   await sleep(750);await mouse(p,'omFirst',2);await waitFor(p,`${$('pg')}.textContent==='1 / 7'`);await save('mouse-first');
   await sleep(750);await mouse(p,'pgLatest',2);await waitFor(p,`${$('pg')}.textContent==='7 / 7'`);await save('mouse-latest');
   await evaluate(p,`${$('omMore')}.click()`);
   const newest=await state(p);await mouse(p,'pgLatest',2);assert.deepEqual(await state(p),newest);
   assert.equal(await evaluate(p,`${$('om')}.classList.contains('open')`),true,'latest shortcut preserves expanded input card');
   await evaluate(p,`${$('omMore')}.click()`);
   // Normal paging to newest also clears unread; no extra render required.
   await evaluate(p,`${$('pgPrev')}.click()`);await fake.addTurn({k:'phone',dev,text:'P102 arrival'},'P102 latest');await waitFor(p,`${$('pgCorner')}.dataset.unread==='1'`);
   await evaluate(p,`${$('pgNext')}.click();${$('pgNext')}.click()`);await waitFor(p,`${$('pg')}.textContent==='8 / 8' && ${$('pgCorner')}.dataset.unread==='0'`);
   // Long history: first shortcut fetches beyond the loaded 50-turn window.
   for(let i=9;i<=62;i++)await fake.addTurn({k:'phone',dev,text:`History ${i}`},`Reply ${i}`);
   await navigate(p,web.url+'?lang='+lang);await waitState(p,'ready');await waitFor(p,`${$('pg')}.textContent==='62 / 62'`);
   await touch(p,'omFirst',2);await waitFor(p,`${$('pg')}.textContent==='1 / 62'`);
   assert.match((await state(p)).words,lang==='zh'?/第 1 份/:/report 1/);
   // Accessible keyboard activation works without adding a single-click action.
   await evaluate(p,`${$('pgLatest')}.focus()`);
   await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
   await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
   await waitFor(p,`${$('pg')}.textContent==='62 / 62'`);
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
   results[variant+'-'+lang+'-'+theme]={result:'pass',geometry,unread:facts};console.log('PASS P102 '+variant+' '+lang+'-'+theme);
  }finally{await p.dispose();}
 }
 writeFileSync(resolve(out,'results.json'),JSON.stringify(results,null,2)+'\n');
 if(preview && previewOut)writeFileSync(resolve(previewOut,'P102-PREVIEW-READY'),'390px 最终方案 B：Sage / Buttery 胶囊两色闪烁；浅深色蓝黄卡各四帧及正常页码预览就绪。\n');
 return results;
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
 const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
 try{await runP102Screens({B,web,fake,preview:process.argv.includes('--preview')});}finally{await B.close();await fake.stop();await web.stop();}
}
