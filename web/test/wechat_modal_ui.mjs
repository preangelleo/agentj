// Local paired fixture only. The image is a solid placeholder, never a scannable QR.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out='reports/qa/wechat-modal-ui-20261102';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
let cases=0;
try { for (const [lang,w] of [['zh',390],['en',320]]) {
 fake.reset();fake.setUp(true);
 const p=await newPage(B,w,844,'dark',{touch:true,allow:[web.url,fake.relay+'/']});
 try {
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();true`);
  await evaluate(p,`import('./js/wechat.js').then(m=>{window.wc=m; const c=document.createElement('canvas');c.width=c.height=280;const x=c.getContext('2d');x.fillStyle='#faf9f5';x.fillRect(0,0,280,280);x.fillStyle='#2c2b26';x.font='18px sans-serif';x.textAlign='center';x.fillText('QR hidden for preview',140,145);window.qr={id:'preview',img:c.toDataURL('image/png').split(',')[1],status:'wait'};wc.show();wc.route({t:'wechat_state',bound:false,qr,setup:{id:'session',remaining_seconds:585}});})`);
  await waitFor(p,`document.getElementById('wc-left').textContent.includes('9:')`);
  assert.equal(await evaluate(p,`document.activeElement.id`),'wc-close');
  const styles=await evaluate(p,`(()=>{const b=document.getElementById('wc-later'),x=document.getElementById('wc-close'),c=getComputedStyle(b);return {classes:b.className,height:b.getBoundingClientRect().height,border:c.borderTopWidth,bg:c.backgroundColor,line:c.textDecorationLine,x:x.textContent,label:x.getAttribute('aria-label'),overflow:document.documentElement.scrollWidth>innerWidth+1}})()`);
  assert.match(styles.classes,/\baj-btn\b/);assert.match(styles.classes,/\baj-btn--quiet\b/);assert.ok(styles.height>=44);assert.equal(styles.bg,'rgba(0, 0, 0, 0)');assert.equal(styles.x,'×');assert.ok(styles.label);assert.equal(styles.overflow,false);
  writeFileSync(`${out}/${lang}-${w}.png`,await shoot(p,''));
  await evaluate(p,`window.realNow=performance.now.bind(performance);performance.now=()=>realNow()+121000;wc.route({t:'wechat_state',qr,setup:{id:'session',remaining_seconds:464}});true`);
  assert.equal(await evaluate(p,`document.getElementById('wc-qr').children.length`),1);
  await evaluate(p,`wc.route({t:'wechat_state',qr,setup:{id:'session',remaining_seconds:2}});wc.route({t:'wechat_state',qr:{...qr,id:'refreshed'},setup:{id:'session',remaining_seconds:600}});true`);
  assert.match(await evaluate(p,`document.getElementById('wc-left').textContent`),/0:0[12]/);
  await waitFor(p,`document.getElementById('wc-qr').children.length===0`,4000);
  await evaluate(p,`wc.route({t:'wechat_state',qr:{...qr,id:'legacy'}});true`);
  assert.equal(await evaluate(p,`document.getElementById('wc-left').hidden`),true);
  await evaluate(p,`document.getElementById('wc-code').value='123456';document.getElementById('wc-later').click();wc.route({t:'wechat_state',qr});true`);
  assert.equal(await evaluate(p,`document.getElementById('wechat-panel').hidden&&document.getElementById('wc-qr').children.length===0&&document.getElementById('wc-code').value===''`),true);
  await evaluate(p,`wc.show();wc.route({t:'wechat_state',qr,setup:{id:'resume',remaining_seconds:40}});Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));true`);
  assert.equal(await evaluate(p,`document.getElementById('wc-qr').children.length`),0);
  await evaluate(p,`Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));true`);
  assert.equal(await evaluate(p,`document.getElementById('wechat-panel').hidden`),false);
  assert.equal(await evaluate(p,`document.getElementById('wc-qr').children.length`),1);
  await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
  await waitFor(p,`document.getElementById('wechat-panel').hidden`);
  await evaluate(p,`wc.show();wc.route({t:'wechat_state',qr});true`);
  await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',modifiers:8});
  assert.equal(await evaluate(p,`document.activeElement.id`),'wc-later');
  await evaluate(p,`document.body.dataset.view='other';true`);
  await waitFor(p,`document.getElementById('wechat-panel').hidden`);
  await evaluate(p,`wc.route({t:'wechat_state',qr});true`);
  assert.equal(await evaluate(p,`document.getElementById('wechat-panel').hidden`),true);
  assert.deepEqual(p.problems,[]);cases++;
 } finally {await p.dispose();}
}} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(out+'/results.json',JSON.stringify({pass:true,cases,synthetic:true})+'\n');console.log('WeChat modal browser PASS',cases);
