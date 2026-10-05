#!/usr/bin/env node
// F17 (PROTOCOL §11) · the sudo card and the secret card against the fake host, in an independent headless Chromium (never :9222).
//   node web/test/elevate.mjs     → cases in parity format + reports/qa/release-0.15/f17-{sudo,secret}-{zh,en}-phone.png
// Cases: a sudo card shows the exact command / why / effect / time left, the password field is masked; approve → the
// answer is signed by this device over the shown digest + nonce + ciphertext and the sealed value opens ONLY with the card's
// host key (checked here in Node, like the host does); the password is nowhere in what the host received, the DOM, localStorage
// or IndexedDB afterwards; a re-sent card (wrong password) shows the retry line; decline sends a signed deny with no value;
// elev_done closes the card; a secret card (zh + en) shows name / purpose / destination / check and saves the same way.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot } from './browser.mjs';
import { elevateFields, elevateDigest, elevateMessage, b64u, unb64u, SEAL_CONTEXT } from '../../protocol/wire.js';

const OUT = fileURLToPath(new URL('../../../reports/qa/release-0.15/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const rec = parityRecorder('web/test/elevate.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const subtle = webcrypto.subtle;
const PW = 'Pw-明文-F17-browser!';
const KEY = 'f17-browser-key-DO-NOT-LEAK-abcdef0123';
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');

async function paired(lang) {
  fake.reset();
  const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J' });
  return p;
}
async function hostCard(kind, extra) {
  const kp = await subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
  const epk = new Uint8Array(await subtle.exportKey('raw', kp.publicKey));
  const m = { t: 'elev', id: hex(16), kind, n: hex(16), epk: b64u(epk), ttl: 120, tries: 3, ...extra };
  return { m, priv: kp.privateKey, epk };
}
/** What serve does with an elev_answer: the signature over what IT showed, then the seal with its one-time key. */
async function hostOpen(card, a) {
  const dev = fake.devs[0];
  const digest = await elevateDigest(card.m.kind, elevateFields(card.m));
  const ctSha = a.ok && a.ct ? Array.from(new Uint8Array(await subtle.digest('SHA-256', unb64u(a.ct))), (b) => b.toString(16).padStart(2, '0')).join('') : null;
  const key = await subtle.importKey('raw', unb64u(dev.sk), { name: 'Ed25519' }, false, ['verify']);
  const msg = elevateMessage(fake.channel, dev.id, card.m.id, card.m.kind, a.ok ? 'allow' : 'deny', card.m.n, a.ts, digest, ctSha);
  assert.ok(await subtle.verify({ name: 'Ed25519' }, key, unb64u(a.sig), msg), 'signature over the shown card');
  if (!a.ok || !a.ct) return null;
  const peEpk = unb64u(a.epk), blob = unb64u(a.ct);
  const pk = await subtle.importKey('raw', peEpk, { name: 'X25519' }, false, []);
  const shared = new Uint8Array(await subtle.deriveBits({ name: 'X25519', public: pk }, card.priv, 256));
  const ikm = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const salt = new Uint8Array([...card.epk, ...peEpk]);
  const k = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode(SEAL_CONTEXT) }, ikm, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const aad = new TextEncoder().encode([SEAL_CONTEXT, fake.channel, dev.id, card.m.id, card.m.kind, card.m.n, digest].join('\n'));
  const pt = await subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12), additionalData: aad }, k, blob.slice(12));
  // a different nonce (a replay against a re-armed card) does not open
  const bad = new TextEncoder().encode([SEAL_CONTEXT, fake.channel, dev.id, card.m.id, card.m.kind, '0'.repeat(32), digest].join('\n'));
  await assert.rejects(subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12), additionalData: bad }, k, blob.slice(12)));
  return new TextDecoder().decode(pt);
}
async function type(p, text) {
  await ev(p, `document.getElementById('elev-input').focus()`);
  await p.send('Input.insertText', { text });
}
const answers = () => fake.log.filter((m) => m.t === 'elev_answer');
async function noTrace(p, secret) {
  const blob = JSON.stringify(fake.log);
  assert.ok(!blob.includes(secret) && !blob.includes(b64u(new TextEncoder().encode(secret))), 'never in what the host received');
  assert.equal(await ev(p, `document.getElementById('elev-input').value`), '', 'the field is cleared');
  assert.equal(await ev(p, `document.documentElement.outerHTML.includes(${JSON.stringify(secret)})`), false, 'not in the DOM');
  assert.equal(await ev(p, `JSON.stringify(Object.assign({}, localStorage)).includes(${JSON.stringify(secret)})`), false, 'not in localStorage');
  const idb = await ev(p, `new Promise((res) => { const out = []; const rq = indexedDB.open('agentjarvis'); rq.onsuccess = () => { const db = rq.result; const names = [...db.objectStoreNames]; if (!names.length) return res('');
    const tx = db.transaction(names, 'readonly'); for (const n of names) { const c = tx.objectStore(n).openCursor(); c.onsuccess = () => { const cur = c.result; if (cur) { try { out.push(JSON.stringify(cur.value, (k, v) => v instanceof CryptoKey ? '[key]' : v)); } catch { out.push('?'); } cur.continue(); } }; }
    tx.oncomplete = () => res(out.join('\\n')); }; rq.onerror = () => res(''); })`);
  assert.ok(!String(idb).includes(secret), 'not in IndexedDB');
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}

try {
  await C('f17-sudo-approve', async () => {
    const p = await paired('zh');
    try {
      const card = await hostCard('sudo', { cmd: 'apt-get install -y ffmpeg', why: '把录音转成 mp3 要用 ffmpeg', effect: '安装一个系统软件包' });
      await fake.send(card.m);
      await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`);
      assert.equal(await ev(p, `document.getElementById('elev-input').type`), 'password');
      assert.equal(await ev(p, `document.getElementById('elev-title').textContent`), 'Agent 想用管理员身份执行一条命令');
      const rows = await ev(p, `[...document.querySelectorAll('#elev-rows dd')].map((x) => x.textContent)`);
      assert.deepEqual(rows, ['apt-get install -y ffmpeg', '把录音转成 mp3 要用 ffmpeg', '安装一个系统软件包']);
      assert.match(await ev(p, `document.getElementById('elev-left').textContent`), /还剩 1\d\d 秒/);
      assert.equal(await ev(p, `document.getElementById('elev-allow').disabled`), true, 'nothing typed: cannot approve');
      await type(p, PW);
      writeFileSync(OUT + 'f17-sudo-zh-phone.png', await shoot(p, 'unused'));
      await ev(p, `document.getElementById('elev-allow').click()`);
      for (let i = 0; i < 100 && !answers().length; i++) await new Promise((r) => setTimeout(r, 50));
      const a = answers()[0];
      assert.ok(a && a.ok === true && a.id === card.m.id && a.n === card.m.n);
      assert.equal(await hostOpen(card, a), PW, 'opens with the card key only');
      await noTrace(p, PW);
      // wrong password → the host re-arms the card: new nonce + key, the retry line
      const again = await hostCard('sudo', { id: card.m.id, cmd: card.m.cmd, why: card.m.why, effect: card.m.effect, bad: 1, tries: 2 });
      again.m.id = card.m.id;
      await fake.send(again.m);
      await waitFor(p, `!document.getElementById('elev-bad').hidden && document.getElementById('elev-bad').textContent.includes('2')`);
      assert.equal(await ev(p, `document.getElementById('elev-input').disabled`), false, 'usable again');
      await type(p, PW + '2');
      await ev(p, `document.getElementById('elev-allow').click()`);
      for (let i = 0; i < 100 && answers().length < 2; i++) await new Promise((r) => setTimeout(r, 50));
      assert.equal(await hostOpen(again, answers()[1]), PW + '2');
      await fake.send({ t: 'elev_done', id: card.m.id, result: 'done', code: 0 });
      await waitFor(p, `document.getElementById('elev').hidden`);
      await noTrace(p, PW + '2');
      assert.equal(p.problems.length, 0, p.problems.join('\n'));
    } finally { await p.dispose(); }
  });

  await C('f17-sudo-decline', async () => {
    const p = await paired('en');
    try {
      const card = await hostCard('sudo', { cmd: 'systemctl restart nginx', why: 'Apply the new config', effect: '' });
      await fake.send(card.m);
      await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`);
      assert.equal(await ev(p, `document.getElementById('elev-title').textContent`), 'Your Agent wants to run one command as admin');
      assert.equal(await ev(p, `document.querySelectorAll('#elev-rows dd').length`), 2, 'no empty effect row');
      writeFileSync(OUT + 'f17-sudo-en-phone.png', await shoot(p, 'unused'));
      await ev(p, `document.getElementById('elev-deny').click()`);
      for (let i = 0; i < 100 && !answers().length; i++) await new Promise((r) => setTimeout(r, 50));
      const a = answers()[0];
      assert.ok(a && a.ok === false && !('ct' in a) && !('epk' in a));
      assert.equal(await hostOpen(card, a), null);
      await fake.send({ t: 'elev_done', id: card.m.id, result: 'denied' });
      await waitFor(p, `document.getElementById('elev').hidden`);
    } finally { await p.dispose(); }
  });

  await C('f17-sudo-helper-approve-only', async () => {
    const p = await paired('zh');
    try {
      const me = fake.devs[0].id;
      const card = await hostCard('sudo', { cmd: 'systemctl restart nginx', why: '让新配置生效', effect: '', helper: [me] });
      await fake.send(card.m);
      await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`);
      assert.equal(await ev(p, `document.getElementById('elev-input').hidden`), true, 'no password field');
      assert.equal(await ev(p, `document.getElementById('elev-allow').disabled`), false);
      assert.equal(await ev(p, `document.getElementById('elev-allow').textContent`), '同意并执行（无需密码）');
      writeFileSync(OUT + 'f17-sudo-helper-zh-phone.png', await shoot(p, 'unused'));
      await ev(p, `document.getElementById('elev-allow').click()`);
      for (let i = 0; i < 100 && !answers().length; i++) await new Promise((r) => setTimeout(r, 50));
      const a = answers()[0];
      assert.ok(a && a.ok === true && !('ct' in a) && !('epk' in a), 'approve-only: signed, nothing sealed');
      assert.equal(await hostOpen(card, a), null);
      // a phone the helper does not list still types the password
      const c2 = await hostCard('sudo', { cmd: 'true', why: 'w', effect: '', helper: ['someone-else-0000'] });
      await fake.send(c2.m);
      await fake.send({ t: 'elev_done', id: card.m.id, result: 'done', code: 0 });
      await waitFor(p, `document.getElementById('elev-input').hidden === false`);
      await fake.send({ t: 'elev_done', id: c2.m.id, result: 'denied' });
      await waitFor(p, `document.getElementById('elev').hidden`);
    } finally { await p.dispose(); }
  });

  // P59 / ADR-A163: a card that names this device's passkey (`fa`) on a page with no WebAuthn → refused on the phone with
  // one clear line, nothing sent; 拒绝 still works. And the host's `elev_refused` (Face ID not accepted) frees the card.
  await C('f17-faceid-unavailable', async () => {
    const p = await paired('zh');
    try {
      await ev(p, `delete window.PublicKeyCredential; true`);
      const card = await hostCard('sudo', { cmd: 'true', why: 'w', effect: '', fa: 'AAAAAAAAAAAAAAAAAAAAAA' });
      await fake.send(card.m);
      await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`);
      await type(p, PW);
      await ev(p, `document.getElementById('elev-allow').click()`);
      await waitFor(p, `document.getElementById('toast').textContent.includes('用不了 Face ID')`);
      await new Promise((r) => setTimeout(r, 300));
      assert.equal(answers().length, 0, 'nothing sent');
      assert.equal(await ev(p, `document.getElementById('elev').hidden`), false, 'the card stays');
      await ev(p, `document.getElementById('elev-deny').click()`);
      for (let i = 0; i < 100 && !answers().length; i++) await new Promise((r) => setTimeout(r, 50));
      const a = answers()[0];
      assert.ok(a && a.ok === false && !('fa' in a), 'decline needs no Face ID');
      await fake.send({ t: 'elev_done', id: card.m.id, result: 'denied' });
      await waitFor(p, `document.getElementById('elev').hidden`);
      await noTrace(p, PW);
    } finally { await p.dispose(); }
  });

  await C('f17-faceid-refused-by-host', async () => {
    const p = await paired('en');
    try {
      const card = await hostCard('sudo', { cmd: 'true', why: 'w', effect: '' });
      await fake.send(card.m);
      await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`);
      await type(p, PW);
      await ev(p, `document.getElementById('elev-allow').click()`);
      for (let i = 0; i < 100 && !answers().length; i++) await new Promise((r) => setTimeout(r, 50));
      assert.equal(await ev(p, `document.getElementById('elev-input').disabled`), true, 'sending');
      await fake.send({ t: 'elev_refused', id: card.m.id, why: 'passkey' });
      await waitFor(p, `document.getElementById('toast').textContent.includes("didn't accept this Face ID")`);
      assert.equal(await ev(p, `document.getElementById('elev-input').disabled`), false, 'usable again');
      assert.equal(await ev(p, `document.getElementById('elev').hidden`), false, 'the card stays');
      await fake.send({ t: 'elev_done', id: card.m.id, result: 'denied' });
      await waitFor(p, `document.getElementById('elev').hidden`);
    } finally { await p.dispose(); }
  });

  for (const lang of ['zh', 'en']) {
    await C(`f17-secret-${lang}`, async () => {
      const p = await paired(lang);
      try {
        const card = await hostCard('secret', { name: 'ELEVENLABS_API_KEY', purpose: lang === 'zh' ? '给视频配音' : 'Voice-over for the video',
          dest: '/home/me/proj/.env (ELEVENLABS_API_KEY=…)', verify: 'GET https://api.elevenlabs.io/v1/user  (xi-api-key: {value})' });
        await fake.send(card.m);
        await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`);
        assert.equal(await ev(p, `document.getElementById('elev-title').textContent`),
          lang === 'zh' ? '请把 ELEVENLABS_API_KEY 贴到下面' : 'Paste ELEVENLABS_API_KEY below');
        assert.equal(await ev(p, `document.querySelectorAll('#elev-rows dd').length`), 4);
        await type(p, '  ' + KEY + '  ');
        writeFileSync(OUT + `f17-secret-${lang}-phone.png`, await shoot(p, 'unused'));
        await ev(p, `document.getElementById('elev-allow').click()`);
        for (let i = 0; i < 100 && !answers().length; i++) await new Promise((r) => setTimeout(r, 50));
        assert.equal(await hostOpen(card, answers()[0]), KEY, 'pasted spaces trimmed');
        await fake.send({ t: 'elev_done', id: card.m.id, result: 'saved', verify: 'ok' });
        await waitFor(p, `document.getElementById('elev').hidden`);
        await noTrace(p, KEY);
        assert.equal(p.problems.length, 0, p.problems.join('\n'));
      } finally { await p.dispose(); }
    });
  }
} finally {
  rec.write();
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `✗ ${failed} failed` : '✓ all F17 card cases passed');
process.exit(failed ? 1 : 0);
