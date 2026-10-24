// p507-fixed-read-floor · P507 L1: new host read receipts complete in one signed request, without WebAuthn.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,rect} from './browser.mjs';
const reads=['doctor'];
const protectedCmds=['status','service_status','rescue_status','update_check','reconnect'];
const out=process.env.P507_OUT;if(out)mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),browser=await launch();
const evidence=[];
try{
 for(const [lang,tv] of [['zh',false],['en',false],['zh',true],['en',true]]){
  fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme:'light'}};fake.st.runCmds=[...reads,...protectedCmds];
  const calls=[];
  fake.st.onApp=async(c,m,send)=>{
   if(m.t!=='cmd_run')return false;
   assert.equal(await fake.verifyControl(c,'cmd_run',m,{cmd:m.cmd}),true);
   calls.push({cmd:m.cmd,fa:!!m.fa});
   const res=x=>send({t:'ctl_res',r:m.r,action:'cmd_run',cmd:m.cmd,...x});
   if(protectedCmds.includes(m.cmd)){await res({ok:false,why:'no_passkey'});return true;}
   assert.ok(reads.includes(m.cmd));assert.equal(m.fa,undefined);
   await res({ok:true,code:0,out:'fixture read result',ms:1});return true;
  };
  const page=await newPage(browser,tv?1600:390,tv?900:844,'light',{touch:false,allow:[web.url,fake.relay+'/']});
  try{
   if(tv)await page.send('Page.addScriptToEvaluateOnNewDocument',{source:"Object.defineProperty(window,'PublicKeyCredential',{value:undefined});"});
   await page.send('Page.addScriptToEvaluateOnNewDocument',{source:"window.p507UVCalls=0;Object.defineProperty(navigator.credentials,'get',{value:()=>{window.p507UVCalls++;throw Error('unexpected UV')}});"});
   await navigate(page,fake.newPairing(web.url+'?lang='+lang));await waitState(page,'awaiting-approval');await fake.approve();await waitState(page,'ready');
   await evaluate(page,"document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();import('./js/settings.js').then(m=>m.openRescue())");
   await waitFor(page,"!document.getElementById('settings').hidden&&!document.getElementById('rs-cmds').hidden");
   const click=async id=>{await evaluate(page,`document.getElementById('${id}').scrollIntoView({block:'center'})`);const r=await rect(page,'#'+id);for(const type of ['mousePressed','mouseReleased'])await page.send('Input.dispatchMouseEvent',{type,x:r.x,y:r.y,button:'left',clickCount:1});};
   for(const cmd of reads){
    await click('rs-cmd-'+cmd);await waitFor(page,"document.getElementById('rs-cmd-out-text')?.textContent.includes('fixture read result')");
    assert.equal(await evaluate(page,"document.getElementById('confirm').hidden"),true);
    assert.equal(calls.filter(c=>c.cmd===cmd).length,1);
   }
   for(const cmd of protectedCmds){
   await click('rs-cmd-'+cmd);
   if(tv)await waitFor(page,"document.getElementById('toast').textContent.length>0");else await waitFor(page,"document.querySelector('#rs-cmd-out .rsfail')?.textContent.length>0");
   }
   assert.equal(calls.length,tv?reads.length:reads.length+protectedCmds.length);assert.equal(await evaluate(page,'window.p507UVCalls'),0);
   evidence.push({lang,tv,reads:reads.length,requests:calls.length,uvCalls:0,reconnect:tv?'phone confirmation required':'no_passkey refused'});
  }finally{await page.close();}
 }
 const rec=parityRecorder('web/test/p507_readonly.mjs');rec.record('p507-fixed-read-floor',true);rec.write();
 if(out)writeFileSync(out+'/result.json',JSON.stringify(evidence,null,2)+'\n');console.log('P507 readonly browser PASS',JSON.stringify(evidence));
}finally{await browser.close();await fake.stop();await web.stop();}
