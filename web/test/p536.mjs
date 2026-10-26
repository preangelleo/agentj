// P536: real paired encrypted browser flow; provider sends are substituted, never real accounts.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=process.env.P536_OUT||'reports/qa/p536/screens';mkdirSync(out,{recursive:true});
const fake=await startFakeHost(),web=await startWebServer(),before=process.env.P536_BEFORE?await startWebServer({publicDir:resolve(process.env.P536_BEFORE)}):null,B=await launch();
const qr=readFileSync('web/test/fixtures/p501/qr.png').toString('base64');
async function setup(server,lang='zh',width=390){
 fake.reset();fake.setUp(true);const p=await newPage(B,width,width===390?844:1000,'dark',{touch:width===390,allow:[server.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(server.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);true`);
 await fake.addTurn({k:'host',text:lang==='zh'?'请给我一句问候':'Say hello'},lang==='zh'?'你好，微信转发与沟通渠道已准备好。':'Hello. Reply forwarding and channels are ready.');
 await waitFor(p,`!document.getElementById('fwdBtn').disabled`);return p;
}
async function shot(p,name){await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);writeFileSync(out+'/'+name+'.png',await shoot(p,''));}
async function settings(p){await evaluate(p,`import('./js/settings.js').then(m=>m.open())`);await waitFor(p,`!document.getElementById('settings').hidden`);await evaluate(p,`document.getElementById('set-s-channels').scrollIntoView({block:'start'});true`);await sleep(100);}
async function state(wx,tg,can=wx){await fake.send({t:'status',s:'idle',channels:{tg_bound:tg,tg_forward:tg}});await fake.send({t:'wechat_state',bound:wx,can_forward:can,forward_for:300,pending:0});}
async function choice(p,x){return evaluate(p,`import('./js/forward-choice.js').then(m=>{m.select('${x}');return m.destination()})`);}
const results=[];
try{
 if(before){const p=await setup(before);try{await shot(p,'before-card');await fake.send({t:'wechat_state',bound:false,pending:0});await settings(p);await shot(p,'before-channels');await evaluate(p,`document.getElementById('set-wechat').click()`);await shot(p,'before-empty');await fake.send({t:'wechat_state',bound:false,pending:0,qr:{id:'a'.repeat(32),status:'wait',img:qr}});await waitFor(p,`document.querySelector('#wc-qr img')?.complete`);await shot(p,'before-qr');}finally{await p.close();}}
 for(const [lang,width] of [['zh',390],['en',390],['zh',1440]]){
 const p=await setup(web,lang,width);const key=lang+'-'+width;
 let sends=[],fail=true;
 fake.st.onApp=async(c,m,send)=>{if(m.t==='wx_fwd'){sends.push(m);await send({t:'wx_fwd_res',r:m.r,ok:!fail,why:fail?'send_failed':undefined});return true;}return false;};
 try{
  await state(false,false);await settings(p);await shot(p,key+'-none-channels');
  assert.deepEqual(await evaluate(p,`[...document.querySelectorAll('input[name="forward-destination"]')].map(x=>[x.value,x.disabled,x.checked])`),[['share',false,true],['wx',true,false],['tg',true,false]]);
  for(const [wx,tg,name] of [[true,false,'wx'],[false,true,'tg'],[true,true,'both']]){
   await state(wx,tg);await sleep(80);await shot(p,key+'-'+name+'-channels');assert.equal(await choice(p,'share'),'share');
   assert.equal(await choice(p,wx?'wx':'tg'),wx?'wx':'tg');
  }
  const layout=await evaluate(p,`['set-wechat-row','set-telegram-row'].map(id=>[...document.getElementById(id).children].map(x=>x.tagName+':'+x.className))`);assert.deepEqual(layout[0],layout[1]);
  // Editable skill requests, no immediate slash/API write.
  const count=fake.st.says?.length||0;
  for(const [id,skill] of [['set-wechat','agentj-wechat'],['set-tgpair','agentj-config']]){
   await settings(p);await evaluate(p,`document.getElementById('${id}').click()`);
   await waitFor(p,`document.getElementById('input').value.includes('${skill}')`);assert.equal(fake.st.says?.length||0,count);
  }
  await state(true,true);await choice(p,'wx');await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);
  await shot(p,key+'-wx-card');
  assert.equal(await evaluate(p,`getComputedStyle(document.querySelector('#fwdBtn .gwx')).display`),'block');
  await evaluate(p,`document.getElementById('fwdBtn').click()`);await waitFor(p,`document.getElementById('fwdBtn').dataset.state==='failed'`);
  assert.equal(sends.length,1);assert.equal(fake.st.tgFwds.length,0);assert.equal(await evaluate(p,`import('./js/forward-choice.js').then(m=>m.destination())`),'wx');
  fail=false;await evaluate(p,`document.getElementById('fwdBtn').click()`);await waitFor(p,`document.getElementById('fwdBtn').dataset.state==='sent'`);assert.equal(sends.length,2);
  await state(true,true,false);assert.equal(await evaluate(p,`import('./js/forward-choice.js').then(m=>m.destination())`),'wx','context expiry preserves preference');
  await state(true,true);await evaluate(p,`document.getElementById('readBtn').click()`);await sleep(100);assert.equal(await evaluate(p,`document.getElementById('rdFwdBtn').dataset.wx`),'1');await shot(p,key+'-wx-reader');await evaluate(p,`document.getElementById('rdReadBtn').click()`);
  await state(false,true);await sleep(80);assert.equal(await evaluate(p,`import('./js/forward-choice.js').then(m=>m.destination())`),'share');
  await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);await state(false,true);await sleep(80);assert.equal(await evaluate(p,`document.getElementById('toast').classList.contains('on')`),false,'unbind notice appears only once');
  await state(true,true);await settings(p);await shot(p,key+'-both-selected-system');
  await choice(p,'tg');await shot(p,key+'-both-selected-tg');
  await choice(p,'wx');await shot(p,key+'-both-selected-wx');
  await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);
  await fake.send({t:'wechat_state',bound:false,pending:0,can_forward:false,qr:{id:'b'.repeat(32),status:'wait',img:qr}});await waitFor(p,`document.querySelector('#wc-qr img')?.complete`);await shot(p,key+'-qr');
  await fake.send({t:'wechat_state',bound:true,can_forward:false,greeting:'needs_message',pending:0});await waitFor(p,`document.getElementById('wc-status').textContent.includes('${lang==='zh'?'发一句话':'Send ClawBot'}')`);await shot(p,key+'-handshake');
  assert.equal(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth+1`),true);
  results.push({lang,width,pass:true,provider:'substituted'});
 }finally{await p.close();}
 }
 writeFileSync(out+'/results.json',JSON.stringify({pass:true,results,transport:'paired Noise; provider substituted'},null,2));const rec=parityRecorder('web/test/p536.mjs');rec.record('p536-forward-channels',true);rec.write();console.log('PASS P536 browser',results.length,'combinations');
}finally{await B.close();await fake.stop();await web.stop();await before?.stop();}
