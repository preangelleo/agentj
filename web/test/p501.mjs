// P501 real Chromium + paired Noise fake host: portable controls, ephemeral QR and signature card. Private baseline comparisons are maintained by the integration visual gate.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=process.env.P501_PHONE_OUT||'reports/qa/p501/screens';mkdirSync(out,{recursive:true});
const fake=await startFakeHost(),current=await startWebServer(),B=await launch();
const qr=readFileSync('web/test/fixtures/p501/qr.png').toString('base64');
const results=[];let signed=0;
const cleanup=`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.documentElement.style.setProperty('scroll-behavior','auto');`;
async function page(web,lang,theme,width){
 fake.reset();fake.setUp(true);
 const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,cleanup+`document.documentElement.dataset.theme='${theme}';const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);true`);
 return p;
}
async function shot(p,name){writeFileSync(out+'/'+name+'.png',await shoot(p,''));}
try{
for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
 const key=`${lang}-${theme}-${width}`;
 const p=await page(current,lang,theme,width);
 const writes=[];
 fake.st.onApp=async(c,m,send)=>{
  if(m.t!=='wechat')return false;
  if(m.op!=='status'){
   const target={op:m.op,id:m.id||'',code:m.code||''};
   assert.equal(await fake.verifyControl(c,'wechat',m,target),true);signed++;writes.push(m);
   await send({t:'ctl_res',r:m.r,action:'wechat',ok:true});
  }else await send({t:'wechat_state',bound:false,pending:0});
  return true;
 };
 try{
  await shot(p,key+'-after-chat');
  await evaluate(p,`import('./js/settings.js').then(m=>m.open())`);
  await waitFor(p,`!document.getElementById('settings').hidden`);
  assert.equal(await evaluate(p,`document.getElementById('set-wechat-row').hidden`),true);
  assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('set-tgpair').closest('.setrow')).borderBottomWidth`),'0px','hidden WeChat row preserves the baseline last-visible Telegram divider');
  await fake.send({t:'wechat_state',bound:false,pending:0});
  await evaluate(p,`import('./js/settings.js').then(m=>m.open())`);await waitFor(p,`!document.getElementById('settings').hidden`);
  await shot(p,key+'-after-settings');
  assert.equal(await evaluate(p,`document.getElementById('set-wechat-row').hidden`),false);
  assert.equal(await evaluate(p,`(()=>{const w=document.getElementById('set-wechat-row'),t=document.getElementById('set-tgpair').closest('.setrow');return !!(w.compareDocumentPosition(t)&Node.DOCUMENT_POSITION_FOLLOWING)})()`),lang==='zh','language recommendation order');
  await evaluate(p,`document.getElementById('set-wechat').click()`);await waitFor(p,`document.getElementById('wechat-panel')?.hidden===false`);
  await evaluate(p,`document.getElementById('wc-setup').click()`);
  for(let i=0;i<50&&!writes.some(m=>m.op==='setup');i++)await sleep(20);
  assert.ok(writes.some(m=>m.op==='setup'),'signed setup sent');
  await fake.send({t:'wechat_state',bound:false,pending:1,qr:{id:'a'.repeat(32),status:'wait',img:qr}});
  await waitFor(p,`document.querySelector('#wc-qr img')?.complete===true`);await shot(p,key+'-qr');
  assert.equal(await evaluate(p,`document.getElementById('wc-qr').hasAttribute('data-screenshot-private')`),true);
  assert.equal(await evaluate(p,`(()=>{const r=document.getElementById('wc-setup').getBoundingClientRect();return document.documentElement.scrollWidth<=innerWidth+1&&r.height<=0})()`),true);
  await fake.send({t:'wechat_state',bound:false,pending:1,qr:{id:'b'.repeat(32),status:'need_verifycode',img:qr}});
  await waitFor(p,`!document.getElementById('wc-code-row').hidden`);
  await evaluate(p,`document.getElementById('wc-code').value='123456';document.getElementById('wc-verify').click()`);
  for(let i=0;i<50&&!writes.some(m=>m.op==='verify');i++)await sleep(20);
  assert.ok(writes.some(m=>m.op==='verify'&&m.id==='b'.repeat(32)&&m.code==='123456'));
  assert.equal(await evaluate(p,`document.getElementById('wc-code').value`),'');
  await fake.send({t:'wechat_state',bound:true,pending:1});await waitFor(p,`!document.getElementById('wc-unbind').hidden`);
  assert.equal(await evaluate(p,`document.getElementById('wc-qr').childElementCount`),0,'bound deletes QR');
  await shot(p,key+'-bound');
  await evaluate(p,`document.getElementById('wc-unbind').click()`);
  for(let i=0;i<50&&!writes.some(m=>m.op==='unbind');i++)await sleep(20);
  assert.ok(writes.some(m=>m.op==='unbind'));await evaluate(p,`document.getElementById('wc-close').click();import('./js/settings.js').then(m=>m.close())`);
  await fake.ask({id:'c'.repeat(32),tool:'wechat_confirm',summary:lang==='zh'?'来自微信 · 主人确认\n转交收件箱里的请求；敏感操作仍需批准。':'From WeChat · Owner confirmation\nTransfer the inbox request; sensitive actions still need approval.',ttl:86400,cat:[]});
  await waitFor(p,`!document.getElementById('apprBtns').hidden`);
  assert.equal(await evaluate(p,`document.getElementById('sheetTitle').textContent`),lang==='zh'?'主人确认':'Owner confirmation');
  assert.equal(await evaluate(p,`document.getElementById('sheet').textContent.includes('wechat_confirm')`),false);
  assert.ok(await evaluate(p,`import('./js/snap.js').then(m=>m.S.asks.get('c'.repeat(32)).deadline-Date.now()>86000000)`),'24-hour host deadline is not clamped to 120 seconds');
  await shot(p,key+'-confirmation');
  assert.equal(await evaluate(p,`document.getElementById('apprBatch').hidden`),true,'no batch grant');
  await evaluate(p,`document.getElementById('apprDeny').click()`);
  for(let i=0;i<50&&!fake.st.answers.length;i++)await sleep(20);
  assert.ok(fake.st.answers.some(m=>m.id==='c'.repeat(32)&&m.ok===false));
  results.push({lang,theme,width,scope:'portable paired controls',screens:['after-chat','after-settings','qr','bound','confirmation'],pass:true});
 }finally{await p.close();}
}
writeFileSync(out+'/result.json',JSON.stringify({pass:true,baselineComparison:'private P505 visual gate',combos:results.length,signedControls:signed,results},null,2));
const rec=parityRecorder('web/test/p501.mjs');rec.record('p501-wechat',true);rec.write();
console.log('PASS P501 phone',results.length,'combinations;',signed,'verified signed controls; portable paired controls');
}finally{await B.close();await fake.stop();await current.stop();}
