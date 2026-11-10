#!/usr/bin/env node
// #0306 A5 browser leg (real client + fake host speaking the host contract; virtual platform authenticator replaces the
// physical biometric only). A remote with Face ID but no roster binding chooses 「同一个主人」 on a friend:
//   start sheet 「发起配对」 → host {ok:false, why:'roster_binding_required', next:'bind', cont} + its roster_bind_offer
//   → the binding sheet says 「这台遥控器第一次用，先用 Face ID 确认一次」 → ONE get() → roster_bind → roster_bind_res ok
//   → the page sends fr_set group same-owner again WITH the ticket, once → the six digits show. Never 「没成功」 / the raw code.
// Then: a cancelled binding → plain line + 「再试一次」, nothing continues; retry → bind → continues once.
// 0.30.6 (contract-a5 DEVIATIONS): `again` — the host turns the continuation down (binding did not stick) and re-offers the
// binding: no automatic second UV, the re-offered sheet is the owner's next tap, then ONE continuation with the fresh ticket;
// `passkey` — next:'passkey' (no ticket): the 「在本机设置」 offer, no binding sheet; `grant` — R-F5 on a same-owner grant card:
// roster_bind_offer → answer_refused roster_binding_required → binding sheet, the card stays open with the binding line.
// zh/en × light/dark × 390/1440 → reports/qa/0304-frozen/web-a5/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { canonicalJson } from '../public/proto/wire.js';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
const OUT = process.env.P0306_OUT || fileURLToPath(new URL('../../../reports/qa/0304-frozen/web-a5/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer(); web.url = web.url.replace('127.0.0.1', 'localhost');
const fake = await startFakeHost(), B = await launch();
const { verifyBindingAssertion, bindChallenge } = await import('../public/js/ownergrant.js');
const checks = [];
const check = (v, text) => { assert.ok(v, text); checks.push(text); };
const b64u = (v) => Buffer.from(v).toString('base64url');
const FRIEND = 'AJ-PH8E-AJT4-26GJ-ACQ9';
const GRANTS = JSON.parse(readFileSync(new URL('./fixtures/p574-grants.json', import.meta.url), 'utf8'));
let H = null;   // per-run host state: { entry, bound, ticket, starts:[], binds:[], dropBind, noPasskey, grant, answers:[] }
fake.st.onApp = async (c, m, reply) => {
  if (m.t === 'pk_peer_req') { await reply({ t: 'pk_peer_res', r: m.r, ok: true, mode: 'direct' }); await reply({ t: 'pk_offer', n: b64u(randomBytes(32)) }); return true; }
  if (m.t === 'pk_reg') { if (H?.bindOnReg && m.roster_sig) H.bound = true; await reply({ t: 'pk_reg_res', ok: true }); return true; }   // 0304 r2: a new key binds with its roster_sig
  if (!H) return false;
  if (m.t === 'roster_bind_get') { await reply({ t: 'roster_bind_offer', entry: H.entry }); return true; }
  if (m.t === 'answer' && H.grant && m.id === H.grant.id) {
    // 0304 R-F5: an allow from a remote that is not on the roster: the offer to this remote, then the refusal; the card stays open
    H.answers.push(m);
    if (!H.bound) { await reply({ t: 'roster_bind_offer', entry: H.entry }); await reply({ t: 'answer_refused', id: m.id, why: 'roster_binding_required', ask: H.grant }); }
    return true;
  }
  if (m.t === 'roster_bind') {
    const ok = await verifyBindingAssertion(H.entry, m.fa, await bindChallenge(H.entry), { rp: 'localhost', origin: new URL(web.url).origin });
    H.binds.push({ ok }); if (ok && !(H.dropBind && H.binds.length === 1)) H.bound = true;   // `again`: the first binding does not stick
    await reply({ t: 'roster_bind_res', ok }); return true;
  }
  if (m.t === 'fr_set' && m.op === 'group' && m.value === 'same-owner') {
    const sigOk = await fake.verifyControl(c, 'fr_set', m, { friend: m.friend, op: m.op, value: m.value });
    H.starts.push({ friend: m.friend, cont: m.cont, sigOk });
    const no = (why, extra = {}) => reply({ t: 'ctl_res', r: m.r, action: 'fr_set', ok: false, why, ...extra });
    if (!sigOk) return no('bad_signature'), true;
    if (H.noPasskey) { await no('roster_binding_required', { next: 'passkey' }); return true; }   // contract-a5 DEVIATION 1: no ticket
    if (!H.bound) {
      // the real host: broadcast the offer, then answer with a one-time continuation ticket
      H.ticket = b64u(randomBytes(24));
      await fake.send({ t: 'roster_bind_offer', entry: H.entry });
      await no('roster_binding_required', { next: 'bind', cont: H.ticket }); return true;
    }
    if (m.cont !== undefined && (m.cont !== H.ticket || m.friend !== FRIEND)) { await no('cont_invalid'); return true; }
    H.ticket = null;   // single use
    const f = fake.st.friends.friends.find((x) => x.id === m.friend);
    f.same_owner = 'pending'; f.same_owner_code = '482913'; f.same_owner_wait = true;   // the start is this side's consent
    await reply({ t: 'ctl_res', r: m.r, action: 'fr_set', ok: true });
    await fake.send({ t: 'fr_changed', friend: m.friend });
    return true;
  }
  return false;
};
async function tap(p, selector) {
  const r = await evaluate(p, `(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...r, button: 'left', clickCount: 1 });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...r, button: 'left', clickCount: 1 });
}
async function view(p, width, scheme) {
  await p.send('Emulation.setDeviceMetricsOverride', { width, height: width === 390 ? 844 : 1050, deviceScaleFactor: 1, mobile: width === 390 });
  await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
  await sleep(150);
}
const shots = async (p, lang, tag, before) => {
  for (const width of [390, 1440]) for (const scheme of ['light', 'dark']) { await view(p, width, scheme); await before?.(); writeFileSync(`${OUT}${width}-${scheme}-${lang}-${tag}.png`, await shoot(p, '')); }
  await view(p, 390, 'light');
};
async function remote(lang, { faceId = true } = {}) {
  fake.friends(sampleFriends());
  const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await p.send('WebAuthn.enable', { enableUI: false });
  await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  await evaluate(p, `PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>true;window.__gets=0;window.__creates=0;const get=navigator.credentials.get.bind(navigator.credentials),create=navigator.credentials.create.bind(navigator.credentials);navigator.credentials.get=o=>{window.__gets++;return get(o);};navigator.credentials.create=o=>{window.__creates++;return create(o);};
    window.__toasts=[];new MutationObserver(()=>{const t=document.querySelector('#toast')?.textContent;if(t&&window.__toasts.at(-1)!==t)window.__toasts.push(t);}).observe(document.body,{subtree:true,childList:true,characterData:true});`);
  H = { entry: null, bound: false, ticket: null, starts: [], binds: [], answers: [], grant: null };
  if (!faceId) return p;
  await evaluate(p, `document.querySelector('#setup-faceid').click()`); await waitFor(p, `!document.querySelector('#confirm').hidden`); await tap(p, '#confirm-yes');
  await waitFor(p, `import('./js/store.js').then(async s=>(await s.dbGet('host')).pk===true)`);
  // An existing Face ID made before the roster binding (0.30.x upgrade): the passkey is there, the binding is not.
  const local = await evaluate(p, `Promise.all([import('./js/store.js'),import('./js/session.js')]).then(async([s,c])=>{const d=await s.dbGet('owner-roster:'+c.channel());await s.dbDel('owner-roster:'+c.channel());return d;})`);
  H.entry = { id: local.id, alg: local.alg, spki: local.spki, rp: local.rp, dev: local.dev, name: 'Warden', sign_pub: local.sign_pub };
  return p;
}
async function chooseSameOwner(p) {
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/friends.js').then(m=>m.open('detail',${JSON.stringify(FRIEND)}))`);
  await waitFor(p, `!!document.querySelector('#fr-group')`);
  await evaluate(p, `(()=>{const s=document.querySelector('#fr-group');s.value='same-owner';s.dispatchEvent(new Event('change'));})()`);
  await waitFor(p, `!document.querySelector('#confirm').hidden`);   // 「发起配对」 (the owner's one tap)
  await tap(p, '#confirm-yes');
}
const T = (p, k) => evaluate(p, `import('./js/t.js').then(m=>m.t(${JSON.stringify(k)}))`);
const noFailure = async (p, lang, leg) => {
  const toasts = await evaluate(p, `window.__toasts`);
  check(!toasts.some((x) => /roster_binding_required|没成功|didn.t work|did not take effect|没生效|重新选择|Choose Same owner again/i.test(x)), `${lang} ${leg}: no failure line, no raw code, no 「choose it again」: ${JSON.stringify(toasts)}`);
};
try {
  for (const lang of ['zh', 'en']) for (const leg of ['continue', 'cancel', 'again']) {
    const zh = lang === 'zh';
    const p = await remote(lang);
    if (leg === 'again') H.dropBind = true;
    const gets0 = await evaluate(p, `window.__gets`);
    await chooseSameOwner(p);
    const first = zh ? '这台遥控器第一次用，先用' : 'First time on this remote — confirm once with';   // + this system's unlock name (t.js bioWords)
    await waitFor(p, `!document.querySelector('#confirm').hidden && document.querySelector('#confirm-text').textContent.startsWith(${JSON.stringify(first)})`);
    check(H.starts.length === 1 && H.starts[0].sigOk && H.starts[0].cont === undefined, `${lang} ${leg}: one signed start, no ticket on it`);
    await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
    if (leg === 'continue') await shots(p, lang, 'first-bind');
    if (leg === 'cancel') {
      await tap(p, '#confirm-no');
      const failed = zh ? '这台遥控器还没确认好，同一个主人还没发起。' : "This remote isn't confirmed yet, so Same owner hasn't started.";
      await waitFor(p, `document.querySelector('#toast')?.textContent.includes(${JSON.stringify(failed)}) && !!document.querySelector('#toast .nt-acts button')`);
      check(H.starts.length === 1 && H.binds.length === 0, `${lang} cancel: nothing continues after a cancelled binding`);
      check(await evaluate(p, `document.querySelector('#toast .nt-acts button').textContent`) === (zh ? '再试一次' : 'Try again'), `${lang} cancel: plain line + retry`);
      await shots(p, lang, 'bind-cancelled');
      await tap(p, '#toast .nt-acts button');
      await waitFor(p, `!document.querySelector('#confirm').hidden && document.querySelector('#confirm-text').textContent.startsWith(${JSON.stringify(first)})`);
    }
    await tap(p, '#confirm-yes');   // the ONE UV
    if (leg === 'again') {
      // the host turned the continuation down (not bound) with a fresh ticket and re-offered the binding: the sheet is the
      // owner's next tap — the page itself never asks for a second UV
      for (let i = 0; i < 200 && H.starts.length < 2; i++) await sleep(25);
      await waitFor(p, `!document.querySelector('#confirm').hidden`, 12000);
      await sleep(300);
      check(H.binds.length === 1 && H.starts.length === 2 && H.starts[1].cont, `${lang} again: one binding, one continuation (turned down by the host)`);
      check(await evaluate(p, `window.__gets`) === gets0 + 1, `${lang} again: no automatic second UV`);
      check(!await evaluate(p, `!!document.querySelector('#toast .nt-acts button')`), `${lang} again: no retry line while the re-offered sheet is up`);
      await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
      await shots(p, lang, 'rebind-offered');
      const spent = H.starts[1].cont;
      await tap(p, '#confirm-yes');   // the owner's own second tap
      await waitFor(p, `document.querySelector('#fr-so-code')?.textContent==='482913'`, 12000);
      check(H.binds.length === 2 && H.starts.length === 3 && H.starts[2].cont && H.starts[2].cont !== spent && H.starts[2].sigOk, `${lang} again: then ONE continuation with the fresh ticket`);
      check(await evaluate(p, `window.__gets`) === gets0 + 2, `${lang} again: exactly one UV per owner tap`);
      await noFailure(p, lang, leg);
      await sleep(400);
      check(H.starts.length === 3, `${lang} again: never loops`);
      H = null; await p.dispose(); continue;
    }
    await waitFor(p, `document.querySelector('#fr-so-code')?.textContent==='482913'`, 12000);
    check(H.binds.length === 1 && H.binds[0].ok, `${lang} ${leg}: binding verified by the host`);
    check(H.starts.length === 2 && H.starts[1].sigOk && H.starts[1].cont && H.starts[1].friend === FRIEND, `${lang} ${leg}: start sent again by itself with the ticket, signed, same friend`);
    check(await evaluate(p, `window.__gets`) === gets0 + 1, `${lang} ${leg}: exactly one UV for the whole flow`);
    check(await evaluate(p, `window.__creates`) === 1, `${lang} ${leg}: the existing passkey is kept (no new key)`);
    await sleep(400);
    check(H.starts.length === 2, `${lang} ${leg}: the ticket is sent once`);
    await noFailure(p, lang, leg);
    if (leg === 'continue') await shots(p, lang, 'code-shown', () => evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`));
    // a replayed binding receipt never sends the ticket again
    await fake.send({ t: 'roster_bind_res', ok: true }); await sleep(300);
    check(H.starts.length === 2, `${lang} ${leg}: a second binding receipt does not continue again`);
    H = null;
    await p.dispose();
  }
  // contract-a5 DEVIATION 1: no passkey on this remote → next:'passkey', no ticket → the 「在本机设置」 offer, not the binding sheet
  for (const lang of ['zh', 'en']) {
    const zh = lang === 'zh';
    const p = await remote(lang, { faceId: false });
    H.noPasskey = true;
    await chooseSameOwner(p);
    const line = await evaluate(p, `import('./js/pkassist.js').then(m=>m.withBio('p0306.so.needPasskey'))`);
    await waitFor(p, `document.querySelector('#toast')?.textContent.includes(${JSON.stringify(line)}) && !!document.querySelector('#toast .nt-acts button')`, 8000);
    check(line.startsWith(zh ? '这台遥控器还没有通行密钥。先在这台上设置' : 'This remote has no passkey yet. Set up'), `${lang} passkey: the add-a-passkey-here line`);
    check(await evaluate(p, `document.querySelector('#confirm').hidden`), `${lang} passkey: no binding sheet`);
    check(await evaluate(p, `document.querySelector('#toast .nt-acts button').textContent`) === await T(p, 'p539.pk.setup'), `${lang} passkey: the existing 「在本机设置」 entry`);
    check(H.starts.length === 1 && H.binds.length === 0, `${lang} passkey: nothing continues, nothing bound`);
    await noFailure(p, lang, 'passkey');
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);   // the browser-storage note (no passkey yet) is not this screen
    await shots(p, lang, 'need-passkey');
    await tap(p, '#toast .nt-acts button');
    await waitFor(p, `!document.querySelector('#confirm').hidden`, 8000);
    check(true, `${lang} passkey: the button opens the Face ID setup (F20)`);
    // 0304 r2 (QA scene 6): finishing that setup goes on by itself — no 「再选一次「同一个主人」」. The new key carries its roster
    // binding (pk_reg roster_sig, like the real host), so the start goes straight through to the six digits: one tap in total.
    H.noPasskey = false; H.bindOnReg = true;
    await tap(p, '#confirm-yes');
    await waitFor(p, `document.querySelector('#fr-so-code')?.textContent==='482913'`, 15000);
    check(H.starts.length === 2 && H.starts[1].sigOk && H.starts[1].cont === undefined && H.starts[1].friend === FRIEND, `${lang} passkey: after the setup the start went again by itself, once, signed, same friend`);
    check(H.binds.length === 0 && await evaluate(p, `window.__gets`) === 0, `${lang} passkey: no extra UV, no binding sheet`);
    await sleep(400);
    check(H.starts.length === 2, `${lang} passkey: never loops`);
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
    await shots(p, lang, 'passkey-continued', () => evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`));
    H = null; await p.dispose();
  }
  // 0304 R-F5: a same-owner grant card on a remote with a passkey but no roster binding
  for (const lang of ['zh', 'en']) {
    const zh = lang === 'zh';
    const p = await remote(lang);
    const fixture = structuredClone(GRANTS[lang]), otg = fixture.otg, oldTid = otg.tid;
    otg.iat = Math.floor(Date.now() / 1000); otg.exp = otg.iat + 86400;
    const core = Object.fromEntries(Object.entries(otg).filter(([k]) => k !== 'tid'));
    otg.tid = Buffer.from(await crypto.subtle.digest('SHA-256', new TextEncoder().encode('agentj-owner-grant-v1\n' + canonicalJson(core)))).toString('hex').slice(0, 32);
    const summary = fixture.summary.replace((zh ? '编号 ' : 'Grant ') + oldTid + '\n', (zh ? '编号 ' : 'Grant ') + otg.tid + '\n');
    const card = { id: randomBytes(16).toString('hex'), tool: 'agentj secret grant', summary, owner_grant: otg, ttl: 120, cat: ['credentials', 'send'], why: zh ? '完成这次测试任务' : 'Complete this test task', passkey_required: true, pk_kind: 'sandbox', fa: H.entry.id, fa_s: 'local' };
    card.fa_ch = await evaluate(p, `Promise.all([import('./js/ownergrant.js'),import('./js/session.js'),import('./js/api.js'),import('./proto/wire.js')]).then(async([o,s,a,w])=>w.b64u(await o.grantChallenge(${JSON.stringify(card)},s.channel(),await a.myDeviceId())))`);
    H.grant = card;
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
    await fake.ask(card); await waitFor(p, `import('./js/snap.js').then(m=>!!m.S.asks.get(${JSON.stringify(card.id)})?.fa_ch)`);
    await waitFor(p, `!document.querySelector('#sheet').hidden && !document.querySelector('#apprAllow').hidden && !document.querySelector('#apprAllow').disabled`);
    const gets0 = await evaluate(p, `window.__gets`);
    await tap(p, '#apprAllow');
    await waitFor(p, `!document.querySelector('#confirm').hidden`, 12000);
    const bindLine = await T(p, 'p0306.grant.bind');
    await waitFor(p, `document.querySelector('#askState')?.textContent===${JSON.stringify(bindLine)}`, 8000);
    check(H.answers.length === 1, `${lang} grant: one allow went to the host`);
    check(await evaluate(p, `document.querySelector('#confirm-text').textContent`) === await T(p, 'p574.bindWhy'), `${lang} grant: the binding sheet (existing passkey, one UV)`);
    check(await evaluate(p, `import('./js/snap.js').then(m=>m.S.asks.has(${JSON.stringify(card.id)}))`), `${lang} grant: the card stays open`);
    await noFailure(p, lang, 'grant');
    await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
    await shots(p, lang, 'grant-bind');
    await tap(p, '#confirm-yes');
    const bound = await T(p, 'p0306.grant.bound');
    await waitFor(p, `document.querySelector('#toast')?.textContent.includes(${JSON.stringify(bound)})`, 12000);
    check(H.binds.length === 1 && H.binds[0].ok && H.bound, `${lang} grant: binding verified by the host`);
    check(await evaluate(p, `window.__gets`) === gets0 + 2, `${lang} grant: the allow's UV + the binding's UV, nothing more`);
    check(await evaluate(p, `document.querySelector('#askState').textContent`) !== bindLine, `${lang} grant: the card goes back to its own text`);
    check(await evaluate(p, `!document.querySelector('#sheet').hidden && !document.querySelector('#apprAllow').disabled`), `${lang} grant: 「批准」 is available again on the same card`);
    await shots(p, lang, 'grant-bound');
    H = null; await p.dispose();
  }
  writeFileSync(OUT + 'checks.json', JSON.stringify({ checks, physicalBiometric: false }, null, 2) + '\n');
  console.log(`P0306 binding-flow phone PASS: ${checks.length} checks; evidence → ${OUT}`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
