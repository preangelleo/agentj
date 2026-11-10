#!/usr/bin/env node
// #0304 A1 browser leg (real client + fake host; virtual platform authenticator replaces the physical biometric only).
// Three real remotes paired with ONE host: A and B are bound (Face ID set up → roster descriptor), C is bound but NOT in this
// round's roster snapshot. A v4 same-owner card goes to every remote:
//   · A and B can both press Allow (no designated signer); B signs → one owner-confirm-v3 with B's own Ed + UV;
//   · the host's ask_done folds the card on A and B; C shows 「start again」 and its Allow stays disabled;
//   · a card with one tampered peer roster entry is unsignable on every remote;
//   · the card lists every remote of BOTH ends under 「确认后，这些遥控器都能签同主人授权」 (R-F4, name + 8-char device id);
//   · 0.30.6 R-F1: a confirm refused with peer_too_old → plain line, Allow stays off; a friends row carrying
//     same_owner_why:'peer_binding_required' → the detail page and the card both say so, the card is not confirmable.
// zh/en × light/dark × 390/1440 → reports/qa/0304-frozen/web-a1/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
const OUT = process.env.P0304_OUT || fileURLToPath(new URL('../../../reports/qa/0304-frozen/web-a1/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer(); web.url = web.url.replace('127.0.0.1', 'localhost');
const fake = await startFakeHost(), B = await launch();
const { ownerConfirmChallenge, verifyBindingAssertion, bindRegistration, SCOPE_V4 } = await import('../public/js/ownergrant.js');
const checks = [], ceremonies = [], registered = new Map();
let confirmWhy = null;   // R-F1: the host's refusal code for the next owner-confirm-v3 (null = accept)
const check = (v, text) => { assert.ok(v, text); checks.push(text); };
const b64u = (v) => Buffer.from(v).toString('base64url');
fake.st.onApp = async (c, m, reply) => {
  // Face ID set up on this remote directly (P547: the host answers `direct` and offers a registration nonce)
  if (m.t === 'pk_peer_req') { await reply({ t: 'pk_peer_res', r: m.r, ok: true, mode: 'direct' }); await reply({ t: 'pk_offer', n: b64u(randomBytes(32)) }); return true; }
  if (m.t === 'pk_reg') { registered.set(c.devId, { id: m.id, alg: m.alg, spki: m.spki, rp: 'localhost', dev: c.devId, sign_pub: m.roster_sign_pub, bind: m.roster_sig }); await reply({ t: 'pk_reg_res', ok: true }); return true; }
  if (m.t === 'fr_set' && m.op === 'owner-confirm-v3') {
    const target = { friend: m.friend, op: m.op, value: m.value };
    const sigOk = await fake.verifyControl(c, 'fr_set', m, target);
    const expected = await ownerConfirmChallenge(target, m.n, fake.channel, c.devId);
    const uvOk = !!m.fa && await verifyBindingAssertion(registered.get(c.devId), m.fa, expected, { rp: 'localhost', origin: new URL(web.url).origin });
    ceremonies.push({ ...m, dev: c.devId, sigOk, uvOk });
    if (confirmWhy) { await reply({ t: 'ctl_res', r: m.r, action: 'fr_set', ok: false, why: confirmWhy }); return true; }
    await reply({ t: 'ctl_res', r: m.r, action: 'fr_set', ok: sigOk && uvOk });
    return true;
  }
  return false;
};
async function tap(p, selector) {
  const r = await evaluate(p, `(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...r, button: 'left', clickCount: 1 });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...r, button: 'left', clickCount: 1 });
}
async function remote(lang, name) {
  const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await p.send('WebAuthn.enable', { enableUI: false });
  await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  await evaluate(p, `PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>true;window.__gets=0;const get=navigator.credentials.get.bind(navigator.credentials);navigator.credentials.get=o=>{window.__gets++;return get(o);};`);
  await evaluate(p, `document.querySelector('#setup-faceid').click()`); await waitFor(p, `!document.querySelector('#confirm').hidden`); await tap(p, '#confirm-yes');
  await waitFor(p, `import('./js/store.js').then(async s=>(await s.dbGet('host')).pk===true)`);
  const dev = await evaluate(p, `import('./js/api.js').then(a=>a.myDeviceId())`);
  const local = await evaluate(p, `Promise.all([import('./js/store.js'),import('./js/session.js')]).then(([s,c])=>s.dbGet('owner-roster:'+c.channel()))`);
  check(local && local.dev === dev, `${lang} ${name}: Face ID setup stored a bound roster descriptor`);
  return { p, dev, entry: { id: local.id, alg: local.alg, spki: local.spki, rp: local.rp, dev: local.dev, name, bind: local.bind, sign_pub: local.sign_pub } };
}
async function peerEntry(name, dev) {
  const ed = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']);
  const key = { priv: ed.privateKey, pub: new Uint8Array(await crypto.subtle.exportKey('raw', ed.publicKey)) };
  const pk = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']);
  const reg = { id: b64u(randomBytes(32)), alg: -7, spki: b64u(await crypto.subtle.exportKey('spki', pk.publicKey)) };
  const s = await bindRegistration(reg, dev, key, 'localhost');
  return { ...reg, rp: 'localhost', dev, name, bind: s.roster_sig, sign_pub: s.roster_sign_pub };
}
const pyDumps = (v) => JSON.stringify(v).replace(/[\u007f-￿]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
async function v4card(hereRoster, peerRoster) {
  const ends = [['AJ-HERE-0000', 'host-ed', 'a'.repeat(32), hereRoster], ['AJ-PEER-1111', 'peer-ed', 'b'.repeat(32), peerRoster]];
  const pairing_scope = pyDumps([SCOPE_V4, ...ends]);
  const challenge = Buffer.from(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(pairing_scope))).toString('hex').slice(0, 32);
  return { id: randomBytes(16).toString('hex'), tool: 'peer_question', summary: 'Same owner', ttl: 120,
    pq: { pairing: true, same_owner: true, pairing_v: 4, friend: 'AJ-PEER-1111', name: 'Father@TB', code: String(BigInt('0x' + challenge) % 1000000n).padStart(6, '0'), challenge, pairing_scope } };
}
const asked = (p, id) => evaluate(p, `import('./js/snap.js').then(s=>s.S.asks.has(${JSON.stringify(id)}))`);
try {
  for (const lang of ['zh', 'en']) {
    const zh = lang === 'zh';
    const A = await remote(lang, zh ? '工作手机' : 'Work phone'), Bp = await remote(lang, 'MacBook'), C = await remote(lang, 'iPad');
    const peers = [await peerEntry(zh ? '备用平板' : 'Spare tablet', 'tabletDDDDDDDDDD'), await peerEntry('Mac mini', 'macEEEEEEEEEEEEE')];
    const card = await v4card([A.entry, Bp.entry], peers);
    await fake.ask(card);
    for (const [r, n] of [[A, 'A'], [Bp, 'B']]) {
      await waitFor(r.p, `document.querySelector('#frOwnerAllow')&&!document.querySelector('#frOwnerAllow').disabled`, 12000);
      check(await evaluate(r.p, `document.querySelector('.fr-owner-code').textContent===${JSON.stringify(card.pq.code)}`), `${lang} ${n}: bound remote shows the scope's six digits and can press Allow`);
    }
    await waitFor(C.p, `!!document.querySelector('#frAsk .fr-owner-changed')`, 12000);
    check(await evaluate(C.p, `document.querySelector('#frOwnerAllow').disabled && !document.querySelector('#frOwnerRetry')`), `${lang} C: bound after the round started → Allow disabled, no useless retry`);
    check(await evaluate(C.p, `document.querySelector('#frAsk .fr-owner-changed').textContent`) === (zh ? '这台遥控器是在这次邀请发起之后才确认的，这一轮签不了。请在另一台遥控器上确认，或到好友页重新发起一次。' : "This remote was confirmed after this invitation started, so it can't sign this round. Confirm on another remote, or start again on the friends page."), `${lang} C: plain start-again line`);
    await tap(A.p, '#frAsk .fr-owner-details > summary'); await waitFor(A.p, `document.querySelector('#frAsk .fr-owner-details').open`);
    const text = await evaluate(A.p, `document.querySelector('#frOwnerSigners').innerText`);
    check(text.includes(zh ? '确认后，这些遥控器都能签同主人授权' : 'After you confirm, all of these remotes can sign same-owner grants'), `${lang}: R-F4 heading (contract wording)`);
    for (const e of [A.entry, Bp.entry, ...peers]) check(text.includes(e.name + ' · ' + e.dev.slice(0, 8)), `${lang}: lists ${e.name} with the first 8 characters of its device id`);
    check(!/待已认可|Awaiting|trusted/i.test(text), `${lang}: no trusted / pending split in v4`);
    for (const width of [390, 1440]) for (const scheme of ['light', 'dark']) {
      for (const r of [A, C]) {
        await r.p.send('Emulation.setDeviceMetricsOverride', { width, height: width === 390 ? 844 : 1050, deviceScaleFactor: 1, mobile: width === 390 });
        await r.p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
        await evaluate(r.p, `import('./js/ui.js').then(m=>m.toastOff())`);
      }
      await sleep(150);
      writeFileSync(`${OUT}${width}-${scheme}-${lang}-signers.png`, await shoot(A.p, ''));
      writeFileSync(`${OUT}${width}-${scheme}-${lang}-start-again.png`, await shoot(C.p, ''));
    }
    // B (not the inviter, not a "root") signs
    const n0 = ceremonies.length, g0 = await evaluate(Bp.p, `window.__gets`), ga = await evaluate(A.p, `window.__gets`);
    await tap(Bp.p, '#frOwnerAllow'); for (let i = 0; i < 200 && ceremonies.length === n0; i++) await sleep(25);
    const c = ceremonies.at(-1);
    check(ceremonies.length === n0 + 1 && c.dev === Bp.dev && c.sigOk && c.uvOk && c.value === card.pq.challenge && c.op === 'owner-confirm-v3', `${lang} B: one owner-confirm-v3 with B's own Ed signature + UV over the shown challenge`);
    check(await evaluate(Bp.p, `window.__gets`) === g0 + 1, `${lang} B: exactly one UV for the confirmation`);
    await fake.askDone(card.id, 'gone');   // the host folds the card on every remote after the first confirm
    for (const [r, n] of [[A, 'A'], [Bp, 'B'], [C, 'C']]) { await waitFor(r.p, `import('./js/snap.js').then(s=>!s.S.asks.has(${JSON.stringify(card.id)}))`); check(!await asked(r.p, card.id), `${lang} ${n}: card folded on ask_done`); }
    check(await evaluate(A.p, `window.__gets`) === ga, `${lang} A: never asked for UV once B signed`);
    // one tampered peer entry → unsignable everywhere
    const bad = await v4card([A.entry, Bp.entry], [{ ...peers[0], spki: peers[1].spki }, peers[1]]);
    await fake.ask(bad);
    for (const [r, n] of [[A, 'A'], [Bp, 'B']]) {
      await waitFor(r.p, `import('./js/snap.js').then(s=>s.S.asks.get(${JSON.stringify(bad.id)})?.pairing_prepare_failed===true)`, 12000);
      check(await evaluate(r.p, `document.querySelector('#frOwnerAllow').disabled`), `${lang} ${n}: one tampered roster entry → Allow disabled`);
    }
    await evaluate(Bp.p, `document.querySelector('#frOwnerAllow').click()`); await sleep(100);
    check(ceremonies.length === n0 + 1, `${lang}: tampered card never signs`);
    await fake.askDone(bad.id, 'gone');
    // R-F1 (contract-a5 DEVIATION 3): the other host has no roster support → the confirm is refused; the card says so, stays unconfirmable
    const old = await v4card([A.entry, Bp.entry], peers);
    await fake.ask(old); confirmWhy = 'peer_too_old';
    await waitFor(Bp.p, `document.querySelector('#frOwnerAllow')&&!document.querySelector('#frOwnerAllow').disabled`, 12000);
    const n1 = ceremonies.length;
    await tap(Bp.p, '#frOwnerAllow'); for (let i = 0; i < 200 && ceremonies.length === n1; i++) await sleep(25);
    const tooOld = zh ? '对方 Agent J 主机端程序版本太旧，用不了「同一个主人」。请先把它升级到最新版，再重新发起。' : 'The other Agent J host is too old for Same owner — update it, then start again.';
    await waitFor(Bp.p, `document.querySelector('#frAsk .fr-owner-peer')?.textContent===${JSON.stringify(tooOld)}`, 8000);
    check(await evaluate(Bp.p, `document.querySelector('#frOwnerAllow').disabled`), `${lang} B: peer_too_old → the plain line on the card, Allow off`);
    check(!/peer_too_old/.test(await evaluate(Bp.p, `document.body.innerText`)), `${lang} B: no raw code`);
    await evaluate(Bp.p, `document.querySelector('#frOwnerAllow').click()`); await sleep(100);
    check(ceremonies.length === n1 + 1, `${lang} B: a refused card cannot be sent again`);
    for (const width of [390, 1440]) for (const scheme of ['light', 'dark']) {
      await Bp.p.send('Emulation.setDeviceMetricsOverride', { width, height: width === 390 ? 844 : 1050, deviceScaleFactor: 1, mobile: width === 390 });
      await Bp.p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
      await evaluate(Bp.p, `import('./js/ui.js').then(m=>m.toastOff())`); await sleep(150);
      writeFileSync(`${OUT}${width}-${scheme}-${lang}-peer-too-old.png`, await shoot(Bp.p, ''));
    }
    confirmWhy = null; await fake.askDone(old.id, 'gone');
    for (const r of [A, Bp, C]) await waitFor(r.p, `import('./js/snap.js').then(s=>!s.S.asks.has(${JSON.stringify(old.id)}))`);
    // the ended card's last state stays on the stage until put away (existing behaviour) — put it away before the next screen
    await evaluate(A.p, `document.querySelector('#sheet:not([hidden]) #sheetClose')?.click()`); await sleep(200);
    // R-F1 on the friends row: same_owner_why while pending → the detail page and the card say it; not confirmable
    const fs = sampleFriends();
    fs.friends = [{ id: 'AJ-PEER-1111', name: 'Father@TB', owner: '', intro: '', group: 'default', state: 'friend', blocked: false, last: 0, unread: 0,
      same_owner: 'pending', same_owner_code: card.pq.code, same_owner_wait: false, same_owner_why: 'peer_binding_required' }];
    fake.friends(fs);
    // t.js puts this system's own unlock name in place of 「Face ID」 (here: Linux Chromium) — compare against the page's own t()
    const needBind = await evaluate(A.p, `import('./js/t.js').then(m=>m.t('p0306.so.peerBinding'))`);
    check(needBind.startsWith(zh ? '请先在对方 Agent J 主机的任意一台遥控器上打开一次「同一个主人」' : 'On the other Agent J host, open Same owner once from one of its remotes'), `${lang}: R-F1 peer_binding_required wording`);
    await evaluate(A.p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/friends.js').then(m=>m.open('detail','AJ-PEER-1111'))`);
    await waitFor(A.p, `document.querySelector('#fr-so .fr-so__peer')?.textContent===${JSON.stringify(needBind)}`, 8000);
    check(true, `${lang} A: friends detail says what to do on the other Agent J host (same_owner_why)`);
    for (const width of [390, 1440]) for (const scheme of ['light', 'dark']) {
      await A.p.send('Emulation.setDeviceMetricsOverride', { width, height: width === 390 ? 844 : 1050, deviceScaleFactor: 1, mobile: width === 390 });
      await A.p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
      await evaluate(A.p, `import('./js/ui.js').then(m=>m.toastOff())`); await sleep(150);
      writeFileSync(`${OUT}${width}-${scheme}-${lang}-peer-binding-detail.png`, await shoot(A.p, ''));
    }
    await evaluate(A.p, `import('./js/controls.js').then(m=>m.openPanel('chat'))`);   // the card shows on the chat stage
    const later = await v4card([A.entry, Bp.entry], peers);
    await fake.ask(later);
    await waitFor(A.p, `document.querySelector('#frAsk .fr-owner-peer')?.textContent===${JSON.stringify(needBind)}`, 12000);
    check(await evaluate(A.p, `document.querySelector('#frOwnerAllow').disabled`), `${lang} A: same_owner_why → the card is not confirmable here`);
    for (const width of [390, 1440]) for (const scheme of ['light', 'dark']) {
      await A.p.send('Emulation.setDeviceMetricsOverride', { width, height: width === 390 ? 844 : 1050, deviceScaleFactor: 1, mobile: width === 390 });
      await A.p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
      await evaluate(A.p, `import('./js/ui.js').then(m=>m.toastOff())`); await sleep(150);
      writeFileSync(`${OUT}${width}-${scheme}-${lang}-peer-binding-card.png`, await shoot(A.p, ''));
    }
    await fake.askDone(later.id, 'gone');
    for (const r of [A, Bp, C]) await r.p.dispose();
  }
  writeFileSync(OUT + 'checks.json', JSON.stringify({ checks, physicalBiometric: false }, null, 2) + '\n');
  console.log(`P0304 any-signer phone PASS: ${checks.length} checks; evidence → ${OUT}`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
