// Negotiation and delivery-only observations through the real browser and isolated Noise host.
import assert from 'node:assert/strict';
import { approvalEnvelopeDigest, b64u } from '../../protocol/wire.js';
import { elevateChallenge } from '../public/js/faceid.js';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, sleep } from './browser.mjs';

const web = await startWebServer(), host = await startFakeHost(), browser = await launch();
const URL = web.url.replace('127.0.0.1', 'localhost');
const waitCase = (page, m) => waitFor(page, `document.getElementById('sheet').dataset.card.startsWith('permission|'+${JSON.stringify(m.id)}+'|')`);
const receipts = m => host.log.filter(f => f.t === 'card_receipt' && f.kind === 'ask' && f.id === m.id);
const subjects = m => receipts(m).map(f => [f.n, f.phase]);
const expect = (m, ...phases) => assert.deepEqual(subjects(m), phases.map(phase => [m.delivery.n, phase]));
async function waitReceipts(m, ...phases) {
  for (let i = 0; i < 80 && receipts(m).length < phases.length; i++) await sleep(50);
  expect(m, ...phases);
}

async function pair(features, width, theme) {
  host.reset();
  host.st.extraCaps = features;
  host.st.prefs = { appearance: { language: 'en', theme } };
  const page = await newPage(browser, width, width < 500 ? 844 : 900, theme, { touch: width < 500, allow: [URL, host.relay + '/'] });
  await navigate(page, host.newPairing(URL + '?lang=en'));
  await waitState(page, 'awaiting-approval');
  await host.approve();
  await waitState(page, 'ready');
  await page.send('Page.bringToFront');
  await evaluate(page, "for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}");
  await host.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await waitFor(page, "import('./js/api.js').then(m=>m.canSign())");
  assert.ok(host.log.some(f => f.t === 'hello' && f.caps?.includes('card_ask_receipt1')));
  return page;
}

async function approval(id, nonce, { uv = false, revision = 1 } = {}) {
  const envelope = {
    v: 2, channel: host.channel, session: String(host.st.epoch), request_id: id.repeat(32), revision,
    nonce: revision.toString(), title: 'Review test note', source: { channel: 'phone', verified: true, text: 'Compare two examples' },
    action: 'Compare a synthetic draft task', details: 'Read an isolated fixture directory', effects: [],
    expires_at: Math.ceil(Date.now() / 1000) + 120,
  };
  const digest = await approvalEnvelopeDigest(envelope);
  const m = { t:'ask', id:envelope.request_id, tool:'Bash', summary:envelope.details, approval_v:2, envelope,
    envelope_sha256:digest, revision, review_state:'pending', ttl:120, delivery:{ n:nonce.repeat(32), ttl:30 } };
  if (uv) {
    m.passkey_required = true;
    m.fa = b64u(new Uint8Array(16).fill(7));
    m.fa_s = 'local';
    m.pk_kind = 'sandbox';
    m.fa_ch = b64u(await elevateChallenge(host.channel, host.devs.at(-1).id, m.id, m.pk_kind, m.id, digest));
  }
  return m;
}

async function uncovered(page, m) {
  await host.ask(m);
  await waitCase(page, m);
  await waitReceipts(m, 'received', 'visible');
}

try {
  // Deploying to a host that only advertises question receipts does not offer approval feedback.
  let page = await pair(['card_receipt1'], 390, 'light');
  let m = await approval('1', 'a');
  await host.ask(m);
  await waitCase(page, m);
  await sleep(450);
  expect(m);
  await page.dispose();

  for (const [width, theme, uv] of [[390, 'light', false], [390, 'dark', true], [1440, 'light', true], [1440, 'dark', false]]) {
    page = await pair(['card_ask_receipt1'], width, theme);
    m = await approval('2', 'a', { uv });
    await uncovered(page, m);
    assert.ok(receipts(m).every(f => Object.keys(f).sort().join(',') === 'id,kind,n,phase,t'));
    assert.equal(host.st.answers.length, 0, 'receipt neither approves nor denies');
    await page.dispose();
  }

  page = await pair(['card_ask_receipt1'], 390, 'dark');
  m = await approval('3', 'a', { uv: true });
  await host.ask({ ...m, envelope_sha256: '0'.repeat(64) });
  await waitCase(page, m);
  await sleep(450);
  expect(m);
  await host.askDone(m.id, 'gone');
  await waitFor(page, `import('./js/snap.js').then(s=>!s.S.asks.has('${m.id}'))`);

  // Even a verified receipt cannot claim a visible presentation under a click-through product notice.
  const pending = await approval('4', 'a', { uv: true });
  const resized = await approval('4', 'b', { uv: true, revision: 2 });
  await evaluate(page, `(() => {
    const veil = document.createElement('div'); veil.id = 'receipt-test-notice';
    veil.className = 'toast on'; veil.dataset.ajOwner = 'aj-notice';
    veil.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#2c2b26;pointer-events:none';
    document.body.append(veil);
  })()`);
  await host.ask(pending);
  await waitReceipts(pending, 'received');
  await host.ask(resized);
  await waitFor(page, `import('./js/snap.js').then(s=>s.S.asks.get('${resized.id}')?.envelope_verified===true && s.S.asks.get('${resized.id}').revision===2)`);
  for (let i = 0; i < 40 && receipts(resized).length < 2; i++) await sleep(50);
  const before = [[pending.delivery.n, 'received'], [resized.delivery.n, 'received']];
  assert.deepEqual(subjects(resized), before);
  await evaluate(page, "document.getElementById('receipt-test-notice').remove()");
  for (let i = 0; i < 40 && receipts(resized).length < 3; i++) await sleep(50);
  const visible = [...before, [resized.delivery.n, 'visible']];
  assert.deepEqual(subjects(resized), visible, 'only the replacement may report being shown');

  await host.send({ ...resized, envelope: { ...resized.envelope, action: 'Different action' } });
  await sleep(350);
  assert.deepEqual(subjects(resized), visible, 'a changed envelope cannot consume a retry slot');
  await evaluate(page, "document.getElementById('sheetClose').click()");
  await waitFor(page, "document.body.dataset.sheet==='0'");
  await host.send({ ...resized, ttl:119, delivery:{ ...resized.delivery, ttl:29 } });
  for (let i = 0; i < 40 && receipts(resized).length < 4; i++) await sleep(50);
  assert.deepEqual(subjects(resized), [...visible, [resized.delivery.n, 'received']]);
  assert.equal(await evaluate(page, "document.body.dataset.sheet"), '0', 'retry preserves folding');
  await host.askDone(resized.id, 'gone');
  await sleep(400);
  assert.equal(receipts(resized).length, 4, 'closed card cannot report visibility');
  assert.equal(host.st.answers.length, 0);
  await page.dispose();
  console.log('0378 approval receipts: old host, four phone/desktop-theme ordinary/UV cases, invalid digest, occlusion, revision, retry, and withdrawal passed');
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
}
