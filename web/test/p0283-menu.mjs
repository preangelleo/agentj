// Real paired client + local Noise; WebKit iPhone viewport and Chromium Android. No physical-device claim.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const require=createRequire(import.meta.url),pw=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/qa571-pw/node_modules/playwright');
const phase=process.env.P0283_MENU_PHASE||'after',out=process.env.P0283_MENU_OUT||'reports/qa/p0283-menu';mkdirSync(out,{recursive:true});
const web=await startWebServer({...(process.env.P0283_MENU_PUBLIC?{publicDir:process.env.P0283_MENU_PUBLIC}:{})}),fake=await startFakeHost(),rows=[];
const FX=JSON.parse(readFileSync('host/tests/fixtures/p360_cards.json'));const WF=JSON.parse(readFileSync('host/tests/fixtures/p244_wf.json'));
try{for(const [device,engine]of[['iphone390','webkit'],['android390','chromium']])for(const theme of ['dark','light']){
 fake.reset();fake.st.caps=['wf1','jcards1'];fake.st.prefs={appearance:{language:'zh',theme},voice:{speak_replies:false,wake_enabled:false}};
 fake.st.onApp=async(c,m,send)=>{if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards:FX.list.cards});return true;}if(m.t==='wf_list'){for(const x of WF.wf_list)await send({...x,r:m.r});return true;}return false;};
 const b=await pw[engine].launch(engine==='chromium'?{executablePath:'/usr/bin/chromium',args:['--no-sandbox']}:{}),ctx=await b.newContext({viewport:{width:390,height:844},deviceScaleFactor:1,isMobile:true,hasTouch:true,colorScheme:theme,userAgent:device==='iphone390'?'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/142.0.0.0 Mobile Safari/537.36'}),p=await ctx.newPage();
 try{
  await p.addInitScript(()=>{Object.defineProperty(window,'AudioContext',{value:undefined,configurable:true});Object.defineProperty(window,'webkitAudioContext',{value:undefined,configurable:true});window.Audio=function(){return {preload:'',volume:1,currentTime:0,paused:true,play(){return Promise.resolve();},pause(){},addEventListener(){},removeEventListener(){}};};});
  await p.goto(fake.newPairing(web.url.replace('127.0.0.1','localhost')+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');await p.evaluate(()=>document.getElementById('phone-env-dismiss')?.click());
  await fake.send({t:'status',s:'idle',name:'Agent J',agent:'claude'});await fake.send({t:'meter',model:'opus',model_name:'Opus 5.5 (1M context)',effort:'medium',at:Math.floor(Date.now()/1000)});
  await p.evaluate(()=>document.getElementById('status').textContent='已送到，正在等待回复');
  await p.evaluate(()=>{const e=document.getElementById('orb');e.dispatchEvent(new PointerEvent('pointerdown',{button:0}));e.dispatchEvent(new PointerEvent('pointerup',{button:0}));});
  await p.waitForFunction(()=>document.querySelectorAll('#five-menu .fp-menu-card').length>=10);await p.waitForTimeout(450);
  const geometry=await p.evaluate(()=>{const rect=e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}};return {cards:[...document.querySelectorAll('#five-menu .fp-menu-card')].map(e=>({id:e.dataset.page||e.dataset.card,title:e.querySelector('h2').textContent,index:getComputedStyle(e).getPropertyValue('--fp-i').trim(),...rect(e)})),statusDisplay:getComputedStyle(document.getElementById('status')).display,meta:rect(document.getElementById('meta')),plus:rect(document.getElementById('cardPlus')),overflow:document.documentElement.scrollWidth>innerWidth};});
  const file=`${out}/${device}-${theme}-${phase}.png`;await p.screenshot({path:file});rows.push({device,width:390,theme,phase,file,sha256:createHash('sha256').update(readFileSync(file)).digest('hex'),geometry});
  if(phase==='after'){
   for(let i=0;i<geometry.cards.length;i++){assert.equal(Number(geometry.cards[i].index),i,`${geometry.cards[i].id} has unique card index`);if(i)assert.equal(geometry.cards[i].y-geometry.cards[i-1].y,44,'fixed accepted peek; no overlapping labels');}
   assert.equal(geometry.statusDisplay,'none','paired menu has no unsolicited delivery text beside model pill');const offline=await p.evaluate(()=>{const previous=document.body.dataset.conn;document.body.dataset.conn='off';const value=getComputedStyle(document.getElementById('status')).display;document.body.dataset.conn=previous;return value;});assert.notEqual(offline,'none','unpaired/offline connection prompt remains visible');assert.equal(geometry.overflow,false);assert.ok(geometry.meta.x+geometry.meta.w<=geometry.plus.x+1,'model pill does not run into +');
  }
  console.log('PASS p0283-menu',device,theme,phase);
 }finally{await ctx.close();await b.close();}
}}finally{writeFileSync(`${out}/${phase}.json`,JSON.stringify({baseline:'6f3ee0fe51 (formal0.28.1); menu/topbar source identical to0.28.2, both exhibit legacy defect',physical:false,rows},null,2));await fake.stop();await web.stop();}
