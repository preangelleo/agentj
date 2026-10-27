// P554: bound WeChat is always selectable (no time window); greyed only when unbound (reason + 去绑定);
// when WeChat refused (computer says needs_message) the forward says how and offers system share. Real paired encrypted browser flow; provider substituted.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=process.env.P554_OUT||'reports/qa/p554/screens';mkdirSync(out,{recursive:true});
const fake=await startFakeHost(),web=await startWebServer(),before=process.env.P554_BEFORE?await startWebServer({publicDir:resolve(process.env.P554_BEFORE)}):null,B=await launch();
async function setup(server,lang){
 fake.reset();fake.setUp(true);const p=await newPage(B,390,844,'dark',{touch:true,allow:[server.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(server.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);window.__shared=[];navigator.share=async d=>{window.__shared.push(d.text);};true`);
 await fake.addTurn({k:'host',text:lang==='zh'?'把这段转给我微信':'Forward this to my WeChat'},lang==='zh'?'这是要转发的回复。':'This is the reply to forward.');
 await waitFor(p,`!document.getElementById('fwdBtn').disabled`);return p;
}
async function shot(p,name,toastOff=true){if(toastOff)await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);writeFileSync(out+'/'+name+'.png',await shoot(p,''));}
async function settings(p){await evaluate(p,`import('./js/settings.js').then(m=>m.open())`);await waitFor(p,`!document.getElementById('settings').hidden`);await evaluate(p,`document.getElementById('set-forward').scrollIntoView({block:'center'});true`);await sleep(120);}
async function closeSettings(p){await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);await sleep(80);}
async function state(wx,can){await fake.send({t:'status',s:'idle',channels:{tg_bound:false,tg_forward:false}});await fake.send({t:'wechat_state',bound:wx,can_forward:can,pending:0});await sleep(80);}
const radios=p=>evaluate(p,`[...document.querySelectorAll('input[name="forward-destination"]')].map(x=>[x.value,x.disabled,x.checked])`);
const why=p=>evaluate(p,`(()=>{const w=document.getElementById('set-fwd-wx-why');return w?[w.hidden,document.getElementById('set-fwd-wx-note').textContent,document.getElementById('set-fwd-wx-bind').hidden]:null})()`);
const results=[];
try{
 if(before){const p=await setup(before,'zh');try{
  await shot(p,'before-zh-card-share');await evaluate(p,`document.getElementById('readBtn').click()`);await sleep(150);await shot(p,'before-zh-reader-share');await evaluate(p,`document.getElementById('rdReadBtn').click()`);await sleep(100);
  await settings(p);await shot(p,'before-zh-missed-push');   // #327 screenshot: bound, but no wechat_state reached the phone
  await state(true,false);await settings(p);await shot(p,'before-zh-bound-refused');
  await state(false,false);await shot(p,'before-zh-unbound');
 }finally{await p.close();}}
 for(const lang of ['zh','en']){
  const p=await setup(web,lang);let asks=[],fwd=[],reply={ok:false,why:'needs_message'};
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='wechat'&&m.op==='status'){asks.push(m);return true;}
   if(m.t==='wx_fwd'){fwd.push(m);await send({t:'wx_fwd_res',r:m.r,...reply});return true;}
   return false;};
  try{
   // (追加 c) the share glyph: 转发 mirrors 回复 on the card and in the reader (p554-icon.test.mjs locks the paths)
   await shot(p,lang+'-card-share');assert.equal(await evaluate(p,`getComputedStyle(document.querySelector('#fwdBtn .g0')).display`),'block');
   await evaluate(p,`document.getElementById('readBtn').click()`);await sleep(150);
   assert.equal(await evaluate(p,`document.querySelector('#rdFwdBtn .g0 path').getAttribute('d')`),'M14 5l6 6-6 6');
   await shot(p,lang+'-reader-share');await evaluate(p,`document.getElementById('rdReadBtn').click()`);await sleep(100);
   // 0. #327: the push was missed (no wechat_state yet) — opening Settings asks, and a bound WeChat becomes selectable.
   fake.st.onApp=async(c,m,send)=>{if(m.t==='wechat'&&m.op==='status'){asks.push(m);await send({t:'wechat_state',bound:true,can_forward:false,pending:0});return true;}return false;};
   await settings(p);await waitFor(p,`!document.querySelector('input[name="forward-destination"][value="wx"]').disabled`);
   assert.equal(asks.length,1);assert.equal(await evaluate(p,`document.getElementById('set-wechat-row').hidden`),false);
   await shot(p,lang+'-missed-push');await closeSettings(p);asks=[];
   fake.st.onApp=async(c,m,send)=>{
    if(m.t==='wechat'&&m.op==='status'){asks.push(m);return true;}
    if(m.t==='wx_fwd'){fwd.push(m);await send({t:'wx_fwd_res',r:m.r,...reply});return true;}
    return false;};
   // 1. Unbound: greyed, one line why, and 去绑定 opens the editable skill request.
   await state(false,false);await settings(p);
   assert.ok(asks.length>=1,'opening Settings asks the computer for the WeChat status');
   assert.deepEqual((await radios(p))[1],['wx',true,false]);
   let w=await why(p);assert.equal(w[0],false);assert.match(w[1],lang==='zh'?/还没绑定/:/not connected/);assert.equal(w[2],false);
   await shot(p,lang+'-unbound');
   await evaluate(p,`document.getElementById('set-fwd-wx-bind').click()`);
   await waitFor(p,`document.getElementById('input').value.includes('agentj-wechat')`);
   await evaluate(p,`document.getElementById('input').value='';true`);
   // 2. Bound but WeChat refused the last send: still selectable; once chosen the line says how it recovers, no bind button.
   await state(true,false);await settings(p);
   assert.deepEqual((await radios(p))[1],['wx',false,false],'bound WeChat stays selectable after a refusal');
   assert.equal((await why(p))[0],true,'nothing to explain while system share is chosen');
   await evaluate(p,`document.querySelector('input[name="forward-destination"][value="wx"]').click()`);await sleep(80);
   assert.deepEqual((await radios(p))[1],['wx',false,true]);
   w=await why(p);assert.equal(w[0],false);assert.match(w[1],lang==='zh'?/拒收/:/refused/);assert.equal(w[2],true);
   await shot(p,lang+'-bound-refused');
   // 3. Forwarding works (any context age): no extra line.
   await state(true,true);assert.equal((await why(p))[0],true);
   await shot(p,lang+'-bound-ok');
   // 4. WeChat refuses: not a silent failure — how-to + one-tap system share of this reply.
   await state(true,false);await closeSettings(p);
   await evaluate(p,`document.getElementById('fwdBtn').click()`);
   await waitFor(p,`document.querySelector('#toast .nt-acts button')`);
   assert.equal(fwd.length,1,'the computer is asked (the platform decides)');
   const toast=await evaluate(p,`[document.getElementById('toast').textContent,document.getElementById('fwdBtn').dataset.state]`);
   assert.match(toast[0],lang==='zh'?/先在 ClawBot 里发一句/:/message from you in ClawBot/);assert.notEqual(toast[1],'failed');
   await shot(p,lang+'-needs-message',false);
   await evaluate(p,`document.querySelector('#toast .nt-acts button').click()`);
   await waitFor(p,`window.__shared.length===1`);
   assert.equal(await evaluate(p,`window.__shared[0]`),lang==='zh'?'这是要转发的回复。':'This is the reply to forward.');
   assert.equal(await evaluate(p,`import('./js/forward-choice.js').then(m=>m.destination())`),'wx','one-tap share does not change the saved destination');
   // 5. After the owner writes to ClawBot it sends straight away.
   reply={ok:true};await state(true,true);await sleep(2200);
   await evaluate(p,`document.getElementById('fwdBtn').click()`);await waitFor(p,`document.getElementById('fwdBtn').dataset.state==='sent'`);
   assert.equal(fwd.length,2);assert.equal(await evaluate(p,`window.__shared.length`),1);
   // 6. Unbinding still resets to system share (P536 contract).
   await state(false,false);assert.equal(await evaluate(p,`import('./js/forward-choice.js').then(m=>m.destination())`),'share');
   assert.equal(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth+1`),true);
   results.push({lang,pass:true,provider:'substituted'});
  }finally{await p.close();}
 }
 // 7. (追加 c) 转发去向 is one Agent J host setting: A's earlier local choice migrates once; B (a fresh remote) shows it;
 // B changes it → A follows at once and after a reload; a refused write goes back and says why. Shots for review.
 for(const lang of ['zh','en']){
  fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme:'system'},ui:{}};fake.st.onApp=null;
  const both=async()=>{await fake.send({t:'status',s:'idle',channels:{tg_bound:true,tg_forward:true}});await fake.send({t:'wechat_state',bound:true,can_forward:true,pending:0});await sleep(100);};
  const dest=p=>evaluate(p,`import('./js/forward-choice.js').then(m=>m.destination())`);
  const lsKeys=p=>evaluate(p,`Object.keys(localStorage).filter(k=>k.startsWith('aj.forward.destination.'))`);
  const pair=async()=>{const p=await newPage(B,390,844,'dark',{touch:true,allow:[web.url,fake.relay+'/']});
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);true`);return p;};
  const A=await pair();let Bp=null;
  try{
   // an older host: A keeps its own local choice (P536), Telegram
   await both();assert.equal(await evaluate(A,`import('./js/forward-choice.js').then(m=>(m.select('tg'),m.destination()))`),'tg');
   assert.equal((await lsKeys(A)).length,1,'pre-P554 local value saved');assert.equal(fake.st.forwardSets.length,0);
   // the host now keeps it: A reconnects, its local value moves to the host once and the local copy goes
   fake.st.forwardSync=true;await navigate(A,web.url+'?lang='+lang);await waitState(A,'ready');
   await waitFor(A,`Object.keys(localStorage).filter(k=>k.startsWith('aj.forward.destination.')).length===0`);
   assert.deepEqual(fake.st.forwardSets.map(x=>[x.dest,x.migrate,x.sigOk]),[['tg',true,true]],'one signed migration');
   assert.equal(fake.st.prefs.ui.forward_destination,'tg');await both();assert.equal(await dest(A),'tg');
   // B: a new remote with nothing local shows the host's choice, sends nothing
   Bp=await pair();await both();assert.equal(await dest(Bp),'tg','B follows the host');assert.equal(fake.st.forwardSets.length,1);
   await settings(Bp);assert.deepEqual((await radios(Bp))[2],['tg',false,true]);
   assert.match(await evaluate(Bp,`document.getElementById('set-fwd-note').textContent`),lang==='zh'?/所有遥控器共用/:/shared by every remote/);
   // B picks WeChat: signed write, A repaints from the broadcast, still WeChat after A reloads
   await evaluate(Bp,`document.querySelector('input[name="forward-destination"][value="wx"]').click()`);
   await waitFor(A,`import('./js/forward-choice.js').then(m=>m.destination()==='wx')`);
   assert.deepEqual(fake.st.forwardSets.at(-1).dest+'/'+fake.st.forwardSets.at(-1).migrate,'wx/false');
   await shot(Bp,lang+'-host-sync-b');
   await navigate(A,web.url+'?lang='+lang);await waitState(A,'ready');await both();assert.equal(await dest(A),'wx','after reload');
   await evaluate(A,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);true`);
   await settings(A);assert.deepEqual((await radios(A))[1],['wx',false,true]);await shot(A,lang+'-host-sync-a');
   assert.equal(fake.st.forwardSets.filter(x=>x.migrate).length,1,'no second migration');
   // a refused write: back to the host's value, the reason in one line
   fake.st.forwardWhy='bad_signature';await evaluate(A,`document.querySelector('input[name="forward-destination"][value="share"]').click()`);
   await waitFor(A,`document.querySelector('input[name="forward-destination"][value="wx"]').checked`);
   assert.match(await evaluate(A,`document.getElementById('toast').textContent`),lang==='zh'?/没能保存到 Agent J 主机/:/not saved on the Agent J host/);
   assert.equal(await dest(Bp),'wx','B unchanged');
   // an unbound channel falls back to system share on this remote but never rewrites the host value
   const n=fake.st.forwardSets.length;await fake.send({t:'wechat_state',bound:false,pending:0});await sleep(100);
   assert.equal(await dest(A),'share');assert.equal(fake.st.forwardSets.length,n);assert.equal(fake.st.prefs.ui.forward_destination,'wx');
   await fake.send({t:'wechat_state',bound:true,can_forward:true,pending:0});await sleep(100);assert.equal(await dest(A),'wx','rebound: the host value again');
   assert.deepEqual([...A.problems,...Bp.problems].filter(x=>!/favicon|ERR_|WebSocket/.test(x)),[]);
   results.push({lang,pass:true,host_sync:true});
  }finally{await A.close();await Bp?.close();}
 }
 writeFileSync(out+'/results.json',JSON.stringify({pass:true,results,transport:'paired Noise; provider substituted'},null,2));
 const rec=parityRecorder('web/test/p554.mjs');rec.record('p554-wechat-window',true);rec.record('p554-forward-host-setting',true);rec.write();console.log('PASS P554 browser',results.length,'languages');
}finally{await B.close();await fake.stop();await web.stop();await before?.stop();}
