// Narrow pre-sign UI test. No authorization or password is submitted; successful UV/seal paths have separate suites.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep,rect} from './browser.mjs';
const out=resolve(process.env.P468_TARGET_OUT||'reports/qa/remote-assist-468-20261106/web-consent-target');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch(),page=web.url.replace('127.0.0.1','localhost');let checks=0;const check=(v,k)=>{assert.ok(v,k);checks++;};
try{
for(const [lang,theme,width] of [['zh','light',390],['en','dark',1440]]){
 fake.reset();fake.setUp(true);fake.st.extraCaps=['assist_term1'];fake.st.prefs={appearance:{language:lang,theme}};
 const h={signed:0,held:null};
 const challenge=(m,send,key_id,seat)=>send({t:'ctl_res',r:m.r,ok:false,why:'passkey_required',n:randomBytes(16).toString('hex'),fa:'fixture-credential',fa_ch:randomBytes(32).toString('base64url'),key_id,seat});
 fake.st.onApp=async(c,m,send)=>{if(m.t==='assist_term_grant'){if(m.sig){h.signed++;return true;}if(m.minutes===15){h.held={m,send};return true;}await challenge(m,send,'official-original','seat-original');return true;}return false;};
 const p=await newPage(B,width,900,theme,{touch:width<500,allow:[web.url,page,fake.relay+'/']});
 const click=async id=>{await evaluate(p,`document.getElementById('${id}').scrollIntoView({block:'center'})`);await sleep(30);const r=await rect(p,'#'+id);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});};
 const fields=()=>evaluate(p,`Object.fromEntries([...document.querySelectorAll('#ast-authorization-target dd')].map(x=>[x.dataset.field,x.textContent]))`);
 const save=async tag=>{await evaluate(p,`document.getElementById('ast-authorization-target').scrollIntoView({block:'center'})`);await sleep(60);writeFileSync(resolve(out,`${tag}-${lang}-${theme}-${width}.png`),await shoot(p,''));};
 try{
  await navigate(p,fake.newPairing(page+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.openRescue();return true})`);await waitFor(p,`!!document.getElementById('ast-grant')`);
  check(await evaluate(p,`!document.getElementById('ast-authorization-target')`),'no target is shown before receiving a challenge');
  await click('ast-grant');await waitFor(p,`!!document.getElementById('ast-authorization-target')`);const f=await fields();check(f.officialIdentity==='official-original'&&f.targetComputer==='seat-original','official identity and target seat visible before signing');check(f.duration.includes('30')&&f.scope===(lang==='zh'?'完整网页终端':'Full web terminal'),'visible scope and selected30');check(h.signed===0,'showing target does not authorize');await save('prepared-30');
  await click('ast-min-15');for(let i=0;i<50&&!h.held;i++)await sleep(20);check(!!h.held,'new duration requests a fresh host challenge');check(await evaluate(p,`!document.getElementById('ast-authorization-target')&&document.getElementById('ast-grant').disabled`),'old target removed and signing disabled while fresh challenge is pending');check(h.signed===0,'changing duration never sends a signature');
  await challenge(h.held.m,h.held.send,'official-next','seat-next');await waitFor(p,`!!document.getElementById('ast-authorization-target')`);const n=await fields();check(n.officialIdentity==='official-next'&&n.targetComputer==='seat-next'&&n.duration.includes('15'),'refreshed identity,target,15 displayed');check(!JSON.stringify(n).includes('original'),'no stale pre-sign target');await save('prepared-15');
  check(!p.offsite.length,'no off-site requests');check(!p.problems.filter(x=>!/WebSocket|ERR_CONNECTION|net::/i.test(x)).length,'no browser errors');console.log('PASS',lang,theme,width);
 }finally{await p.dispose();}
}
writeFileSync(resolve(out,'result.json'),JSON.stringify({checks,kind:'real-page-local-noise-presign-target-only',signed:0},null,2)+'\n');console.log('checks',checks);
}finally{await B.close();await fake.stop();await web.stop();}
