import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out=new URL('../../../reports/qa/p98/phone-env/',import.meta.url);mkdirSync(out,{recursive:true});
const results={},web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 for(const language of ['zh','en']) {
  for(const platform of ['ios','android']) {
   const ua=platform==='ios'?'Mozilla/5.0 (iPhone) Safari/605 MicroMessenger':'Mozilla/5.0 (Linux; Android 15) Chrome/130 Line/14';
   const p=await newPage(B,360,780,'light',{ua,touch:true,allow:[web.url,fake.relay+'/']});
   await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`Object.defineProperty(navigator,'storage',{value:{persisted:async()=>false}});Object.defineProperty(navigator,'clipboard',{value:{writeText:async v=>{window.copiedLink=v}}});`});
   await navigate(p,web.url+'?lang='+language);
   await waitFor(p,`!document.getElementById('phone-env').hidden`);
   const text=await evaluate(p,`document.getElementById('phone-env-text').textContent`);
   assert.match(text,platform==='ios'?/Safari/:/Chrome/);
   assert.equal(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth`),true);
   await evaluate(p,`document.getElementById('phone-env-copy').click()`);
   await waitFor(p,`!!window.copiedLink`);
   assert.equal(await evaluate(p,`window.copiedLink`),web.url);
   writeFileSync(new URL(platform+'-'+language+'-pair.png',out),await shoot(p,''));
   await navigate(p,'about:blank');
   await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await waitFor(p,`!document.getElementById('phone-env').hidden && !document.getElementById('setup-faceid').hidden`);
   await evaluate(p,`document.getElementById('phone-env-dismiss').click()`);
   assert.equal(await evaluate(p,`document.getElementById('phone-env').hidden`),true);
   await evaluate(p,`document.getElementById('aj-menu').open=true`);
   writeFileSync(new URL(platform+'-'+language+'-menu.png',out),await shoot(p,''));
   // Supported platform authenticator: Later can be reversed via the menu's forced offer request.
   await evaluate(p,`PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>true`);
   const nonce=Buffer.alloc(32,1).toString('base64url');
   await fake.send({t:'pk_offer',n:nonce});
   await waitFor(p,`!document.getElementById('confirm').hidden`);
   await evaluate(p,`document.getElementById('confirm-no').click()`);
   await evaluate(p,`document.getElementById('setup-faceid').click()`);
   await waitFor(p,`document.getElementById('setup-faceid').hidden===false`);
   await fake.send({t:'pk_offer',n:nonce});
   await waitFor(p,`!document.getElementById('confirm').hidden`);
   await evaluate(p,`document.getElementById('confirm-no').click()`);
   results['phone-env-'+platform+'-'+language]='pass';await p.dispose();
  }
  const p=await newPage(B,360,780,'light',{ua:'Mozilla/5.0 (iPhone) Safari/605',touch:true,allow:[web.url]});
  await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`Object.defineProperty(window,'indexedDB',{value:{open(){throw new DOMException('blocked','SecurityError')}}});`});
  await navigate(p,web.url+'?lang='+language);await waitState(p,'error');
  await waitFor(p,`!document.getElementById('phone-env').hidden`);
  assert.match(await evaluate(p,`document.getElementById('phone-env-text').textContent`),/Safari/);
  writeFileSync(new URL('storage-blocked-'+language+'.png',out),await shoot(p,''));
  results['phone-env-blocked-'+language]='pass';await p.dispose();
 }
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('results.json',out),JSON.stringify(results,null,2)+'\n');
if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p98_phone_env.mjs',results}));
console.log('PASS',results);
