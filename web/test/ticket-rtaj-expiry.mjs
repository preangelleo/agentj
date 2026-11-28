// Run after `node web/build.mjs`. The journey uses the actual web client and a local Noise fixture. Expiry uses
// a stepped page clock and deliberately withholds the host's terminal frame.
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import * as wire from '../../protocol/wire.js';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { evaluate, launch, navigate, newPage, rect, sleep, waitFor, waitState } from './browser.mjs';

const web = await startWebServer();
const host = await startFakeHost();
const browser = await launch();
const url = web.url.replace('127.0.0.1', 'localhost');
const evidence = process.env.RTAJ_SCREENSHOTS;
const failures = [];
let sequence = 0;

const named = id => `document.querySelector('#sheet').dataset.card.includes('${id}')`;
const visible = selector => `document.querySelector('${selector}')?.checkVisibility()`;
const logFor = id => host.log.filter(frame => frame.id === id && ['answer', 'q_answer'].includes(frame.t));
const nextId = () => (++sequence).toString(16).padStart(32, '0');

function check(label, fn) {
  try { fn(); console.log('PASS', label); } catch (error) { failures.push(label); console.log('FAIL', label, error.message); }
}

async function mark(page, label) {
  if (!evidence) return;
  mkdirSync(evidence, {recursive: true});
  const image = await page.send('Page.captureScreenshot', {format: 'png'});
  writeFileSync(join(evidence, label + '.png'), Buffer.from(image.data, 'base64'));
}

async function pair(width, theme, lang) {
  host.st.prefs = {appearance: {language: lang, theme}, voice: {speak_replies: false}};
  const page = await newPage(browser, width, width === 390 ? 844 : 900, theme,
    {touch: width === 390, allow: [url, host.relay + '/']});
  await navigate(page, host.newPairing(url + '?lang=' + lang));
  await waitState(page, 'awaiting-approval');
  await host.approve();
  await waitState(page, 'ready');
  await page.send('Page.bringToFront');
  await evaluate(page, `for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){
    const button=document.getElementById(id); if(button?.offsetParent) button.click();
  }`);
  await waitFor(page, "import('./js/api.js').then(m=>m.canSign())");
  await evaluate(page, '(() => { const original = Date.now; window.fixtureOffset = 0; Date.now = () => original() + window.fixtureOffset; })()');
  return {page, width, dev: host.devs.at(-1)};
}

async function press({page, width}, {x, y}, phase) {
  if (width === 390) {
    if (phase !== 'up') await page.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x, y}]});
    if (phase !== 'down') await page.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
  } else {
    if (phase !== 'up') await page.send('Input.dispatchMouseEvent', {type: 'mousePressed', x, y, button: 'left', clickCount: 1});
    if (phase !== 'down') await page.send('Input.dispatchMouseEvent', {type: 'mouseReleased', x, y, button: 'left', clickCount: 1});
  }
}

async function act(client, selector) {
  await waitFor(client.page, visible(selector));
  await sleep(300);
  await press(client, await rect(client.page, selector));
}

async function approval(id, revision = 1) {
  const envelope = {
    v: 2, channel: host.channel, session: String(host.st.epoch), request_id: id, revision, nonce: String(revision),
    title: 'Approval fixture', source: {channel: 'phone', verified: true, text: 'Check the synthetic note'},
    action: 'Review the synthetic note', details: 'Inspect draft fixture ' + revision, effects: [],
    expires_at: Math.ceil(Date.now() / 1000) + 120,
  };
  return {id, tool: 'Bash', summary: envelope.details, approval_v: 2, envelope,
    envelope_sha256: await wire.approvalEnvelopeDigest(envelope), envelope_verified: true, revision, review_state: 'pending', ttl: 120};
}

async function approveVisible(client, card) {
  const {page} = client;
  await evaluate(page, 'window.fixtureOffset = 0');
  await host.ask(card);
  await waitFor(page, named(card.id));
  await waitFor(page, `import('./js/snap.js').then(m=>m.S.asks.get('${card.id}')?.envelope_verified)`);
}

async function expectFrame(id) {
  for (let i = 0; i < 60 && !logFor(id).length; ++i) await sleep(50);
  assert.equal(logFor(id).length, 1, 'one signed frame reached the Noise fixture');
  return logFor(id)[0];
}

async function verify(dev, sig, message) {
  const key = await webcrypto.subtle.importKey('raw', wire.unb64u(dev.sk), {name: 'Ed25519'}, false, ['verify']);
  return webcrypto.subtle.verify({name: 'Ed25519'}, key, wire.unb64u(sig), message);
}

async function question(client) {
  const id = nextId();
  const qs = [{q: 'Does this match?', h: '', m: false, o: [{l: '正确'}, {l: '不对'}]}];
  await evaluate(client.page, 'window.fixtureOffset = 0');
  await host.send({t: 'question', id, qs, ttl: 180});
  await waitFor(client.page, named(id));
  return {id, qs};
}

try {
  for (const [width, theme, lang] of [[390, 'light', 'zh'], [390, 'dark', 'zh'], [1440, 'light', 'en'], [1440, 'dark', 'en']]) {
    const client = await pair(width, theme, lang);
    const {page, dev} = client;
    const label = width + '-' + theme;
    host.st.holdAnswers = true;

    // Before 120 seconds, Approve means allow on the exact signed envelope.
    let card = await approval(nextId());
    await approveVisible(client, card);
    await evaluate(page, 'window.fixtureOffset = 118000');
    await act(client, '#apprAllow');
    const a = await expectFrame(card.id);
    assert.equal(a.ok, true);
    assert.equal(a.revision, card.revision);
    assert.equal(await verify(dev, a.sig, await wire.approvalV2Message(host.channel, dev.id, card, 'allow', [])), true);
    await host.askDone(card.id, 'allow');
    await waitFor(page, `!(${named(card.id)})`);
    console.log('PASS', label, 'approve before 120 seconds reached fixture with verified v2 allow');

    // No terminal frame has arrived. Advance past 120 and try the same onscreen button.
    card = await approval(nextId());
    await approveVisible(client, card);
    await sleep(300);
    const button = await rect(page, '#apprAllow');
    await evaluate(page, 'window.fixtureOffset = 121000');
    await sleep(1150);
    await mark(page, label + '-stale');
    const buttons = await evaluate(page, visible('#apprBtns'));
    await press(client, button);
    await sleep(500);
    check(label + ' expired approval controls are gone', () => assert.equal(buttons, false));
    check(label + ' expired approval never signs or sends', () => assert.equal(logFor(card.id).length, 0));

    // Keep the expired fixture outstanding. Only a fresh ID may become actionable.
    const retry = await approval(nextId());
    // Leave the local clock past the old deadline while the replacement arrives.
    await host.ask(retry);
    try {
      await waitFor(page, named(retry.id), 1600);
      await act(client, '#apprDeny');
      const d = await expectFrame(retry.id);
      assert.equal(d.ok, false);
      assert.equal(await verify(dev, d.sig, await wire.approvalV2Message(host.channel, dev.id, retry, 'deny', [])), true);
      console.log('PASS', label, 'fresh ID independently denied with verified signature');
    } catch (error) { failures.push(label + ' fresh retry'); console.log('FAIL', label, 'fresh retry', error.message); }
    await host.askDone(card.id, 'timeout');
    await host.askDone(retry.id, 'deny');
    await waitFor(page, `!(${named(retry.id)})`);

    for (const option of [1, 2]) {
      const q = await question(client);
      await act(client, `#qs .opt[data-n="${option}"]`);
      await act(client, '#askSend');
      const pick = await expectFrame(q.id);
      assert.deepEqual(pick.pick, [[option]]);
      assert.equal(await verify(dev, pick.sig, await wire.questionMessage(host.channel, dev.id, q.id, 'answer', q.qs, [[option]])), true);
      await waitFor(page, `import('./js/snap.js').then(m=>!m.S.qs.has('${q.id}'))`);
    }
    console.log('PASS', label, 'both question options kept their own signed option number');

    const q = await question(client);
    await act(client, '#qs .opt[data-n="1"]');
    const send = await rect(page, '#askSend');
    await evaluate(page, 'window.fixtureOffset = 181000');
    await sleep(1150);
    const canAnswer = await evaluate(page, visible('#askSend'));
    await press(client, send);
    await sleep(500);
    check(label + ' expired question controls are gone', () => assert.equal(canAnswer, false));
    check(label + ' expired question never signs or sends', () => assert.equal(logFor(q.id).length, 0));
    await host.send({t: 'question_done', id: q.id, result: 'timeout'});
    await client.page.dispose();
  }
  // Two independently paired remotes display the same approval. While a press is held on one,
  // the other answers. Releasing the old control must not send a second decision.
  const mobile = await pair(390, 'dark', 'zh');
  const desktop = await pair(1440, 'light', 'en');
  let card = await approval(nextId());
  await approveVisible(mobile, card);
  await waitFor(desktop.page, named(card.id));
  await waitFor(desktop.page, "!document.querySelector('#apprAllow').disabled");
  await sleep(300);
  const oldButton = await rect(desktop.page, '#apprAllow');
  await press(desktop, oldButton, 'down');
  await act(mobile, '#apprAllow');
  const first = await expectFrame(card.id);
  assert.equal(first.ok, true);
  assert.equal(await verify(mobile.dev, first.sig, await wire.approvalV2Message(host.channel, mobile.dev.id, card, 'allow', [])), true);
  await host.askDone(card.id, 'allow');
  await waitFor(desktop.page, `!(${named(card.id)})`);
  await press(desktop, oldButton, 'up');
  await sleep(300);
  assert.equal(logFor(card.id).length, 1);
  console.log('PASS paired phone and desktop: only the first completed card action was sent');
  await mobile.page.dispose();

  // A revision of the same request arrives during a press. Only a fresh gesture may sign it.
  card = await approval(nextId());
  await approveVisible(desktop, card);
  await sleep(300);
  const control = await rect(desktop.page, '#apprAllow');
  await press(desktop, control, 'down');
  const replacement = await approval(card.id, 2);
  await host.ask(replacement);
  await waitFor(desktop.page, `import('./js/snap.js').then(m=>m.S.asks.get('${card.id}')?.revision===2)`);
  await press(desktop, control, 'up');
  await sleep(300);
  assert.equal(logFor(card.id).length, 0);
  await act(desktop, '#apprAllow');
  const second = await expectFrame(card.id);
  assert.equal(second.revision, 2);
  assert.equal(await verify(desktop.dev, second.sig, await wire.approvalV2Message(host.channel, desktop.dev.id, replacement, 'allow', [])), true);
  await host.askDone(card.id, 'allow');
  console.log('PASS a held pointer did not authorize the replacement revision');

  // Only the Host's affirmative renewal capability permits the explicit CTA. It sends
  // a separate signed review intent, never a new decision on the expired request.
  const review = await approval(nextId());
  await approveVisible(desktop, review);
  host.st.onApp = async (_session, message, send) => {
    if (message.t !== 'approval_review') return false;
    await send({t:'approval_review_result', id:message.id, event_id:message.event_id, ok:true, state:'restart_requested'});
    return true;
  };
  await host.send({t:'ask_done', id:review.id, result:'deny', reason:'timeout', renewable:true});
  await waitFor(desktop.page, "document.querySelector('#toast.on button')?.textContent.includes('new card')");
  await sleep(250);
  assert.equal(host.log.filter(m => m.t === 'approval_review' && m.id === review.id).length, 0);
  await act(desktop, '#toast.on button');
  await waitFor(desktop.page, "document.querySelector('#toast.on')?.textContent.includes('recheck')");
  const requests = host.log.filter(m => m.t === 'approval_review' && m.id === review.id);
  assert.equal(requests.length, 1);
  const {op, sig, event_id: eventId} = requests[0];
  assert.equal(op, 'renew');
  assert.equal(await verify(desktop.dev, sig, wire.approvalReviewMessage(host.channel, desktop.dev.id, review, 'renew', eventId)), true);
  assert.equal(logFor(review.id).length, 0);
  await host.send({t:'ask_done', id:review.id, result:'deny', reason:'timeout', renewable:true});
  await sleep(300);
  assert.equal(host.log.filter(m => m.t === 'approval_review' && m.id === review.id).length, 1);
  console.log('PASS known Host timeout: explicit signed renew once, zero old approval decisions');

  for (const reason of ['agent_gone', 'timeout']) {
    const pending = await approval(nextId());
    await approveVisible(desktop, pending);
    await act(desktop, '#apprAllow');
    await expectFrame(pending.id);
    await host.send({t:'ask_done', id:pending.id, result:'deny', reason});
    await waitFor(desktop.page, "document.querySelector('#toast.on')?.textContent.includes('original request ended') || document.querySelector('#toast.on')?.textContent.includes('approval expired')");
    assert.equal(await evaluate(desktop.page, "document.querySelector('#toast.on button') === null"), true);
    assert.equal(host.log.filter(m => m.t === 'approval_review' && m.id === pending.id).length, 0);
  }
  console.log('PASS no signed renewal on unknown native execution status or timeout without Host renewal capability');
  await desktop.page.dispose();
  assert.deepEqual(failures, []);
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
}
