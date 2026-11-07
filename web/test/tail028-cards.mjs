// Real client + Noise fake host. All messages are synthetic; no external transport.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost,sampleFriends} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitFor,waitState,shoot} from './browser.mjs';
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});
const fake=await startFakeHost();
const browser=await launch({args:['--no-sandbox','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1']});
let checks=0;
const bot={id:'a'.repeat(32),slug:'synthetic-shop',title:'Synthetic support',description:'Synthetic only',prompt:'Fixture scope',background:'',timezone:'UTC',language:'auto',provider:{source:'main'},template:'customer_service',hours:null,enabled:false,limits:{host_tokens:10000},safety:{provider:'openrouter'},telegram:{}};
try {
 for(const width of [390,1440]) {
  fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:'en',theme:'dark'},voice:{speak_replies:false,wake_enabled:false}};
  const friends=sampleFriends();friends.hist[friends.friends[0].id]=[{mid:'synthetic',dir:'in',text:'SYNTHETIC FRIEND MESSAGE',ts:Date.now(),s:''}];fake.friends(friends);
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards:[],system:[],colors:{}});return true;}
   if(m.t==='bots_read'){
    const op=m.request.op;
    const data=op==='list'?{bots:[bot]}:op==='detail'?{bot}:op==='handoffs'?{handoffs:[]}:
     op==='history'?(m.request.visitor?{messages:[{role:'user',text:'SYNTHETIC SUPPORT MESSAGE',ts:Date.now()}]}:{sessions:[{visitor:'fixture-visitor',messages:1}]}):{};
    await send({t:'bots_res',r:m.r,ok:true,...data});return true;
   }return false;
  };
  const p=await newPage(browser,width,900,'dark',{touch:width===390,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,"document.getElementById('phone-env-dismiss')?.click()");
  await evaluate(p,"import('./js/five-pages.js').then(m=>m.toggleMenu())");
  await waitFor(p,"!!document.querySelector('[data-page=friends]') && !!document.querySelector('[data-page=bots]')");checks++;
  const keys=await evaluate(p,"import('./js/five-pages.js').then(m=>m.cardKeys())");
  assert.ok(keys.some(k=>k.key==='friends'));assert.ok(keys.some(k=>k.key==='bots'));assert.equal(new Set(keys.map(k=>k.letter)).size,keys.length);checks++;
  await evaluate(p,"document.querySelector('[data-page=friends]').click()");await waitFor(p,"document.body.dataset.view==='friends'");checks++;
  await evaluate(p,`import('./js/friends.js').then(m=>m.open('chat',${JSON.stringify(friends.friends[0].id)}))`);
  await waitFor(p,"document.getElementById('fr-main').textContent.includes('SYNTHETIC FRIEND MESSAGE')");checks++;
  await evaluate(p,"import('./js/five-pages.js').then(m=>m.openTarget('bots'))");
  await waitFor(p,"document.body.dataset.view==='bots' && document.getElementById('bots-side').textContent.includes('Synthetic support')");checks++;
  await evaluate(p,"[...document.querySelectorAll('#bots-side button')].find(b=>b.textContent.includes('Synthetic support')).click()");
  await waitFor(p,"!!document.querySelector('.bots-tabs')");
  await evaluate(p,"[...document.querySelectorAll('.bots-tabs button')].find(b=>b.textContent==='Conversations').click()");
  await waitFor(p,"[...document.querySelectorAll('#bots-main button')].some(b=>b.textContent.includes('fixture-'))");
  await evaluate(p,"[...document.querySelectorAll('#bots-main button')].find(b=>b.textContent.includes('fixture-')).click()");
  await waitFor(p,"document.getElementById('bots-main').textContent.includes('SYNTHETIC SUPPORT MESSAGE')");checks++;
  assert.deepEqual(p.offsite,[]);assert.deepEqual(p.problems,[]);checks++;
  mkdirSync(new URL('../../../reports/qa/p0280-tail/',import.meta.url),{recursive:true});
  writeFileSync(new URL(`../../../reports/qa/p0280-tail/cards-${width}.png`,import.meta.url),await shoot(p));await p.dispose();
 }
 console.log(JSON.stringify({checks,scope:'real Chromium client/Noise, synthetic friend and support messages; no real Host or model calls'}));
} finally {await browser.close();await fake.stop();await web.stop();}
