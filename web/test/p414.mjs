// P414 (PROTOCOL §37.9–§37.14, ADR-P414-bang) 「!」 commands, the computer-password vault on sudo cards, the vshare card,
// Settings → 安全 → 「电脑密码」, and the full-screen terminal — on the real page against the local fake host (real Noise; every
// §8 control signature, §11 seal, `save:` tail and Face ID challenge checked like the host does), own headless Chromium (never
// :9222) with a CDP virtual platform authenticator as Face ID's stand-in: `hasPrf` (a passkey made with the prf extension) on
// the light runs, no PRF on the dark ones (the typed-only fallback). The fake host speaks §37.9–§37.14 exactly, so this walk
// does not depend on the host build. Checks:
//   · 「!ls」 with the terminal off / an older host → one explanation (what 「!」 does, the command to run), nothing sent
//   · 「!ls」 with the terminal closed but allowed → intro (the line named) → Face ID → ready → collapsed → term_line {bang: sid}
//     → term_bang ok + the owner page with src.term → the result card above the reply (12 lines folded, 展开, 「已隐藏 N 处」);
//     never a chat `say`; open → the next 「!」 line goes straight to term_line; the chip says 「正在运行 !…」 meanwhile; a second
//     one waits; the host's `running` refusal → 放回输入框; `failed` (it ran, no chat page) → said once, never offered back
//   · full screen: Noir pixel, no header / footer rules, the grabber follows the mouse and the finger (spring back short of the
//     threshold, collapse past it), Esc and the exit icon collapse, a double click / double tap changes nothing, the chip's
//     打开 / 结束, 复制输出 (clipboard), 截屏发到聊天 (a PNG in the tray, not sent, page collapsed)
//   · term_go: empty input → the line in it + note, nothing sent; sent unedited → term_confirm agent:1 「这条命令由 Agent 填写」
//     + signed; non-empty input → kept + the chip; a Cf line is not shown; term_sudo on a collapsed page brings it up (PRF 64 B
//     with vn) and it goes down again
//   · term_go without a line → full screen, empty input, no chip
//   · §11 sudo card: 「用 Face ID 填入电脑密码」 (vault:1, PRF sealed kind vault, prf1 ‖ prf2) · the same card re-armed with has 0
//     and no stale (the host build: that Face ID did not open it) → 「没能解开」 + typed / 记住 · stale line + typed + 「用 Face ID
//     记住」 (pct kind vault_save over digest|sha256(ct), signed save: tail, elev_done.saved → 已记住) · no PRF → nothing sent /
//     typed only · no passkey → no vault button
//   · vshare card (requester id, credential, paired date, type; tries 1) → Face ID + PRF sealed kind vshare → elev_done `saved`
//     (as the real host ends it) → 「已允许」 / no PRF → nothing sent
//   · Settings 电脑密码 row: has / share / nopeer / off / none; 让这台也能用 = vault_share two steps (signed, PRF sealed kind
//     vault_share) → waiting → 已能用 / denied; 清除这台 = term_forget
//   · no localStorage / IndexedDB write carries terminal output; no console errors / off-site requests
//   · zh / en × light / dark at 390 px + zh 1440 px; shots → reports/qa/p414/web/ (P414_OUT)
// The fake host's answers follow the real host (host/agentj/{elevate,term_bang,term_vshare}.py); the real host and
// the page are checked against each other in p414_contract.test.mjs.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { inflateSync } from 'node:zlib';
import { generateKeyPairSync, randomBytes, webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect, key } from './browser.mjs';
import { controlObject, elevateFields, elevateDigest, elevateMessage, b64u, unb64u, SEAL_CONTEXT } from '../../protocol/wire.js';

const subtle = webcrypto.subtle;
const OUT = process.env.P414_OUT || 'reports/qa/p414/web';
const enc = new TextEncoder();
const hex = (n) => randomBytes(n).toString('hex');
const sha = async (s) => Buffer.from(await subtle.digest('SHA-256', typeof s === 'string' ? enc.encode(s) : s)).toString('hex');
const $ = (id) => `document.getElementById('${id}')`;
const MARK = 'P414-OUT-MARK';
const PROMPT = 'owner@box:~/work$ ';
const LONG = Array.from({ length: 30 }, (_, i) => `line ${String(i + 1).padStart(2, '0')}  ${MARK}`).join('\n') + '\n';
const BLOCK = (x = {}) => ({ on: true, os: true, me: true, web_ok: true, sudo: 'prf', minutes: 15, idle_s: 300, open: false, ...x });
const PASSWORD = 'tr0ub4dor & 3';
// P559 naming: headless Chromium is Linux → Touch ID; Face ID only on iPhone / iPad
const L = {
  zh: { exTitle: '「!」开头 = 在 Agent J 主机上运行命令', off: 'agentj terminal enable', old: '版本较旧', bangIntro: '要运行「!ls -la」', hidden: '已隐藏 2 处',
    more: '展开全部（30 行）', chipLeft: /^终端 · 剩 1[45] 分钟$/, chipRun: '终端 · 正在运行 !sleep 5', copied: '已复制', shot: '截图已放进附件',
    agentTitle: '这条命令由 Agent 填写', goNote: 'Agent J 填好了一条命令', use: '用 Touch ID 填入 Agent J 主机登录密码', stale: '记住的 Agent J 主机登录密码不对了', saved: '已记住',
    vsTitle: '「iPad」想用记住的 Agent J 主机登录密码', vHas: '已记住 · 这台扫脸即可用', vShare: '已记住 · 这台还不能用', vOff: '没有记住（第一次用 sudo 时输一次）',
    vShared: '这台现在也能用记住的 Agent J 主机登录密码了。', vDenied: '另一台设备拒绝了', noPrf: '没能解开记住的密码', vNoPrf: '不支持记住 Agent J 主机登录密码', busy: '还没结束', useNote: '只执行这一条命令', saveNote: 'Agent J 主机会把它加密保存', bangFailed: '没能把结果放进聊天', bangProgram: '终端里有程序在等输入', vsDone: '已允许那台设备使用记住的 Agent J 主机登录密码' },
  en: { exTitle: '“!” at the start = run a command on the Agent J host', off: 'agentj terminal enable', old: 'too old', bangIntro: 'To run “!ls -la”', hidden: '2 items that look like passwords',
    more: 'Show all (30 lines)', chipLeft: /^Terminal · 1[45] min left$/, chipRun: 'Terminal · running !sleep 5', copied: 'Copied', shot: 'The screenshot is in your attachments',
    agentTitle: 'This command was filled in by the Agent', goNote: 'Agent J filled in a command', use: 'Fill in the password with Touch ID', stale: 'no longer works', saved: 'Remembered.',
    vsTitle: '“iPad” wants to use the remembered Agent J host login password', vHas: 'Remembered · Touch ID on this device fills it in', vShare: 'Remembered · not usable on this device yet',
    vOff: 'Not remembered (type it once the first time sudo asks)', vShared: 'This device can now use the remembered Agent J host login password.', vDenied: 'the other device said no',
    noPrf: 'could not unlock the remembered password', vNoPrf: 'cannot remember the Agent J host login password', busy: 'has not finished', useNote: 'for this one command only', saveNote: 'the Agent J host keeps it encrypted', bangFailed: 'could not be added to the chat', bangProgram: 'A program in the terminal is waiting for input', vsDone: 'That device may now use the remembered Agent J host login password.' },
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
/** One screen pixel (CSS px) → [r, g, b] (a 1×1 PNG from CDP, inflated by hand). */
async function pixel(p, x, y) {
  const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip: { x, y, width: 1, height: 1, scale: 1 } });
  const b = Buffer.from(data, 'base64');
  let o = 8, idat = [], type = 2;
  while (o < b.length) {
    const len = b.readUInt32BE(o), t = b.toString('ascii', o + 4, o + 8);
    if (t === 'IHDR') type = b[o + 8 + 9];
    if (t === 'IDAT') idat.push(b.subarray(o + 8, o + 8 + len));
    o += 12 + len;
  }
  const raw = inflateSync(Buffer.concat(idat));
  return [raw[1], raw[2], raw[3], type];
}

export async function runP414({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');

  // ---- the host side of §37.9–§37.14 (what serve / terminal.py / term_vault.py do, minus the shell and the crypto store)
  const H = { fa: null, calls: [], lines: [], keys: [], closes: [], sudoOk: [], answers: [], shares: [], forgets: [], ch: new Map(), id: null,
    conn: null, errors: [], goH: null, confirm: null, hold: null, elev: new Map(), shareRes: 'ok', bangs: [], rearm: null };
  const dev = () => fake.devs[0];
  const faOk = (fa, ch) => { try { const cd = JSON.parse(Buffer.from(fa.cd, 'base64url').toString()); return cd.type === 'webauthn.get' && cd.challenge === ch && fa.id === H.fa; } catch { return false; } };
  const ready = async (send, r) => {
    H.id = hex(8);
    await send({ t: 'term_ready', r, id: H.id, expires_at: Math.floor(Date.now() / 1000) + 15 * 60, idle_s: 300, sudo: 'prf', cwd: '~/work' });
    await send({ t: 'term_out', id: H.id, seq: 1, data: PROMPT, prompt: true });
  };
  const finishBang = async (send, m, output, extra = {}) => {
    await send({ t: 'term_out', id: m.id, seq: 9, data: output + PROMPT, prompt: true });
    const turn = await fake.addTurn({ k: 'phone', dev: dev().id, name: '', text: '!' + m.line, term: { out: output, cut: false, hidden: 0, state: 'done', sid: m.bang, ...extra } },
      'Agent J: ' + m.line + ' ran.', 'done');
    await send({ t: 'term_bang', id: m.id, sid: m.bang, ok: true, turn: turn.id, state: extra.state || 'done' });
  };
  fake.st.onApp = async (c, m, send) => {
    try {
      if (m.t === 'term_open' && !m.sig) {
        H.calls.push({ step: 1 });
        const n = hex(16), k = await oneTimeKey(), salt = randomBytes(32), vn = randomBytes(32);
        const ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode(['p414', n].join('\n')))));
        H.ch.set(n, { k, ch, used: false });
        return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'passkey_required', n, fa: H.fa, fa_ch: ch, salt: b64u(salt), vn: b64u(vn), epk: b64u(k.pub) }), true;
      }
      if (m.t === 'term_open') {
        const x = H.ch.get(m.n);
        const sigOk = await fake.verifyControl(c, 'term_open', m, { mode: 'line' });
        const fa = !!x && faOk(m.fa, x.ch);
        let prf = 0;
        if (x && m.pct) prf = (await unseal(x.k.priv, x.k.pub, m.pepk, m.pct, [fake.channel, dev().id, m.n, 'term_prf', m.n, await sha('line')])).length;
        H.calls.push({ step: 2, sigOk, fa, prf });
        if (!x || x.used || !sigOk || !fa) return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'passkey_failed' }), true;
        x.used = true;
        await send({ t: 'ctl_pending', r: m.r, action: 'term_open', timeout_ms: 180000 });
        await ready(send, m.r);
        return true;
      }
      if (m.t === 'term_line') {
        const signed = typeof m.sig === 'string';
        const rec = { line: m.line, seq: m.seq, bang: m.bang || null, signed };
        if (signed) {
          const x = H.confirm;
          rec.sigOk = !!x && m.n === x.n && await fake.verifyControl(c, 'term_line', m, { id: m.id, line: m.line });
          rec.fa = !!x && faOk(m.fa, x.fa_ch); rec.agent = !!x?.agent;
          H.confirm = null;
        }
        H.lines.push(rec);
        if (m.id !== H.id) return true;
        const digest = await sha(m.line);
        if (!signed && (/^rm -rf /.test(m.line) || (H.goH && digest === H.goH))) {   // H1: an unedited Agent line is always confirmed
          const agent = !!H.goH && digest === H.goH;
          H.goH = null;
          H.confirm = { n: hex(16), agent }; H.confirm.fa_ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('confirm-' + H.confirm.n))));
          await send({ t: 'term_confirm', id: m.id, seq: m.seq, line: m.line, why: agent ? 'Agent 填写的命令' : '删除文件', why_en: agent ? 'filled in by the Agent' : 'deletes files',
            n: H.confirm.n, fa_ch: H.confirm.fa_ch, ...(agent ? { agent: 1 } : {}) });
          return true;
        }
        if (m.bang) {
          H.bangs.push(m.bang);
          if (m.line === 'busy-test') { await send({ t: 'term_bang', id: m.id, sid: m.bang, ok: false, why: 'running' }); return true; }
          // P414 (R3-1): the shell is not at its prompt (a program reads input) → refused, not written, no page
          if (m.line === 'program-test') { H.prog = { send }; await send({ t: 'term_bang', id: m.id, sid: m.bang, ok: false, why: 'program' }); return true; }
          if (m.line === 'fail-test') {   // term_bang.py: the line ran, its owner page could not be written
            await send({ t: 'term_out', id: m.id, seq: 9, data: `fail-test: ok ${MARK}\n` + PROMPT, prompt: true });
            await send({ t: 'term_bang', id: m.id, sid: m.bang, ok: false, why: 'failed', state: 'done' });
            return true;
          }
          if (m.line.startsWith('sleep')) { H.hold = { m, send }; return true; }
          if (m.line === 'ls -la') { await finishBang(send, m, LONG, { hidden: 2 }); return true; }
          await finishBang(send, m, `${m.line}: ok ${MARK}\n`);
          return true;
        }
        await send({ t: 'term_out', id: m.id, seq: 3, data: (signed ? 'done: ' : 'ran: ') + m.line + '\n' + PROMPT, prompt: true });
        return true;
      }
      if (m.t === 'term_keys') { H.keys.push(m.key); return true; }
      if (m.t === 'term_close') { H.closes.push(m.id); return true; }
      if (m.t === 'term_sudo_ok') {
        const x = H.sudo;
        const sigOk = !!x && m.n === x.n && await fake.verifyControl(c, 'term_sudo', m, { id: m.id, rid: m.rid });
        let prf = 0;
        if (x && m.pct) prf = (await unseal(x.k.priv, x.k.pub, m.pepk, m.pct, [fake.channel, dev().id, m.n, 'term_prf', m.n, await sha(`${m.id}\n${m.rid}`)])).length;
        H.sudoOk.push({ sigOk, fa: !!x && faOk(m.fa, x.fa_ch), prf });
        return true;
      }
      if (m.t === 'elev_answer' && H.elev.has(m.id)) {
        const { c: card, k } = H.elev.get(m.id), d = dev();
        const digest = await elevateDigest(card.kind, elevateFields(card));
        const ctSha = m.ok && m.ct ? await sha(unb64u(m.ct)) : null, save = m.pct ? await sha(unb64u(m.pct)) : null;
        const key_ = await subtle.importKey('raw', unb64u(d.sk), { name: 'Ed25519' }, false, ['verify']);
        const sigOk = await subtle.verify({ name: 'Ed25519' }, key_, unb64u(m.sig), elevateMessage(fake.channel, d.id, m.id, card.kind, m.ok ? 'allow' : 'deny', card.n, m.ts, digest, ctSha, m.grant || null, save));
        const faCh = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode(['agentjarvis/passkey/elevate/v1', fake.channel, d.id, m.id, card.kind, 'approve', card.n, digest].join('\n')))));
        const a = { id: m.id, kind: card.kind, ok: m.ok, sigOk, fa: !!m.fa && faOk(m.fa, faCh), vault: m.vault ?? null, save: !!save, ct: null, pct: 0 };
        if (m.ok && m.ct) {
          const kind = card.kind === 'vshare' ? 'vshare' : m.vault ? 'vault' : card.kind;
          const v = await unseal(k.priv, k.pub, m.epk, m.ct, [fake.channel, d.id, m.id, kind, card.n, digest]);
          a.ct = kind === 'sudo' ? new TextDecoder().decode(v) : v.length;
        }
        if (m.pct) a.pct = (await unseal(k.priv, k.pub, m.pepk, m.pct, [fake.channel, d.id, m.id, 'vault_save', card.n, digest + '|' + ctSha])).length;
        H.answers.push(a);
        if (m.vault === 1 && H.rearm === m.id) {   // elevate.py: the wrap did not open with that PRF → the same card, has 0, no stale
          H.rearm = null;
          const k2 = await oneTimeKey(), c2 = { ...card, n: hex(16), epk: b64u(k2.pub), v: { has: 0, vs: b64u(randomBytes(32)) } };
          H.elev.set(c2.id, { c: c2, k: k2 });
          await send(c2);
          return true;
        }
        H.elev.delete(m.id);
        // elevate.py: a vshare card ends `saved` (the requester's wrap was written); a sudo card `done` (+ code, + saved:1)
        await send({ t: 'elev_done', id: m.id, result: !m.ok ? 'denied' : card.kind === 'vshare' ? 'saved' : 'done', ...(m.ok && card.kind === 'sudo' ? { code: 0 } : {}), ...(m.ok && m.pct ? { saved: 1 } : {}) });
        return true;
      }
      if (m.t === 'vault_share' && !m.sig) {
        const n = hex(16), k = await oneTimeKey();
        const ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('share-' + n))));
        H.share = { n, k, ch };
        return send({ t: 'ctl_res', r: m.r, action: 'vault_share', ok: false, why: 'passkey_required', n, fa: H.fa, fa_ch: ch, salt: b64u(randomBytes(32)), epk: b64u(k.pub) }), true;
      }
      if (m.t === 'vault_share') {
        const x = H.share;
        const sigOk = !!x && m.n === x.n && await fake.verifyControl(c, 'vault_share', m, {});
        let prf = 0;
        if (x && m.pct) prf = (await unseal(x.k.priv, x.k.pub, m.pepk, m.pct, [fake.channel, dev().id, m.n, 'vault_share', m.n, await sha(controlObject('vault_share', {}))])).length;
        H.shares.push({ sigOk, fa: !!x && faOk(m.fa, x.ch), prf });
        await send({ t: 'ctl_pending', r: m.r, action: 'vault_share', timeout_ms: 150000 });
        await sleep(300);
        await send({ t: 'ctl_res', r: m.r, action: 'vault_share', ...(H.shareRes === 'ok' ? { ok: true } : { ok: false, why: H.shareRes }) });
        return true;
      }
      if (m.t === 'term_forget') {
        const sigOk = await fake.verifyControl(c, 'term_forget', m, {});
        H.forgets.push(sigOk);
        await send({ t: 'ctl_res', r: m.r, action: 'term_forget', ok: sigOk });
        return true;
      }
    } catch (e) { H.errors.push(String(e && e.stack || e)); }
    return false;
  };
  const card = async (kind, extra) => {
    const k = await oneTimeKey();
    const c = kind === 'sudo'
      ? { t: 'elev', id: hex(16), kind, n: hex(16), epk: b64u(k.pub), ttl: 120, tries: 3, cmd: 'apt update', why: 'refresh the package lists', effect: '', fa: H.fa, ...extra }
      : { t: 'elev', id: hex(16), kind, n: hex(16), epk: b64u(k.pub), ttl: 120, tries: 1, dev: 'Qx7cK2mN9pLw', name: 'iPad', cred: 'cr3dABCDEFGHIJKLMNOPqrstuv', paired: 1760400000, flags: ['web'], fa: H.fa, ...extra };
    H.elev.set(c.id, { c, k });
    await fake.send(c);
    return c;
  };
  const V = (has) => ({ has, vs: b64u(randomBytes(32)), vn: b64u(randomBytes(32)) });

  const combos = [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 390], ['zh', 'light', 1440], ['zh', 'dark', 1440]];
  for (const [lang, theme, w] of combos) {
    const s = L[lang], tag = `${lang}-${theme}-${w}`, full = w === 390 && lang === 'zh' && theme === 'light';
    const withPrf = theme === 'light';
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang, theme } }; fake.st.terminal = null; fake.st.vault = null;
    Object.assign(H, { calls: [], lines: [], keys: [], closes: [], sudoOk: [], answers: [], shares: [], forgets: [], id: null, errors: [], goH: null, confirm: null, hold: null, bangs: [], shareRes: 'ok', rearm: null });
    H.elev.clear();
    const p = await newPage(B, w, w === 390 ? 844 : 900, theme, { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    // every storage write the page makes is recorded (the terminal output must never be in one)
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => { const W = window.__p414w = { ls: [], idb: [] };
      const si = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { W.ls.push(String(k) + '=' + String(v)); return si.call(this, k, v); };
      const txt = (v) => { try { return JSON.stringify(v, (k, x) => (x instanceof ArrayBuffer || ArrayBuffer.isView(x) ? new TextDecoder().decode(x) : x)) || ''; } catch { return String(v); } };
      for (const f of ['put', 'add']) { const o = IDBObjectStore.prototype[f]; IDBObjectStore.prototype[f] = function (...a) { W.idb.push(txt(a[0])); return o.apply(this, a); }; } })()` });
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: withPrf } });
    let n = 0;
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${String(++n).padStart(2, '0')}-${name}.png`), await shoot(p, ''));
    const click = async (id) => {
      await evaluate(p, `${$(id)}.scrollIntoView({block:'center'})`); await sleep(350);
      // Transient receipt notices can intentionally cover this point. Wait for
      // actual hit testing, never bypass an overlay with HTMLElement.click().
      await waitFor(p, `(()=>{const b=document.getElementById('${id}'),r=b.getBoundingClientRect(),h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return h&&b.contains(h)})()`, 6000);
      const r = await rect(p, '#' + id);
      await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    };
    const shown = (id) => evaluate(p, `!!${$(id)} && !${$(id)}.hidden && ${$(id)}.getClientRects().length > 0`);
    const pageOpen = () => evaluate(p, `!!${$('term')} && !${$('term')}.hidden`);
    const chipOn = () => evaluate(p, `!!${$('term-chip')} && !${$('term-chip')}.hidden`);
    const sheetKind = () => evaluate(p, `${$('term-sheetwrap')}?.hidden === false ? ${$('term-sheetwrap')}.dataset.kind : ''`);
    const say = async (text) => {
      await evaluate(p, `(${$('input')}.value = '', ${$('input')}.focus(), true)`);
      await p.send('Input.insertText', { text });
      await sleep(80);
      await click('send');
      await sleep(250);
    };
    const audit = async (label, sel) => {
      const a = await evaluate(p, `(()=>{const small=[...document.querySelectorAll(${JSON.stringify(sel)})].filter(e=>e.getClientRects().length&&!e.closest('[hidden]')).map(e=>{const r=e.getBoundingClientRect();return {id:e.id||e.className,w:Math.round(r.width),h:Math.round(r.height)}}).filter(x=>x.h<44||x.w<44);return {small,over:document.documentElement.scrollWidth>innerWidth+1}})()`);
      check(!a.small.length && !a.over, `${label}: taps ≥ 44 px, no overflow ${tag} ${JSON.stringify(a)}`);
    };
    const contrast = async (fgSel, bgSel) => evaluate(p, `(()=>{const rgb=(c)=>c.match(/\\d+(\\.\\d+)?/g).slice(0,3).map(Number);const L=(c)=>{const v=rgb(c).map(x=>{x/=255;return x<=.03928?x/12.92:Math.pow((x+.055)/1.055,2.4)});return .2126*v[0]+.7152*v[1]+.0722*v[2]};const a=L(getComputedStyle(document.querySelector(${JSON.stringify(fgSel)})).color),b=L(getComputedStyle(document.querySelector(${JSON.stringify(bgSel)})).backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)})()`);
    const setVault = async (b) => { fake.st.vault = b; await fake.send(fake.prefsMsg()); await sleep(200); await evaluate(p, `import('./js/settings.js').then(m=>{m.render();return true})`); };
    const setTerm = async (b) => { fake.st.terminal = b; await fake.send(fake.prefsMsg()); await sleep(200); await evaluate(p, `import('./js/settings.js').then(m=>{m.render();return true})`); };
    const lastAnswer = async (k = 1) => { for (let i = 0; i < 100 && H.answers.length < k; i++) await sleep(50); return H.answers.at(-1); };
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      if (withPrf) {
        H.fa = await evaluate(p, `navigator.credentials.create({publicKey:{rp:{id:'localhost',name:'Agent J'},user:{id:new Uint8Array(16).fill(4),name:'p414',displayName:'p414'},challenge:new Uint8Array(32),pubKeyCredParams:[{type:'public-key',alg:-7}],authenticatorSelection:{residentKey:'required',userVerification:'required',authenticatorAttachment:'platform'},extensions:{prf:{}}}}).then(c=>{const b=new Uint8Array(c.rawId);let s='';for(const x of b)s+=String.fromCharCode(x);return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'')})`);
      } else {
        const credId = randomBytes(16); H.fa = b64u(credId);
        const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
        await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
          rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p414-fixture').toString('base64'), signCount: 0 } });
      }
      const nSay0 = fake.st.says.length;

      // ---- 1. 「!」 on an older host (no terminal block) and with the terminal off: one explanation, nothing sent
      await say('!ls');
      await waitFor(p, `${$('toast')}.classList.contains('on') && ${$('toast')}.textContent.includes(${JSON.stringify(s.old)})`, 5000);
      check(await evaluate(p, `${$('confirm')}.hidden && !${$('toast')}.querySelector('button')`), 'older host → buttonless upgrade explanation ' + tag);
      await waitFor(p, `!${$('toast')}.classList.contains('on')`, 4000);
      check(await evaluate(p, `getComputedStyle(${$('toast')}).pointerEvents === 'none'`), 'expired explanation cannot intercept clicks ' + tag);
      await setTerm(BLOCK({ on: false, me: false }));
      await say('!ls');
      await waitFor(p, `${$('toast')}.classList.contains('on') && ${$('toast')}.textContent.includes(${JSON.stringify(s.off)})`, 5000);
      const ex = await evaluate(p, `${$('toast')}.textContent`);
      check(ex.includes(s.off) && ex.includes('https://agentj.app/docs/web-terminal/') && await evaluate(p, `${$('confirm')}.hidden && !${$('toast')}.querySelector('button')`), 'off → command and guide in buttonless explanation ' + tag + ex);
      await save('bang-explain-off');
      await waitFor(p, `!${$('toast')}.classList.contains('on')`, 4000);
      check(await evaluate(p, `getComputedStyle(${$('toast')}).pointerEvents === 'none'`), 'expired explanation cannot intercept clicks ' + tag);
      check(fake.st.says.length === nSay0 && !H.calls.length && !H.lines.length, '「!」 never went out as chat or to the terminal ' + tag);
      check((await evaluate(p, `${$('input')}.value`)) === '!ls', 'the field keeps the line ' + tag);

      // ---- 2. 「!ls -la」 with the terminal closed: intro → Face ID → collapsed → term_line {bang} → the result card
      await setTerm(BLOCK());
      await evaluate(p, `window.__p414w.ls.length = 0; window.__p414w.idb.length = 0; true`);
      await say('!ls -la');
      await waitFor(p, `${$('term-sheetwrap')}?.dataset.kind === 'intro' && !${$('term-sheetwrap')}.hidden && !${$('term-sh-yes')}.disabled`, 10000);
      check((await evaluate(p, `${$('term-sh-text')}.textContent`)).includes(s.bangIntro), 'intro names the 「!」 line ' + tag);
      if (full) await save('bang-intro');
      await click('term-sh-yes');
      await waitFor(p, `!!${$('bangCard')} && ${$('bangOut')}.textContent.includes(${JSON.stringify(MARK)})`, 10000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ calls: H.calls, lines: H.lines, errors: H.errors, problems: p.problems })); });
      const c2 = H.calls.at(-1), l2 = H.lines.at(-1);
      check(c2.step === 2 && c2.sigOk && c2.fa && c2.prf === (withPrf ? 64 : 0), `open: signed + Face ID, PRF ${withPrf ? 'prf1 ‖ prf2 (vn)' : 'absent'} ` + tag + JSON.stringify(c2));
      check(l2.line === 'ls -la' && /^[A-Za-z0-9_-]{22}$/.test(l2.bang || '') && !l2.signed, 'term_line {line, bang: <22-char sid>} ' + tag + JSON.stringify(l2));
      check(fake.st.says.length === nSay0, 'never a chat say ' + tag);
      await waitFor(p, `${$('term')}.hidden && !!${$('term-chip')} && !${$('term-chip')}.hidden`, 3000).catch(() => {});
      check(!(await pageOpen()) && await chipOn(), 'the page stays collapsed; the chip shows ' + tag);
      check(s.chipLeft.test(await evaluate(p, `${$('term-chip-text')}.textContent`)), 'chip 「终端 · 剩 N 分钟」 ' + tag);
      const cardInfo = await evaluate(p, `({folded:${$('bangCard')}.classList.contains('folded'),more:${$('bangMore')}?.textContent,note:${$('bangNote')}?.textContent,om:${$('omText')}.textContent,mono:getComputedStyle(${$('bangOut')}).fontFamily,above:${$('bangCard')}.nextElementSibling===${$('words')},h:${$('bangOut')}.getBoundingClientRect().height,copy:!!${$('bangCard')}.querySelector('.cc-btn')})`);
      check(cardInfo.folded && cardInfo.more === s.more && cardInfo.note.includes(s.hidden) && cardInfo.om === '!ls -la' && /mono/i.test(cardInfo.mono) && cardInfo.above && cardInfo.h < 360 && cardInfo.copy,
        'result card: above the reply, monospace, folded to ~12 lines, 展开, 「已隐藏 N 处」, copy icon ' + tag + JSON.stringify(cardInfo));
      check(await contrast('#bangOut', '#bangCard') >= 4.5, 'card text contrast ≥ 4.5 ' + tag);
      await save('bang-result-card');
      await click('bangMore');
      await waitFor(p, `!${$('bangCard')}.classList.contains('folded') && ${$('bangOut')}.textContent.includes('line 30')`).catch(async e => {
        const diagnostic = await evaluate(p, `(async()=>{const b=document.getElementById('bangMore'),r=b.getBoundingClientRect();return {folded:document.getElementById('bangCard').className,text:document.getElementById('bangOut').textContent.includes('line 30'),rect:{x:r.x,y:r.y,w:r.width,h:r.height},hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML.slice(0,400),state:(await import('./js/bang.js'))._debug(),problems:[]}})()`);
        await save('expand-failure');
        throw new Error(e.message+' '+JSON.stringify(diagnostic)+' '+JSON.stringify(p.problems));
      });
      if (full) await save('bang-result-expanded');
      await audit('chat card + chip', '#bangCard button, #term-chip button');

      // ---- 3. open → the next 「!」 line goes straight to term_line; pending chip; a second waits; host `running` → 放回输入框
      const nOpen = H.calls.length;
      await say('!sleep 5');
      await waitFor(p, `${$('term-chip-text')}.textContent === ${JSON.stringify(s.chipRun)}`, 5000);
      check(H.calls.length === nOpen && H.lines.at(-1).line === 'sleep 5' && !!H.lines.at(-1).bang, 'open terminal: no second Face ID, straight to term_line ' + tag);
      await save('chip-running');
      const nl3 = H.lines.length;
      await say('!pwd');
      check(H.lines.length === nl3 && (await evaluate(p, `${$('toast')}.textContent`)).includes(s.busy) && (await evaluate(p, `${$('input')}.value`)) === '!pwd', 'one 「!」 at a time: the second waits in the field ' + tag);
      await finishBang(H.hold.send, H.hold.m, `slept ${MARK}\n`); H.hold = null;
      await waitFor(p, `${$('omText')}.textContent === '!sleep 5'`, 5000);
      await waitFor(p, `!${$('term-chip')}.dataset.run || ${$('term-chip')}.dataset.run === '0'`, 5000);
      await evaluate(p, `${$('input')}.value = ''; true`);
      await say('!busy-test');
      await waitFor(p, `${$('toast')}.textContent.length > 0 && !!${$('toast')}.querySelector('button')`, 5000);
      await evaluate(p, `${$('toast')}.querySelector('button').click(); true`);
      await waitFor(p, `${$('input')}.value.includes('!busy-test')`, 3000);
      check(true, 'host `running` refusal → 放回输入框 puts the line back ' + tag);
      await evaluate(p, `${$('input')}.value = ''; ${$('input')}.dispatchEvent(new Event('input')); true`);
      await say('!fail-test');
      await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.bangFailed)})`, 5000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ lines: H.lines.slice(-3), errors: H.errors, toast: await evaluate(p, `${$('toast')}.textContent`), input: await evaluate(p, `${$('input')}.value`) })); });
      check(!(await evaluate(p, `!!${$('toast')}.querySelector('button')`)) && (await evaluate(p, `${$('input')}.value`)) === '' && fake.st.says.length === nSay0,
        'host `failed` (it ran, no chat page) → said once, never offered back, no chat say ' + tag);
      if (full) await save('bang-failed');
      // ---- 3b. P414 (R3-1): host `program` → the terminal full screen + 「请在终端里直接回答」, never offered back; then the phone
      //      itself holds the next 「!」 line while the program still waits (not sent; the page opens again)
      await evaluate(p, `${$('input')}.value = ''; ${$('input')}.dispatchEvent(new Event('input')); true`);
      await say('!program-test');
      await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.bangProgram)})`, 5000);
      await waitFor(p, `!${$('term')}.hidden`, 3000);
      check(!(await evaluate(p, `!!${$('toast')}.querySelector('button')`)) && (await evaluate(p, `${$('input')}.value`)) === '' && fake.st.says.length === nSay0,
        'host `program` → terminal full screen, explained, never offered back as a 「!」 line ' + tag);
      if (full) await save('bang-program');
      await click('term-exit');
      await waitFor(p, `${$('term')}.hidden && !${$('term-chip')}.hidden`, 3000);
      await evaluate(p, `${$('toast')}.textContent = ''; true`);
      const nl4 = H.lines.length;
      await say('!again-test');
      await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.bangProgram)})`, 5000);
      check(H.lines.length === nl4 && (await pageOpen()) && (await evaluate(p, `${$('input')}.value`)) === '!again-test',
        'phone: a program still waits → the 「!」 line is not sent; the terminal opens; the field keeps it ' + tag);
      await H.prog.send({ t: 'term_out', id: H.id, seq: 9, data: PROMPT, prompt: true }); H.prog = null;
      await evaluate(p, `${$('input')}.value = ''; ${$('input')}.dispatchEvent(new Event('input')); true`);
      await click('term-exit');
      await waitFor(p, `${$('term')}.hidden && !${$('term-chip')}.hidden`, 3000);

      // ---- 4. full screen: Noir, no rules, grabber (mouse + finger), Esc, exit icon, no double-click, copy, screenshot
      await click('term-chip-open');
      await waitFor(p, `!${$('term')}.hidden && ${$('term')}.dataset.phase === 'ready'`, 5000); await sleep(300);
      const look = await evaluate(p, `({bg:getComputedStyle(${$('term')}).backgroundColor,out:getComputedStyle(${$('term-out')}).backgroundColor,fg:getComputedStyle(${$('term-out')}).color,top:getComputedStyle(document.querySelector('.term-top')).borderBottomWidth,bot:getComputedStyle(document.querySelector('.term-bottom')).borderTopWidth,exit:${$('term-exit')}.getAttribute('aria-label'),grab:${$('term-grab')}.getBoundingClientRect().left+${$('term-grab')}.getBoundingClientRect().width/2})`);
      const or = await rect(p, '#term-out');
      const px = await pixel(p, Math.round(or.left + or.w - 30), Math.round(or.top + or.h - 30));
      check(look.bg === 'rgb(44, 43, 38)' && look.out === 'rgb(44, 43, 38)' && look.fg === 'rgb(250, 249, 245)' && look.top === '0px' && look.bot === '0px', 'Noir page, Lotion text, no header / footer rule ' + tag + JSON.stringify(look));
      check(px[0] === 44 && px[1] === 43 && px[2] === 38, 'Noir pixel in the output area ' + tag + JSON.stringify(px));
      check(Math.abs(look.grab - w / 2) <= 1 && look.exit === (lang === 'zh' ? '收起终端' : 'Collapse the terminal'), 'grabber centred, exit icon 「收起终端」 ' + tag);
      check(await contrast('#term-out', '#term') >= 4.5, 'terminal text contrast ≥ 4.5 ' + tag);
      await audit('terminal', '#term button, #term input');
      await save('terminal-full');
      // no double click / double tap: on the output, the top bar and the grabber nothing happens
      for (const sel of ['#term-out', '#term-left', '#term-rec', '#term-title']) {
        const r = await rect(p, sel);
        for (const cc of [1, 2]) {
          await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
          await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: cc });
          await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: cc });
        }
        await evaluate(p, `document.querySelector(${JSON.stringify(sel)}).dispatchEvent(new MouseEvent('dblclick', {bubbles: true, detail: 2})); true`);
      }
      await sleep(300);
      check(await pageOpen() && !(await chipOn()) && H.closes.length === 0, 'double click / dblclick on the page: still open, nothing closed ' + tag + JSON.stringify([await pageOpen(), await chipOn(), H.closes.length, await evaluate(p, `import('./js/termhook.js').then(m=>JSON.stringify(m._debug()))`)]));
      // the grabber follows the mouse; short of the threshold it springs back
      const g = await rect(p, '#term-grab');
      await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: g.x, y: g.y });
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: g.x, y: g.y, button: 'left', clickCount: 1 });
      for (let y = 10; y <= 50; y += 10) await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: g.x, y: g.y + y, button: 'left', buttons: 1 });
      const follow = await evaluate(p, `${$('term')}.style.transform`);
      check(follow === 'translateY(50px)', 'the page follows the mouse ' + tag + ' ' + follow);
      if (full) await save('grabber-drag');
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: g.x, y: g.y + 50, button: 'left', clickCount: 1 });
      await sleep(600);
      check(await pageOpen() && (await evaluate(p, `${$('term')}.style.transform`)) === '', 'short of the threshold → springs back ' + tag);
      // past the threshold → collapse (mouse)
      await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: g.x, y: g.y });
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: g.x, y: g.y, button: 'left', clickCount: 1 });
      for (let y = 40; y <= 320; y += 40) await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: g.x, y: g.y + y, button: 'left', buttons: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: g.x, y: g.y + 320, button: 'left', clickCount: 1 });
      await waitFor(p, `${$('term')}.hidden && !${$('term-chip')}.hidden`, 3000);
      check(H.closes.length === 0, 'drag past the threshold → collapsed, the session runs on ' + tag);
      // the finger: touch emulation for this gesture only
      await click('term-chip-open'); await waitFor(p, `!${$('term')}.hidden`, 3000); await sleep(300);
      await p.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 });
      const g2 = await rect(p, '#term-grab');
      await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: g2.x, y: g2.y }] });
      for (let y = 30; y <= 60; y += 30) { await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: g2.x, y: g2.y + y }] }); await sleep(30); }
      const tf = await evaluate(p, `${$('term')}.style.transform`);
      await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await sleep(600);
      check(tf === 'translateY(60px)' && await pageOpen(), 'the page follows the finger, springs back short of the threshold ' + tag + ' ' + tf);
      await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: g2.x, y: g2.y }] });
      for (let y = 60; y <= 360; y += 60) { await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: g2.x, y: g2.y + y }] }); await sleep(20); }
      await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      await waitFor(p, `${$('term')}.hidden && !${$('term-chip')}.hidden`, 3000);
      await p.send('Emulation.setTouchEmulationEnabled', { enabled: false });
      check(H.closes.length === 0, 'finger drag past the threshold → collapsed ' + tag);
      // Esc and the exit icon collapse; 打开 brings it back
      await click('term-chip-open'); await waitFor(p, `!${$('term')}.hidden`, 3000);
      await key(p, 'Escape');
      await waitFor(p, `${$('term')}.hidden && !${$('term-chip')}.hidden`, 3000);
      await click('term-chip-open'); await waitFor(p, `!${$('term')}.hidden`, 3000);
      await click('term-exit');
      await waitFor(p, `${$('term')}.hidden && !${$('term-chip')}.hidden`, 3000);
      check(H.closes.length === 0, 'Esc / exit icon → collapsed, nothing closed ' + tag);
      await save('collapsed-chip');
      // 复制输出 → the clipboard holds the page's output text
      await click('term-chip-open'); await waitFor(p, `!${$('term')}.hidden`, 3000);
      await click('term-copy');
      await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.copied)})`, 3000);
      const clip = await evaluate(p, `navigator.clipboard.readText()`);
      check(clip.includes('slept ' + MARK) && clip.includes('line 30'), '复制输出 → the output text in the clipboard ' + tag);
      // 截屏发到聊天 → a PNG in the tray, collapsed, nothing sent
      const nSay = fake.st.says.length;
      await evaluate(p, `(() => { const o = URL.createObjectURL; URL.createObjectURL = (b) => { if (b && b.type === 'image/png') window.__p414png = b; return o.call(URL, b); }; return true; })()`);
      await click('term-shot');
      await waitFor(p, `${$('term')}.hidden && document.querySelectorAll('#tray .chip').length === 1`, 5000);
      const tray = await evaluate(p, `({n:document.querySelectorAll('#tray .chip.img').length,name:document.querySelector('#tray .chip .nm')?.textContent||''})`);
      check(tray.n === 1 && fake.st.says.length === nSay && (await evaluate(p, `${$('toast')}.textContent`)).includes(s.shot), '截屏发到聊天 → an image in the attachments, not sent ' + tag + JSON.stringify(tray));
      await save('screenshot-attached');
      const png = await evaluate(p, `window.__p414png ? window.__p414png.arrayBuffer().then((b) => { let s = ''; for (const x of new Uint8Array(b)) s += String.fromCharCode(x); return { b64: btoa(s), name: window.__p414png.name } }) : null`);
      check(!!png && /^terminal-\d{8}-\d{6}\.png$/.test(png.name) && Buffer.from(png.b64, 'base64').subarray(1, 4).toString() === 'PNG', 'the attachment is a PNG of the output ' + tag + JSON.stringify(png && png.name));
      writeFileSync(resolve(out, `${tag}-${String(++n).padStart(2, '0')}-the-attached-png.png`), Buffer.from(png.b64, 'base64'));
      await evaluate(p, `document.querySelector('#tray .chip .x').click(); true`);
      await waitFor(p, `document.querySelectorAll('#tray .chip').length === 0`, 3000);

      // ---- 5. term_go: empty input → the line, not sent; unedited → forced 「这条命令由 Agent 填写」; non-empty → the chip
      const nl5 = H.lines.length;
      const goCmd = 'sudo systemctl restart nginx';
      H.goH = await sha(goCmd);
      await fake.send({ t: 'term_go', cmd: goCmd, h: H.goH });
      await waitFor(p, `!${$('term')}.hidden && ${$('term-input')}.value === ${JSON.stringify(goCmd)}`, 5000);
      check(H.lines.length === nl5 && (await evaluate(p, `${$('term-gonote')}.textContent`)).includes(s.goNote) && await shown('term-gonote'), 'term_go → full screen, the line in the input + note, nothing sent ' + tag);
      await save('go-prefilled');
      await evaluate(p, `${$('term-input')}.focus()`); await key(p, 'Enter', { text: '\r' });
      await waitFor(p, `${$('term-sheetwrap')}.dataset.kind === 'agent' && !${$('term-sheetwrap')}.hidden`, 5000);
      check((await evaluate(p, `${$('term-sh-title')}.textContent`)) === s.agentTitle && (await evaluate(p, `${$('term-sh-pre')}.textContent`)) === goCmd, 'unedited Agent line → 「这条命令由 Agent 填写」 + the full line ' + tag);
      await save('go-agent-confirm');
      await click('term-sh-yes');
      for (let i = 0; i < 100 && !H.lines.at(-1).signed; i++) await sleep(50);
      const sl = H.lines.at(-1);
      if (!sl.signed) {
        await save('go-sign-failure');
        const diagnostic=await evaluate(p, `(()=>{const b=document.getElementById('term-sh-yes'),r=b.getBoundingClientRect(),n=document.getElementById('toast');return {button:{x:r.x,y:r.y,w:r.width,h:r.height,disabled:b.disabled},hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML.slice(0,500),toast:{text:n.textContent,cls:n.className,pe:getComputedStyle(n).pointerEvents},sheet:document.getElementById('term-sheetwrap').hidden}})()`);
        console.log('P414 sign diagnostic '+JSON.stringify(diagnostic)+' problems='+JSON.stringify(p.problems)+' host='+JSON.stringify(H.errors));
      }
      check(sl.signed && sl.sigOk && sl.fa && sl.agent && sl.line === goCmd, '运行 → signed + Face ID ' + tag + JSON.stringify(sl));
      await evaluate(p, `${$('term-input')}.focus()`); await p.send('Input.insertText', { text: 'echo mine' });
      await fake.send({ t: 'term_go', cmd: 'df -h', h: await sha('df -h') });
      await waitFor(p, `!${$('term-gochip')}.hidden`, 5000);
      check((await evaluate(p, `${$('term-input')}.value`)) === 'echo mine' && (await evaluate(p, `${$('term-gochip')}.textContent`)).includes('df -h'), 'non-empty input kept, the Agent line waits in a chip ' + tag);
      await save('go-chip');
      await click('term-gochip');
      check((await evaluate(p, `${$('term-input')}.value`)) === 'df -h' && await evaluate(p, `${$('term-gochip')}.hidden`), 'tap the chip → the line goes in ' + tag);
      await fake.send({ t: 'term_go', cmd: 'ls ‮rm', h: hex(32) }); await sleep(300);
      check((await evaluate(p, `${$('term-input')}.value`)) === 'df -h' && await evaluate(p, `${$('term-gochip')}.hidden`), 'a line with a bidi character is never shown ' + tag);
      await evaluate(p, `${$('term-input')}.value = 'echo a\\u200bb'; ${$('term-form')}.requestSubmit(); true`); await sleep(150);
      check(H.lines.length === nl5 + 2 && await shown('term-bad'), 'typed zero-width character → not sent, says why ' + tag);
      await evaluate(p, `${$('term-input')}.value = ''; true`);
      // term_sudo while collapsed → up again (PRF with vn = 64 B), answered → down again
      await click('term-exit'); await waitFor(p, `${$('term')}.hidden`, 3000);
      await fake.send({ t: 'term_go' });   // term_bang.py: `agentj terminal open` without --cmd
      await waitFor(p, `!${$('term')}.hidden`, 5000);
      check((await evaluate(p, `${$('term-input')}.value`)) === '' && await evaluate(p, `${$('term-gochip')}.hidden`) && H.lines.length === nl5 + 2,
        'term_go without a line → full screen, empty input, no chip, nothing sent ' + tag);
      await click('term-exit'); await waitFor(p, `${$('term')}.hidden`, 3000);
      const sk = await oneTimeKey();
      H.sudo = { rid: hex(8), n: hex(16), k: sk }; H.sudo.fa_ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('sudo-' + H.sudo.n))));
      await fake.send({ t: 'term_sudo', id: H.id, rid: H.sudo.rid, argv: ['/usr/bin/apt', 'update'], line: 'sudo apt update', differs: false, n: H.sudo.n, fa_ch: H.sudo.fa_ch,
        salt: b64u(randomBytes(32)), vn: b64u(randomBytes(32)), epk: b64u(sk.pub) });
      await waitFor(p, `!${$('term')}.hidden && ${$('term-sheetwrap')}.dataset.kind === 'sudo'`, 5000);
      await click('term-sh-yes');
      for (let i = 0; i < 100 && !H.sudoOk.length; i++) await sleep(50);
      check(H.sudoOk.length === 1 && H.sudoOk[0].sigOk && H.sudoOk[0].fa && H.sudoOk[0].prf === (withPrf ? 64 : 0), 'term_sudo on a collapsed page: up, signed, PRF prf1 ‖ prf2 ' + tag + JSON.stringify(H.sudoOk));
      await waitFor(p, `${$('term')}.hidden && !${$('term-chip')}.hidden`, 3000);
      check(true, 'answered → collapsed again ' + tag);

      // ---- 6. §11 sudo card with the vault (§37.10)
      const c1 = await card('sudo', { v: V(1) });
      await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && !${$('elev-vault')}.hidden`, 5000);
      check((await evaluate(p, `${$('elev-vault')}.textContent`)) === s.use && await shown('elev-input'), '「用 Face ID 填入电脑密码」 first, the field as the second way ' + tag);
      check((await evaluate(p, `${$('elev-note')}.textContent`)).includes(s.useNote), 'the note says the remembered password is used for this one command ' + tag);
      await sleep(500);   // the card's entry animation (scale) settles first
      await audit('sudo card', '#elev button');
      await save('sudo-vault');
      await click('elev-vault');
      if (withPrf) {
        const a = await lastAnswer(1);
        check(a.id === c1.id && a.ok && a.vault === 1 && a.sigOk && a.fa && a.ct === 64 && !a.save, 'vault:1, PRF sealed kind vault (prf1 ‖ prf2), signed over sha256(ct), Face ID ' + tag + JSON.stringify(a));
      } else {
        await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.noPrf)})`, 5000);
        check(!H.answers.length && !(await evaluate(p, `${$('elev')}.hidden`)), 'no PRF from this authenticator → nothing sent, the card stays for the typed password ' + tag);
        await evaluate(p, `${$('elev-input')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
        await click('elev-allow');
        const a = await lastAnswer(1);
        check(a.ok && a.ct === PASSWORD && !a.vault && a.sigOk, 'typed fallback works ' + tag);
      }
      await waitFor(p, `${$('elev')}.hidden`, 5000);
      if (withPrf) {   // host build (§37.10): a PRF that does not open the wrap → the same card again, has 0, no stale
        const cr = await card('sudo', { v: V(1) }); H.rearm = cr.id;
        await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && !${$('elev-vault')}.hidden`, 5000); await sleep(400);
        await click('elev-vault');
        await waitFor(p, `!${$('elev-stale')}.hidden && ${$('elev-stale')}.textContent.includes(${JSON.stringify(s.noPrf)})`, 5000);
        const rr = await evaluate(p, `({save:!${$('elev-save-opt')}.hidden,vault:${$('elev-vault')}.hidden,field:!${$('elev-input')}.hidden,allow:!${$('elev-allow')}.disabled})`);
        check(rr.save && rr.vault && rr.field && rr.allow, 're-armed without stale → 「没能解开」, the field + 记住, no vault button ' + tag + JSON.stringify(rr));
        await save('sudo-vault-rearmed');
        await click('elev-deny'); await waitFor(p, `${$('elev')}.hidden`, 5000);
      }
      // stale + typed + 「用 Face ID 记住」
      const c2c = await card('sudo', { v: V(0), stale: 1 });
      await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && !${$('elev-stale')}.hidden`, 5000);
      const st = await evaluate(p, `({stale:${$('elev-stale')}.textContent,save:!${$('elev-save-opt')}.hidden,on:${$('elev-save-on')}.checked,vault:${$('elev-vault')}.hidden})`);
      check(st.stale.includes(s.stale) && st.save && st.on && st.vault, 'stale → 「记住的电脑密码不对了」, 「用 Face ID 记住」 on, no vault button ' + tag + JSON.stringify(st));
      check((await evaluate(p, `${$('elev-note')}.textContent`)).includes(s.saveNote), 'the note no longer says 「不保存」 while 记住 is ticked ' + tag);
      await save('sudo-stale-remember');
      await evaluate(p, `${$('elev-input')}.focus()`); await p.send('Input.insertText', { text: PASSWORD });
      const nA2 = H.answers.length;
      await click('elev-allow');
      const a2 = await lastAnswer(nA2 + 1);
      check(a2.id === c2c.id && a2.ok && a2.ct === PASSWORD && a2.sigOk && a2.fa && (withPrf ? a2.save && a2.pct === 32 : !a2.save && a2.pct === 0),
        `typed + remember: ${withPrf ? 'pct sealed kind vault_save over digest|sha256(ct), signed save: tail' : 'no PRF → typed only, no pct'} ` + tag + JSON.stringify(a2));
      if (withPrf) await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.saved)})`, 5000);
      check(!(await evaluate(p, `document.documentElement.outerHTML.includes(${JSON.stringify(PASSWORD)})`)), 'the password is gone from the page ' + tag);
      await waitFor(p, `${$('elev')}.hidden`, 5000);
      // no passkey on the card (no fa) → no vault button, no 记住
      await card('sudo', { v: V(1), fa: undefined });
      await waitFor(p, `${$('elev')} && !${$('elev')}.hidden`, 5000);
      check(await evaluate(p, `${$('elev-vault')}.hidden && ${$('elev-save-opt')}.hidden`), 'no passkey → typed only ' + tag);
      await click('elev-deny'); await waitFor(p, `${$('elev')}.hidden`, 5000);

      // ---- 7. vshare card (§37.11)
      const vc = await card('vshare', { v: V(1) });
      await waitFor(p, `${$('elev')} && !${$('elev')}.hidden && ${$('elev-title')}.textContent === ${JSON.stringify(s.vsTitle)}`, 5000);
      const rows = await evaluate(p, `${$('elev-rows')}.textContent`);
      check(rows.includes('Qx7cK2mN9pLw') && rows.includes('cr3dABCDEFGHIJKL…') && rows.includes(lang === 'zh' ? '网页版' : 'Web version') && /2025/.test(rows) && await evaluate(p, `${$('elev-input')}.hidden`),
        'vshare: requester id, passkey (short), paired date, type; no field ' + tag + ' ' + rows);
      await save('vshare-card');
      await click('elev-allow');
      if (withPrf) {
        const a = await lastAnswer(H.answers.length + 1);
        check(a.id === vc.id && a.kind === 'vshare' && a.ok && a.sigOk && a.fa && a.ct === 64, '同意 → Face ID + PRF sealed kind vshare ' + tag + JSON.stringify(a));
        await waitFor(p, `document.body.textContent.includes(${JSON.stringify(s.vsDone)})`, 5000);
        check(true, 'elev_done `saved` → 「已允许」 ' + tag);
      } else {
        await waitFor(p, `${$('toast')}.textContent.length > 0`, 5000); await sleep(300);
        check(!H.answers.some((x) => x.id === vc.id), 'no PRF → nothing sent ' + tag);
        await click('elev-deny');
        const a = await lastAnswer(H.answers.length + 1);
        check(a.id === vc.id && !a.ok && a.sigOk, '拒绝 → signed deny ' + tag);
      }
      await waitFor(p, `${$('elev')}.hidden`, 5000);

      // ---- 8. Settings → 安全 → 电脑密码
      await evaluate(p, `import('./js/settings.js').then(m=>{m.open();return true})`);
      await waitFor(p, `!${$('settings')}.hidden`);
      check(await evaluate(p, `${$('set-vault-row')}.hidden`), 'older host (no vault block) → no row ' + tag);
      await setVault({ has: true, any: true, peers: 1, mode: 'prf' });
      await evaluate(p, `${$('set-vault-row')}.scrollIntoView({block:'center'})`);
      check((await evaluate(p, `${$('set-vault-state')}.textContent`)) === s.vHas && await shown('set-vault-forget') && !(await shown('set-vault-share')) && !(await shown('set-term-forget')), 'has → 「已记住 · 这台扫脸即可用」 + 清除这台 (one clear button) ' + tag);
      await save('vault-row-has');
      await click('set-vault-forget');
      for (let i = 0; i < 100 && !H.forgets.length; i++) await sleep(50);
      check(H.forgets.length === 1 && H.forgets[0], '清除这台 = signed term_forget ' + tag);
      await setVault({ has: false, any: false, peers: 0, mode: null });
      check((await evaluate(p, `${$('set-vault-state')}.textContent`)) === s.vOff && await evaluate(p, `${$('set-vault-acts')}.hidden`), 'nothing remembered → says the first sudo asks once ' + tag);
      await setVault({ has: false, any: true, peers: 1, mode: null });
      check((await evaluate(p, `${$('set-vault-state')}.textContent`)) === s.vShare && await shown('set-vault-share'), 'remembered elsewhere → 「让这台也能用」 ' + tag);
      await audit('vault row', '#set-vault-row button');
      await save('vault-row-share');
      await click('set-vault-share');
      await waitFor(p, `!${$('confirm')}.hidden`, 5000);
      if (full) await save('vault-share-confirm');
      await click('confirm-yes');
      if (withPrf) {
        await waitFor(p, `${$('set-vault-last')}.textContent === ${JSON.stringify(s.vShared)}`, 8000)
          .catch(async (e) => { throw new Error(e.message + JSON.stringify({ shares: H.shares, errors: H.errors, last: await evaluate(p, `${$('set-vault-last')}.textContent`) })); });
        check(H.shares.length === 1 && H.shares[0].sigOk && H.shares[0].fa && H.shares[0].prf === 32, 'vault_share: signed, Face ID, PRF sealed kind vault_share → 已能用 ' + tag + JSON.stringify(H.shares));
        await save('vault-share-done');
        H.shareRes = 'denied';
        await setVault({ has: false, any: true, peers: 1, mode: null });
        await click('set-vault-share'); await waitFor(p, `!${$('confirm')}.hidden`, 5000); await click('confirm-yes');
        await waitFor(p, `${$('set-vault-last')}.textContent.includes(${JSON.stringify(s.vDenied)})`, 8000);
        check(true, 'denied → 「另一台设备拒绝了」 ' + tag);
      } else {
        await waitFor(p, `${$('set-vault-last')}.textContent.includes(${JSON.stringify(s.vNoPrf)})`, 8000);
        check(!H.shares.length, 'no PRF → says so, nothing sent ' + tag);
      }

      // ---- 9. nothing stored; no errors
      const wr = await evaluate(p, `window.__p414w`);
      check(!wr.ls.some((x) => x.includes(MARK)) && !wr.idb.some((x) => x.includes(MARK)) && !wr.idb.some((x) => x.includes(PASSWORD)), 'no storage write carries terminal output or the password ' + tag);
      check(!H.errors.length, 'fake host saw no malformed message ' + tag + ' ' + H.errors.join(' | '));
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS P414 ' + tag);
    } finally { await p.dispose(); }
  }
  fake.st.onApp = null; fake.st.terminal = null; fake.st.vault = null;
  console.log(`P414: ${checks} checks; shots → ${out}`);
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP414({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
