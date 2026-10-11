// P142 item 8 (A16): the sign-in QR card, the secret pickup card and the sudo/secret card are layers — ESC does the card's own
// quiet way out (never a hidden destructive step: the secret card's first ESC only arms 「不提供」, like its button), and no chat
// shortcut ("?" = shortcuts, "s" = settings) leaks through any of them or the setup page. Owned headless Chromium + fake host;
// the browser and pages are reaped at the end (P142 item 9) and the run asserts nothing it started is left.
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,key,sleep} from './browser.mjs';
import {leftovers} from '../../tests/lib/reap.mjs';
const ok=(v,n)=>{assert.ok(v,n);console.log('PASS '+n);};
const hex=()=>randomBytes(16).toString('hex');
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const sent=(t,from)=>fake.log.slice(from).filter(m=>m.t===t||m.app?.t===t||m.m?.t===t);
let p;
try{
  p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url));await waitState(p,'ready'.replace('ready','awaiting-approval'));await fake.approve();await waitState(p,'ready');
  await evaluate(p,"document.getElementById('phone-env-dismiss')?.click()");
  const noLeak=async(what)=>{
    await key(p,'?');await key(p,'s');await sleep(150);
    const st=await evaluate(p,"JSON.stringify({keys:document.getElementById('keys').hidden,settings:document.getElementById('settings').hidden,view:document.body.dataset.view,ae:document.activeElement?.id})");
    ok(await evaluate(p,"document.getElementById('keys').hidden && document.getElementById('settings').hidden"),what+': "?" and "s" do not leak through '+st);
  };
  // control: the shortcut works when nothing is open
  await evaluate(p,"document.activeElement?.blur()");await key(p,'?');await waitFor(p,"!document.getElementById('keys').hidden");
  ok(true,'control: "?" opens the shortcut list with no card open');
  await key(p,'Escape');await waitFor(p,"document.getElementById('keys').hidden");

  // 1. the sign-in QR card
  const lid=hex(),png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
  await evaluate(p,`import('./js/logincard.js').then(m=>m.add({id:${JSON.stringify(lid)},n:${JSON.stringify(hex())},site:'gmail',title:'Gmail',img:${JSON.stringify(png)},ttl:120}))`);
  await waitFor(p,"document.getElementById('lqr') && !document.getElementById('lqr').hidden");
  await evaluate(p,"document.activeElement?.blur()");await noLeak('sign-in QR card');
  let n0=fake.log.length;
  await key(p,'Escape');await sleep(400);
  ok(JSON.stringify(fake.log.slice(n0)).includes('login_qr_cancel'),'sign-in QR card: ESC = 「稍后」 (login_qr_cancel sent)');
  await evaluate(p,`import('./js/logincard.js').then(m=>m.done({id:${JSON.stringify(lid)},result:'expired'}))`);
  await waitFor(p,"document.getElementById('lqr').hidden || !document.getElementById('lqr-close').hidden");
  if(!(await evaluate(p,"document.getElementById('lqr').hidden"))){await key(p,'Escape');await waitFor(p,"document.getElementById('lqr').hidden");}
  ok(await evaluate(p,"document.getElementById('lqr').hidden"),'sign-in QR card: the ended card closes with ESC');

  // 2. the secret pickup card (not opened)
  const sid=hex();
  await evaluate(p,`import('./js/secretout.js').then(m=>m.add({id:${JSON.stringify(sid)},n:${JSON.stringify(hex())},kind:'text',name:'proxy link',purpose:'P142 test',size:12,ttl:600}))`);
  await waitFor(p,"document.getElementById('sout') && !document.getElementById('sout').hidden");
  await evaluate(p,"document.activeElement?.blur()");await noLeak('secret pickup card');
  n0=fake.log.length;await key(p,'Escape');await sleep(400);
  ok(JSON.stringify(fake.log.slice(n0)).includes('secret_out_decline'),'secret pickup card: ESC = its 「不需要了」 (secret_out_decline sent)');
  await evaluate(p,`import('./js/secretout.js').then(m=>m.done({id:${JSON.stringify(sid)},result:'declined'}))`);

  // 3. the secret card (F17): the first ESC only arms 「不提供」 — the card stays, nothing is sent
  const eid=hex(),epk=Buffer.alloc(32,7).toString('base64url');
  await evaluate(p,`import('./js/elevate.js').then(m=>m.add({id:${JSON.stringify(eid)},n:${JSON.stringify(hex())},kind:'secret',epk:${JSON.stringify(epk)},name:'OPENAI_API_KEY',purpose:'P142 test',dest:'~/.config/x.env',ttl:600}))`);
  await waitFor(p,"document.getElementById('elev') && !document.getElementById('elev').hidden");
  await evaluate(p,"document.activeElement?.blur()");await noLeak('secret card');
  const label0=await evaluate(p,"document.getElementById('elev-deny').textContent");
  n0=fake.log.length;await key(p,'Escape');await sleep(300);
  ok(!(await evaluate(p,"document.getElementById('elev').hidden"))&&await evaluate(p,"document.getElementById('elev-deny').textContent")!==label0
     &&fake.log.length===n0,'secret card: first ESC only arms 「不提供」 (card stays, nothing sent)');
  await evaluate(p,`import('./js/elevate.js').then(m=>m.done({id:${JSON.stringify(eid)},result:'gone'}))`);

  // 4. the setup page holds the keyboard too
  await evaluate(p,"import('./js/setup.js').then(m=>m.open(false))");await waitFor(p,"document.body.dataset.view==='setup'");
  await evaluate(p,"document.activeElement?.blur()");await noLeak('setup page');
  await key(p,'Escape');await waitFor(p,"document.body.dataset.view==='chat'");ok(true,'setup page: ESC back to the chat');
}finally{
  await B.close();await fake.stop();await web.stop();
}
await sleep(300);
const left=leftovers([B.profile||'aj-web-chrome-never']);
ok(left.length===0,'nothing this run started is left: '+JSON.stringify(left));
process.exit(0);
