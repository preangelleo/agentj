// P408 (PROTOCOL §38) Settings → 安全 → 「网页终端」 and the terminal page on the real page against the local fake host (real
// Noise; the §8 control signatures, the §11 seal of the login password and of the PRF output, and every Face ID challenge
// checked like the host does), own headless Chromium (never :9222) with a CDP virtual platform authenticator as Face ID's
// stand-in — with `hasPrf` (a passkey made on the page with the prf extension) for the light runs, without for the dark ones.
// Checks:
//   · the row: older host (no block) → none · off → the command to run at the computer + ⧉, no switch · on, not on the list →
//     `agentj terminal allow <this device>` · os:false → the row stays (P417 #185: 暂不支持，下一版支持) · pk:false → 设置 Face ID first · allowed → 打开网页终端 + the password mode in plain words
//   · open → intro (最多 N 分钟) → Face ID (assertion over the host's challenge; PRF sealed iff the authenticator gave one) →
//     ctl_pending → the login card INSIDE the page (host-origin line, masked field, never the chat's sudo card) → sealed +
//     signed elev_answer → term_ready; a second open → no login card
//   · lines: harmless → output as text (\r handled, HTML inert) · control characters / TAB → nothing sent · Ctrl+C / Ctrl+\ →
//     term_keys int / quit · ↑ = this session's history · dangerous → confirm sheet: 取消 sends nothing, 运行 sends a signed +
//     asserted line · sudo sheet (argv, `differs` warning) → term_sudo_ok signed (+PRF) / term_sudo_no · the 「运行中的程序」 hint
//   · 「在终端里打开」 prefills the input and sends nothing · host close reasons → 「终端已关闭（…）」 → back to Settings ·
//     「结束」 / Esc · the link dropped → 「连接断了，终端已关闭」 + 紧急修复 · no passkey → 「设置 Face ID」, nothing sent ·
//     term_forget signed (no Face ID) · nothing written to localStorage / IndexedDB while a terminal is open · no console errors
//   · zh / en × light / dark at 390 px + zh 1440 px; shots → reports/qa/p408/web/ (P408_OUT)
//   · P417 #185: a computer browser (1440, fine pointer) → 开启并打开网页终端 → intro → Face ID over {"mode":"enable"} → the login
//     card → the terminal opens in this window; no passkey → 设置 Face ID; an older / unknown host kind → only the command; a
//     phone (iPhone user agent + touch, 390) → 「网页终端只能在电脑浏览器里操控」, no button — zh + en shots (p417-*)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateKeyPairSync, randomBytes, webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect, key } from './browser.mjs';
import { controlMessage, controlObject, elevateFields, elevateDigest, elevateMessage, b64u, unb64u, SEAL_CONTEXT } from '../../protocol/wire.js';

const subtle = webcrypto.subtle;
const OUT = process.env.P408_OUT || 'reports/qa/p408/web';
const enc = new TextEncoder();
const hex = (n) => randomBytes(n).toString('hex');
const sha = async (s) => Buffer.from(await subtle.digest('SHA-256', typeof s === 'string' ? enc.encode(s) : s)).toString('hex');
const $ = (id) => `document.getElementById('${id}')`;
// P559 naming: headless Chromium is Linux → Touch ID; Face ID only on iPhone / iPad (the P417 iPhone pass checks only s.phone)
const L = {
  zh: { off: '网页终端没有开启。要用的话，在 Agent J 主机上运行下面这行：', os: '这台 Agent J 主机的系统暂不支持网页终端，下一版支持。', setup: '先在上面设置 Touch ID', nopkRow: '先在上面设置 Touch ID',
    phone: '因为安全性和便捷性的原因，网页终端只能在电脑浏览器里操控。', enable: '开启并打开网页终端', offDesk: '网页终端还没开启。可以直接在这里开启', notMeDesk: '这个浏览器还不能用网页终端。', repair: '重新配对这台遥控器，就能直接在这里开启。',
    orCli: '也可以在 Agent J 主机上运行下面这行：', introEnable: '再输入一次 Agent J 主机的登录密码', notMe: '这台设备不在允许名单里，在 Agent J 主机上运行下面这行：', open: '打开网页终端',
    none: '第一次打开时要输入一次 Agent J 主机登录密码。', prf: 'Agent J 主机登录密码已加密保存，只有你扫脸才能解开。', intro: '最多 15 分钟', fa: '用 Touch ID 确认',
    closed: (w) => `终端已关闭（${w}）`, why: { timeout: '时间到了', idle: '太久没有输入', estop: '已全部停下', relay_lost: '连接断了', owner: '你结束了它' },
    dropped: '连接断了，终端已关闭。', nopk: '打开网页终端前要先用 Touch ID 确认，这台手机还没设置 Touch ID。', inTerm: '在终端里打开', differs: '注意：' },
  en: { off: 'The web terminal is off. To use it, run this line on the Agent J host:', os: "This Agent J host's system does not support the web terminal yet; the next version will.", setup: 'Set up Touch ID above first', nopkRow: 'Set up Touch ID above first',
    phone: 'For safety and convenience, the web terminal can only be used from a computer browser.', enable: 'Switch on and open', offDesk: 'The web terminal is off. You can switch it on right here', notMeDesk: 'This browser cannot use the web terminal yet.', repair: 'Pair this remote again to switch it on right here.',
    orCli: 'Or run this line on the Agent J host:', introEnable: "type the Agent J host's login password once", notMe: 'This device is not on the allowed list. Run this line on the Agent J host:',
    open: 'Open web terminal', none: 'The first time you open it, you type your Agent J host login password once.',
    prf: 'Your Agent J host login password is stored encrypted; only your Touch ID can unlock it.', intro: 'At most 15 minutes', fa: 'Confirm with Touch ID',
    closed: (w) => `Terminal closed (${w})`, why: { timeout: 'time is up', idle: 'no input for too long', estop: 'everything was stopped', relay_lost: 'the connection dropped', owner: 'you ended it' },
    dropped: 'The connection dropped; the terminal is closed.', nopk: 'Opening the web terminal needs Touch ID first, and this phone has not set it up yet.', inTerm: 'Open in terminal', differs: 'Careful:' },
};
const PASSWORD = 'correct horse 7!';
const OUTPUT = 'a.txt  b.txt\n<b id="inj">x</b>\nprogress 10%\rprogress 100%\n';
const PROMPT = 'owner@box:~/work$ ';
const BLOCK = (x = {}) => ({ on: true, os: true, me: true, web_ok: true, sudo: null, minutes: 15, idle_s: 300, open: false, ...x });

/** The host's side of §11's seal: X25519 + HKDF + AES-GCM with the AAD the phone used. */
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

export async function runP408({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');   // WebAuthn wants a domain rp id

  // ---- the host side of §38 (what terminal.py + serve do, minus the shell)
  const H = { fa: null, passkey: true, prf: false, cached: false, calls: [], lines: [], keys: [], closes: [], sudoOk: [], sudoNo: [], forgets: [],
    login: null, open: null, ch: new Map(), id: null, conn: null, errors: [] };
  const dev = () => fake.devs[0];
  const faOk = (fa, ch) => { try { const cd = JSON.parse(Buffer.from(fa.cd, 'base64url').toString()); return cd.type === 'webauthn.get' && cd.challenge === ch && fa.id === H.fa; } catch { return false; } };
  const ready = async (send, r) => {
    H.id = hex(8);
    await send({ t: 'term_ready', r, id: H.id, expires_at: Math.floor(Date.now() / 1000) + 15 * 60, idle_s: 300, sudo: H.prf ? 'prf' : 'local', cwd: '~/work' });
    await send({ t: 'term_out', id: H.id, seq: 1, data: PROMPT, prompt: true });
  };
  fake.st.onApp = async (c, m, send) => {
    try {
      if (m.t === 'term_open' && !m.sig) {                         // step 1: the host's nonce, challenge, salt, one-time key
        const mode = m.mode === 'enable' ? 'enable' : 'line';      // P417 #185: a computer browser switching it on
        H.calls.push({ step: 1, mode });
        if (!H.passkey) return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'no_passkey' }), true;
        const n = hex(16), k = await oneTimeKey(), salt = randomBytes(32), digest = await sha(controlObject('term_open', { mode }));
        const ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode(['p408', n, digest].join('\n')))));
        H.ch.set(n, { k, salt, ch, used: false, mode });
        return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'passkey_required', n, fa: H.fa, fa_ch: ch, salt: b64u(salt), epk: b64u(k.pub) }), true;
      }
      if (m.t === 'term_open') {                                   // step 2: signature over OUR nonce + Face ID + (PRF)
        const x = H.ch.get(m.n), mode = x ? x.mode : 'line';
        const sigOk = await fake.verifyControl(c, 'term_open', m, { mode }) && (m.mode || 'line') === mode;
        const fa = !!x && faOk(m.fa, x.ch);
        let prf = null;
        if (x && m.pct) prf = await unseal(x.k.priv, x.k.pub, m.pepk, m.pct, [fake.channel, dev().id, m.n, 'term_prf', m.n, await sha(mode)]);
        H.calls.push({ step: 2, sigOk, fa, prf: prf ? prf.length : 0, reuse: !!x?.used, mode });
        if (!x || x.used || !sigOk || !fa) return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: !x || x.used ? 'replay' : !sigOk ? 'bad_signature' : 'passkey_failed' }), true;
        x.used = true;
        await send({ t: 'ctl_pending', r: m.r, action: 'term_open', timeout_ms: 180000 });
        if (H.cached) { await ready(send, m.r); return true; }
        const k = await oneTimeKey();
        H.login = { m: { t: 'elev', id: hex(16), kind: 'login', n: hex(16), epk: b64u(k.pub), ttl: 120, tries: 3, term: m.r, fa: H.fa }, k, r: m.r, send };
        await send(H.login.m);
        return true;
      }
      if (m.t === 'elev_answer' && H.login && m.id === H.login.m.id) {   // the login card, answered like a sudo card
        const L_ = H.login, d = dev();
        const digest = await elevateDigest('login', elevateFields(L_.m));
        const ctSha = m.ok && m.ct ? await sha(unb64u(m.ct)) : null;
        const key_ = await subtle.importKey('raw', unb64u(d.sk), { name: 'Ed25519' }, false, ['verify']);
        const sigOk = await subtle.verify({ name: 'Ed25519' }, key_, unb64u(m.sig), elevateMessage(fake.channel, d.id, m.id, 'login', m.ok ? 'allow' : 'deny', L_.m.n, m.ts, digest, ctSha));
        const faCh = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode(['agentjarvis/passkey/elevate/v1', fake.channel, d.id, m.id, 'login', 'approve', L_.m.n, digest].join('\n')))));
        const pw = m.ok ? new TextDecoder().decode(await unseal(L_.k.priv, L_.k.pub, m.epk, m.ct, [fake.channel, d.id, m.id, 'login', L_.m.n, digest])) : null;
        H.loginAnswer = { sigOk, fa: !!m.fa && faOk(m.fa, faCh), pw, ok: m.ok };
        H.login = null;
        await send({ t: 'elev_done', id: m.id, result: m.ok ? 'done' : 'denied' });
        if (!m.ok) { await send({ t: 'ctl_res', r: L_.r, action: 'term_open', ok: false, why: 'cancelled' }); return true; }
        H.cached = true;
        await ready(send, L_.r);
        return true;
      }
      if (m.t === 'term_line') {
        const signed = typeof m.sig === 'string';
        const rec = { id: m.id, seq: m.seq, line: m.line, signed };
        if (signed) {
          const x = H.confirm;
          rec.sigOk = !!x && m.n === x.n && await fake.verifyControl(c, 'term_line', m, { id: m.id, line: m.line });
          rec.fa = !!x && faOk(m.fa, x.fa_ch);
          H.confirm = null;
        }
        H.lines.push(rec);
        if (m.id !== H.id) return true;
        if (!signed && /^rm -rf /.test(m.line)) {
          H.confirm = { n: hex(16) }; H.confirm.fa_ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('confirm-' + H.confirm.n))));
          await send({ t: 'term_confirm', id: m.id, seq: m.seq, line: m.line, why: '删除文件', why_en: 'deletes files', n: H.confirm.n, fa_ch: H.confirm.fa_ch });
          return true;
        }
        if (m.line === 'ls') await send({ t: 'term_out', id: m.id, seq: 2, data: OUTPUT + PROMPT, prompt: true });
        else if (m.line === 'python3') await send({ t: 'term_out', id: m.id, seq: 3, data: 'Python 3.13\n>>> ' });   // a program in front: no prompt marker
        else if (signed) await send({ t: 'term_out', id: m.id, seq: 4, data: 'removed build/\n' + PROMPT, prompt: true });
        return true;
      }
      if (m.t === 'term_keys') { H.keys.push(m.key); return true; }
      if (m.t === 'term_close') { H.closes.push(m.id); return true; }
      if (m.t === 'term_sudo_ok') {
        const x = H.sudo;
        const sigOk = !!x && m.n === x.n && await fake.verifyControl(c, 'term_sudo', m, { id: m.id, rid: m.rid });
        let prf = 0;
        if (x && m.pct) prf = (await unseal(x.k.priv, x.k.pub, m.pepk, m.pct, [fake.channel, dev().id, m.n, 'term_prf', m.n, await sha(`${m.id}\n${m.rid}`)])).length;
        H.sudoOk.push({ rid: m.rid, sigOk, fa: !!x && faOk(m.fa, x.fa_ch), prf });
        return true;
      }
      if (m.t === 'term_sudo_no') { H.sudoNo.push(m.rid); return true; }
      if (m.t === 'term_forget') {
        const sigOk = await fake.verifyControl(c, 'term_forget', m, {});
        H.forgets.push(sigOk);
        await send({ t: 'ctl_res', r: m.r, action: 'term_forget', ok: sigOk, ...(sigOk ? {} : { why: 'bad_signature' }) });
        return true;
      }
    } catch (e) { H.errors.push(String(e && e.stack || e)); }
    return false;
  };
  const sudoAsk = async (differs) => {
    const k = await oneTimeKey();
    H.sudo = { rid: hex(8), n: hex(16), k };
    H.sudo.fa_ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('sudo-' + H.sudo.n))));
    await fake.send({ t: 'term_sudo', id: H.id, rid: H.sudo.rid, argv: differs ? ['/usr/bin/apt', 'remove', 'openssh-server'] : ['/usr/bin/apt', 'install', 'htop'],
      line: 'sudo apt install htop', differs, n: H.sudo.n, fa_ch: H.sudo.fa_ch, salt: b64u(randomBytes(32)), epk: b64u(k.pub) });
  };

  const combos = [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 390], ['zh', 'light', 1440], ['zh', 'dark', 1440]];
  for (const [lang, theme, w] of combos) {
    const s = L[lang], tag = `${lang}-${theme}-${w}`, full = w === 390 && lang === 'zh' && theme === 'light';
    const withPrf = theme === 'light';
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang, theme } }; fake.st.terminal = null; fake.st.runCmds = ['doctor', 'status'];
    Object.assign(H, { passkey: true, prf: withPrf, cached: false, calls: [], lines: [], keys: [], closes: [], sudoOk: [], sudoNo: [], forgets: [], login: null, id: null, errors: [], loginAnswer: null });
    const p = await newPage(B, w, w === 390 ? 844 : 900, theme, { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    // count every storage write the page makes (the terminal must make none)
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => { const W = window.__p408w = { ls: [], idb: 0 };
      const si = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { W.ls.push(String(k) + '=' + String(v).slice(0, 200)); return si.call(this, k, v); };
      for (const f of ['put', 'add']) { const o = IDBObjectStore.prototype[f]; IDBObjectStore.prototype[f] = function (...a) { W.idb++; return o.apply(this, a); }; } })()` });
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: withPrf } });
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${name}.png`), await shoot(p, ''));
    const click = async (id) => {
      await evaluate(p, `${$(id)}.scrollIntoView({block:'center'})`); await sleep(60);
      const r = await rect(p, '#' + id);
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    };
    const typeLine = async (text, enter = true) => {
      await evaluate(p, `${$('term-input')}.focus()`);
      if (text) await p.send('Input.insertText', { text });
      if (enter) await key(p, 'Enter', { text: '\r' });
      await sleep(120);
    };
    const shown = (id) => evaluate(p, `!!${$(id)} && !${$(id)}.hidden && ${$(id)}.getClientRects().length > 0`);
    const sheetKind = () => evaluate(p, `${$('term-sheetwrap')}?.hidden === false ? ${$('term-sheetwrap')}.dataset.kind : ''`);
    const pageOpen = () => evaluate(p, `!!${$('term')} && !${$('term')}.hidden`);
    const audit = async (label, sel) => {
      const a = await evaluate(p, `(()=>{const small=[...document.querySelectorAll(${JSON.stringify(sel)})].filter(e=>e.getClientRects().length&&!e.closest('[hidden]')).map(e=>{const r=e.getBoundingClientRect();return {id:e.id||e.className,w:Math.round(r.width),h:Math.round(r.height)}}).filter(x=>x.h<44||x.w<44);return {small,over:document.documentElement.scrollWidth>innerWidth+1}})()`);
      check(!a.small.length && !a.over, `${label}: taps ≥ 44 px, no overflow ${tag} ${JSON.stringify(a)}`);
    };
    /** open → intro → Face ID → (login card) → ready */
    const openTerminal = async (via = 'set-term-open', { expectLogin, shots = false } = {}) => {
      const before = H.calls.length;
      await click(via);
      await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'intro' && !${$('term-sheetwrap')}.hidden && !${$('term-sh-yes')}.disabled`, 10000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ problems: p.problems, calls: H.calls, errors: H.errors, dbg: await evaluate(p, `import('./js/termhook.js').then(m=>JSON.stringify(m._debug()))`), html: await evaluate(p, `document.getElementById('term')?.outerHTML.slice(0,600) || 'no #term'`) })); });
      check(H.calls.length === before + 1 && H.calls.at(-1).step === 1, 'step 1 only: nothing signed before the tap ' + tag);
      if (shots) {
        check((await evaluate(p, `${$('term-sh-text')}.textContent`)).includes(s.intro) && await evaluate(p, `${$('term-sh-yes')}.textContent`) === s.fa, 'intro names the limit; 用 Face ID 确认 ' + tag);
        await save('4-intro');
      }
      await click('term-sh-yes');
      if (expectLogin) {
        await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'login' && !${$('term-sheetwrap')}.hidden`, 10000);
        const c2 = H.calls.at(-1);
        check(c2.step === 2 && c2.sigOk && c2.fa && !c2.reuse, 'step 2: signed over the host nonce + a real assertion over its challenge ' + tag);
        check(withPrf ? c2.prf === 32 : c2.prf === 0, `PRF output ${withPrf ? 'sealed to the host (32 B opened)' : 'absent without an authenticator PRF'} ` + tag);
        const lg = await evaluate(p, `({type:${$('term-sh-pw')}.type,ac:${$('term-sh-pw')}.autocomplete,origin:${$('term-sh-origin')}.textContent,elev:!!document.getElementById('elev')&&!document.getElementById('elev').hidden,inPage:${$('term')}.contains(${$('term-sh-pw')})})`);
        check(lg.type === 'password' && lg.ac === 'off' && lg.origin.length > 8 && !lg.elev && lg.inPage, 'login card inside the page, masked, host-origin line, not the chat card ' + tag + JSON.stringify(lg));
        await click('term-sh-yes');                                            // empty → says so, nothing sent
        await waitFor(p, `!${$('term-sh-bad')}.hidden`);
        check(!H.loginAnswer, 'empty password → nothing sent ' + tag);
        await evaluate(p, `${$('term-sh-pw')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
        if (shots) await save('5-login');
        await click('term-sh-yes');
        await waitFor(p, `${$('term')}.dataset.phase === 'ready'`, 10000);
        check(H.loginAnswer && H.loginAnswer.sigOk && H.loginAnswer.fa && H.loginAnswer.pw === PASSWORD, 'password sealed + signed + Face ID, opened only by the host ' + tag);
        check(!(await evaluate(p, `document.documentElement.outerHTML.includes(${JSON.stringify(PASSWORD)}) || ${$('term-sh-pw')}.value !== ''`)), 'password gone from the page ' + tag);
      } else {
        await waitFor(p, `${$('term')}.dataset.phase === 'ready'`, 10000);
        check(H.calls.at(-1).sigOk && H.calls.at(-1).fa, 'second open: signed + asserted ' + tag);
      }
      await waitFor(p, `${$('term-out')}.textContent.includes(${JSON.stringify(PROMPT.trim())})`);
    };
    const hostClose = async (why) => {
      await fake.send({ t: 'term_close', id: H.id, why, minutes: 3 });
      await waitFor(p, `${$('term-status')}.textContent === ${JSON.stringify(s.closed(s.why[why]))}`, 5000);
      await waitFor(p, `${$('term')}.hidden && !${$('settings')}.hidden`, 5000);
      check(await evaluate(p, `${$('set-term-last')}.textContent`) === s.closed(s.why[why]), `host close ${why} → closed line, back to Settings ` + tag);
      check(await evaluate(p, `${$('term-out')}.textContent === '' && ${$('term-input')}.value === ''`), 'output and input wiped from memory ' + tag);
    };
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      assert.equal(await evaluate(p, `document.documentElement.getAttribute('data-theme')`), theme);
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      // the passkey: made on the page with the prf extension (PRF run) or injected (no PRF)
      if (withPrf) {
        H.fa = await evaluate(p, `navigator.credentials.create({publicKey:{rp:{id:'localhost',name:'Agent J'},user:{id:new Uint8Array(16).fill(3),name:'p408',displayName:'p408'},challenge:new Uint8Array(32),pubKeyCredParams:[{type:'public-key',alg:-7}],authenticatorSelection:{residentKey:'required',userVerification:'required',authenticatorAttachment:'platform'},extensions:{prf:{}}}}).then(c=>{const b=new Uint8Array(c.rawId);let s='';for(const x of b)s+=String.fromCharCode(x);return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'')})`);
      } else {
        const credId = randomBytes(16); H.fa = b64u(credId);
        const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
        await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
          rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p408-fixture').toString('base64'), signCount: 0 } });
      }
      const myId = fake.devs[0].id;
      const setBlock = async (b) => { fake.st.terminal = b; await fake.send(fake.prefsMsg()); await sleep(250); await evaluate(p, `import('./js/settings.js').then(m=>{m.render();return true})`); };
      await evaluate(p, `import('./js/settings.js').then(m=>{m.open();return true})`);
      await waitFor(p, `!${$('settings')}.hidden`);

      // ---- the row: older host → none · off · not on the list · os:false → none · allowed
      check(await evaluate(p, `${$('set-term-row')}.hidden`), 'older host (no block) → no row ' + tag);
      await setBlock(BLOCK({ on: false, me: false }));
      await waitFor(p, `!${$('set-term-row')}.hidden`);
      const off = await evaluate(p, `({st:${$('set-term-state')}.textContent,cmd:${$('set-term-cmd')}.textContent,copy:!!document.querySelector('[data-copy="set-term-cmd"]'),acts:${$('set-term-acts')}.hidden,sw:!!${$('set-term-row')}.querySelector('[role=switch]'),inSafety:!!${$('set-term-row')}.closest('#set-s-safety')})`);
      check(off.st === s.off && off.cmd === 'agentj terminal enable' && off.copy && off.acts && !off.sw && off.inSafety, 'off → the command to run at the computer, ⧉, no switch, in 安全 ' + tag + JSON.stringify(off));
      await evaluate(p, `${$('set-term-row')}.scrollIntoView({block:'center'})`); await save('1-off');
      await setBlock(BLOCK({ me: false }));
      check(await evaluate(p, `${$('set-term-state')}.textContent`) === s.notMe && await evaluate(p, `${$('set-term-cmd')}.textContent`) === `agentj terminal allow ${myId}`, 'not on the list → allow <this device> ' + tag);
      if (full) { await evaluate(p, `${$('set-term-row')}.scrollIntoView({block:'center'})`); await save('2-not-allowed'); }
      // P417 #185 (P437: Windows only — macOS is supported): an unsupported host keeps the row — 「暂不支持，下一版支持」, no command, no button
      await setBlock(BLOCK({ os: false, on: false, me: false }));
      const os = await evaluate(p, `({hid:${$('set-term-row')}.hidden,st:${$('set-term-state')}.textContent,cmd:${$('set-term-cmdline')}.hidden,acts:${$('set-term-acts')}.hidden,pk:${$('set-term-pk')}.hidden})`);
      check(!os.hid && os.st.startsWith(s.os) && os.cmd && os.acts && os.pk, 'os:false → the row stays: not yet, next version ' + tag + JSON.stringify(os));
      await evaluate(p, `${$('set-term-row')}.scrollIntoView({block:'center'})`); await save('4-os-not-yet');
      // P417 #185: allowed, but this phone has no passkey on the computer → 「设置 Face ID」 instead of 「打开网页终端」
      await setBlock(BLOCK({ pk: false }));
      const np = await evaluate(p, `({acts:${$('set-term-acts')}.hidden,pk:!${$('set-term-pk')}.hidden,txt:${$('set-term-pk')}.textContent,btn:${$('set-term-faceid')}.textContent})`);
      check(np.acts && np.pk && np.txt.includes(s.nopkRow) && np.btn === s.setup, 'pk:false → set up Face ID first, no open button ' + tag + JSON.stringify(np));
      await evaluate(p, `${$('set-term-row')}.scrollIntoView({block:'center'})`); await save('5-no-faceid');
      await setBlock(BLOCK({ on: false, me: false, pk: false }));
      check(await evaluate(p, `!${$('set-term-pk')}.hidden && ${$('set-term-cmd')}.textContent === 'agentj terminal enable'`), 'off + pk:false → the command and the Face ID line ' + tag);
      await setBlock(BLOCK());
      const ok = await evaluate(p, `({st:${$('set-term-state')}.textContent,cmd:${$('set-term-cmdline')}.hidden,acts:!${$('set-term-acts')}.hidden,open:${$('set-term-open')}.textContent,forget:${$('set-term-forget')}.hidden,note:${$('set-term-note')}.textContent})`);
      check(ok.st === s.none && ok.cmd && ok.acts && ok.open === s.open && ok.forget && /15/.test(ok.note), 'allowed → entry button + mode in plain words, no forget yet ' + tag + JSON.stringify(ok));
      await audit('row', '#set-term-row button');
      await evaluate(p, `${$('set-term-row')}.scrollIntoView({block:'center'})`); await save('3-allowed');
      check(!(await evaluate(p, `!!document.querySelector('script[src*="terminal.js"]')`)), 'the terminal module is not loaded before the first open ' + tag);

      // ---- open (first time: the login card) — storage writes counted from here
      await evaluate(p, `window.__p408w.ls.length = 0; window.__p408w.idb = 0; true`);
      await openTerminal('set-term-open', { expectLogin: true, shots: true });
      const chrome = await evaluate(p, `({rec:!${$('term-rec')}.hidden&&${$('term-rec')}.textContent.length>0,left:${$('term-left')}.textContent,end:!${$('term-end')}.hidden,ac:${$('term-input')}.getAttribute('autocapitalize'),acr:${$('term-input')}.getAttribute('autocorrect'),sp:${$('term-input')}.spellcheck,nopw:${$('term-nopw')}.textContent,bg:getComputedStyle(${$('term')}).backgroundColor,mono:getComputedStyle(${$('term-out')}).fontFamily})`);
      check(chrome.rec && /1[45]:\d\d/.test(chrome.left) && chrome.end && chrome.ac === 'off' && chrome.acr === 'off' && chrome.sp === false && chrome.nopw.length > 4, 'ready: 录像中, time left, 结束, input attrs, password warning ' + tag + JSON.stringify(chrome));
      check(chrome.bg === 'rgb(44, 43, 38)' && /mono/i.test(chrome.mono), 'dark page (Noir), monospace output ' + tag);
      await audit('terminal', '#term button, #term input');

      // harmless line → output as text, \r overwrite, HTML inert
      await typeLine('ls');
      await waitFor(p, `${$('term-out')}.textContent.includes('progress 100%')`)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ problems: p.problems, lines: H.lines, errors: H.errors, out: await evaluate(p, `${$('term-out')}.textContent`), dbg: await evaluate(p, `JSON.stringify([...document.scripts].map(s=>s.src))`) })); });
      const o = await evaluate(p, `({t:${$('term-out')}.textContent,inj:!!document.getElementById('inj')})`);
      check(H.lines.at(-1).line === 'ls' && H.lines.at(-1).seq === 1 && !H.lines.at(-1).signed, 'term_line {id, seq, line} ' + tag);
      check(o.t.includes('<b id="inj">x</b>') && !o.inj && !o.t.includes('progress 10%\n') && o.t.includes('a.txt  b.txt'), 'output is text, \\r overwrites, HTML inert ' + tag);
      await save('6-output');
      // control characters / TAB: nothing sent
      const nLines = H.lines.length;
      await evaluate(p, `${$('term-input')}.value = 'echo a\\tb'; ${$('term-form')}.requestSubmit(); true`); await sleep(150);
      check(H.lines.length === nLines && await shown('term-bad'), 'TAB → not sent, says why ' + tag);
      await evaluate(p, `${$('term-input')}.value = ''; true`);
      // keys
      await click('term-k-int'); await click('term-k-quit'); await sleep(150);
      check(H.keys.join() === 'int,quit', 'Ctrl+C → int, Ctrl+\\ → quit ' + tag);
      await click('term-k-up');
      check(await evaluate(p, `${$('term-input')}.value`) === 'ls', '↑ = this session\'s last line ' + tag);
      await click('term-k-down'); await click('term-k-ins0');
      check(await evaluate(p, `${$('term-input')}.value`) === '|', '↓ back to the draft; | inserted ' + tag);
      await evaluate(p, `${$('term-input')}.value = ''; true`);

      // dangerous line → confirm: 取消 sends nothing; 运行 sends a signed + asserted line
      await typeLine('rm -rf build');
      await waitFor(p, `${$('term-sheetwrap')}.dataset.kind === 'confirm' && !${$('term-sheetwrap')}.hidden`);
      check(await evaluate(p, `${$('term-sh-pre')}.textContent`) === 'rm -rf build' && (await evaluate(p, `${$('term-sh-text')}.textContent`)).includes(lang === 'en' ? 'deletes files' : '删除文件'), 'confirm shows the line + reason ' + tag);
      await save('7-confirm');
      const nl2 = H.lines.length;
      await click('term-sh-no'); await sleep(200);
      check(H.lines.length === nl2 && await sheetKind() === '', '取消 → nothing sent ' + tag);
      await typeLine('rm -rf build');
      await waitFor(p, `${$('term-sheetwrap')}.dataset.kind === 'confirm' && !${$('term-sheetwrap')}.hidden`);
      await key(p, 'Escape'); await sleep(150);
      check(await sheetKind() === '' && await pageOpen(), 'Esc closes the sheet (= 取消), the page stays ' + tag);
      await typeLine('rm -rf build');
      await waitFor(p, `${$('term-sheetwrap')}.dataset.kind === 'confirm' && !${$('term-sheetwrap')}.hidden`);
      await click('term-sh-yes');
      await waitFor(p, `${$('term-out')}.textContent.includes('removed build/')`, 10000);
      const sl = H.lines.at(-1);
      check(sl.signed && sl.sigOk && sl.fa && sl.line === 'rm -rf build', '运行 → signed (term_line {id,line}) + Face ID over the host challenge ' + tag);

      // sudo: plain, then `differs` with its warning; 不允许 → term_sudo_no; 允许 → signed (+PRF)
      await sudoAsk(false);
      await waitFor(p, `${$('term-sheetwrap')}.dataset.kind === 'sudo' && !${$('term-sheetwrap')}.hidden`);
      check(await evaluate(p, `${$('term-sh-pre')}.textContent`) === '/usr/bin/apt install htop' && !(await shown('term-sh-warn')), 'sudo sheet: argv, no warning ' + tag);
      const rid1 = H.sudo.rid;
      await click('term-sh-no'); await sleep(150);
      check(H.sudoNo.includes(rid1), '不允许 → term_sudo_no ' + tag);
      await sudoAsk(true);
      await waitFor(p, `${$('term-sheetwrap')}.dataset.kind === 'sudo' && !${$('term-sheetwrap')}.hidden`);
      check(await shown('term-sh-warn') && (await evaluate(p, `${$('term-sh-warn')}.textContent`)).startsWith(s.differs), 'differs → a visible warning ' + tag);
      await save('8-sudo-differs');
      await click('term-sh-yes');
      for (let i = 0; i < 100 && !H.sudoOk.length; i++) await sleep(50);
      check(H.sudoOk.length === 1 && H.sudoOk[0].sigOk && H.sudoOk[0].fa && H.sudoOk[0].prf === (withPrf ? 32 : 0), 'term_sudo_ok signed + Face ID ' + (withPrf ? '+ PRF sealed ' : '') + tag + JSON.stringify(H.sudoOk));

      // a program in front: the hint
      await typeLine('python3');
      await waitFor(p, `${$('term-out')}.textContent.includes('>>> ')`);
      await typeLine('print(1)', false);
      check(await shown('term-prog'), 'program in front → 「会交给正在运行的程序」 hint ' + tag);
      if (full) await save('9-program-hint');
      await evaluate(p, `${$('term-input')}.value = ''; true`);

      // nothing stored while the terminal was open
      const wr = await evaluate(p, `window.__p408w`);
      check(!wr.ls.length && wr.idb === 0, 'no localStorage / IndexedDB writes during the session ' + tag + JSON.stringify(wr));
      check(!(await evaluate(p, `JSON.stringify(Object.assign({}, localStorage)).includes('progress 100%')`)), 'output not in localStorage ' + tag);

      // host closes: timeout → closed line → Settings
      await hostClose('timeout');
      await save('10-closed-settings');

      // second open via 「在终端里打开」: no login card; the command prefilled, nothing sent
      await setBlock(BLOCK({ sudo: withPrf ? 'prf' : 'local' }));
      await evaluate(p, `import('./js/settings.js').then(m=>{m.openRescue();return true})`);
      await waitFor(p, `!!${$('rs-cmd-term-doctor')}`);
      check(await evaluate(p, `${$('rs-cmd-term-doctor')}.textContent`) === s.inTerm, '「在终端里打开」 next to 「执行」 ' + tag);
      await audit('cmd rows', '#rs-cmd-list button');
      if (full) { await evaluate(p, `${$('rs-cmds')}.scrollIntoView({block:'start'})`); await save('11-in-terminal-button'); }
      const nl3 = H.lines.length;
      await openTerminal('rs-cmd-term-doctor', { expectLogin: false });
      check(await evaluate(p, `${$('term-input')}.value`) === 'agentj doctor' && H.lines.length === nl3, 'prefilled, not sent ' + tag);
      if (full) await save('12-prefilled');
      if (full) {
        await hostClose('idle');
        for (const why of ['estop', 'relay_lost']) { await openTerminal('set-term-open', { expectLogin: false }); await hostClose(why); }
      } else await hostClose('idle');

      // 「结束」 and Esc — P414 (§37.14): Esc collapses (the session runs on behind the chip above the message box), 打开 brings
      // the page back; 「结束」 ends it
      await openTerminal('set-term-open', { expectLogin: false });
      const nClose0 = H.closes.length;
      await key(p, 'Escape');
      await waitFor(p, `${$('term')}.hidden && !!${$('term-chip')} && !${$('term-chip')}.hidden`, 5000);
      check(H.closes.length === nClose0 && await sheetKind() === '', 'P414: Esc collapses — nothing closed, the chip shows ' + tag);
      if (full) await save('13-collapsed-chip');
      await click('term-chip-open');
      await waitFor(p, `!${$('term')}.hidden && ${$('term-chip')}.hidden`, 5000);
      check(await pageOpen() && await sheetKind() === '', 'P414: 打开 on the chip → full screen again ' + tag);
      const nClose = H.closes.length;
      await click('term-end');
      await waitFor(p, `${$('term-status')}.textContent === ${JSON.stringify(s.closed(s.why.owner))}`);
      check(H.closes.length === nClose + 1 && H.closes.at(-1) === H.id, '结束 → term_close {id} ' + tag);
      await waitFor(p, `${$('term')}.hidden`, 5000);

      // the link drops: say so, never pretend
      await openTerminal('set-term-open', { expectLogin: false });
      fake.kick();
      await waitFor(p, `${$('term-status')}.textContent === ${JSON.stringify(s.dropped)}`, 20000);
      check(await shown('term-rescue') && await evaluate(p, `${$('term-input')}.disabled`), 'dropped → 「连接断了，终端已关闭」 + 紧急修复, input off ' + tag);
      await save('14-dropped');
      await click('term-back');
      await waitFor(p, `${$('term')}.hidden && !${$('settings')}.hidden`);
      await waitState(p, 'ready', 20000);

      // no passkey: the 「设置 Face ID」 offer, nothing signed
      H.passkey = false;
      const nCalls = H.calls.length;
      await evaluate(p, `import('./js/settings.js').then(m=>{m.open();return true})`);
      await click('set-term-open');
      await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.nopk)}) && !!${$('toast')}.querySelector('button')`, 10000);
      check(H.calls.length === nCalls + 1 && H.calls.at(-1).step === 1 && await evaluate(p, `${$('term')}.hidden`), 'no passkey → 设置 Face ID offer, nothing signed, page gone ' + tag);
      if (full) await save('15-no-passkey');
      H.passkey = true;

      // 清除已记住的电脑密码: signed term_forget, no Face ID
      await evaluate(p, `${$('set-term-row')}.scrollIntoView({block:'center'})`);
      check(await shown('set-term-forget') && await evaluate(p, `${$('set-term-state')}.textContent`) === (withPrf ? s.prf : (lang === 'zh' ? 'Agent J 主机登录密码已加密保存在 Agent J 主机本地，你扫脸后才会使用。' : 'Your Agent J host login password is stored encrypted on the Agent J host itself; it is used only after your Touch ID.')), 'cached mode in plain words + clear button ' + tag);
      if (full) await save('16-cached-mode');
      await click('set-term-forget');
      for (let i = 0; i < 100 && !H.forgets.length; i++) await sleep(50);
      check(H.forgets.length === 1 && H.forgets[0], 'term_forget signed (object {}) ' + tag);

      check(!H.errors.length, 'fake host saw no malformed message ' + tag + ' ' + H.errors.join(' | '));
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS P408 ' + tag);
    } finally { await p.dispose(); }
  }
  // ---- P417 #185: a COMPUTER browser switches the terminal on from the row (Face ID + the host's login password → it opens
  // in this window); a PHONE (iPhone user agent + touch, 390 px) only gets one sentence and no button. zh + en, shots.
  const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
  for (const [lang, w] of [['zh', 1440], ['en', 1440], ['zh', 390], ['en', 390]]) {
    const s = L[lang], phone = w === 390, tag = `p417-${lang}-${phone ? 'phone' : 'desktop'}`;
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang, theme: 'light' } }; fake.st.terminal = null; fake.st.runCmds = ['doctor', 'status'];
    Object.assign(H, { passkey: true, prf: false, cached: false, calls: [], lines: [], keys: [], closes: [], sudoOk: [], sudoNo: [], forgets: [], login: null, id: null, errors: [], loginAnswer: null });
    const p = await newPage(B, w, phone ? 844 : 900, 'light', phone ? { ua: IPHONE, touch: true, allow: [web.url, PAGE, fake.relay + '/'] } : { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${name}.png`), await shoot(p, ''));
    const click = async (id) => {
      await evaluate(p, `${$(id)}.scrollIntoView({block:'center'})`); await sleep(60);
      const r = await rect(p, '#' + id);
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    };
    const vis = (id) => evaluate(p, `!!${$(id)} && !${$(id)}.hidden && ${$(id)}.getClientRects().length > 0`);
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      const credId = randomBytes(16); H.fa = b64u(credId);
      const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
      await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
        rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p417-fixture').toString('base64'), signCount: 0 } });
      const setBlock = async (b) => { fake.st.terminal = b; await fake.send(fake.prefsMsg()); await sleep(250); await evaluate(p, `import('./js/settings.js').then(m=>{m.render();return true})`); };
      await evaluate(p, `import('./js/settings.js').then(m=>{m.open();return true})`);
      await waitFor(p, `!${$('settings')}.hidden`);
      const judged = await evaluate(p, `import('./js/termhook.js').then(m=>m.computerBrowser())`);
      check(judged === !phone, `the page judges itself ${phone ? 'a phone' : 'a computer browser'} ` + tag);
      if (phone) {
        for (const b of [BLOCK({ kind: 'phone' }), BLOCK({ on: false, me: false, kind: 'desktop' })]) {   // the host's kind or the page's own judgement
          await setBlock(b);
          await waitFor(p, `!${$('set-term-row')}.hidden`);
          const r = await evaluate(p, `({st:${$('set-term-state')}.textContent,btn:[...${$('set-term-row')}.querySelectorAll('button')].filter(e=>e.getClientRects().length&&!e.closest('[hidden]')).map(e=>e.id),cmd:${$('set-term-cmdline')}.hidden})`);
          check(r.st === s.phone && !r.btn.length && r.cmd, 'phone → one sentence, no button, no command ' + tag + JSON.stringify(r));
        }
        await evaluate(p, `${$('set-term-row')}.scrollIntoView({block:'center'})`); await save('row');
      } else {
        await setBlock(BLOCK({ on: false, me: false, kind: 'desktop', pk: false }));
        await waitFor(p, `!${$('set-term-row')}.hidden`);
        check(!(await vis('set-term-en')) && await vis('set-term-pk'), 'no passkey on this device → 设置 Face ID first, no enable button ' + tag);
        await setBlock(BLOCK({ me: false, kind: 'desktop' }));
        check((await evaluate(p, `${$('set-term-state')}.textContent`)).startsWith(s.notMeDesk) && await vis('set-term-en'), 'on, this browser not listed → enable here ' + tag);
        await setBlock(BLOCK({ on: false, me: false, kind: 'unknown' }));
        check(!(await vis('set-term-en')) && await evaluate(p, `${$('set-term-state')}.textContent`) === s.off + ' ' + s.repair, 'host does not know it paired as a computer → the command + 「重新配对」 (RESUME 9 ④) ' + tag);
        await setBlock(BLOCK({ on: false, me: false, kind: 'desktop' }));
        const r = await evaluate(p, `({st:${$('set-term-state')}.textContent,en:${$('set-term-enable')}.textContent,or:${$('set-term-or')}.textContent,cmd:${$('set-term-cmd')}.textContent,acts:${$('set-term-acts')}.hidden})`);
        check(r.st.startsWith(s.offDesk) && r.en === s.enable && r.or === s.orCli && r.cmd === 'agentj terminal enable' && r.acts && await vis('set-term-en') && await vis('set-term-cmdline'),
          'desktop, off → 开启并打开网页终端 + the command as the other way ' + tag + JSON.stringify(r));
        await evaluate(p, `${$('set-term-row')}.scrollIntoView({block:'center'})`); await save('1-row');
        await click('set-term-enable');
        await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'intro' && !${$('term-sheetwrap')}.hidden && !${$('term-sh-yes')}.disabled`, 10000);
        check(H.calls.length === 1 && H.calls[0].step === 1 && H.calls[0].mode === 'enable', 'step 1 asks for an ENABLE nonce, nothing signed yet ' + tag + JSON.stringify(H.calls));
        check((await evaluate(p, `${$('term-sh-text')}.textContent`)).includes(s.introEnable), 'intro says Face ID + the host login password ' + tag);
        await save('2-intro');
        await click('term-sh-yes');
        await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'login' && !${$('term-sheetwrap')}.hidden`, 10000);
        const c2 = H.calls.at(-1);
        check(c2.step === 2 && c2.mode === 'enable' && c2.sigOk && c2.fa, 'step 2 signed over {"mode":"enable"} + Face ID over its challenge ' + tag + JSON.stringify(c2));
        await evaluate(p, `${$('term-sh-pw')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
        await save('3-password');
        await click('term-sh-yes');
        await waitFor(p, `${$('term')}.dataset.phase === 'ready'`, 10000);
        check(H.loginAnswer && H.loginAnswer.sigOk && H.loginAnswer.fa && H.loginAnswer.pw === PASSWORD, 'the login card: sealed + signed + Face ID ' + tag);
        await waitFor(p, `${$('term-out')}.textContent.includes(${JSON.stringify(PROMPT.trim())})`);
        check(await evaluate(p, `!${$('term')}.hidden`), 'switched on → the terminal is open in this window ' + tag);
        await save('4-open');
      }
      check(!H.errors.length, 'fake host saw no malformed message ' + tag + ' ' + H.errors.join(' | '));
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS P417 #185 ' + tag);
    } finally { await p.dispose(); }
  }
  fake.st.onApp = null; fake.st.terminal = null; fake.st.runCmds = null;
  console.log(`P408: ${checks} checks; shots → ${out}`);
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP408({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
