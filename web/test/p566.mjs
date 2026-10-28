#!/usr/bin/env node
// P566 browser leg (#364 华为 Pad 10-28 21:02 · #354 Sammi 17:50 · #357 · #346): the REAL page against the fake host in headless
// Chromium; navigator.credentials.get is held open while the test drops the socket, the way the system's fingerprint sheet
// does (dev_gone → resume). Checked:
//   key card     the sheet's drop + the host's replay of the same card → the sealed value still reaches the host, signed,
//                with the assertion, on the NEW connection; get() done while offline → 「正在补交…」 on the card, then sent
//   pickup card  the same for 「用 Touch ID 查看」 → secret_out_open on the new connection → the value shows
//   approval     api.approve with Face ID → the signed answer on the new connection
//   refusal      the Agent J host refuses (stale) → the reason on the card + a notice above the card's scrim
//   expiry       a card at 0 goes by itself + 「这张卡已过期，请让 Agent 重发」
//   Settings     「已设好」 → 「测试一下」 → pk_check → 「正常」
//   node web/test/p566.mjs
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
import { elevateFields, elevateDigest, b64u, unb64u, SEAL_CONTEXT } from '../../protocol/wire.js';

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: process.env.P566_PUBLIC || undefined });   // P566_PUBLIC: run the 0.25.18a1 page (before)
web.url = web.url.replace('127.0.0.1', 'localhost');
const fake = await startFakeHost();
const B = await launch();
const subtle = webcrypto.subtle;
const KEY = 'p566-browser-key-DO-NOT-LEAK-0123456789';
const CRED = b64u(new Uint8Array(16).fill(5));
let failed = 0;
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const C = async (name, fn) => { try { await fn(); console.log('PASS ' + name); } catch (e) { failed++; console.log('FAIL ' + name + ': ' + (e.stack || e)); } };
const visible = (sel) => `(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !!e && !e.hidden && !e.closest('[hidden]') && e.getClientRects().length > 0; })()`;
const txt = (sel) => `(document.querySelector(${JSON.stringify(sel)})?.textContent || '')`;
const tr = (p, key) => evaluate(p, `import('./js/t.js').then(({ t }) => t(${JSON.stringify(key)}))`);
const bio = (p, key) => evaluate(p, `import('./js/pkassist.js').then((m) => m.withBio(${JSON.stringify(key)}))`);
let mark = 0;   // fake.log keeps every earlier case's messages: each case reads only its own
const got = (t, id = null) => fake.log.slice(mark).filter((m) => m.t === t && (!id || m.id === id));   // id: an earlier case's page may still hand its own in

async function paired() {
  fake.reset();
  mark = fake.log.length;
  const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=zh'));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  // the system sheet: held open until the test says so (a real one sends the page to the background meanwhile)
  await evaluate(p, `(() => { window.__get = { n: 0, hold: false, release: null };
    navigator.credentials.get = (o) => new Promise((res) => {
      const ids = (o.publicKey.allowCredentials || []).map((c) => new Uint8Array(c.id));
      window.__get.n++;
      const answer = () => res({ rawId: ids[0].buffer, authenticatorAttachment: 'platform', response: { clientDataJSON: new Uint8Array(40).buffer,
        authenticatorData: new Uint8Array(37).buffer, signature: new Uint8Array(70).buffer, userHandle: null } });
      if (window.__get.hold) window.__get.release = answer; else answer();
    });
    return true; })()`);
  return p;
}
async function hostCard(extra = {}) {
  const kp = await subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
  const epk = new Uint8Array(await subtle.exportKey('raw', kp.publicKey));
  const m = { t: 'elev', id: hex(16), kind: 'secret', n: hex(16), epk: b64u(epk), ttl: 120, tries: 3, name: 'OPENAI_API_KEY',
    purpose: '给剪辑工作流调用模型', dest: '~/.claude/settings.json', verify: '', fa: CRED, fa_s: 'local', ...extra };
  return { m, priv: kp.privateKey, epk };
}
async function openSeal(card, a) {   // what serve does with the sealed value (elevate.mjs hostOpen, the seal half)
  for (const dev of fake.devs) { try { return await openFor(card, a, dev); } catch { /* another case's page still connected */ } }
  throw new Error('the sealed value opens for no paired device');
}
async function openFor(card, a, dev) {
  const digest = await elevateDigest(card.m.kind, elevateFields(card.m));
  const peEpk = unb64u(a.epk), blob = unb64u(a.ct);
  const pk = await subtle.importKey('raw', peEpk, { name: 'X25519' }, false, []);
  const shared = new Uint8Array(await subtle.deriveBits({ name: 'X25519', public: pk }, card.priv, 256));
  const ikm = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const k = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array([...card.epk, ...peEpk]), info: new TextEncoder().encode(SEAL_CONTEXT) },
    ikm, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const aad = new TextEncoder().encode([SEAL_CONTEXT, fake.channel, dev.id, card.m.id, card.m.kind, card.m.n, digest].join('\n'));
  return new TextDecoder().decode(await subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12), additionalData: aad }, k, blob.slice(12)));
}
async function dropDuringSheet(p, { offlineAtRelease = false } = {}) {
  await waitFor(p, 'window.__get.n === 1 && !!window.__get.release');
  if (offlineAtRelease) fake.refuseNextResumes(2);   // stays offline a while after the drop
  fake.kick(1006);
  if (offlineAtRelease) {
    await waitFor(p, `window.__ajState !== 'ready'`);
    await evaluate(p, 'window.__get.release()');
    return;
  }
  await waitFor(p, `window.__ajState !== 'ready'`);
  await waitState(p, 'ready', 20000);
}

try {
  await C('#364 key card: the drop behind the sheet + the host replaying the card → the sealed answer arrives on the new connection', async () => {
    const p = await paired();
    const card = await hostCard();
    await fake.send(card.m);
    await waitFor(p, visible('#elev'));
    await evaluate(p, `document.getElementById('elev-input').focus()`);
    await p.send('Input.insertText', { text: KEY });
    await evaluate(p, 'window.__get.hold = true');
    await evaluate(p, `document.getElementById('elev-allow').click()`);
    await dropDuringSheet(p);
    await fake.send(card.m);                         // serve's on_ready replays every open card (same nonce)
    await sleep(200);
    assert.equal(await evaluate(p, `document.getElementById('elev-input').value.length`), KEY.length, 'the typed key is still in the field');
    await evaluate(p, 'window.__get.release()');
    await waitFor(p, '1', 100);
    for (let i = 0; i < 100 && !got('elev_answer', card.m.id).length; i++) await sleep(50);
    const [a] = got('elev_answer', card.m.id);
    assert.ok(a, 'an answer reached the host');
    assert.equal(a.ok, true); assert.equal(a.n, card.m.n); assert.ok(a.fa && a.fa.id === CRED, 'with the assertion');
    assert.equal(await openSeal(card, a), KEY, 'the sealed value opens with the card key');
    await p.close();
  });

  await C('#364 key card: get() done while offline → 「正在补交…」 on the card, then sent after the reconnect', async () => {
    const p = await paired();
    const card = await hostCard();
    await fake.send(card.m);
    await waitFor(p, visible('#elev'));
    await evaluate(p, `document.getElementById('elev-input').focus()`);
    await p.send('Input.insertText', { text: KEY });
    await evaluate(p, 'window.__get.hold = true');
    await evaluate(p, `document.getElementById('elev-allow').click()`);
    await dropDuringSheet(p, { offlineAtRelease: true });
    const handing = await tr(p, 'p566.handing');
    await waitFor(p, `${txt('#elev-err')} === ${JSON.stringify(handing)}`, 8000);
    assert.equal(got('elev_answer', card.m.id).length, 0);
    await waitState(p, 'ready', 30000);
    await fake.send(card.m);
    for (let i = 0; i < 200 && !got('elev_answer', card.m.id).length; i++) await sleep(50);
    const [a] = got('elev_answer', card.m.id);
    assert.ok(a && a.fa, 'handed in after the reconnect');
    assert.equal(a.n, card.m.n);
    assert.equal(await openSeal(card, a), KEY);
    await p.close();
  });

  await C('#364 pickup card: 「用 Touch ID 查看」 survives the drop → secret_out_open on the new connection → the value shows', async () => {
    const p = await paired();
    const id = hex(16), n = hex(16);
    const m = { t: 'secret_out', id, n, name: 'GITHUB_TOKEN', purpose: '推送', kind: 'text', size: 12, ttl: 300, fa: CRED, fa_s: 'local' };
    await fake.send(m);
    await waitFor(p, visible('#sout-open'));
    await sleep(300);                                // the challenge is made when the card arrives
    await evaluate(p, 'window.__get.hold = true');
    await evaluate(p, `document.getElementById('sout-open').click()`);
    await dropDuringSheet(p);
    await fake.send(m);                              // the host re-sends the open card after ready (same nonce)
    await evaluate(p, 'window.__get.release()');
    for (let i = 0; i < 100 && !got('secret_out_open', id).length; i++) await sleep(50);
    const [o] = got('secret_out_open', id);
    assert.ok(o && o.n === n && o.fa && o.fa.id === CRED, 'the open request with the assertion');
    await fake.send({ t: 'secret_out_val', id, kind: 'text', value: 'ghp_example12', filename: '' });
    await waitFor(p, visible('#sout-value'), 5000);
    await p.close();
  });

  await C('#364 approval card: a Face ID approval survives the drop → the signed answer on the new connection', async () => {
    const p = await paired();
    const ask = { id: hex(16), tool: 'Bash', summary: 'rm -rf build/', passkey_required: true, fa: CRED, fa_s: 'local', fa_ch: b64u(new Uint8Array(32).fill(1)) };
    await evaluate(p, 'window.__get.hold = true');
    await evaluate(p, `window.__ap = import('./js/api.js').then((a) => a.approve(${JSON.stringify(ask)}, 'allow')); true`);
    await dropDuringSheet(p);
    await evaluate(p, 'window.__get.release()');
    assert.deepEqual(await evaluate(p, 'window.__ap'), { ok: true });
    const [a] = got('answer', ask.id);
    assert.ok(a && a.id === ask.id && a.ok === true && a.fa && a.fa.id === CRED);
    await p.close();
  });

  await C('#354 a refusal says why on the card and above it (stale clock)', async () => {
    const p = await paired();
    fake.st.onApp = async (c, m, reply) => { if (m.t === 'elev_answer') { await reply({ t: 'elev_refused', id: m.id, why: 'stale' }); return true; } return false; };
    const card = await hostCard();
    await fake.send(card.m);
    await waitFor(p, visible('#elev'));
    await evaluate(p, `document.getElementById('elev-input').focus()`);
    await p.send('Input.insertText', { text: KEY });
    await evaluate(p, `document.getElementById('elev-allow').click()`);
    const why = await tr(p, 'p566.r.clock');
    await waitFor(p, `${txt('#elev-err')} === ${JSON.stringify(why)}`, 5000);
    assert.ok(await evaluate(p, visible('#elev-err')));
    const z = await evaluate(p, `(() => { const t = document.getElementById('toast'), e = document.getElementById('elev');
      return { over: t.classList.contains('over-dlg') && t.parentElement === document.body, t: +getComputedStyle(t).zIndex, e: +getComputedStyle(e).zIndex, text: t.textContent }; })()`);
    assert.ok(z.over && z.t > z.e, JSON.stringify(z));
    assert.ok(z.text.includes(why));
    assert.equal(await evaluate(p, visible('#elev')), true, 'the card stays for another try');
    fake.st.onApp = null;
    await p.close();
  });

  await C('#357 a card at 0 goes by itself and says so; nothing is sent', async () => {
    const p = await paired();
    const card = await hostCard({ ttl: 2 });
    await fake.send(card.m);
    await waitFor(p, visible('#elev'));
    await sleep(2800);
    assert.equal(await evaluate(p, visible('#elev')), false, 'gone');
    assert.ok((await evaluate(p, txt('#toast'))).includes(await tr(p, 'p566.expired')));
    assert.equal(got('elev_answer', card.m.id).length, 0);
    await p.close();
  });

  await C('#346 Settings 「已设好」 → 「测试一下」 → pk_check → 「正常」; 「重新设置」 beside it', async () => {
    const p = await paired();
    fake.st.onApp = async (c, m, reply) => { if (m.t === 'pk_check') { await reply({ t: 'pk_check_res', r: m.r, ok: true, s: 'local' }); return true; } return false; };
    await fake.send({ t: 'pk_state', s: 'local', fa: CRED, fa_ch: b64u(new Uint8Array(32).fill(2)), n: 'Tn' + 'A'.repeat(20) });
    await evaluate(p, `document.getElementById('setBtn').click()`);
    await waitFor(p, visible('#set-pk-test'));
    assert.ok(await evaluate(p, visible('#set-pk-reset')));
    await evaluate(p, `document.getElementById('set-pk-test').click()`);
    for (let i = 0; i < 100 && !got('pk_check').length; i++) await sleep(50);
    assert.equal(got('pk_check')[0].fa.id, CRED);
    const ok = await bio(p, 'p566.pk.testOk');
    await waitFor(p, `${txt('#toast')}.includes(${JSON.stringify(ok)})`, 5000);
    fake.st.onApp = null;
    await p.close();
  });
} finally {
  await B.close(); await fake.stop(); await web.stop?.();
}
console.log(failed ? `P566 browser: ${failed} FAILED` : 'P566 browser: all PASS');
process.exit(failed ? 1 : 0);
