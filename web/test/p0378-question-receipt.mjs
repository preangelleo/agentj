import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { questionMessage, unb64u } from '../../protocol/wire.js';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, sleep } from './browser.mjs';

const web = await startWebServer(), host = await startFakeHost(), browser = await launch();
const first = '#qs .opt[data-q="0"][data-n="1"]';
const pending = '#pending541:not([hidden]),#pendTag:not([hidden])';
const displayed = 'document.body.dataset.sheet==="1"';
const activeQuestion = title => `${displayed} && document.getElementById("sheetTitle").textContent===${JSON.stringify(title)}`;
const ack = id => host.log.filter(frame => frame.t === 'card_receipt' && frame.id === id);
const phases = id => ack(id).map(frame => frame.phase);
const card = (id, nonce, extra = {}) => ({
  t: 'question', id: id.repeat(32), text_max: 2000, ttl: 120, delivery: { n: nonce.repeat(32), ttl: 30 },
  qs: [{ q: 'Which sample?', h: '', m: true, o: [{ l: 'Example A', d: '' }, { l: 'Example B', d: '' }] }],
  ...extra,
});

async function pair(supports) {
  host.reset();
  host.st.extraCaps = supports ? ['card_receipt1'] : [];
  host.st.prefs = { appearance: { language: 'en', theme: 'light' }, voice: { speak_replies: false, wake_enabled: false } };
  const url = web.url.replace('127.0.0.1', 'localhost');
  const page = await newPage(browser, 390, 844, 'light', { touch: true, allow: [url, host.relay + '/'] });
  await navigate(page, host.newPairing(url + '?lang=en'));
  await waitState(page, 'awaiting-approval');
  await host.approve();
  await waitState(page, 'ready');
  await page.send('Page.bringToFront');
  await evaluate(page, "for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}");
  await host.send({ t: 'status', s: 'idle', name: 'Agent J' });
  assert.equal(await evaluate(page, "import('./js/session.js').then(s=>s.peer.cardReceipt1)"), supports);
  assert.ok(host.log.some(frame => frame.t === 'hello' && frame.caps?.includes('card_receipt1')));
  return page;
}
async function sent(id, expected) {
  for (let count = 0; count < 100 && ack(id).length < expected.length; count++) await sleep(50);
  assert.deepEqual(phases(id), expected);
}
async function click(page, selector) {
  await evaluate(page, `document.querySelector(${JSON.stringify(selector)}).click()`);
}
async function close(page, question) {
  await host.send({ t: 'question_done', id: question.id, result: 'gone' });
  await waitFor(page, `import('./js/snap.js').then(s=>!s.S.qs.has(${JSON.stringify(question.id)}))`);
}

async function legacy(page) {
  const old = card('1', 'a');
  await host.send(old);
  await waitFor(page, activeQuestion(old.qs[0].q));
  await sleep(250);
  assert.deepEqual(phases(old.id), []);
}
async function acknowledge(page) {
  const active = card('1', 'b');
  await host.send(active);
  await waitFor(page, activeQuestion(active.qs[0].q));
  await sent(active.id, ['received', 'visible']);
  for (const frame of ack(active.id)) {
    assert.deepEqual(frame, { t: 'card_receipt', kind: 'question', id: active.id, n: active.delivery.n, phase: frame.phase });
  }
  assert.equal(host.st.qAnswers.length, 0, 'display did not answer the question');

  // A corrupt duplicate with the same id and probe must not count, even though the original still shows.
  await host.send({ ...active, qs: [{ ...active.qs[0], q: 'Different text' }] });
  await sleep(300);
  assert.deepEqual(phases(active.id), ['received', 'visible']);

  await click(page, first);
  await waitFor(page, `document.querySelector(${JSON.stringify(first)}).getAttribute('aria-pressed')==='true'`);
  await click(page, '#sheetClose');
  await waitFor(page, `!(${displayed}) && !!document.querySelector(${JSON.stringify(pending)})`);
  await host.send(active);
  await sent(active.id, ['received', 'visible', 'received']);
  await host.send(active);
  await sleep(200);
  assert.equal(await evaluate(page, displayed), false, 'a duplicate never reopens a user-folded card');

  await click(page, pending);
  await waitFor(page, activeQuestion(active.qs[0].q));
  await sent(active.id, ['received', 'visible', 'received', 'visible']);
  await sleep(250);
  assert.equal(ack(active.id).length, 4, 'at most one repeat of each stage');
  assert.equal(await evaluate(page, `document.querySelector(${JSON.stringify(first)}).getAttribute('aria-pressed')`), 'true');

  await click(page, '#askSend');
  for (let count = 0; count < 100 && !host.st.qAnswers.length; count++) await sleep(50);
  const [answer] = host.st.qAnswers;
  assert.deepEqual(answer.pick, [[1]], 'only the owner action produced a signed answer carrying the original selection');
  const device = host.devs.at(-1);
  const key = await webcrypto.subtle.importKey('raw', unb64u(device.sk), { name: 'Ed25519' }, false, ['verify']);
  assert.equal(await webcrypto.subtle.verify({ name: 'Ed25519' }, key, unb64u(answer.sig),
    await questionMessage(host.channel, device.id, active.id, 'answer', active.qs, answer.pick)), true);
}

async function hiddenAndOccluded(page) {
  const hidden = card('2', 'c');
  await evaluate(page, "Object.defineProperty(document,'hidden',{configurable:true,get:()=>true})");
  await host.send(hidden);
  await sent(hidden.id, ['received']);
  await sleep(250);
  assert.deepEqual(phases(hidden.id), ['received']);
  await evaluate(page, `(() => {
    const veil = document.createElement('div');
    veil.id = 'receipt-fixture-veil';
    veil.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#2c2b26';
    document.body.append(veil);
    delete document.hidden;
  })()`);
  await waitFor(page, "document.elementFromPoint(innerWidth/2,innerHeight/2)?.id==='receipt-fixture-veil'");
  await sleep(375);
  assert.deepEqual(phases(hidden.id), ['received']);
  await evaluate(page, `(() => {
    const veil = document.getElementById('receipt-fixture-veil');
    veil.className = 'toast on';
    veil.dataset.ajOwner = 'aj-notice';
    veil.style.pointerEvents = 'none';
    veil.textContent = 'Example foreground notice';
  })()`);
  await waitFor(page, "document.elementFromPoint(innerWidth/2,innerHeight/2)?.id!=='receipt-fixture-veil'");
  await sleep(375);
  assert.deepEqual(phases(hidden.id), ['received'], 'a click-through product notice can still hide the choices');
  await evaluate(page, "document.getElementById('receipt-fixture-veil').remove()");
  await sent(hidden.id, ['received', 'visible']);
  await close(page, hidden);
}

async function lostBeforeEncryption(page) {
  const delayed = card('5', 'a');
  await evaluate(page, "Object.defineProperty(document,'hidden',{configurable:true,get:()=>true})");
  await host.send(delayed);
  await sent(delayed.id, ['received']);
  await evaluate(page, `import('./js/session.js').then(s => {
    const current = s.gen();
    const pause = new Promise(resolve => { window.releaseReceiptFixture = resolve; });
    current.chain = current.chain.then(() => pause);
    window.pausedReceiptFixture = current.chain;
    delete document.hidden;
  })`);
  await waitFor(page, "import('./js/session.js').then(s=>s.gen().chain!==window.pausedReceiptFixture)");
  await evaluate(page, `(() => {
    Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});
    window.releaseReceiptFixture();
    delete window.releaseReceiptFixture;
    delete window.pausedReceiptFixture;
  })()`);
  await sleep(300);
  assert.deepEqual(phases(delayed.id), ['received']);
  await evaluate(page, 'delete document.hidden');
  await sent(delayed.id, ['received', 'visible']);
  await close(page, delayed);
}

async function expireAndReconnect(page) {
  const expired = card('3', 'd', { ttl: 1, delivery: { n: 'd'.repeat(32), ttl: 1 } });
  await evaluate(page, "Object.defineProperty(document,'hidden',{configurable:true,get:()=>true})");
  await host.send(expired);
  await sent(expired.id, ['received']);
  await sleep(1300);
  await evaluate(page, 'delete document.hidden');
  await sleep(250);
  assert.deepEqual(phases(expired.id), ['received']);
  await close(page, expired);

  const reconnect = card('4', 'e');
  await evaluate(page, "Object.defineProperty(document,'hidden',{configurable:true,get:()=>true})");
  await host.send(reconnect);
  await sent(reconnect.id, ['received']);
  host.kick();
  await waitFor(page, "window.__ajState!=='ready'");
  await waitState(page, 'ready');
  await evaluate(page, 'delete document.hidden');
  const { delivery, ...historical } = reconnect;
  await host.send(historical);  // Existing on-ready question display, with no new delivery probe.
  await waitFor(page, activeQuestion(historical.qs[0].q));
  await sleep(300);
  assert.deepEqual(phases(reconnect.id), ['received']);
  await close(page, reconnect);
}

async function refusers(page) {
  const clean = card('a', 'f');
  const negative = [
    { ...clean, delivery: { n: 'bad', ttl: 30 } },
    { ...clean, delivery: { ...clean.delivery, ttl: 61 } },
    { ...clean, delivery: { ...clean.delivery, extra: true } },
    { ...clean, text_max: '2000' },
    { ...clean, qs: [{ ...clean.qs[0], h: 'menu\nscreen' }] },
    { ...clean, qs: [{ ...clean.qs[0], m: 'true' }] },
    { ...clean, qs: [{ ...clean.qs[0], o: [{ l: 'Example A', d: 5 }] }] },
  ];
  for (const [i, update] of negative.entries()) {
    const q = { ...update, id: 'b'.repeat(31) + i.toString(16) };
    await host.send(q);
    await waitFor(page, activeQuestion(q.qs[0].q));
    await sleep(200);
    assert.deepEqual(phases(q.id), []);
    await close(page, q);
  }
  await host.ask({ id: '9'.repeat(32), tool: 'FixtureHarness', summary: 'Sample approval', ttl: 120, delivery: clean.delivery });
  await waitFor(page, "document.getElementById('sheet').dataset.card.startsWith('permission|')");
  await sleep(200);
  assert.deepEqual(phases('9'.repeat(32)), [], 'permission cards cannot use this receipt');
}

let step = 'legacy-capability';
try {
  let page = await pair(false);
  try { await legacy(page); } finally { await page.dispose(); }
  page = await pair(true);
  try {
    step = 'typed-display-signed-answer';
    await acknowledge(page);
    step = 'hidden-occluded';
    await hiddenAndOccluded(page);
    step = 'visibility-lost-before-send';
    await lostBeforeEncryption(page);
    step = 'expiry-reconnect';
    await expireAndReconnect(page);
    step = 'invalid-wire-shapes';
    await refusers(page);
    assert.deepEqual(page.problems, []);
    assert.deepEqual(page.offsite, []);
  } finally { await page.dispose(); }
  console.log('p0378-question-display-receipt PASS');
  console.log('p0378-question-no-approval-bypass PASS');
  console.log('p0378-question-receipt-refusals PASS');
} catch (error) {
  console.error('P44-JOURNEY-FAIL ' + JSON.stringify({ journey: 'ordinary-question-delivery', step, location: 'paired-chat' }));
  throw error;
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
}
