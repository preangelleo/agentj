// Real tab transitions on the paired, encrypted local web client. Read visibility metadata only.
import assert from 'node:assert/strict';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, sleep } from './browser.mjs';

const web = await startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'});
const fake = await startFakeHost();
const B = await launch();
let p, other;
async function waitPresence(expect) {
  for (let i = 0; i < 60; i++) {
    if (fake.log.filter(m => m.t === 'vis').at(-1)?.fg === expect) return;
    await sleep(50);
  }
  assert.fail('Host did not receive current visibility ' + expect);
}
try {
  const page = web.url.replace('127.0.0.1', 'localhost');
  fake.setUp(true);
  p = await newPage(B, 1200, 900, 'light', {allow:[page, web.url, fake.relay + '/']});
  // Reproduce a missed foreground transition without setting document.visibilityState ourselves.
  await p.send('Page.addScriptToEvaluateOnNewDocument', {source:`
    document.addEventListener('visibilitychange', e => {
      if (document.visibilityState === 'visible') e.stopImmediatePropagation();
    }, true);
  `});
  await navigate(p, fake.newPairing(page));
  await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
  const {targetInfo} = await p.send('Target.getTargetInfo');
  other = await B.browser.send('Target.createTarget', {url:'about:blank',browserContextId:targetInfo.browserContextId});
  await B.browser.send('Target.activateTarget', {targetId:other.targetId});
  await waitFor(p, "document.visibilityState === 'hidden'");
  await waitPresence(false);
  await p.send('Page.bringToFront');
  await waitFor(p, "document.visibilityState === 'visible'");
  await waitPresence(true);
  const vis = fake.log.filter(m => m.t === 'vis').map(m => m.fg);
  assert.ok(vis.includes(false) && vis.at(-1) === true);
  console.log(JSON.stringify({actualHiddenAndVisible:true, missedVisibleEvent:true, orderedVisibility:vis}));
} finally {
  if (other) await B.browser.send('Target.closeTarget', {targetId:other.targetId}).catch(()=>{});
  await p?.dispose(); await B.close(); await fake.stop(); await web.stop();
}
