// 0.30.3 (董事长 11-09 14:5x, Mac Chrome): after the FIRST switch-on (Face ID + host password) the terminal opened but the page
// stayed on the chat — the real host sends the login card as a shared-popup special card (approval_v 2) over the chat, which
// hides the terminal page. Checks, zh / en × light / dark × 1440 (computer browser) + zh light 390:
//   · enable → intro → Face ID → the login card arrives as approval_v 2 → password → term_ready → the TERMINAL PAGE is in front
//     (body[data-term=1], #term visible) and no special card is left waiting (the page colour is back)
//   · back to the chat, the host's 「网页终端已打开」 notice with card {term:'open'} → 「进入终端」 → the open terminal again
//   · the same notice on a phone whose terminal is NOT open → no button
// Shots → reports/qa/0303-term-nav (P0303_OUT).
// Shots (zh / en × light / dark × 390 / 1440) → reports/qa/p0269-term/web (FULL_TERMINAL_OUT overrides); `line-*` = before (line mode), `pty-*` = after.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateKeyPairSync, randomBytes, webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect, key } from './browser.mjs';
import { controlObject, elevateFields, elevateDigest, elevateMessage, b64u, unb64u, SEAL_CONTEXT, approvalEnvelopeDigest } from '../../protocol/wire.js';

const subtle = webcrypto.subtle;
const OUT = process.env.P0303_OUT || 'reports/qa/0303-term-nav';
const enc = new TextEncoder();
const hex = (n) => randomBytes(n).toString('hex');
const sha = async (s) => Buffer.from(await subtle.digest('SHA-256', typeof s === 'string' ? enc.encode(s) : s)).toString('hex');
const $ = (id) => `document.getElementById('${id}')`;
const PASSWORD = 'correct horse 7!';
const PROMPT = '\x1b]133;A\x07owner@box:~/work$ ';
// a vim-like full screen: alternate screen, clear, home, coloured text, `~` rows, a reverse-video status line at the bottom
const VIM = (rows) => '\x1b[?1049h\x1b[H\x1b[2J\x1b[1;32m# notes.md\x1b[0m\r\n\x1b[33mtoken: \x1b[0m[已隐藏]\r\n' +
  Array.from({ length: Math.max(0, rows - 3) }, () => '\x1b[34m~\x1b[0m').join('\r\n') + `\x1b[${rows};1H\x1b[7m"notes.md" 2L -- INSERT --\x1b[0m\x1b[1;3H`;
const BLOCK = (x = {}) => ({ mobile: true, resume: true, prefill: true, kind:'phone', on: true, os: true, me: true, web_ok: true, sudo: null, minutes: 15, idle_s: 300, open: false, modes: ['line', 'pty'], ...x });

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


export async function runP0303({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');

  // ---- the host side of §38 + §A (terminal.py with a PTY, minus the PTY)
  const H = {}; let realPty = null;
  const resetH = () => Object.assign(H, { fa: null, cached: false, calls: [], prefills: [], ins: [], resizes: [], lines: [], closes: [], login: null, ch: new Map(), id: null, errors: [], loginAnswer: null, confirm: null });
  resetH();
  const dev = () => fake.devs[0];
  const faOk = (fa, ch) => { try { const cd = JSON.parse(Buffer.from(fa.cd, 'base64url').toString()); return cd.type === 'webauthn.get' && cd.challenge === ch && fa.id === H.fa; } catch { return false; } };
  const ready = async (send, r, mode) => {
    H.id = H.resuming || hex(8); H.resuming=null;
    await send({ t: 'term_ready', r, id: H.id, expires_at: Math.floor(Date.now() / 1000) + 15 * 60, idle_s: 300, sudo: 'local', cwd: '~/work' });
    await send(mode === 'pty' ? { t: 'term_out', id: H.id, seq: 1, d: PROMPT, prompt: true } : { t: 'term_out', id: H.id, seq: 1, data: 'owner@box:~/work$ ', prompt: true });
  };
  fake.st.onApp = async (c, m, send) => {
    try {
      if (m.t === 'term_open' && !m.sig) {
        const mode = ['enable', 'pty'].includes(m.mode) ? m.mode : 'line';
        H.calls.push({ step: 1, mode, resume:m.resume, cols: m.cols, rows: m.rows });
        const n = hex(16), k = await oneTimeKey(), salt = randomBytes(32), digest = await sha(controlObject('term_open', { mode, ...(m.resume?{resume:m.resume}:{}) }));
        const ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode(['p0269', n, digest].join('\n')))));
        H.ch.set(n, { k, salt, ch, used: false, mode, resume:m.resume });
        return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'passkey_required', n, fa: H.fa, fa_ch: ch, salt: b64u(salt), epk: b64u(k.pub) }), true;
      }
      if (m.t === 'term_open') {
        const x = H.ch.get(m.n), mode = x ? x.mode : 'line';
        const sigOk = await fake.verifyControl(c, 'term_open', m, { mode, ...(x?.resume?{resume:x.resume}:{}) }) && (m.mode || 'line') === mode;
        const fa = !!x && faOk(m.fa, x.ch);
        H.calls.push({ step: 2, sigOk, fa, mode, resume:m.resume, cols: m.cols, rows: m.rows, reuse: !!x?.used });
        if (!x || x.used || !sigOk || !fa) return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'bad_signature' }), true;
        x.used = true; if(m.resume)H.resuming=m.resume;
        await send({ t: 'ctl_pending', r: m.r, action: 'term_open', timeout_ms: 180000 });
        if (H.cached) { await ready(send, m.r, mode); return true; }
        const k = await oneTimeKey();
        H.login = { m: { t: 'elev', id: hex(16), kind: 'login', n: hex(16), epk: b64u(k.pub), ttl: 120, tries: 3, term: m.r, fa: H.fa }, k, r: m.r, mode };
        const e = { v: 2, request_id: H.login.m.id, channel: fake.channel, revision: 1, session: '1', special: { tool: 'TerminalUnlock' } };
        Object.assign(H.login.m, { approval_v: 2, envelope: e, envelope_sha256: await approvalEnvelopeDigest(e), revision: 1, review_state: 'pending' });
        await send(H.login.m);
        return true;
      }
      if (m.t === 'elev_answer' && H.login && m.id === H.login.m.id) {
        const L_ = H.login, d = dev();
        const digest = await elevateDigest('login', elevateFields(L_.m));
        const ctSha = m.ok && m.ct ? await sha(unb64u(m.ct)) : null;
        const key_ = await subtle.importKey('raw', unb64u(d.sk), { name: 'Ed25519' }, false, ['verify']);
        const sigOk = await subtle.verify({ name: 'Ed25519' }, key_, unb64u(m.sig), elevateMessage(fake.channel, d.id, m.id, 'login', m.ok ? 'allow' : 'deny', L_.m.n, m.ts, digest, ctSha));
        const pw = m.ok ? new TextDecoder().decode(await unseal(L_.k.priv, L_.k.pub, m.epk, m.ct, [fake.channel, d.id, m.id, 'login', L_.m.n, digest])) : null;
        H.loginAnswer = { sigOk, pw };
        H.login = null;
        await send({ t: 'elev_done', id: m.id, result: m.ok ? 'done' : 'denied' });
        H.cached = true;
        await ready(send, L_.r, L_.mode);
        return true;
      }
      if (m.t === 'term_in') {
        if(realPty) realPty.stdin.write(JSON.stringify({d:m.d})+'\n');
        H.ins.push({ id: m.id, seq: m.seq, d: m.d, bytes: Buffer.byteLength(String(m.d)) });
        H.buf = (H.buf || '') + m.d;   // keystrokes arrive one frame each; the shell sees one stream
        if (m.id === H.id && H.buf.endsWith('ls\r')) H.buf = '', await send({ t: 'term_out', id: H.id, seq: 2, d: 'ls\r\n\x1b[1;34mbuild\x1b[0m  notes.md\r\n' + PROMPT, prompt: true });
        return true;
      }
      if (m.t === 'term_prefill') { H.prefills.push(m); await send({t:'term_prefill_res',id:H.id,seq:m.seq,ok:!H.prefillBusy,why:H.prefillBusy?'busy':undefined}); return true; }
      if (m.t === 'term_resize') { if(realPty)realPty.stdin.write(JSON.stringify(m)+'\n'); H.resizes.push({ id: m.id, cols: m.cols, rows: m.rows }); return true; }
      if (m.t === 'term_line') { H.lines.push({ seq: m.seq, line: m.line }); return true; }
      if (m.t === 'term_close') { H.closes.push(m.id); return true; }
    } catch (e) { H.errors.push(String(e && e.stack || e)); }
    return false;
  };
  // P0303_PWA=1: an Android phone's Home-Screen app (display-mode: standalone, Android Chrome user agent), light + dark
  const PWA = process.env.P0303_PWA === '1';
  const UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';
  for (const [lang, theme, w] of PWA ? [['zh', 'light', 412], ['zh', 'dark', 412]] : [['zh', 'light', 1440], ['zh', 'dark', 1440], ['en', 'light', 1440], ['en', 'dark', 1440], ['zh', 'light', 390]]) {
    const tag = `${PWA ? 'android-pwa-' : ''}${lang}-${theme}-${w}`, narrow = w < 1000;
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang, theme } }; fake.st.terminal = null;
    resetH();
    const p = await newPage(B, w, narrow ? (PWA ? 915 : 844) : 900, theme, { touch: narrow, allow: [web.url, PAGE, fake.relay + '/'], ...(PWA ? { ua: UA } : {}) });
    if (PWA) await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'display-mode', value: 'standalone' }] });
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: false } });
    const save = async (name) => writeFileSync(resolve(out, `${name}-${tag}.png`), await shoot(p, ''));
    const click = async (id) => {
      await evaluate(p, `${$(id)}.scrollIntoView({block:'nearest'})`); await sleep(40);
      const r = await rect(p, '#' + id);
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    };
    const setBlock = async (b) => { fake.st.terminal = b; await fake.send(fake.prefsMsg()); await sleep(250); await evaluate(p, `import('./js/settings.js').then(m=>{m.render();return true})`); };
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      const credId = randomBytes(16); H.fa = b64u(credId);
      const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
      await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
        rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p0303-fixture').toString('base64'), signCount: 0 } });
      // the notice on a phone without an open terminal: no button
      await fake.addTurn({ k: 'sys', text: '' }, '网页终端已打开（另一台，最长 15 分钟）', 'done', { term: 'open' });
      await sleep(600);
      check(await evaluate(p, `!document.querySelector('[data-term-enter]')`), 'no 「进入终端」 when this phone has no open terminal ' + tag);
      await evaluate(p, `import('./js/settings.js').then(m=>{m.open();return true})`);
      await waitFor(p, `!${$('settings')}.hidden`);
      await setBlock(BLOCK({ on: narrow, me: false, kind: narrow ? 'phone' : 'desktop', mobile: true }));
      const via = await evaluate(p, `${$('set-term-enable')} && ${$('set-term-enable')}.getClientRects().length ? 'set-term-enable' : 'set-term-open'`);
      await click(via);
      await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'intro' && !${$('term-sheetwrap')}.hidden && !${$('term-sh-yes')}.disabled`, 15000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ via, problems: p.problems, calls: H.calls, errors: H.errors })); });
      await click('term-sh-yes');
      await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'login' && !${$('term-sheetwrap')}.hidden`, 10000);
      check(await evaluate(p, `${$('term')}.hidden && document.body.dataset.approvalSpecial === '1'`), 'the login card is the shared popup over the chat (as the real host sends it) ' + tag);
      await sleep(400);   // envelope verified
      await evaluate(p, `${$('term-sh-pw')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
      await save('1-login');
      await click('term-sh-yes');
      await waitFor(p, `${$('term')}.dataset.phase === 'ready'`, 10000);
      check(H.loginAnswer && H.loginAnswer.sigOk && H.loginAnswer.pw === PASSWORD, 'password sealed + signed ' + tag);
      await waitFor(p, `!${$('term')}.hidden && document.body.dataset.term === '1'`, 5000)
        .catch(() => { throw new Error('the terminal page did not come to the front after the first switch-on ' + tag); });
      check(await evaluate(p, `document.body.dataset.approvalSpecial !== '1'`), 'no special card left waiting (page colour back) ' + tag);
      await setBlock(BLOCK({ on: true, me: true, kind: narrow ? 'phone' : 'desktop', mobile: true, open: true }));   // what the host pushes after enabling
      await sleep(300); await save('2-terminal-in-front');
      await evaluate(p, `history.back()`); await sleep(500);
      check(await evaluate(p, `${$('term')}.hidden`), 'back on the chat page ' + tag);
      await fake.addTurn({ k: 'sys', text: '' }, lang === 'zh' ? '网页终端已打开（这台，最长 15 分钟）' : 'Web terminal opened (this one, up to 15 min)', 'done', { term: 'open' });
      await waitFor(p, `!!document.querySelector('[data-term-enter]')`, 8000);
      const btn = await evaluate(p, `(()=>{const b=document.querySelector('[data-term-enter]');const r=b.getBoundingClientRect();return {text:b.textContent,h:Math.round(r.height),w:Math.round(r.width)}})()`);
      check(btn.text === (lang === 'zh' ? '进入终端' : 'Go to terminal') && btn.h >= 44, '「进入终端」 under the notice, ≥ 44 px ' + tag + JSON.stringify(btn));
      await save('3-chat-enter-button');
      await evaluate(p, `document.querySelector('[data-term-enter]').click()`);
      await waitFor(p, `!${$('term')}.hidden`, 8000);
      check(true, '「进入终端」 brings the open terminal back ' + tag);
      await save('4-entered');
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !H.errors.length, 'no console errors ' + tag + JSON.stringify([problems.slice(0, 3), H.errors]));
      console.log('PASS 0303 ' + tag);
    } finally { await p.dispose(); }
  }
  fake.st.onApp = null; fake.st.terminal = null;
  console.log(`P0303: ${checks} checks; shots → ${out}`);
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP0303({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
