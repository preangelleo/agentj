// Product phone over real Noise fixture: unavailable iOS authenticator guidance and long signed-write receipts.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out=new URL('../../../reports/qa/p100/phone/',import.meta.url);mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();let replyTimer;
try{
 fake.st.onApp=async(c,m,send)=>{
  if(m.t!=='bots_write')return false;
  if(m.request.op==='long')await send({t:'ctl_pending',r:m.r,action:'bots_write',timeout_ms:620000});
  if(m.request.op==='bad_pending')await send({t:'ctl_pending',r:m.r,action:'other',timeout_ms:620000});
  replyTimer=setTimeout(()=>send({t:'ctl_res',r:m.r,ok:true}),300);return true;
 };
 for(const language of ['zh','en']){
  const p=await newPage(B,390,844,'light',{ua:'Mozilla/5.0 (iPhone) Safari/605',touch:true,allow:[web.url,fake.relay+'/']});
  await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>false;Object.defineProperty(navigator,'storage',{value:{persist:async()=>true,persisted:async()=>true}});`});
  console.log(language,'pair-entry');
  await navigate(p,web.url+'?lang='+language);await waitFor(p,"!document.getElementById('phone-env').hidden");
  const check=async()=>{const text=await evaluate(p,"document.getElementById('phone-env-text').textContent");assert.match(text,language==='zh'?/自动填充密码和通行密钥/:/AutoFill Passwords and Passkeys/);assert.match(text,language==='zh'?/屏幕使用时间/:/Screen Time/);assert.match(text,language==='zh'?/每次扫码/:/Each scan/);assert.equal(await evaluate(p,'document.documentElement.scrollWidth<=innerWidth'),true);};
  await check();writeFileSync(new URL(language+'-pair.png',out),await shoot(p,''));
  console.log(language,'fresh-context-for-noise');
  await navigate(p,'about:blank');
  console.log(language,'noise-pair');
  await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,"document.getElementById('phone-env-dismiss').click();document.getElementById('aj-menu').open=true;document.getElementById('setup-faceid').click()");await waitFor(p,"!document.getElementById('phone-env').hidden");await check();
  writeFileSync(new URL(language+'-settings.png',out),await shoot(p,''));
  // Scale only the two control deadlines, preserving the protocol's exact values and callback order.
  await evaluate(p,"window.__timer=setTimeout;window.setTimeout=(fn,ms,...args)=>__timer(fn,ms===20000?150:ms===620000?1500:ms,...args)");
  for(const [op,expected] of [['long',true],['no_pending',false],['bad_pending',false]]){
   const result=await evaluate(p,`import('./js/controls.js').then(m=>m.signedWrite('bots_write',{request:{op:${JSON.stringify(op)}}},{t:'bots_write',request:{op:${JSON.stringify(op)}}}))`);
   assert.equal(result.ok,expected,op);if(!expected)assert.equal(result.why,'timeout');
   await new Promise(r=>setTimeout(r,350));
  }
  await p.dispose();
 }
 writeFileSync(new URL('results.json',out),JSON.stringify({ios_pair_and_settings_guidance:true,zh_en_390_screens:true,authenticated_pending_extends_request:true,missing_or_wrong_action_pending_times_out:true,deadline_scaling:'20s→150ms;620s→1500ms; reply300ms',production_requests:false},null,2)+'\n');console.log('P100 phone guidance and pending-receipt browser PASS');
}finally{clearTimeout(replyTimer);await B.close();await fake.stop();await web.stop();}
