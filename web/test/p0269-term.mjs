// 0.26.9 #463 (ADR-0269-w-term, contract §A): the full web terminal on the real page against the local fake host (real Noise,
// the §8 control signatures, the §11 seal of the login password, every Face ID challenge checked like the host does), own
// headless Chromium (never :9222) with a CDP virtual platform authenticator as Face ID's stand-in.
// Checks:
//   · the host block `terminal.modes` with "pty" → open: step 1 asks {mode:"pty", cols, rows}; step 2 signed over {"mode":"pty"}
//     (+ the size) with a real assertion; the login card still inside the page; term_ready → xterm (our pinned copy, no
//     off-site request) replaces the line input
//   · term_out `d` with vim-style escapes (alternate screen, cursor moves, colours, reverse video) renders as a screen; the
//     host's 「[已隐藏]」 stays as sent
//   · typing (keyboard / paste) → term_in in order, `seq` 1, 2, 3 …; a 20 000-byte paste → frames ≤ 8 KiB, re-joined equal
//   · the window changes size → term_resize with the fitted grid (only when it differs)
//   · the extra-keys bar (390 px): Esc Tab ↑ (CSI, then SS3 after the program asks for application cursor keys) ← Ctrl+C
//     Ctrl+D Ctrl+Z | ~ / → the right bytes; Ctrl / Alt sticky for one key (aria-pressed); every key ≥ 44 px
//   · Esc / Tab on the keyboard inside the terminal go to the program (the page stays open)
//   · term_confirm over the terminal: 取消 → Ctrl+U (term_in "\u0015"); 运行 → the signed + asserted term_line
//   · term_close from the host → 「终端已关闭」, the xterm is gone from the page
//   · fallback: a host without "pty" → the old line mode (step 1 without mode, the input line, 「逐行模式」 note)
//   · no console errors, no off-site requests, nothing written to storage
// Shots (zh / en × light / dark × 390 / 1440) → reports/qa/p0269-term/web (P0269_TERM_OUT overrides); `line-*` = before (line mode), `pty-*` = after.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateKeyPairSync, randomBytes, webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect, key } from './browser.mjs';
import { controlObject, elevateFields, elevateDigest, elevateMessage, b64u, unb64u, SEAL_CONTEXT } from '../../protocol/wire.js';

const subtle = webcrypto.subtle;
const OUT = process.env.P0269_TERM_OUT || 'reports/qa/p0269-term/web';
const enc = new TextEncoder();
const hex = (n) => randomBytes(n).toString('hex');
const sha = async (s) => Buffer.from(await subtle.digest('SHA-256', typeof s === 'string' ? enc.encode(s) : s)).toString('hex');
const $ = (id) => `document.getElementById('${id}')`;
const PASSWORD = 'correct horse 7!';
const PROMPT = '\x1b]133;A\x07owner@box:~/work$ ';
// a vim-like full screen: alternate screen, clear, home, coloured text, `~` rows, a reverse-video status line at the bottom
const VIM = (rows) => '\x1b[?1049h\x1b[H\x1b[2J\x1b[1;32m# notes.md\x1b[0m\r\n\x1b[33mtoken: \x1b[0m[已隐藏]\r\n' +
  Array.from({ length: Math.max(0, rows - 3) }, () => '\x1b[34m~\x1b[0m').join('\r\n') + `\x1b[${rows};1H\x1b[7m"notes.md" 2L -- INSERT --\x1b[0m\x1b[1;3H`;
const BLOCK = (x = {}) => ({ on: true, os: true, me: true, web_ok: true, sudo: null, minutes: 15, idle_s: 300, open: false, modes: ['line', 'pty'], ...x });

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

export async function runP0269Term({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');

  // ---- the host side of §38 + §A (terminal.py with a PTY, minus the PTY)
  const H = {};
  const resetH = () => Object.assign(H, { fa: null, cached: false, calls: [], ins: [], resizes: [], lines: [], closes: [], login: null, ch: new Map(), id: null, errors: [], loginAnswer: null, confirm: null });
  resetH();
  const dev = () => fake.devs[0];
  const faOk = (fa, ch) => { try { const cd = JSON.parse(Buffer.from(fa.cd, 'base64url').toString()); return cd.type === 'webauthn.get' && cd.challenge === ch && fa.id === H.fa; } catch { return false; } };
  const ready = async (send, r, mode) => {
    H.id = hex(8);
    await send({ t: 'term_ready', r, id: H.id, expires_at: Math.floor(Date.now() / 1000) + 15 * 60, idle_s: 300, sudo: 'local', cwd: '~/work' });
    await send(mode === 'pty' ? { t: 'term_out', id: H.id, seq: 1, d: PROMPT, prompt: true } : { t: 'term_out', id: H.id, seq: 1, data: 'owner@box:~/work$ ', prompt: true });
  };
  fake.st.onApp = async (c, m, send) => {
    try {
      if (m.t === 'term_open' && !m.sig) {
        const mode = ['enable', 'pty'].includes(m.mode) ? m.mode : 'line';
        H.calls.push({ step: 1, mode, cols: m.cols, rows: m.rows });
        const n = hex(16), k = await oneTimeKey(), salt = randomBytes(32), digest = await sha(controlObject('term_open', { mode }));
        const ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode(['p0269', n, digest].join('\n')))));
        H.ch.set(n, { k, salt, ch, used: false, mode });
        return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'passkey_required', n, fa: H.fa, fa_ch: ch, salt: b64u(salt), epk: b64u(k.pub) }), true;
      }
      if (m.t === 'term_open') {
        const x = H.ch.get(m.n), mode = x ? x.mode : 'line';
        const sigOk = await fake.verifyControl(c, 'term_open', m, { mode }) && (m.mode || 'line') === mode;
        const fa = !!x && faOk(m.fa, x.ch);
        H.calls.push({ step: 2, sigOk, fa, mode, cols: m.cols, rows: m.rows, reuse: !!x?.used });
        if (!x || x.used || !sigOk || !fa) return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'bad_signature' }), true;
        x.used = true;
        await send({ t: 'ctl_pending', r: m.r, action: 'term_open', timeout_ms: 180000 });
        if (H.cached) { await ready(send, m.r, mode); return true; }
        const k = await oneTimeKey();
        H.login = { m: { t: 'elev', id: hex(16), kind: 'login', n: hex(16), epk: b64u(k.pub), ttl: 120, tries: 3, term: m.r, fa: H.fa }, k, r: m.r, mode };
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
        H.ins.push({ id: m.id, seq: m.seq, d: m.d, bytes: Buffer.byteLength(String(m.d)) });
        H.buf = (H.buf || '') + m.d;   // keystrokes arrive one frame each; the shell sees one stream
        if (m.id === H.id && H.buf.endsWith('ls\r')) H.buf = '', await send({ t: 'term_out', id: H.id, seq: 2, d: 'ls\r\n\x1b[1;34mbuild\x1b[0m  notes.md\r\n' + PROMPT, prompt: true });
        return true;
      }
      if (m.t === 'term_resize') { H.resizes.push({ id: m.id, cols: m.cols, rows: m.rows }); return true; }
      if (m.t === 'term_line') {
        const signed = typeof m.sig === 'string';
        const rec = { seq: m.seq, line: m.line, signed };
        if (signed) {
          const x = H.confirm;
          rec.sigOk = !!x && m.n === x.n && await fake.verifyControl(c, 'term_line', m, { id: m.id, line: m.line });
          rec.fa = !!x && faOk(m.fa, x.fa_ch);
          H.confirm = null;
        }
        H.lines.push(rec);
        return true;
      }
      if (m.t === 'term_close') { H.closes.push(m.id); return true; }
    } catch (e) { H.errors.push(String(e && e.stack || e)); }
    return false;
  };
  const confirm = async (seq, line) => {
    H.confirm = { n: hex(16) }; H.confirm.fa_ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('confirm-' + H.confirm.n))));
    await fake.send({ t: 'term_confirm', id: H.id, seq, line, why: '删除文件', why_en: 'deletes files', n: H.confirm.n, fa_ch: H.confirm.fa_ch });
  };

  const combos = [['zh', 'light', 390], ['en', 'dark', 390], ['zh', 'dark', 1440], ['en', 'light', 1440]];
  for (const [lang, theme, w] of combos) {
    const tag = `${lang}-${theme}-${w}`, narrow = w < 500;
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang, theme } }; fake.st.terminal = null;
    resetH();
    const h0 = narrow ? 844 : 900;
    const p = await newPage(B, w, h0, theme, { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => { const W = window.__w = { ls: [], idb: 0 };
      const si = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { W.ls.push(String(k)); return si.call(this, k, v); };
      for (const f of ['put', 'add']) { const o = IDBObjectStore.prototype[f]; IDBObjectStore.prototype[f] = function (...a) { W.idb++; return o.apply(this, a); }; } })()` });
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
    const term = async () => evaluate(p, `(async()=>{const m=await import('./js/termhook.js');return JSON.stringify(m._debug())})()`);
    const ins = (from) => H.ins.slice(from).map((x) => x.d).join('');
    const setBlock = async (b) => { fake.st.terminal = b; await fake.send(fake.prefsMsg()); await sleep(250); await evaluate(p, `import('./js/settings.js').then(m=>{m.render();return true})`); };
    const openTerminal = async ({ login, shots }) => {
      const before = H.calls.length;
      await click('set-term-open');
      await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'intro' && !${$('term-sheetwrap')}.hidden && !${$('term-sh-yes')}.disabled`, 15000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ problems: p.problems, calls: H.calls, errors: H.errors, hook: await term() })); });
      check(H.calls.length === before + 1 && H.calls.at(-1).step === 1, 'step 1 only before the tap ' + tag);
      if (shots) await save(shots + '-1-intro');
      await click('term-sh-yes');
      if (login) {
        await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'login' && !${$('term-sheetwrap')}.hidden`, 10000);
        check(await evaluate(p, `${$('term')}.contains(${$('term-sh-pw')}) && ${$('term-sh-pw')}.type === 'password'`), 'the login card is still inside the page ' + tag);
        await evaluate(p, `${$('term-sh-pw')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
        if (shots) await save(shots + '-2-login');
        await click('term-sh-yes');
        await waitFor(p, `${$('term')}.dataset.phase === 'ready'`, 10000);
        check(H.loginAnswer && H.loginAnswer.sigOk && H.loginAnswer.pw === PASSWORD, 'password sealed + signed, opened only by the host ' + tag);
      } else await waitFor(p, `${$('term')}.dataset.phase === 'ready'`, 10000);
    };
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      const credId = randomBytes(16); H.fa = b64u(credId);
      const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
      await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
        rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p0269-fixture').toString('base64'), signCount: 0 } });
      await evaluate(p, `import('./js/settings.js').then(m=>{m.open();return true})`);
      await waitFor(p, `!${$('settings')}.hidden`);

      // ---- before: a host without "pty" = the line mode (the fallback), with its plain note
      await setBlock(BLOCK({ modes: ['line'] }));
      check(JSON.parse(await term()).info.pty === false, 'modes without "pty" → no full terminal ' + tag);
      await openTerminal({ login: true, shots: 'line' });
      const s1 = H.calls.find((c) => c.step === 1);
      check(s1.mode === 'line' && s1.cols === undefined, 'line mode: step 1 without mode / size ' + tag + JSON.stringify(s1));
      await waitFor(p, `${$('term-out')}.textContent.includes('owner@box')`);
      const ln = await evaluate(p, `({input:!${$('term-input')}.hidden&&${$('term-input')}.getClientRects().length>0,xt:${$('term-xt')}.hidden,note:!${$('term-lineonly')}.hidden?${$('term-lineonly')}.textContent:'',mode:${$('term')}.dataset.mode,xterm:!!document.querySelector('.xterm'),mod:!!document.querySelector('script[src*="termpty.js"]')})`);
      check(ln.input && ln.xt && ln.mode === 'line' && !ln.xterm && !ln.mod && /vim/.test(ln.note), 'line mode: the input line, the 「逐行模式」 note, xterm never loaded ' + tag + JSON.stringify(ln));
      await save('line-3-ready');
      await click('term-end'); await sleep(200);
      await waitFor(p, `${$('term')}.hidden`, 5000);

      // ---- after: "pty" → the full terminal
      H.calls.length = 0;
      await setBlock(BLOCK());
      check(JSON.parse(await term()).info.pty === true, 'modes with "pty" → the full terminal ' + tag);
      await openTerminal({ login: false, shots: 'pty' });
      const p1 = H.calls.find((c) => c.step === 1), p2 = H.calls.find((c) => c.step === 2);
      check(p1.mode === 'pty' && p1.cols >= 20 && p1.cols <= 400 && p1.rows >= 5 && p1.rows <= 200, 'step 1: {mode:"pty", cols, rows} ' + tag + JSON.stringify(p1));
      check(p2.mode === 'pty' && p2.sigOk && p2.fa && !p2.reuse && p2.cols >= 20 && p2.rows >= 5, 'step 2: signed over {"mode":"pty"} + Face ID + the size ' + tag + JSON.stringify(p2));
      await waitFor(p, `!!document.querySelector('#term-xt .xterm') && document.querySelector('#term-xt .xterm-rows')?.textContent.includes('owner@box')`, 8000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ problems: p.problems, errors: H.errors, dbg: await term() })); });
      // 0.26.9 security L-2: the live xterm's OSC 8 links — https only, a new tab with no opener, never confirm()
      {
        const L = await evaluate(p, `(async()=>{const m=await import('./js/termhook.js');const pg=m._page();const opened=[],asked=[];const o=window.open,c=window.confirm;
          window.open=(...a)=>{opened.push(a);return null};window.confirm=(...a)=>{asked.push(a);return true};
          try{return JSON.stringify({link:pg&&pg.dbg.link,res:['javascript:alert(1)','http://agentj.app/x','file:///etc/passwd','https://u:p@agentj.app/','https://agentj.app/docs'].map(u=>pg?pg.link(u):null),opened,asked})}finally{window.open=o;window.confirm=c}})()`);
        const r = JSON.parse(L);
        check(r.link === true, 'L-2: the xterm has our link handler ' + tag + L);
        check(JSON.stringify(r.res) === JSON.stringify([false, false, false, false, true]) && r.opened.length === 1 && r.opened[0][0] === 'https://agentj.app/docs'
          && r.opened[0][1] === '_blank' && r.opened[0][2] === 'noopener,noreferrer' && r.asked.length === 0, 'L-2: https only, no opener, no confirm() ' + tag + L);
      }
      const ui = await evaluate(p, `({form:${$('term-form')}.hidden,keys:${$('term-keys')}.hidden,mode:${$('term')}.dataset.mode,note:${$('term-lineonly')}.hidden,bar:getComputedStyle(${$('term-xkeys')}).display,css:!!document.getElementById('term-xterm-css'),bg:getComputedStyle(document.querySelector('#term-xt .xterm-viewport')||document.body).backgroundColor,mods:[...document.scripts].map(s=>s.src).filter(s=>/term/.test(s)).map(s=>s.replace(location.origin,''))})`);
      check(ui.form && ui.keys && ui.mode === 'pty' && ui.note && ui.css, 'pty: the line input and its keys give way; no 「逐行模式」 note ' + tag + JSON.stringify(ui));
      check(narrow ? ui.bar === 'flex' : ui.bar === 'none', 'extra-keys bar: shown narrow, hidden on a wide desktop ' + tag + JSON.stringify(ui));
      check(ui.mods.every((s) => s.startsWith('/js/')), 'only our own modules ' + tag + JSON.stringify(ui.mods));
      // the resize after the fit: only when the fitted grid differs from what the open said
      await sleep(300);
      const z0 = await evaluate(p, `(()=>{const t=document.querySelector('#term-xt .xterm-rows');return t?t.children.length:0})()`);
      const lastRs = H.resizes.at(-1) || { cols: p2.cols, rows: p2.rows };
      check(lastRs.rows === z0 && H.resizes.every((r) => r.id === H.id), 'the host knows the fitted grid (rows on screen = rows sent) ' + tag + JSON.stringify({ z0, lastRs, rs: H.resizes, p2 }));

      // typing: keyboard → term_in in order
      let n0 = H.ins.length;
      await evaluate(p, `document.querySelector('#term-xt .xterm-helper-textarea').focus()`);
      await p.send('Input.insertText', { text: 'ls' }); await key(p, 'Enter', { text: '\r' });
      await waitFor(p, `document.querySelector('#term-xt .xterm-rows').textContent.includes('notes.md')`, 5000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ ins: H.ins, problems: p.problems, errors: H.errors, act: await evaluate(p, 'document.activeElement && (document.activeElement.className + "#" + document.activeElement.id)'), rows: await evaluate(p, "document.querySelector('#term-xt .xterm-rows').textContent.slice(0,300)") })); });
      check(ins(n0) === 'ls\r', 'typed keys → term_in "ls\\r" ' + tag + JSON.stringify(H.ins.slice(n0)));
      const seqs = H.ins.map((x) => x.seq);
      check(seqs.every((s, i) => s === i + 1) && H.ins.every((x) => x.id === H.id), 'term_in seq 1, 2, 3 … ' + tag + JSON.stringify(seqs));
      // Esc / Tab on the keyboard inside the terminal → the program; the page stays open
      n0 = H.ins.length;
      await key(p, 'Escape'); await key(p, 'Tab'); await sleep(150);
      check(ins(n0) === '\x1b\t' && await evaluate(p, `!${$('term')}.hidden && ${$('term-sheetwrap')}.hidden`), 'Esc / Tab inside the terminal go to the program ' + tag + JSON.stringify(ins(n0)));
      // vim-style full screen + the masked token
      const rows = (await evaluate(p, `document.querySelector('#term-xt .xterm-rows').children.length`));
      await fake.send({ t: 'term_out', id: H.id, seq: 3, d: VIM(rows) });
      await waitFor(p, `document.querySelector('#term-xt .xterm-rows').textContent.includes('-- INSERT --')`, 5000);
      const vim = await evaluate(p, `(()=>{const r=[...document.querySelectorAll('#term-xt .xterm-rows > div')].map(d=>d.textContent);const inv=[...document.querySelectorAll('#term-xt .xterm-rows span')].some(s=>/INSERT/.test(s.textContent)&&(s.className.includes('inverse')||getComputedStyle(s).backgroundColor!=='rgba(0, 0, 0, 0)'));return {first:r[0],second:r[1],last:r[r.length-1],tilde:r.filter(x=>x.trim()==='~').length,prompt:r.some(x=>x.includes('owner@box')),inv}})()`);
      check(vim.first.startsWith('# notes.md') && vim.second.includes('[已隐藏]') && vim.last.includes('-- INSERT --') && vim.tilde >= rows - 4 && !vim.prompt && vim.inv,
        'vim-like escapes render as a screen (alternate screen, colours, reverse video); 「[已隐藏]」 as sent ' + tag + JSON.stringify(vim));
      await save('pty-3-vim');
      // application cursor keys (the program asked) → ↑ = SS3
      await fake.send({ t: 'term_out', id: H.id, seq: 4, d: '\x1b[?1h' }); await sleep(100);
      if (narrow) {
        n0 = H.ins.length;
        for (const k of ['esc', 'tab', 'up', 'left', 'intr', 'eof', 'susp', 'pipe', 'tilde', 'slash']) await click('term-x-' + k);
        await sleep(150);
        check(ins(n0) === '\x1b\t\x1bOA\x1bOD\x03\x04\x1a|~/', 'extra keys → Esc Tab SS3-↑ SS3-← ^C ^D ^Z | ~ / ' + tag + JSON.stringify(ins(n0)));
        await fake.send({ t: 'term_out', id: H.id, seq: 5, d: '\x1b[?1l' }); await sleep(80);
        n0 = H.ins.length;
        await click('term-x-up'); await sleep(80);
        check(ins(n0) === '\x1b[A', 'normal cursor keys → CSI ↑ ' + tag);
        // sticky Ctrl / Alt: one key only
        n0 = H.ins.length;
        await click('term-x-ctrl');
        check(await evaluate(p, `${$('term-x-ctrl')}.getAttribute('aria-pressed')`) === 'true', 'Ctrl stays pressed ' + tag);
        await save('pty-4-ctrl');
        await evaluate(p, `document.querySelector('#term-xt .xterm-helper-textarea').focus()`);
        await p.send('Input.insertText', { text: 'c' }); await p.send('Input.insertText', { text: 'c' }); await sleep(120);
        check(ins(n0) === '\x03c' && await evaluate(p, `${$('term-x-ctrl')}.getAttribute('aria-pressed')`) === 'false', 'Ctrl + c = ^C, then released ' + tag + JSON.stringify(ins(n0)));
        n0 = H.ins.length;
        await click('term-x-alt'); await evaluate(p, `document.querySelector('#term-xt .xterm-helper-textarea').focus()`);
        await p.send('Input.insertText', { text: 'x' }); await sleep(100);
        check(ins(n0) === '\x1bx', 'Alt + x = ESC x ' + tag + JSON.stringify(ins(n0)));
        n0 = H.ins.length;
        await click('term-x-ctrl'); await click('term-x-left'); await sleep(100);
        check(ins(n0) === '\x1b[1;5D', 'Ctrl + ← = CSI 1;5 D ' + tag + JSON.stringify(ins(n0)));
        const small = await evaluate(p, `[...document.querySelectorAll('#term-xkeys button')].map(b=>{const r=b.getBoundingClientRect();return [b.id,Math.round(r.width),Math.round(r.height)]}).filter(x=>x[1]<44||x[2]<44)`);
        check(!small.length, 'every extra key ≥ 44 px ' + tag + JSON.stringify(small));
        const labels = await evaluate(p, `[...document.querySelectorAll('#term-xkeys button')].every(b=>(b.getAttribute('aria-label')||'').length>1)`);
        check(labels, 'every extra key has a spoken label ' + tag);
      }
      // a big paste: ≤ 8 KiB frames, in order, nothing lost
      n0 = H.ins.length;
      const big = 'x'.repeat(9000) + '中'.repeat(3000) + 'y'.repeat(2000);   // 20 000 bytes in UTF-8
      await evaluate(p, `(()=>{const ta=document.querySelector('#term-xt .xterm-helper-textarea');ta.focus();const dt=new DataTransfer();dt.setData('text/plain',${JSON.stringify(big)});ta.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));return 1})()`);
      await sleep(600);
      const frames = H.ins.slice(n0);
      const joined = frames.map((x) => x.d).join('').replace(/^\x1b\[200~|\x1b\[201~$/g, '');
      check(joined === big && frames.length >= 3 && frames.every((x) => x.bytes <= 8192), 'a 20 000-byte paste → ≤ 8 KiB frames, re-joined equal ' + tag + JSON.stringify(frames.map((x) => x.bytes)));
      // back to the shell; the dangerous-line confirm over the terminal
      await fake.send({ t: 'term_out', id: H.id, seq: 6, d: '\x1b[?1049l' + PROMPT + 'rm -rf build', prompt: true });
      await confirm(7, 'rm -rf build');
      await waitFor(p, `${$('term-sheetwrap')}.dataset.kind === 'confirm' && !${$('term-sheetwrap')}.hidden`);
      check(await evaluate(p, `${$('term-sh-pre')}.textContent`) === 'rm -rf build' && await evaluate(p, `!!document.querySelector('#term-xt .xterm')`), 'confirm sheet over the live terminal ' + tag);
      await save('pty-5-confirm');
      n0 = H.ins.length;
      const nl = H.lines.length;
      await click('term-sh-no'); await sleep(200);
      check(ins(n0) === '\u0015' && H.lines.length === nl, '取消 → Ctrl+U to the PTY, no line ' + tag + JSON.stringify(H.ins.slice(n0)));
      await confirm(8, 'rm -rf build');
      await waitFor(p, `${$('term-sheetwrap')}.dataset.kind === 'confirm' && !${$('term-sheetwrap')}.hidden`);
      await click('term-sh-yes');
      await waitFor(p, `${$('term-sheetwrap')}.hidden`, 8000)
        .catch((e) => { throw new Error(e.message + ' ' + JSON.stringify({ lines: H.lines, errors: H.errors, problems: p.problems })); });
      await sleep(150);
      const cl = H.lines.at(-1);
      check(cl && cl.signed && cl.sigOk && cl.fa && cl.seq === 8 && cl.line === 'rm -rf build', '运行 → the signed + asserted line ' + tag + JSON.stringify(cl));
      await fake.send({ t: 'term_out', id: H.id, seq: 9, d: '\r\nremoved build/\r\n' + PROMPT, prompt: true });
      await sleep(150);
      await save('pty-6-after');
      // copy output = the terminal's own text
      const copied = await evaluate(p, `(()=>{const r=document.querySelector('#term-xt .xterm-rows').textContent;return r.includes('removed build/')})()`);
      check(copied, 'output after the confirm ' + tag);
      // last (it leaves the emulated viewport offset): the window changes (soft keyboard / rotation stand-in) → term_resize with the new grid
      const nr = H.resizes.length;
      await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h0 - 300, deviceScaleFactor: 1, mobile: narrow });
      await sleep(700);
      const rs = H.resizes.slice(nr);
      const fitted = await evaluate(p, `document.querySelector('#term-xt .xterm-rows').children.length`);
      check(rs.length >= 1 && rs.at(-1).rows === fitted && rs.at(-1).rows < lastRs.rows && rs.at(-1).cols >= 20, 'smaller window → term_resize with the fitted rows ' + tag + JSON.stringify({ rs, fitted }));
      await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h0, deviceScaleFactor: 1, mobile: narrow });
      await sleep(600);
      // storage: nothing written while the terminal is open
      const st = await evaluate(p, `({ls:window.__w.ls.filter(k=>/term|pty|xterm/i.test(k)),idb:window.__w.idb})`);
      check(!st.ls.length, 'nothing about the terminal in localStorage ' + tag + JSON.stringify(st));
      // the host closes it → the page closes, the xterm is gone
      await fake.send({ t: 'term_close', id: H.id, why: 'idle', minutes: 3 });
      await waitFor(p, `${$('term')}.hidden`, 6000);
      check(await evaluate(p, `!document.querySelector('.xterm') && !document.getElementById('term-xkeys')`), 'closed → xterm and its keys disposed ' + tag);
      check(!H.errors.length, 'fake host saw no malformed message ' + tag + ' ' + H.errors.join(' | '));
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS p0269-term ' + tag);
    } finally { await p.dispose(); }
  }
  fake.st.onApp = null; fake.st.terminal = null;
  console.log(`p0269-term: ${checks} checks; shots → ${out}`);
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP0269Term({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
