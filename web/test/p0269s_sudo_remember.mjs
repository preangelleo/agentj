#!/usr/bin/env node
// 0.26.9 #482 (chairman 11-04: 「那个对勾是个骗人的…第一次只要有一次输对了就存下来，以后只要能够刷 face ID，就不需要再手动输入了」).
// The sudo card's 「用 Face ID 记住（下次扫脸即可）」 on the REAL page against fakehost.mjs, own headless Chromium (never :9222),
// a CDP virtual platform authenticator as Face ID's stand-in. Cards are shaped like the real host's since P573 (`helper_uv` 0,
// no `passkey_required`) — the shape under which the box silently did nothing (P573 skipped the assertion, so no PRF, no `pct`).
//   1. first card (v.has 0): type the password, box ticked → ONE Face ID → answer carries `fa` + `pct` (PRF over `vs`, sealed
//      kind vault_save over digest|sha256(ct), signed save: tail) → elev_done saved:1 → 「已记住」
//   2. next card (v.has 1, the same salt): no password field, no 同意 — only 「用 Face ID 填入…」 → vault:1, the PRF over the
//      same salt is the very value that was saved (so the host's wrap opens), no typed value
//   3. that Face ID gives no PRF on the vault card → nothing sent, the field comes back with the reason
//   4. an authenticator without PRF, box ticked → the command still runs, and the end notice says it was NOT remembered
//   5. the host ends `done` without `saved` (it could not keep it) → the notice says so (never silent)
//   P0269S_OUT=<dir> node web/test/p0269s_sudo_remember.mjs      (default reports/qa/p0269s-sudo/web)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateKeyPairSync, randomBytes, webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect } from './browser.mjs';
import { elevateFields, elevateDigest, elevateMessage, b64u, unb64u, SEAL_CONTEXT } from '../../protocol/wire.js';

const subtle = webcrypto.subtle, enc = new TextEncoder();
const OUT = process.env.P0269S_OUT || resolve(new URL('../../../', import.meta.url).pathname, 'reports/qa/p0269s-sudo/web');
const hex = (n) => randomBytes(n).toString('hex');
const sha = async (s) => Buffer.from(await subtle.digest('SHA-256', typeof s === 'string' ? enc.encode(s) : s)).toString('hex');
const $ = (id) => `document.getElementById('${id}')`;
const PASSWORD = 'synthetic pw 482';
const L = {
  zh: { saved: '已记住', use: '填入 Agent J 主机登录密码', noPrfUse: '没能解开记住的密码', noPrfSave: '这台设备不能记住密码', notSaved: '这次没有记住密码' },
  en: { saved: 'Remembered.', use: 'Fill in the password', noPrfUse: 'could not unlock the remembered password', noPrfSave: 'This device cannot remember the password', notSaved: 'was not remembered this time' },
};

async function unseal(priv, hostEpk, peEpkB64, ctB64, aadParts) {
  const peEpk = unb64u(peEpkB64), blob = unb64u(ctB64);
  const pk = await subtle.importKey('raw', peEpk, { name: 'X25519' }, false, []);
  const shared = new Uint8Array(await subtle.deriveBits({ name: 'X25519', public: pk }, priv, 256));
  const ikm = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const k = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array([...hostEpk, ...peEpk]), info: enc.encode(SEAL_CONTEXT) }, ikm, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  return new Uint8Array(await subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12), additionalData: enc.encode([SEAL_CONTEXT, ...aadParts].join('\n')) }, k, blob.slice(12)));
}
async function oneTimeKey() {
  const kp = await subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
  return { priv: kp.privateKey, pub: new Uint8Array(await subtle.exportKey('raw', kp.publicKey)) };
}

export async function runP0269sSudo({ B, web, fake, out = OUT, langs = ['zh', 'en'] }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');
  const H = { fa: null, elev: new Map(), answers: [], errors: [], saveMode: 'saved' };
  const dev = () => fake.devs[0];
  const faOk = (fa, ch) => { try { const cd = JSON.parse(Buffer.from(fa.cd, 'base64url').toString()); return cd.type === 'webauthn.get' && cd.challenge === ch && fa.id === H.fa; } catch { return false; } };
  fake.st.onApp = async (c, m, send) => {
    try {
      if (m.t !== 'elev_answer' || !H.elev.has(m.id)) return false;
      const { c: card, k } = H.elev.get(m.id), d = dev();
      const digest = await elevateDigest(card.kind, elevateFields(card));
      const ctSha = m.ok && m.ct ? await sha(unb64u(m.ct)) : null, save = m.pct ? await sha(unb64u(m.pct)) : null;
      const key = await subtle.importKey('raw', unb64u(d.sk), { name: 'Ed25519' }, false, ['verify']);
      const sigOk = await subtle.verify({ name: 'Ed25519' }, key, unb64u(m.sig), elevateMessage(fake.channel, d.id, m.id, card.kind, m.ok ? 'allow' : 'deny', card.n, m.ts, digest, ctSha, m.grant || null, save));
      const faCh = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode(['agentjarvis/passkey/elevate/v1', fake.channel, d.id, m.id, card.kind, 'approve', card.n, digest].join('\n')))));
      const a = { id: m.id, ok: m.ok, sigOk, fa: !!m.fa && faOk(m.fa, faCh), vault: m.vault ?? null, typed: null, prf: null, savePrf: null };
      if (m.ok && m.ct) {
        const v = await unseal(k.priv, k.pub, m.epk, m.ct, [fake.channel, d.id, m.id, m.vault ? 'vault' : 'sudo', card.n, digest]);
        if (m.vault) a.prf = Buffer.from(v).toString('hex'); else a.typed = new TextDecoder().decode(v);
      }
      if (m.pct) a.savePrf = Buffer.from(await unseal(k.priv, k.pub, m.pepk, m.pct, [fake.channel, d.id, m.id, 'vault_save', card.n, digest + '|' + ctSha])).toString('hex');
      H.answers.push(a);
      H.elev.delete(m.id);
      // elevate.py: `saved: 1` only when a pct came AND the host kept it (H.saveMode 'lost' = term_vault.create failed)
      await send({ t: 'elev_done', id: m.id, result: m.ok ? 'done' : 'denied', ...(m.ok ? { code: 0 } : {}), ...(m.ok && m.pct && H.saveMode === 'saved' ? { saved: 1 } : {}) });
      return true;
    } catch (e) { H.errors.push(String(e && e.stack || e)); return false; }
  };
  // what elevate.py card_msg sends for a sudo card since P573: helper_uv 0 (this phone is not a helper), no passkey_required
  const card = async (v) => {
    const k = await oneTimeKey();
    const c = { t: 'elev', id: hex(16), kind: 'sudo', n: hex(16), epk: b64u(k.pub), ttl: 120, tries: 3, cmd: 'apt update', why: 'refresh the package lists',
      effect: '', interpreter: false, reason: '', helper_uv: 0, v, fa: H.fa, fa_s: 'local' };
    H.elev.set(c.id, { c, k });
    await fake.send(c);
    return c;
  };
  const lastAnswer = async (k) => { for (let i = 0; i < 120 && H.answers.length < k; i++) await sleep(50); return H.answers[k - 1]; };

  for (const lang of langs) for (const withPrf of [true, false]) {
    const s = L[lang], tag = `${lang}-${withPrf ? 'prf' : 'noprf'}`;
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang, theme: 'light' } };
    Object.assign(H, { answers: [], errors: [], saveMode: 'saved' }); H.elev.clear();
    const p = await newPage(B, 390, 844, 'light', { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: withPrf } });
    let shot = 0;
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${String(++shot).padStart(2, '0')}-${name}.png`), await shoot(p, ''));
    const click = async (id) => {
      await evaluate(p, `${$(id)}.scrollIntoView({block:'center'})`); await sleep(350);
      await waitFor(p, `(()=>{const b=document.getElementById('${id}'),r=b.getBoundingClientRect(),h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return h&&b.contains(h)})()`, 6000);
      const r = await rect(p, '#' + id);
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await p.send('Input.dispatchMouseEvent', { type, x: r.x, y: r.y, button: 'left', clickCount: 1 });
    };
    const shown = (id) => evaluate(p, `!!${$(id)} && !${$(id)}.hidden && ${$(id)}.getClientRects().length > 0`);
    const notice = (needle) => waitFor(p, `[...document.querySelectorAll('#toast, .notice, [role=status], [role=alert]')].some(e => e.textContent.includes(${JSON.stringify(needle)}))`, 6000);
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      if (withPrf) {
        H.fa = await evaluate(p, `navigator.credentials.create({publicKey:{rp:{id:'localhost',name:'Agent J'},user:{id:new Uint8Array(16).fill(4),name:'p0269s',displayName:'p0269s'},challenge:new Uint8Array(32),pubKeyCredParams:[{type:'public-key',alg:-7}],authenticatorSelection:{residentKey:'required',userVerification:'required',authenticatorAttachment:'platform'},extensions:{prf:{}}}}).then(c=>{const b=new Uint8Array(c.rawId);let s='';for(const x of b)s+=String.fromCharCode(x);return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'')})`);
      } else {
        const credId = randomBytes(16); H.fa = b64u(credId);
        const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
        await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
          rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p0269s-fixture').toString('base64'), signCount: 0 } });
      }
      const salt = randomBytes(32);

      // ---- 1. first time: typed + 「用 Face ID 记住」 (ticked by default)
      await card({ has: 0, vs: b64u(salt) });
      await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && !${$('elev-input')}.hidden`, 6000); await sleep(400);
      check(await shown('elev-save-opt') && await evaluate(p, `${$('elev-save-on')}.checked`), '1 「用 Face ID 记住」 shown and ticked ' + tag);
      await save('first-typed-remember');
      await evaluate(p, `${$('elev-input')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
      await click('elev-allow');
      const a1 = await lastAnswer(1);
      check(a1 && a1.ok && a1.sigOk && a1.typed === PASSWORD, '1 the typed password reached the host, signed ' + tag + JSON.stringify(a1 && { ...a1, typed: !!a1.typed }));
      check(a1.fa, '1 ticking 「用 Face ID 记住」 asks Face ID (the PRF comes with that assertion) ' + tag);
      if (withPrf) {
        check(a1.savePrf && a1.savePrf.length === 64, '1 pct = the PRF over vs, sealed vault_save over digest|sha256(ct), signed save: tail ' + tag);
        await notice(s.saved);
        check(true, '1 elev_done saved:1 → 「已记住」 ' + tag);
      } else {
        check(!a1.savePrf, '1 no PRF from this authenticator → no pct ' + tag);
        await notice(s.noPrfSave);
        // e2e_p59_f17 reads the next toast naming the biometric (「Touch ID 没通过…」): this end notice must not name it
        check(!(await evaluate(p, `[...document.querySelectorAll('#toast, .notice, [role=status], [role=alert]')].some(e => /Face ID|Touch ID|指纹/.test(e.textContent) && e.textContent.includes(${JSON.stringify(s.noPrfSave)}))`)),
          '1 no PRF → the end notice says it was not remembered, without naming the biometric ' + tag);
      }
      await waitFor(p, `${$('elev')}.hidden`, 6000);
      check(!(await evaluate(p, `document.documentElement.outerHTML.includes(${JSON.stringify(PASSWORD)})`)), '1 the password is gone from the page ' + tag);
      if (!withPrf) {
        // ---- 1b. this device showed it gives no PRF: the next typed card offers no box, so no extra Face ID (#473: fewer taps)
        await card({ has: 0, vs: b64u(randomBytes(32)) });
        await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && !${$('elev-input')}.hidden`, 6000); await sleep(400);
        check(!(await shown('elev-save-opt')), '1b no PRF seen → no 「用 Face ID 记住」 on the next card ' + tag);
        await evaluate(p, `${$('elev-input')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
        await click('elev-allow');
        const b = await lastAnswer(2);
        check(b.ok && b.sigOk && b.typed === PASSWORD && !b.fa && !b.savePrf, '1b typed + 同意 = one tap again (P573), no Face ID ' + tag + JSON.stringify({ ...b, typed: !!b.typed }));
        await waitFor(p, `${$('elev')}.hidden`, 6000);
      }

      if (withPrf) {
        // ---- 2. next time: the host holds the wrap (v.has 1, the wrap's salt) → Face ID only, no field
        await card({ has: 1, vs: b64u(salt), vn: b64u(randomBytes(32)) });
        await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && !${$('elev-vault')}.hidden`, 6000); await sleep(400);
        const ui = { field: await shown('elev-input'), label: await shown('elev-label'), allow: await shown('elev-allow'), save: await shown('elev-save-opt'),
          vault: await evaluate(p, `${$('elev-vault')}.textContent`) };
        check(!ui.field && !ui.label && !ui.allow && !ui.save && ui.vault.includes(s.use), '2 next sudo: only 「用 Face ID 填入…」, no password field ' + tag + JSON.stringify(ui));
        await save('next-faceid-only');
        await click('elev-vault');
        const a2 = await lastAnswer(2);
        check(a2.ok && a2.vault === 1 && a2.sigOk && a2.fa && a2.typed === null && a2.prf && a2.prf.length === 128, '2 vault:1, prf1 ‖ prf2 sealed kind vault, no typed value ' + tag);
        check(a2.prf.slice(0, 64) === a1.savePrf, '2 the PRF over the same salt equals the saved one (the host wrap opens) ' + tag);
        await waitFor(p, `${$('elev')}.hidden`, 6000);

        // ---- 3. the vault card, but this Face ID gives no PRF → nothing sent, the field comes back with the reason
        await p.send('WebAuthn.removeVirtualAuthenticator', { authenticatorId });
        const { authenticatorId: a2id } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
          hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: false } });
        const credId = randomBytes(16); H.fa = b64u(credId);
        const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
        await p.send('WebAuthn.addCredential', { authenticatorId: a2id, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
          rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p0269s-fixture2').toString('base64'), signCount: 0 } });
        await card({ has: 1, vs: b64u(salt), vn: b64u(randomBytes(32)) });
        await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && !${$('elev-vault')}.hidden`, 6000); await sleep(400);
        await click('elev-vault');
        await waitFor(p, `!${$('elev-input')}.hidden && !${$('elev-stale')}.hidden && ${$('elev-stale')}.textContent.includes(${JSON.stringify(s.noPrfUse)})`, 6000);
        check(H.answers.length === 2 && await shown('elev-allow') && !(await shown('elev-vault')), '3 no PRF on the vault card → nothing sent, the field + 同意 back, the reason above it ' + tag);
        await save('vault-noprf-field-back');
        await click('elev-deny'); await waitFor(p, `${$('elev')}.hidden`, 6000);
      }
    } finally { await p.dispose(); }
    check(!H.errors.length, 'fake host saw no malformed message ' + tag + ' ' + H.errors.join(' | '));
  }

  // ---- 5. the host ends `done` without `saved` although a pct came (term_vault.create failed) → said, never silent
  {
    const lang = langs[0], s = L[lang], tag = `${lang}-hostlost`;
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang, theme: 'light' } };
    Object.assign(H, { answers: [], errors: [], saveMode: 'lost' }); H.elev.clear();
    const p = await newPage(B, 390, 844, 'light', { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    await p.send('WebAuthn.enable', { enableUI: false });
    await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: true } });
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      H.fa = await evaluate(p, `navigator.credentials.create({publicKey:{rp:{id:'localhost',name:'Agent J'},user:{id:new Uint8Array(16).fill(5),name:'p0269s',displayName:'p0269s'},challenge:new Uint8Array(32),pubKeyCredParams:[{type:'public-key',alg:-7}],authenticatorSelection:{residentKey:'required',userVerification:'required',authenticatorAttachment:'platform'},extensions:{prf:{}}}}).then(c=>{const b=new Uint8Array(c.rawId);let s='';for(const x of b)s+=String.fromCharCode(x);return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'')})`);
      await card({ has: 0, vs: b64u(randomBytes(32)) });
      await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && !${$('elev-input')}.hidden`, 6000); await sleep(400);
      await evaluate(p, `${$('elev-input')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
      const r = await rect(p, '#elev-allow');
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await p.send('Input.dispatchMouseEvent', { type, x: r.x, y: r.y, button: 'left', clickCount: 1 });
      const a = await lastAnswer(1);
      check(a && a.savePrf, '5 the pct went out ' + tag);
      await waitFor(p, `[...document.querySelectorAll('#toast, .notice, [role=status], [role=alert]')].some(e => e.textContent.includes(${JSON.stringify(s.notSaved)}))`, 6000);
      check(true, '5 done without saved → 「这次没有记住密码…」 ' + tag);
    } finally { await p.dispose(); }
  }
  fake.st.onApp = null;
  console.log(`P0269S sudo remember: ${checks} checks PASS; shots → ${out}`);
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP0269sSudo({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
