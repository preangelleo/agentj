// P575: real page in WebKit, local host; DOM clicks follow the existing Linux WebKit fixture contract.
// WPE has no native media backend (same limitation as p205_rec_webkit): Audio is a stand-in.
// Media decoding/playback success and physical iPhone gesture policy remain device acceptance.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const out = new URL('../../../reports/qa/p575-linux/webkit/', import.meta.url).pathname;
mkdirSync(out, {recursive:true});
const web = await startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'}), fake = await startFakeHost();
const url = web.url.replace('127.0.0.1','localhost');
const browser = await pw.webkit.launch();
let checks=0;
const check=(v,m)=>{assert.ok(v,m); checks++;};
try {
 for(const language of ['zh','en']) for(const theme of ['light','dark']) {
  fake.reset(); fake.st.prefSet=true; fake.st.prefs={appearance:{language,theme:'system'}};
  const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,colorScheme:theme});
  await ctx.addInitScript(()=>{
   window.AudioContext=undefined; window.webkitAudioContext=undefined;
   window.__AJ_TEST_CUES__=true;
   window.__cuePlays=[];
   window.Audio=function(src){return {src,preload:'',volume:1,currentTime:0,paused:true,ended:false,muted:false,
    play(){this.paused=false;window.__cuePlays.push({src:this.src,muted:this.muted,volume:this.volume});return Promise.resolve();},
    pause(){this.paused=true;}, addEventListener(){}, removeEventListener(){}};};
  });
  const p=await ctx.newPage(); console.log('START',language,theme); p.on('crash',()=>console.error('PAGE CRASH')); p.on('pageerror',e=>console.error('PAGE ERROR',String(e)));
  const wait=()=>p.waitForTimeout(800);
  const click=s=>p.evaluate(s=>document.querySelector(s).click(),s);
  const state=async(s)=>{await fake.send({t:'status',s,agent:'claude',name:'AgentJ'});await wait();};
  const cues=()=>p.evaluate(()=>({n:+(document.body.dataset.cues||0),last:document.body.dataset.cue}));
  const busy=async(n)=>{await fake.send({t:'busy',by:'AgentJ',task_count:n,tasks:Array.from({length:n},(_,i)=>({id:'k'+i,kind:'temp',label:'task',since:1760000000,c:i}))});await wait();};
  const reload=async()=>{await p.goto(url+'?lang='+language);await p.waitForFunction(()=>window.__ajState==='ready');await state('idle');};
  try {
   await p.goto(fake.newPairing(url+'?lang='+language));
   await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();
   await p.waitForFunction(()=>window.__ajState==='ready');await state('idle');
   await p.evaluate(()=>document.getElementById('phone-env-dismiss')?.click());
   await click('#setBtn');await wait();
   for(const id of ['idle','working','waiting','question','offline','all-done']) {
    check(await p.locator('#set-cue-'+id).getAttribute('aria-checked')==='true',id+' defaults on');
    await click('#set-cue-'+id);check(await p.locator('#set-cue-'+id).getAttribute('aria-checked')==='false',id+' switches off');
    await click('#set-cue-'+id);
    check(await p.evaluate(id=>document.querySelector('[data-cue-play="'+id+'"]')?.getAttribute('aria-label'),id),id+' named preview');
   }
   await p.locator('#set-cues').scrollIntoViewIfNeeded();await p.screenshot({path:out+language+'-'+theme+'.png'});
   check(await p.locator('#set-s-read input[type=range]').count()===1,'one volume');
   await click('#set-cue-offline');await reload();await click('#setBtn');await wait();
   check(await p.locator('#set-cue-offline').getAttribute('aria-checked')==='false','setting survives reload');
   await click('#set-cue-offline');await click('[data-cue-play="question"]');await wait();
   check((await cues()).last==='question','preview reaches player');
   await click('#setBtn');await state('idle');const base=(await cues()).n;
   await state('working');check((await cues()).last==='working','working cue');await p.waitForTimeout(2100);
   await fake.send({t:'status',s:'waiting',agent:'claude',name:'AgentJ'});
   await fake.ask({id:'a'.repeat(32),tool:'Bash',summary:'git status',ttl:120});await wait();
   check((await cues()).n===base+2 && (await cues()).last==='waiting','card and orange deduplicated');
   await fake.askDone([...fake.st.asksOpen.keys()][0],'allow');await busy(1);await state('working');await p.waitForTimeout(2100);
   await state('idle');check((await cues()).last==='idle','remaining task gives idle cue');
   await busy(0);check((await cues()).last==='all-done' && (await cues()).n===base+5,'last task finishes once');
   await busy(0);await state('idle');check((await cues()).n===base+5,'no duplicate all done');
   await p.evaluate(()=>localStorage.setItem('aj.cueOff','all-done'));await reload();
   await click('#setBtn');await click('[data-cue-play="question"]');await wait();await click('#setBtn');
   await state('working');await state('idle');check((await cues()).last==='idle','all done off falls back to idle');
   await p.evaluate(()=>localStorage.setItem('aj.sfxVolume','-15'));await reload();
   await click('#setBtn');await click('[data-cue-play="question"]');await click('#setBtn');await state('working');await state('idle');
   check((await cues()).n===0,'zero volume mutes previews and transitions');
  } finally {await ctx.close();}
 }
 console.log('p575 WebKit PASS '+checks+' checks; '+out);
} finally {await browser.close();await fake.stop();await web.stop();}
