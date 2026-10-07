// P75: real encrypted browser history and water geometry. All resources are local fixtures.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {cdp,launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=new URL('../../../reports/qa/p75/',import.meta.url);mkdirSync(out,{recursive:true});
const results={};const web=await startWebServer(),fake=await startFakeHost(),B=await (async()=>{
 if(!process.env.AJ_P75_CDP)return launch();
 const v=await (await fetch("http://127.0.0.1:9222/json/version")).json();const browser=cdp(v.webSocketDebuggerUrl);await browser.ready;
 const send=browser.send;
 // The shared desktop compositor does not paint isolated-context windows here.
 // Use a tab owned by this suite in the default context; erase only our random local origin in finally.
 browser.send=async(method,params={})=>method==='Target.createBrowserContext'?{}:send(method,params);
 return {port:9222,browser,close:async()=>browser.close()};
})();
let ownedPage;
const capture=async p=>{if(!process.env.AJ_P75_CDP)return shoot(p,'');
 const r=await Promise.race([p.send('Page.captureScreenshot',{format:'png',fromSurface:true}),new Promise((_,reject)=>setTimeout(()=>reject(new Error('9222 screenshot timeout')),15000))]);return Buffer.from(r.data,'base64');};
const check=(name)=>{results[name]='pass';console.log('PASS '+name);};
try {
 const p=ownedPage=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 const tx=()=>evaluate(p,`document.getElementById('words').textContent`);
 const pg=()=>evaluate(p,`document.getElementById('pg').textContent`);
 const open=()=>evaluate(p,`document.getElementById('slashBtn').click();document.getElementById('silentEntry').click()`);
 await fake.addTurn({k:'host',text:'one'},'answer one');await waitFor(p,`document.getElementById('words').textContent.includes('answer one')`);
 await fake.addTurn({k:'agent',text:'silent tail'},'〔不回群〕');await sleep(300);
 assert.match(await tx(),/answer one/);assert.equal(await pg(),'1 / 1');check('silent:tail');
 await fake.addTurn({k:'host',text:'two'},'answer two');await waitFor(p,`document.getElementById('words').textContent.includes('answer two')`);
 await evaluate(p,`document.getElementById('pgPrev').click()`);await sleep(100);
 await fake.addTurn({k:'agent'},'〔不回群〕');await sleep(300);assert.match(await tx(),/answer one/);check('silent:arrival-stays');
 await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'k',code:'KeyK'});await sleep(100);assert.match(await tx(),/answer two/);
 await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'j',code:'KeyJ'});await sleep(100);assert.match(await tx(),/answer one/);
 await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'g',code:'KeyG'});await sleep(100);assert.match(await tx(),/answer two/);
 await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'G',code:'KeyG',modifiers:8});await sleep(100);assert.match(await tx(),/answer one/);check('silent:middle-jkgG');
 await open();await waitFor(p,`document.querySelectorAll('#silentList details').length===2`);
 assert.equal(await evaluate(p,`document.querySelectorAll('#words details').length`),0);
 await evaluate(p,`document.querySelector('#silentList summary').click()`);
 if(process.env.AJ_P75_CDP)await p.send('Page.bringToFront');
 writeFileSync(new URL(process.env.AJ_P75_CDP?'cdp9222-silent-raw.png':'silent-panel.png',out),await capture(p));
 await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});assert.equal(await evaluate(p,`document.getElementById('silent').hidden`),true);
 await fake.clearHistory();await sleep(150);await fake.addTurn({k:'agent'},'〔不回群〕');await sleep(300);
 assert.equal(await pg(),'0 / 0');assert.ok(!(await tx()).includes('〔不回群〕'));check('silent:all-zero');
 await open();await fake.clearHistory();await sleep(300);
 assert.equal(await evaluate(p,`document.querySelectorAll('#silentList details').length`),0);assert.equal(await pg(),'0 / 0');check('silent:clear');
 await evaluate(p,`document.getElementById('silentClose').click()`);
 // Seed a history older than the newest batch, then reconnect so real hist_page pagination loads it.
 await fake.addTurn({k:'host',text:'old'},'old visible answer');
 for(let i=0;i<105;i++)await fake.addTurn({k:'agent',text:'old silent '+i},'〔不回群〕');
 await navigate(p,web.url);await waitState(p,'ready');await waitFor(p,`document.getElementById('pg').textContent==='1 / 1'`,20000);
 assert.match(await tx(),/old visible answer/);check('silent:history-across');
 await open();await waitFor(p,`document.querySelectorAll('#silentList details').length===105`);check('silent:panel-history');
 await evaluate(p,`document.getElementById('silentClose').click()`);
 // At the end of a full half-width translation both layers must still span the viewport.
 await evaluate(p,`document.querySelectorAll('.wave').forEach(e=>{e.style.animation='none';e.style.transform='translateX(-50%)'});document.querySelector('.water').style.height='50%';document.querySelector('.water').dataset.known='1'`);
 const geom=await evaluate(p,`Array.from(document.querySelectorAll('.water .wave')).map(e=>{const r=e.getBoundingClientRect(),w=e.parentElement.getBoundingClientRect();return {width:r.width,parent:w.width,left:r.left,right:r.right,viewport:document.documentElement.getBoundingClientRect().width,roundedInnerWidth:innerWidth}})`);
 writeFileSync(new URL("f34-geometry.json",out),JSON.stringify(geom,null,2));
 for(const x of geom){assert.equal(x.width,2*x.parent);assert.ok(x.left<=0 && x.right>=x.viewport);}
 mkdirSync(new URL('f34/',out),{recursive:true});writeFileSync(new URL('f34/translated.png',out),await capture(p));
 await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 await evaluate(p,`document.querySelector('.water').dataset.compacting='1'`);
 assert.equal(await evaluate(p,`getComputedStyle(document.querySelector('.water')).animationDuration`),'14s');
 assert.equal(await evaluate(p,`getComputedStyle(document.querySelector('.water')).animationIterationCount`),'1');check('water:double-width-reduced-motion');
 // Simulated iOS hidden interval + two host handshake-timeout closes, using the same real Noise device key.
 fake.refuseNextResumes(2);
 await evaluate(p,`Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));`);
 fake.kick(4010);await sleep(4000);
 assert.notEqual(await evaluate(p,`window.__ajState`),'revoked');
 assert.equal(await evaluate(p,`import('./js/store.js').then(m=>m.dbGet('host')).then(h=>h.approved)`),true);
 await evaluate(p,`Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));`);
 await waitState(p,'ready',12000);check('session:hidden-timeouts-resume');
 assert.deepEqual(p.problems,[]);
} finally {if(ownedPage && process.env.AJ_P75_CDP)await ownedPage.send('Storage.clearDataForOrigin',{origin:new URL(web.url).origin,storageTypes:'all'}).catch(()=>{});if(ownedPage)await ownedPage.dispose();await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('browser-results.json',out),JSON.stringify(results,null,2)+'\n');
if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p75.mjs',results}));
