// Close the paired page, then reopen the ordinary URL in the same WebKit device store.
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {ticketValue} from '../public/js/renew.js';
import {createRequire} from 'node:module';
import {startFakeHost} from './fakehost.mjs';
import {startWebServer} from './serve.mjs';

const {webkit, devices} = createRequire(import.meta.url)(
  process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const web = await startWebServer({publicDir:process.env.AJ_TEST_WEB_PUBLIC}), host = await startFakeHost();
const browser = await webkit.launch();
try {
  const context = await browser.newContext({...devices['iPhone 13'],serviceWorkers:'block', viewport:{width:390,height:844},deviceScaleFactor:1});
  // WPE has no native audio playback backend on the release runner.
  await context.addInitScript(()=>{
    window.Audio=undefined;
    for (const method of ['play','pause','load']) HTMLMediaElement.prototype[method]=
      method==='play' ? ()=>Promise.resolve() : ()=>{};
  });
  const page = await context.newPage();
  // A fresh browser storage area can have an HttpOnly ticket even though it has no approved device key.
  // Seed only this temporary loopback origin. Refuse and count any proof requests before they reach the test host.
  await context.addCookies([{name:'aj_rt',value:ticketValue({
    relayIndex:0,channel:host.channel,hostPub:Buffer.from(host.hostPub,'base64url')
  },randomBytes(16).toString('base64url'),randomBytes(32).toString('base64url')),
  domain:'127.0.0.1',path:'/.aj/rt',httpOnly:true,sameSite:'Strict'}]);
  let proofs=0;
  await context.route('**/.aj/rt',async route=>{
    if (route.request().postDataJSON()?.op==='prove') { proofs++; await route.fulfill({status:403}); }
    else await route.continue();
  });
  await page.goto(web.url+'robots.txt');
  assert.equal(await page.evaluate(async()=>{
    const result=await fetch('/.aj/rt',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({op:'has'})});
    return (await result.json()).has;
  }),true,'positive control: the origin recognizes the test ticket');
  await page.goto(web.url+'?relay='+encodeURIComponent(host.relay));
  await page.waitForFunction(()=>window.__ajState==='idle' || window.__ajState==='connecting',null,{timeout:8000})
    .catch(()=>{throw Error('Unpaired startup did not settle; proof requests='+proofs);});
  assert.equal(proofs,0,'cold launch must not request a ticket proof for a new device key');
  await page.locator('#pair-view').waitFor({state:'visible'});
  await page.evaluate(()=>{window.dispatchEvent(new Event('online'));document.dispatchEvent(new Event('visibilitychange'));});
  // Wait through one turn of browser startup and foreground work before checking the absence of network activity.
  await page.waitForTimeout(400);
  assert.equal(proofs,0,'new browser keys need owner approval even with a validly formatted ticket cookie');
  assert.equal(host.conns,0,'the unpaired page cannot open a host session');
  await page.goto(host.newPairing(web.url));
  await page.waitForFunction(()=>window.__ajState==='awaiting-approval');
  await host.approve();
  await page.waitForFunction(()=>window.__ajState==='ready' && document.body.dataset.sync==='ready');
  await page.close();

  const yesterday = Date.now() - 86400000;
  for (let id=1;id<=152;id++){
    const owner = [1,51,103,152].includes(id);
    const row = await host.addTurn(owner ? {k:'phone',text:'Question '+id} :
      {k:'agent',via:'background_done',text:'Background task'}, owner ? 'Answer '+id : 'Task completed');
    row.ts = id <= 51 ? yesterday : Date.now();
  }
  const reads = [];
  host.st.onApp = async (_c,m)=>{
    if (m.t==='hist_get') reads.push(m.before ?? 'head');
    return false;
  };

  const reopened = await context.newPage();
  await reopened.goto(web.url);
  await reopened.waitForFunction(()=>window.__ajState==='ready' && document.body.dataset.sync==='ready');
  await reopened.evaluate(()=>{
    document.getElementById('phone-env-dismiss')?.click();
    document.getElementById('a2hs-ok')?.click();
  });
  async function assertCard(id) {
    await reopened.waitForFunction(id=>document.getElementById('omText').textContent==='Question '+id &&
      document.getElementById('words').textContent.includes('Answer '+id), id, {timeout:4000});
  }
  await assertCard(152);
  for (const id of [103,51,1]) {
    // WebKit automation only exposes native tap. Feed a rightward swipe into the product DOM gesture handler.
    await reopened.locator('#deck').evaluate(deck=>{
      for (const [type,x] of [['touchstart',80],['touchmove',250],['touchend',250]]) {
        const event = new Event(type,{bubbles:true,cancelable:true});
        Object.defineProperty(event,'touches',{value:type==='touchend' ? [] : [{clientX:x,clientY:260}]});
        deck.dispatchEvent(event);
      }
    });
    try { await assertCard(id); }
    catch {throw Error('Previous card did not reach '+id+
      '; history requests '+JSON.stringify(reads));}
  }
  assert.equal(await reopened.locator('#pgPrev').isDisabled(),true);
  assert.ok(reads.includes(103) && reads.includes(53) && reads.includes(3));
  await reopened.close();
  await host.revokeAll();
  host.st.removalNotice={t:'removed',why:'replaced',moved:true};
  const stale = await context.newPage();
  await stale.goto(web.url);
  await stale.locator('#revoked-twoid').waitFor({state:'visible'});
  assert.equal(await stale.locator('#chat-view').isVisible(),false);
  const handshakes = host.st.firsts.length;
  await stale.close();
  const next = await context.newPage();
  await next.goto(web.url);
  await next.locator('#revoked-view').waitFor({state:'visible'});
  assert.equal(host.st.firsts.length,handshakes,'a confirmed replaced pairing does not handshake on cold launch');
  assert.equal(await next.locator('#repair').isVisible(),true,'a replaced entry offers manual pairing');
  await next.locator('#repair').tap();
  await next.locator('#pair-view').waitFor({state:'visible'});
  assert.equal(host.allowList().length,0,'selecting repair grants no access');
  await next.goto(host.newPairing(web.url));
  await next.waitForFunction(()=>window.__ajState==='awaiting-approval');
  assert.equal(host.allowList().length,0,'the host still has to approve the new pairing');
  await host.approve();
  await next.waitForFunction(()=>window.__ajState==='ready' && document.getElementById('words').textContent.includes('Answer 152'));
  assert.equal(host.st.turns.length,152,'manual repair preserves the server history');
  console.log(JSON.stringify({engine:'WebKit',version:browser.version(),viewport:390,resumed:true,reads,
    ownerCards:[152,103,51,1],crossedYesterday:true,authenticatedRemoval:true,noStaleResume:true,manualPairApproved:true,noTicketProofForNewIdentity:true}));
} finally {await browser.close();await host.stop();await web.stop();}
