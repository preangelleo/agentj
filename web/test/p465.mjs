// P465: all bound remotes must remain reachable with real wheel/click input.
import assert from 'node:assert/strict';
import {launch,newPage,evaluate,navigate,waitFor,waitState,key as press,rect,shoot,sleep,swipe} from './browser.mjs';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
// Uses the repository's isolated Chromium helper; no extra npm installation.
function locator(p,sel){return {
 async click(){await evaluate(p,`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'nearest'})`);const r=await rect(p,sel);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});},
 async evaluate(fn){return evaluate(p,`(${fn})(document.querySelector(${JSON.stringify(sel)}))`);},
 async textContent(){return this.evaluate(e=>e.textContent);},
 async fill(value){return evaluate(p,`document.querySelector(${JSON.stringify(sel)}).value=${JSON.stringify(value)}`);},
 async hover(){await evaluate(p,`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'nearest'})`);const r=await rect(p,sel);await p.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:r.x,y:r.y});p.mousePos=r;},
 locator(child){return locator(p,sel+' '+child);},last(){return locator(p,sel+':last-child');}}}

const out='reports/qa/p465';mkdirSync(out+'/screens',{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const results=[],failures=[];
try { for(const count of [6,10]) for(const lang of ['zh','en']) for(const theme of ['light','dark']) {
 const key=`390-${lang}-${theme}-${count}`,p=await newPage(B,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
 p.locator=sel=>locator(p,sel);p.evaluate=(fn,arg)=>evaluate(p,`(${fn})(${JSON.stringify(arg)??''})`);p.waitForFunction=(fn,arg)=>waitFor(p,`(${fn})(${JSON.stringify(arg)??''})`);p.screenshot=async({path})=>writeFileSync(path,await shoot(p));p.mouse={wheel:async(x,y)=>{await p.send('Input.dispatchMouseEvent',{type:'mouseWheel',x:p.mousePos.x,y:p.mousePos.y,deltaX:x,deltaY:y});await sleep(80)}};p.waitForTimeout=sleep;
 try {
 fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme}};
 fake.remotes({limit:10,items:Array.from({length:count},(_,i)=>({id:`remote${i}`,name:`Remote ${i+1}`,paired_at:1700000000-i,online:i===0,me:i===0,source:'phone'}))});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(theme=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=theme;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important}';document.head.append(s)},theme);
 await p.locator('#setBtn').click();await p.locator('#set-remotes-open').click();await p.waitForFunction(n=>document.querySelectorAll('#rm-list .rmitem').length===n,count);
 assert.equal(await p.locator('#rm-limit').textContent(),'10');
 const geometry=await p.locator('#set-remotes').evaluate(e=>({height:e.clientHeight,scroll:e.scrollHeight,top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,overflow:getComputedStyle(e).overflowY,viewport:innerHeight}));
 console.log(key,JSON.stringify(geometry));
 assert.ok(geometry.bottom<=geometry.viewport+1,'scroll container must fit the viewport');
 const listGeometry=await p.locator('#rm-list').evaluate(e=>({height:e.clientHeight,scroll:e.scrollHeight,focusable:e.tabIndex>=0}));
 assert.ok(listGeometry.scroll>listGeometry.height,'list itself has bounded scroll range');
 assert.ok(listGeometry.focusable,'list accepts keyboard scrolling');
 await p.screenshot({path:`${out}/screens/${key}-top.png`});
 await p.locator('#rm-list').evaluate(e=>e.focus());await press(p,'End',{keyCode:35});await sleep(150);
 assert.ok(await p.locator('#rm-list').evaluate(e=>e.scrollTop>0),'keyboard End scrolls list');
 await press(p,'Home',{keyCode:36});await sleep(150);
 const touch=await rect(p,'#rm-list');await swipe(p,touch.x,touch.y+60,touch.x,touch.y-60);await sleep(150);
 assert.ok(await p.locator('#rm-list').evaluate(e=>e.scrollTop>0),'touch swipe scrolls list');
 // Real wheel, no scrollIntoView/JS click: those could hide an unreachable list.
 await p.locator('#rm-list').hover();for(let i=0;i<8;i++)await p.mouse.wheel(0,700);await p.waitForTimeout(250);
 const last=p.locator('#rm-list .rmitem').last(),rename=last.locator('[data-act=rename]'),unbind=last.locator('[data-act=unbind]');
 for(const control of [rename,unbind])assert.ok(await control.evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)?.closest('button')===e}),'last remote actions reachable after wheel');
 await p.screenshot({path:`${out}/screens/${key}-bottom.png`});
 await rename.click();await p.locator('.rmrename input').fill('Last remote renamed');await p.locator('.rmrename button[type=submit]').click();
 await p.waitForFunction(()=>[...document.querySelectorAll('.rmname__t')].some(e=>e.textContent==='Last remote renamed'));
 assert.equal(fake.st.remotes.writes.at(-1).sigOk,true);
 await p.locator(`#rm-list [data-id=remote${count-1}] [data-act=unbind]`).click();await p.locator('#confirm-yes').click();await p.waitForFunction(n=>document.querySelectorAll('#rm-list .rmitem').length===n,count-1);
 assert.equal(fake.st.remotes.writes.at(-1).sigOk,true);
 assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
 results.push({key,count,geometry,listGeometry,keyboard:true,touch:true,signedRename:true,signedUnbind:true,pass:true});console.log('PASS',key);
 } catch(e){failures.push({key,error:String(e.stack||e)});console.log('FAIL',key,String(e));}finally{await p.dispose()}
 }}finally{await B.close();await fake.stop();await web.stop()}
writeFileSync(out+'/results.json',JSON.stringify({results,failures,combined:JSON.parse(readFileSync('web/public/version.json')).combined},null,2)+'\n');
assert.deepEqual(failures,[]);
