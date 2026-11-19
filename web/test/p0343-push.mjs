// Reconnect a paired page using its pre-upgrade notification choices and existing browser subscription.
import assert from 'node:assert/strict';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, evaluate, waitState } from './browser.mjs';
import { setTimeout as sleep } from 'node:timers/promises';

const web = await startWebServer(), host = await startFakeHost(), browser = await launch();
const key = Buffer.from(Uint8Array.of(4, ...new Uint8Array(64).fill(5))).toString('base64url');
const other = Buffer.from(Uint8Array.of(4, ...new Uint8Array(64).fill(6))).toString('base64url');
let page;
try {
  page = await newPage(browser, 390, 844, 'light', { allow: [web.url, host.relay + '/'] });
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
    const sub = { options: {applicationServerKey: Uint8Array.of(4, ...new Uint8Array(64).fill(5))},
      toJSON: () => ({endpoint:'https://example.test/push',keys:{p256dh:'fixture',auth:'fixture'}}),
      unsubscribe: () => { throw Error('must keep existing subscription'); } };
    window.fixturePush = {sub, available:sub, prompts:0, created:0};
    Object.defineProperty(Notification, 'permission', {get:()=>'granted'});
    Notification.requestPermission = async () => { window.fixturePush.prompts++; return 'granted'; };
    PushManager.prototype.getSubscription = async () => window.fixturePush.available;
    PushManager.prototype.subscribe = async () => { window.fixturePush.created++; throw Error('must not create'); };
  })()` });
  await navigate(page, host.newPairing(web.url));
  await waitState(page, 'awaiting-approval'); await host.approve(); await waitState(page, 'ready');
  const count = () => host.log.filter(m => m.t === 'push_sub').length;
  const ev = s => evaluate(page, s);
  // Direct calls await the normal production handler. push_sub still crosses the paired Noise session.
  const ready = (k, restore = true) => ev(`import('./js/push.js').then(async p => {
    p.gotPushStatus(false, undefined, ${restore}); await p.gotPushKey('${k}');
  })`);
  await ready(key);
  assert.equal(count(), 0, 'permission and a subscription alone cannot turn notifications on');
  await ev("localStorage.setItem('aj.pushExplicitOff','0')");
  await ready(key);
  assert.equal(count(), 0, 'missing second durable choice must not enable notifications');
  await ev(`import('./js/push.js').then(async p => {await p.setKind('security', false); await p.enablePush();})`);
  for (let i = 0; i < 40 && count() !== 1; i++) await sleep(50);
  assert.equal(count(), 1, 'explicit enable seeded old-format consent');
  await ready(other); await ready(key, false);
  await ev('fixturePush.available = null'); await ready(key);
  assert.equal(count(), 1, 'key mismatch, explicit Host off, and missing browser subscription are final');
  await ev('fixturePush.available = fixturePush.sub');
  for (const bad of ['broken json', '["unknown"]', 'null']) {
    await ev(`localStorage.setItem('aj.pushKinds', ${JSON.stringify(bad)})`);
    await ready(key);
    await sleep(100);
    assert.equal(count(), 1, 'damaged persisted categories must not broaden prior preferences');
  }
  await ev("localStorage.setItem('aj.pushKinds',JSON.stringify(['ask','reply']))");
  // A real reconnect hint and key arrive through the same paired message handler as production.
  await host.send({t:'push_state', on:false, restore_existing:true});
  await host.send({t:'push_key', k:key});
  for (let i = 0; i < 40 && count() !== 2; i++) await sleep(50);
  assert.equal(count(), 2, 'initial absent host record recovers exactly the existing subscription');
  assert.deepEqual(host.log.filter(m => m.t === 'push_sub').at(-1).kinds, ['ask','reply']);
  await host.send({t:'push_state', on:true});
  await sleep(100);
  assert.equal(count(), 2, 'Host acknowledgement must not cause a second registration');
  await ev("localStorage.setItem('aj.pushExplicitOff','1')");
  await ready(key);
  assert.equal(count(), 2, 'a durable off takes precedence over a retained browser subscription');
  assert.deepEqual(await ev('[fixturePush.prompts, fixturePush.created]'), [1,0]);
  assert.deepEqual(page.offsite, []);
  console.log('p0343-push PASS');
} finally {
  if (page) await page.dispose();
  await browser.close(); await host.stop(); await web.stop();
}
