// P445: real paired page, isolated Chromium, synthetic clipboard/installer only. No real accounts or service changes.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
const out='reports/qa/p445';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
let checks=0;const results=[];
const check=(v,msg)=>{assert.ok(v,msg);checks++;};
try {for(const width of [390,1440])for(const theme of ['light','dark'])for(const lang of ['zh','en']){
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.st.onApp=async(c,m,send)=>{
  const reply=async(t,v)=>{await send({t,r:m.r,...v});return true;};
  if(m.t==='jcard_list')return reply('jcard_items',{cards:[{id:'notes',name:{zh:'记事本',en:'Notebook'},on:true,official:true,accent:'windstorm',plus:'agentj-add-note'}]});
  if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:'notes',template:'notes',entries:[{id:'n1',text:'Meeting at 10:00',editable:true}],revision:'fixture'});
  return false;
 };
 const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 const tag=`${lang}-${theme}-${width}`,done=lang==='zh'?'已复制':'Copied';
 try {
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.documentElement.dataset.theme='${theme}';window.clip=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async s=>{window.clip.push(s)}}})`);
  await fake.addTurn({k:'phone',text:'Owner message'},'Reply with `agentj doctor`\n\n```sh\nagentj status\n```');
  await waitFor(p,`document.querySelector('#words .copy')`);
  const shot=async name=>{await sleep(100);writeFileSync(`${out}/${tag}-${name}.png`,await shoot(p));};
  const copied=async(sel,want,name)=>{
   await evaluate(p,`document.querySelector(${JSON.stringify(sel)}).click()`);
   await waitFor(p,`document.querySelector(${JSON.stringify(sel)}).dataset.done==='1'`);
   check(await evaluate(p,`window.clip.at(-1)===${JSON.stringify(want)}`),tag+' '+name+' clipboard');
   check(await evaluate(p,`document.querySelector(${JSON.stringify(sel)}).getAttribute('aria-label')===${JSON.stringify(done)}`),tag+' '+name+' label');
   check(await evaluate(p,`(()=>{const b=document.querySelector(${JSON.stringify(sel)}),tick=b.querySelector('.cc-ok');return getComputedStyle(tick).display!=='none'&&b.querySelector('.cc-result').textContent===${JSON.stringify(done)}&&getComputedStyle(b.querySelector('.cc-result')).display!=='none';})()`),tag+' '+name+' visible tick and label');
   const g=await evaluate(p,`(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect();return {on:n.classList.contains('on'),circle:n.classList.contains('circle'),buttons:n.querySelectorAll('button').length,cx:r.x+r.width/2,cy:r.y+r.height/2,w:innerWidth,h:innerHeight,text:n.textContent}})()`);
   check(g.on&&g.circle&&g.buttons===0&&g.text===done&&Math.abs(g.cx-g.w/2)<2&&Math.abs(g.cy-g.h/2)<2,tag+' '+name+' centred glance '+JSON.stringify(g));
   await shot(name);
  };
  await copied('#omCopy','Owner message','source');
  await copied('#copyReply','Reply with `agentj doctor`\n\n```sh\nagentj status\n```','reply');
  await copied('#words .copy','agentj status','code-block');
  await waitFor(p,`!document.querySelector('#words .copy').dataset.done`,2500);
  check(await evaluate(p,`document.querySelector('#words .copy').textContent!==${JSON.stringify(done)}`),tag+' code restored');
  await evaluate(p,`document.getElementById('setBtn').dispatchEvent(new PointerEvent('contextmenu',{bubbles:true,cancelable:true,button:2,pointerType:'mouse'}))`);
  check(await evaluate(p,`document.getElementById('confirm').hidden&&document.getElementById('toast').classList.contains('circle')&&!document.querySelector('#toast button')`),tag+' gear help is a buttonless glance');
  await shot('gear-help');await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,3000);
  await evaluate(p,`import('./js/relay.js').then(m=>m.remoteReader(true))`);
  await waitFor(p,`document.body.dataset.reader==='1'`);await sleep(400);
  await copied('#rdCopyBtn','Reply with `agentj doctor`\n\n```sh\nagentj status\n```','reader');
  await evaluate(p,`import('./js/relay.js').then(m=>m.remoteReader(false))`);
  await evaluate(p,`import('./js/jcards.js').then(m=>m.refresh())`);
  await evaluate(p,`import('./js/five-pages.js').then(m=>m.openTarget('notes'))`);
  await waitFor(p,`document.querySelector('[data-note-op=copy]')`);await sleep(400);
  await copied('[data-note-op=copy]','Meeting at 10:00','notebook');
  // Repeated click extends the same button's feedback timer, then restores the original SVG/label.
  await sleep(800);await evaluate(p,`document.querySelector('[data-note-op=copy]').click()`);await sleep(850);
  check(await evaluate(p,`document.querySelector('[data-note-op=copy]').dataset.done==='1'`),tag+' repeat holds tick');
  await waitFor(p,`!document.querySelector('[data-note-op=copy]').dataset.done`,2500);
  check(await evaluate(p,`!!document.querySelector('[data-note-op=copy] svg')`),tag+' original icon restored');
  // A refusal must not announce success, even if the browser throws before returning a Promise.
  await evaluate(p,`navigator.clipboard.writeText=()=>{throw new Error('fixture refusal')};document.execCommand=()=>false;document.querySelector('[data-note-op=copy]').click()`);
  await waitFor(p,`document.querySelector('[data-note-op=copy]').dataset.done==='fail'`);
  check(await evaluate(p,`document.getElementById('toast').dataset.kind==='error'&&document.getElementById('toast').classList.contains('circle')`),tag+' failure visible');
  await shot('copy-failed');
  await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,3000);
  // Exercise real noticeSheet used by settings/news/bang, not a separate timer fixture.
  await evaluate(p,`Promise.all([import('./js/ui.js'),import('./js/t.js')]).then(([u,T])=>u.noticeSheet(T.t('p302.prefTitle'),T.t('p302.prefOffline')))`);
  await waitFor(p,`document.getElementById('toast').classList.contains('on')`);
  check(await evaluate(p,`document.getElementById('confirm').hidden&&document.querySelector('#toast.circle.glance')&&document.querySelectorAll('#toast button').length===0`),tag+' pure notice buttonless');
  await shot('notice');await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,3000);
  await evaluate(p,"import('./js/ui.js').then(u=>u.noticeSheet('Notice','Try `agentj doctor --reconnect`'))");
  check(await evaluate(p,`document.querySelectorAll('#toast button').length===0&&document.getElementById('toast').classList.contains('circle')`),tag+' command notice has no hidden copy action');
  check(await evaluate(p,`(()=>{const r=document.getElementById('toast').getBoundingClientRect();return Math.abs(r.width-r.height)<1&&Math.abs(r.x+r.width/2-innerWidth/2)<2&&Math.abs(r.y+r.height/2-innerHeight/2)<2;})()`),tag+' long notice round and centred');
  await shot('notice-command');await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,3000);
  // Completion of the actual installer state machine uses the same notification renderer.
  await evaluate(p,`Promise.all([import('./js/asr-setup.js'),import('./js/ui.js'),import('./js/t.js')]).then(([a,u,T])=>a.createAsrSetup({t:T.t,notify:u.notify,confirm:async()=>true,install:async()=>({ok:true,state:'ready'}),ready(){},repaint(){}}).run())`);
  await waitFor(p,`document.getElementById('toast').dataset.kind==='success'`);
  check(await evaluate(p,`document.querySelectorAll('#toast button').length===0&&!document.getElementById('toast').dataset.hold&&document.getElementById('toast').classList.contains('circle')`),tag+' ready buttonless');
  await shot('voice-ready');await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,3000);
  check(p.problems.filter(x=>!/WebSocket connection|ERR_CONNECTION_REFUSED/.test(x)).length===0,tag+' no browser errors '+p.problems.join('|'));
  results.push({tag,pass:true});console.log('PASS',tag);
 } finally {await p.dispose();}
}} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(`${out}/results.json`,JSON.stringify({checks,results,scope:'Isolated Chromium viewport/touch emulation, local paired fake host; no physical-device acceptance'},null,2));
console.log(`P445 ${checks} checks passed`);
