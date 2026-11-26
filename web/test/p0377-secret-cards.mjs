// Local paired phone fixture. It checks the existing signature and seal for a multi-key entry and the old single-key entry.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes, webcrypto } from 'node:crypto';
import { join, resolve } from 'node:path';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep } from './browser.mjs';
import { approvalEnvelopeDigest, elevateFields, elevateDigest, elevateMessage, b64u, unb64u, SEAL_CONTEXT } from '../../protocol/wire.js';

const out = process.env.AJ_SECRET_UI_OUT;
const beforeDir = process.env.AJ_SECRET_UI_BEFORE;
if (out) mkdirSync(out, { recursive: true });
const before = beforeDir ? await startWebServer({ publicDir: resolve(beforeDir) }) : null;
const web = await startWebServer();
const host = await startFakeHost();
const browser = await launch();
const subtle = webcrypto.subtle;
const names = ['SERVICE_CLIENT_ID', 'SERVICE_CLIENT_SECRET'];
const fields = "[...document.querySelectorAll('#elev .elev-input')]";
const visible = "document.querySelector('#elev')?.hidden===false";
const noValues = fields + ".every(i=>!i.value)";
const enc = new TextEncoder();
const sample = () => 'local-fixture-' + randomBytes(12).toString('hex');
const hex = () => randomBytes(16).toString('hex');

async function card(lang, { multiple = false, fieldNames = names, ttl = 600, fa, ownerMessage, ...overrides } = {}) {
  const zh = lang === 'zh';
  const prompt = zh ? '帮我配置日历服务。' : 'Set up the calendar service for me.';
  const message = ownerMessage ?? (await host.addTurn({ k: 'phone', name: zh ? '你' : 'You', text: prompt },
    zh ? '请填写下面的密钥卡。' : 'Open the key card below.')).id;
  const pair = await subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
  const epk = new Uint8Array(await subtle.exportKey('raw', pair.publicKey));
  const id = hex();
  const m = { t: 'elev', id, kind: 'secret', n: hex(), epk: b64u(epk), ttl, tries: 3,
    name: multiple ? fieldNames.join(', ') : fieldNames[0], ...(multiple ? { names: fieldNames } : {}),
    purpose: zh ? '保存日历服务密钥' : 'Save calendar service keys', dest: 'env:/fixture/calendar.env', verify: '',
    button_only: !fa, passkey_required: !!fa, ...(fa ? { fa, fa_s: 'local' } : {}),
    approval_v: 2, revision: 1, review_state: 'pending', ...overrides };
  m.envelope = { v: 2, channel: host.channel, session: String(host.st.epoch), request_id: id, revision: 1,
    special: { tool: 'SecretInput' }, title: m.purpose, action: m.purpose, details: m.dest,
    expires_at: Math.floor(Date.now() / 1000) + ttl, source: { message_id: message, root_message_id: message,
      session: String(host.st.epoch), channel: 'phone', sender: zh ? '你' : 'You', text: prompt, verified: true, suspicious: false } };
  m.envelope_sha256 = await approvalEnvelopeDigest(m.envelope);
  return { m, priv: pair.privateKey, epk };
}
async function open(page, m) {
  await host.send(m);
  await waitFor(page, "document.getElementById('elev-run-main')?.offsetWidth>0");
  await evaluate(page, "document.getElementById('elev-run-main').click()");
  await waitFor(page, visible + " && document.querySelector('#elev .auth-warn541')?.hidden");
}
async function withPage(server, width, theme, lang, run) {
  host.reset();
  host.st.prefs = { appearance: { language: lang, theme }, voice: { speak_replies: false, wake_enabled: false } };
  const url = server.url.replace('127.0.0.1', 'localhost');
  const page = await newPage(browser, width, width === 390 ? 844 : 900, theme,
    { touch: width === 390, allow: [url, host.relay + '/'] });
  try {
    await navigate(page, host.newPairing(url + '?lang=' + lang));
    await waitState(page, 'awaiting-approval'); await host.approve(); await waitState(page, 'ready');
    await host.send({ t: 'status', s: 'idle', name: 'Agent J' });
    await evaluate(page, "for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}");
    await run(page);
  } finally { await page.dispose(); }
}
async function answerFor(id) {
  const answer = () => host.log.find(m => m.t === 'elev_answer' && m.id === id);
  for (let i = 0; i < 100 && !answer(); i++) await sleep(50);
  assert.ok(answer()?.ct && answer()?.sig, 'paired browser sent an encrypted, signed answer');
  return answer();
}
async function checkAnswer(card, answer, expected) {
  const dev = host.devs.at(-1);
  const digest = await elevateDigest(card.m.kind, elevateFields(card.m));
  const blob = unb64u(answer.ct);
  const hash = Buffer.from(await subtle.digest('SHA-256', blob)).toString('hex');
  const approveKey = await subtle.importKey('raw', unb64u(dev.sk), { name: 'Ed25519' }, false, ['verify']);
  const msg = elevateMessage(host.channel, dev.id, card.m.id, card.m.kind, 'allow', card.m.n, answer.ts, digest, hash);
  assert.ok(await subtle.verify({ name: 'Ed25519' }, approveKey, unb64u(answer.sig), msg), 'signature covers the requested input');
  const epk = unb64u(answer.epk);
  const pk = await subtle.importKey('raw', epk, { name: 'X25519' }, false, []);
  const shared = await subtle.deriveBits({ name: 'X25519', public: pk }, card.priv, 256);
  const ikm = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const salt = new Uint8Array([...card.epk, ...epk]);
  const aes = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt, info: enc.encode(SEAL_CONTEXT) }, ikm, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const aad = (d) => enc.encode([SEAL_CONTEXT, host.channel, dev.id, card.m.id, card.m.kind, card.m.n, d].join('\n'));
  const iv = blob.slice(0, 12);
  const pt = new Uint8Array(await subtle.decrypt({ name: 'AES-GCM', iv, additionalData: aad(digest) }, aes, blob.slice(12)));
  try { assert.ok(new TextDecoder().decode(pt) === expected, 'only the intended fields were sealed in the expected format'); }
  finally { pt.fill(0); }
  await assert.rejects(subtle.decrypt({ name: 'AES-GCM', iv, additionalData: aad('0'.repeat(64)) }, aes, blob.slice(12)));
  assert.ok(!JSON.stringify(host.log).includes(expected), 'the host Noise frame contains ciphertext only');
}
async function type(page, index, value) {
  await evaluate(page, fields + '[' + index + '].focus()');
  await page.send('Input.insertText', { text: value });
}
const submit = (page) => evaluate(page, "document.querySelector('#elev-allow').click()");

async function virtualPasskey(page) {
  await page.send('WebAuthn.enable');
  await page.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  return evaluate(page, `navigator.credentials.create({ publicKey: {
    challenge: crypto.getRandomValues(new Uint8Array(32)), rp: { name: 'Local fixture' },
    user: { id: new Uint8Array([1]), name: 'fixture', displayName: 'Fixture' },
    pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
    authenticatorSelection: { authenticatorAttachment: 'platform', residentKey: 'required', userVerification: 'required' }
  } }).then(c => c.id)`);
}
async function capture(page, name) {
  if (!out) return;
  await evaluate(page, "document.fonts.ready.then(()=>{document.activeElement?.blur();document.getElementById('elev-form').scrollTop=0})");
  assert.ok(await evaluate(page, noValues), 'only blank fields can be captured');
  writeFileSync(join(out, name + '.png'), await shoot(page));
}

async function checkFieldLabels(page, lang, fieldNames, expectWrap = false) {
  const labels = await evaluate(page, fields + `.map(input => {
    const label = input.labels[0];
    const box = label.getBoundingClientRect();
    const form = document.querySelector('#elev-form');
    const bounds = form.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(label);
    const rects = [...range.getClientRects()];
    return {
      text: label.textContent,
      lines: new Set(rects.map(rect => rect.top)).size,
      fits: rects.every(rect => rect.left >= Math.max(box.left, bounds.left) - 1 &&
        rect.right <= Math.min(box.right, bounds.right) + 1 &&
        rect.top >= box.top - 1 && rect.bottom <= box.bottom + 1),
      wraps: getComputedStyle(label).whiteSpace === 'normal',
      ellipsis: getComputedStyle(label).textOverflow === 'ellipsis',
    };
  })`);
  assert.deepEqual(labels.map(label => label.text),
    fieldNames.map(name => lang === 'zh' ? '把 ' + name + ' 粘贴到这里' : 'Paste ' + name + ' here'));
  assert.ok(labels.every(label => label.lines > 0 && label.fits && label.wraps && !label.ellipsis),
    'all field label text stays within the card and label bounds');
  if (expectWrap) assert.ok(labels[1].lines >= 2, 'the long second field label wraps completely');
}

async function checkActions(page) {
  const buttons = await evaluate(page, `[...document.querySelectorAll('#elev .special-buttons541 > button')].map(button => {
    const style = getComputedStyle(button);
    const rect = button.getBoundingClientRect();
    return { id: button.id, fill: style.backgroundColor, color: style.color,
      borders: [style.borderTopWidth, style.borderRightWidth, style.borderBottomWidth, style.borderLeftWidth],
      top: rect.top, width: rect.width, height: rect.height };
  })`);
  assert.deepEqual(buttons.map(button => button.id), ['elev-allow', '', 'elev-deny']);
  for (const button of buttons) {
    assert.equal(button.fill, 'rgb(250, 249, 245)');
    assert.equal(button.color, 'rgb(44, 43, 38)');
    assert.deepEqual(button.borders, ['0px', '0px', '0px', '0px']);
    assert.ok(button.width >= 44 && button.height >= 44);
  }
  const [save, question, deny] = buttons;
  assert.ok(save.width > question.width && save.height > question.height && save.top < question.top);
  assert.equal(question.top, deny.top);
}

try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const lang of ['zh', 'en']) {
    const special = width === 390 && theme === 'light' && lang === 'zh';
    if (before) await withPage(before, width, theme, lang, async page => {
      const fa = special ? await virtualPasskey(page) : null;
      const batch = await card(lang, { multiple: true, fa });
      await open(page, batch.m);
      assert.equal(await evaluate(page, fields + '.length'), 2, 'the before fixture is the previous multi-field card');
      await capture(page, `secret-before-${width}-${lang}-${theme}`);
    });
    await withPage(web, width, theme, lang, async page => {
      const fa = special ? await virtualPasskey(page) : null;
      const batch = await card(lang, { multiple: true, fa });
      await open(page, batch.m);
      await evaluate(page, 'document.fonts.ready.then(()=>true)');
      assert.ok(await evaluate(page, fields + ".every(i=>i.type==='password'&&i.labels.length===1)"));
      assert.equal(await evaluate(page, "getComputedStyle(document.querySelector('#elev-form')).backgroundColor"), 'rgb(44, 43, 38)', 'Noir card');
      await checkFieldLabels(page, lang, names);
      await checkActions(page);
      await capture(page, `secret-after-${width}-${lang}-${theme}`);
      if (!special) return;

      await waitFor(page, `import('./js/approval-special541.js').then(a=>!!a.specialApprovalById(${JSON.stringify(batch.m.id)})?.faCh)`);
      const legacy = await card(lang, { ownerMessage: batch.m.envelope.source.message_id });
      await host.send(legacy.m);
      await waitFor(page, "document.getElementById('elev-run-main')?.offsetWidth>0");
      assert.equal(await evaluate(page, fields + '.length'), 2, 'the earlier card remains open alongside a separate pending card');
      const a = sample(), b = sample();
      await type(page, 0, a);
      await submit(page);
      assert.ok(await evaluate(page, "document.getElementById('elev-empty').textContent.includes('SERVICE_CLIENT_SECRET') && !document.getElementById('elev-empty').hidden"));
      assert.equal(host.log.filter(m => m.t === 'elev_answer').length, 0);
      console.log('p0377-web-incomplete-batch PASS');
      await type(page, 1, b);
      await submit(page);
      const response = await answerFor(batch.m.id);
      assert.equal(response.values_v, 2);
      assert.ok((unb64u(response.fa.ad)[32] & 4) !== 0, 'WebAuthn reports user verification');
      await checkAnswer(batch, response, JSON.stringify([a, b]));
      await waitFor(page, noValues);
      assert.ok(!JSON.stringify(host.log).includes(a) && !JSON.stringify(host.log).includes(b));
      console.log('p0377-web-batch-uv-signed-sealed PASS');

      await host.send({ t: 'elev_done', id: batch.m.id, result: 'saved' });
      await waitFor(page, "document.querySelector('#elev')?.hidden && document.getElementById('elev-run-main')?.offsetWidth>0");
      await evaluate(page, "document.getElementById('elev-run-main').click()");
      await waitFor(page, visible + ' && ' + fields + '.length===1 && ' + noValues);
      const old = sample();
      await type(page, 0, old);
      await submit(page);
      const oldResponse = await answerFor(legacy.m.id);
      assert.equal(oldResponse.values_v, undefined);
      await checkAnswer(legacy, oldResponse, old);
      console.log('p0377-web-concurrent-and-legacy PASS');
      await host.send({ t: 'elev_done', id: legacy.m.id, result: 'saved' });
      await waitFor(page, "document.querySelector('#elev')?.hidden");

      const overlong = await card(lang, { multiple: true });
      await open(page, overlong.m);
      await evaluate(page, fields + ".forEach(i=>{i.value='樣'.repeat(1400)})");
      await submit(page);
      await waitFor(page, "!document.querySelector('#elev-err').hidden && !document.querySelector('#elev-allow').disabled");
      assert.ok(!host.log.some(m => m.t === 'elev_answer' && m.id === overlong.m.id));
      await evaluate(page, "import('./js/elevate.js').then(e=>e.forgetInput())");
      assert.ok(await evaluate(page, noValues));
      await host.send({ t: 'elev_done', id: overlong.m.id, result: 'gone' });
      await waitFor(page, "document.querySelector('#elev')?.hidden");
      console.log('p0377-web-aggregate-size-limit PASS');

      const malformed = await card(lang, { multiple: true, names: [...names].reverse() });
      await host.send(malformed.m);
      await sleep(150);
      assert.ok(await evaluate(page, "document.querySelector('#elev')?.hidden && document.querySelector('#elev-run').hidden"));
      console.log('p0377-web-tampered-fields PASS');

      const expiring = await card(lang, { multiple: true, ttl: 2 });
      await open(page, expiring.m);
      await type(page, 0, sample());
      await waitFor(page, "document.querySelector('#elev')?.hidden && " + noValues, 5000);
      assert.ok(!host.log.some(m => m.t === 'elev_answer' && m.id === expiring.m.id));
      console.log('p0377-web-expiry-clears-fields PASS');
    });
  }
  for (const lang of ['zh', 'en']) await withPage(web, 390, 'light', lang, async page => {
    const fieldNames = [names[0], names[1] + '_SCOPE'.repeat(12)];
    const batch = await card(lang, { multiple: true, fieldNames });
    await open(page, batch.m);
    await evaluate(page, 'document.fonts.ready.then(()=>true)');
    await checkFieldLabels(page, lang, fieldNames, true);
  });
  console.log('p0377-web-round2-card-render PASS');
} finally {
  await browser.close(); await host.stop(); await web.stop(); await before?.stop();
}
