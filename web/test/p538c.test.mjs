import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cpSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {launch,newPage,navigate,evaluate,waitFor,cdp} from './browser.mjs';
import {startWebServer} from './serve.mjs';
const publicDir=fileURLToPath(new URL('../public/',import.meta.url));
const repo=fileURLToPath(new URL('../../../',import.meta.url));
const sample=`document.querySelector('#words').className='words';document.querySelector('#words').textContent='Same short reply';document.querySelector('.omtxt').textContent='Same input';`;
const sizes=`({fz:getComputedStyle(document.documentElement).getPropertyValue('--fz'),rs:document.documentElement.style.getPropertyValue('--rs'),input:parseFloat(getComputedStyle(document.querySelector('.omtxt')).fontSize),reply:parseFloat(getComputedStyle(document.querySelector('#words')).fontSize)})`;
test('#538c saved scales own desktop/TV thresholds, including mixed old CSS and JS', {timeout:60000},async()=>{
 const b=await launch();const dir=mkdtempSync(join(tmpdir(),'aj-538c-'));
 try{
  for(const mix of ['current','old-css','old-js']){
   const root=join(dir,mix);cpSync(publicDir,root,{recursive:true});
   for(const file of mix==='old-css'?['app.css']:mix==='old-js'?['js/relay.js','js/settings.js','js/typography.js']:[])writeFileSync(join(root,file),execFileSync('git',['show','48baf2e936e9f0119dc0cd7bdd5753cbe291ac1b:web/public/'+file],{cwd:repo}));
   const s=await startWebServer({publicDir:root});
   try{for(const pageFz of ['m','l','s']){
    const p=await newPage(b,1680,960,'dark',{allow:[s.url]});
    try{
     await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('aj.pageFz','${pageFz}');localStorage.setItem('aj.fontScale','1.25');`});
     await navigate(p,s.url);
     // Call the same production initializer as app.js; this isolates typography from pairing readiness.
     await evaluate(p,`(async()=>{${sample} (await import('./js/tvremote.js')).initRemote({});})()`);
     for(const width of [1680,1599,1800,1440]){
      await p.send('Emulation.setDeviceMetricsOverride',{width,height:960,deviceScaleFactor:1,mobile:false});
      const a=await evaluate(p,sizes);const f={m:1,l:1.2,s:.85}[pageFz];
      assert.ok(Math.abs(a.reply-18*f*1.25)<.001,JSON.stringify({mix,pageFz,width,a}));
      assert.equal(await evaluate(p,"localStorage.getItem('aj.fontScale')"),'1.25');
     }
    }finally{await p.dispose();}
   }}finally{await s.stop();}
  }
 }finally{await b.close();rmSync(dir,{recursive:true,force:true});}
});
test('#538c message scale applies equally; seat inheritance happens once and tabs synchronize only their own seat', {timeout:60000},async()=>{
 const b=await launch(),s=await startWebServer();let p,q;
 try{
  p=await newPage(b,1440,960,'light',{allow:[s.url]});
  await navigate(p,s.url);
  await evaluate(p,`localStorage.setItem('aj.fontScale','1.4');localStorage.setItem('aj.pageFz','l');`);
  await navigate(p,s.url+'abcdefgh/');
  assert.deepEqual(await evaluate(p,`[localStorage.getItem('aj.fontScale'),localStorage.getItem('aj.pageFz')]`),['1.4','l']);
  await evaluate(p,`(async()=>{${sample} (await import('./js/settings.js')).setFontStep(2);})()`);
  const base=await evaluate(p,sizes);
  await evaluate(p,`(async()=>{(await import('./js/settings.js')).setFontStep(4);})()`);
  const large=await evaluate(p,sizes);
  assert.ok(Math.abs(large.input/base.input-1.25)<.0001);assert.ok(Math.abs(large.reply/base.reply-1.25)<.0001);
  const {targetId}=await b.browser.send('Target.createTarget',{url:'about:blank',browserContextId:p.ctx});
  q=cdp(`ws://127.0.0.1:${b.port}/devtools/page/${targetId}`);await q.ready;await q.send('Page.enable');await q.send('Runtime.enable');
  await navigate(q,s.url+'abcdefgh/');
  await evaluate(q,`(async()=>{(await import('./js/settings.js')).setFontStep(1);})()`);
  await waitFor(p,`document.documentElement.style.getPropertyValue('--rs')==='0.9'`);
  await evaluate(p,`(async()=>{const m=await import('./js/settings.js');m.setFontStep(m.FONT_STEPS.indexOf(m.fontScale())+1);})()`);
  await waitFor(q,`document.documentElement.style.getPropertyValue('--rs')==='1'`);
  await evaluate(q,`(async()=>{const m=await import('./js/settings.js');m.setFontStep(m.FONT_STEPS.indexOf(m.fontScale())+1);})()`);
  await waitFor(p,`document.documentElement.style.getPropertyValue('--rs')==='1.1'`);
  await evaluate(q,`(async()=>{const m=await import('./js/settings.js');m.setFontStep(1);})()`);
  await waitFor(p,`document.documentElement.style.getPropertyValue('--rs')==='0.9'`);
  await evaluate(q,`AJSeat.root.setItem('aj.fontScale','1.6');AJSeat.root.setItem('aj.fontScale@bcdefghi','1.6');AJSeat.root.setItem('aj.pageFz','s');AJSeat.root.setItem('aj.pageFz@bcdefghi','s');`);
  assert.equal((await evaluate(p,sizes)).rs,'0.9');
  await navigate(p,s.url+'abcdefgh/');
  assert.deepEqual(await evaluate(p,`[localStorage.getItem('aj.fontScale'),localStorage.getItem('aj.pageFz')]`),['0.9','l']);
  await evaluate(q,`AJSeat.root.setItem('aj.pageFz@abcdefgh','s')`);
  await waitFor(p,`document.documentElement.style.getPropertyValue('--fz')==='0.85'`);
  assert.equal(await evaluate(p,`document.querySelector('#set-typography-value')`),null);
 }finally{q?.close();await p?.dispose();await b.close();await s.stop();}
});
