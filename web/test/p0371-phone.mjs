// 0371 r3 「旧版本升级失败」 card — e2e on the real page against the local fake host (real Noise; §8 control signatures checked
// like the host does), own headless Chromium (never :9222), a CDP virtual authenticator as Face ID's stand-in. Synthetic data.
// Per combo (zh/en × light/dark × 390, zh × light/dark × 1440), host version 0.30.65, the old host's own failure words.
// 390 first screen: the words from their first line down to what the line does (the whole paragraph); zh: the line block too.
// Pass "off" — the page as shipped (old-updater-line.js OLD_FIX_RUN = false):
//   1 no web terminal on this remote → the page's plain words replace the host's alarming sentence and say what the line
//     does; the fixed line in a code block with copy only (no 「修复连接」); the host's own text is whole inside
//     「查看原始信息」; one guide link; the reader opens with the same words
//   2 the web terminal may be used here → still copy only, nothing sent
//   5 the host comes back on 0.30.69 → this page keeps the plain words (never back to the alarming sentence); a new
//     failure page on 0.30.69 is the host's own card with 「修复连接」
// Pass "on" — the same page with that one module served with the switch on (what Jarvis turns on after the real-host run):
//   2 「执行并升级」 left of copy (≥44 px, no red) → intro → Face ID → exactly one unsigned term_line {line: the constant,
//     bang, run:1, from: page id} → the host's confirm card (the 0.30.47-0.30.65 classifier's own why, no Face ID) → 取消 =
//     not run, nothing more sent → 再跑一次 → 运行 → one signed resend; never a chat say / cmd_run
//   3 the service restarts under the terminal (the real ending of a successful run) → the box says the disconnect is expected
//   4 back on 0.30.69 → the card stays, without 「执行」
// Shots → P0371_OUT (reports/qa/p0371); P0371_SHOTS = a second folder for the review shots (oldhost-heal-card-r4-*.png).
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateKeyPairSync, randomBytes, webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect } from './browser.mjs';
import { b64u } from '../../protocol/wire.js';

const subtle = webcrypto.subtle, enc = new TextEncoder();
const OUT = process.env.P0371_OUT || 'reports/qa/p0371', SHOTS = process.env.P0371_SHOTS || '';
const hex = (n) => randomBytes(n).toString('hex');
const LINE = '( umask 077; chmod -R go-w ~/.local/state/agentj/install-artifacts/dependencies; agentj update apply --yes )';
const PROMPT = 'owner@box:~/work$ ';
const PHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const BLOCK = (x = {}) => ({ mobile: true, resume: true, prefill: true, kind: 'desktop', on: true, os: true, me: true, web_ok: true, sudo: 'local', minutes: 15, idle_s: 300, open: false, modes: ['line', 'pty'], ...x });
// the old host's own words (0.30.58-0.30.65 vocabulary), a synthetic ticket id
const HOST = { zh: '升级到 0.30.69 没成功（已校验的安装包在安装前被改动或替换，已停止安装）；Agent J 主机仍是 0.30.65，照常可用；版本和错误类别（不含日志）已自动发给 Agent J 客服（st_xxxxxxxxxxxxxxxxxxxxxx）。',
  en: 'The upgrade to 0.30.69 did not go through (the verified package changed or was replaced before installing, so installation stopped); still on 0.30.65, working as before; the version and error category (no logs) went to Agent J support automatically (st_xxxxxxxxxxxxxxxxxxxxxx).' };
const L = {
  zh: { likely: '多半', scary: '被改动或替换', does: ['依赖缓存目录改成仅本人可写', '立即升级并重启 Agent J', '正在进行的任务会被打断'], raw: '查看原始信息', cut: '终端已断开', refused: '没有执行：你选了取消', why: '需要你确认', run: '执行并升级' },
  en: { likely: 'most likely', scary: 'changed or was replaced', does: ['dependency cache writable only by you', 'updates and restarts Agent J at once', 'interrupting any task in progress'], raw: 'Show the original message', cut: 'The terminal disconnected', refused: 'Not run', why: 'needs your confirmation', run: 'Run and update' },
};

export async function runP0371({ B, web, fake, out = OUT, shots = SHOTS }) {
  mkdirSync(out, { recursive: true }); if (shots) mkdirSync(shots, { recursive: true });
  let checks = 0;
  const fails = [];
  const check = (ok, label) => { if (!ok) fails.push(label); else checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');
  const H = {};
  const resetH = () => Object.assign(H, { fa: null, ch: new Map(), lines: [], cmds: [], confirm: null, id: null, errors: [], other: [] });
  resetH();
  const faOk = (fa, ch) => { try { const cd = JSON.parse(Buffer.from(fa.cd, 'base64url').toString()); return cd.type === 'webauthn.get' && cd.challenge === ch && fa.id === H.fa; } catch { return false; } };
  fake.st.onApp = async (c, m, send) => {
    try {
      if (m.t === 'term_open' && !m.sig) {
        const n = hex(16), ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('p0371-open-' + n))));
        H.ch.set(n, { ch, used: false, mode: m.mode || 'line' });
        return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'passkey_required', n, fa: H.fa, fa_ch: ch, salt: b64u(randomBytes(32)), epk: b64u(randomBytes(32)) }), true;
      }
      if (m.t === 'term_open') {
        const x = H.ch.get(m.n);
        if (!x || x.used || !await fake.verifyControl(c, 'term_open', m, { mode: x.mode }) || !faOk(m.fa, x.ch)) return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'bad_signature' }), true;
        x.used = true;
        await send({ t: 'ctl_pending', r: m.r, action: 'term_open', timeout_ms: 180000 });
        H.id = hex(8);
        await send({ t: 'term_ready', r: m.r, id: H.id, expires_at: Math.floor(Date.now() / 1000) + 900, idle_s: 300, sudo: 'local', cwd: '~/work' });
        await send({ t: 'term_out', id: H.id, seq: 1, data: PROMPT, prompt: true });
        return true;
      }
      if (m.t === 'term_line') {
        const signed = typeof m.sig === 'string';
        const rec = { line: m.line, bang: m.bang || null, run: m.run, from: m.from, signed, keys: Object.keys(m).sort().join(',') };
        if (signed) {
          const x = H.confirm;
          rec.sigOk = !!x && m.n === x.n && await fake.verifyControl(c, 'term_line', m, { id: m.id, line: m.line });
          rec.faSent = !!m.fa; H.confirm = null;
        }
        H.lines.push(rec);
        if (!signed) {   // what terminal.py of 0.30.47-0.30.65 answers to this line: classify() → 「需要你确认」, uv 0, no agent mark
          H.confirm = { n: hex(16) };
          await send({ t: 'term_confirm', id: m.id, seq: m.seq, line: m.line, why: '需要你确认', why_en: 'needs your confirmation', n: H.confirm.n,
            fa_ch: b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('confirm-' + H.confirm.n)))), uv: 0 });
          return true;
        }
        // confirmed: the update runs; its last step restarts the service (the test drops the host next) — no term_bang comes
        await send({ t: 'term_out', id: m.id, seq: 9, data: '  ✓ 服务重启已交给独立任务 / service restart scheduled independently\n', prompt: false });
        return true;
      }
      if (m.t === 'cmd_run') { H.cmds.push(m.cmd); return false; }
      if (m.t && /^term_/.test(m.t) && !['term_close', 'term_resize'].includes(m.t)) H.other.push(m.t);
    } catch (e) { H.errors.push(String(e && e.stack || e)); }
    return false;
  };

  // the one module with the switch on (pass "on"): the shipped bytes with `false` → `true`, nothing else
  const LINE_FILE = new URL('../public/js/old-updater-line.js', import.meta.url);
  const shipped = readFileSync(LINE_FILE, 'utf8'), flipped = shipped.replace('export const OLD_FIX_RUN = false;', 'export const OLD_FIX_RUN = true;');
  assert.ok(shipped.includes('export const OLD_FIX_RUN = false;') && flipped !== shipped, 'the shipped switch is off');
  const combos = [['zh', 'light', 390, 'off'], ['zh', 'light', 390, 'on'], ['zh', 'dark', 390, 'off'], ['zh', 'dark', 390, 'on'], ['en', 'light', 390, 'off'], ['en', 'light', 390, 'on'], ['en', 'dark', 390, 'off'],
    ['zh', 'light', 1440, 'off'], ['zh', 'light', 1440, 'on'], ['zh', 'dark', 1440, 'off'], ['zh', 'dark', 1440, 'on']];
  for (const [lang, theme, w, sw] of (process.env.P0371_FIRST_ONLY ? combos.slice(0, 2) : combos)) {
    const on = sw === 'on';
    const s = L[lang], tag = `${lang}-${w}-${theme}`, h = w === 390 ? 844 : 900;
    fake.reset(); fake.setUp(true);
    fake.st.prefs = { appearance: { language: lang, theme }, voice: { speak_replies: false } }; fake.st.terminal = null; fake.st.autoReply = false;
    fake.st.runCmds = ['doctor', 'reconnect', 'status', 'version']; fake.st.version = '0.30.65';
    resetH();
    const p = await newPage(B, w, h, theme, { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    if (on) {
      p.on((m) => { if (m.method === 'Fetch.requestPaused') p.send('Fetch.fulfillRequest', { requestId: m.params.requestId, responseCode: 200,
        responseHeaders: [{ name: 'content-type', value: 'text/javascript; charset=utf-8' }, { name: 'cache-control', value: 'no-store' }], body: Buffer.from(flipped).toString('base64') }).catch(() => {}); });
      await p.send('Fetch.enable', { patterns: [{ urlPattern: '*/js/old-updater-line.js*' }] });
    }
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
    const save = async (name, review = false) => {
      const png = await shoot(p, '');
      writeFileSync(resolve(out, `${tag}-${sw}-${name}.png`), png);
      if (review && shots) writeFileSync(resolve(shots, `oldhost-heal-card-r4-${tag}-${name}.png`), png);
    };
    const clickSel = async (sel) => {
      await evaluate(p, `document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`); await sleep(80);
      const r = await rect(p, sel);
      await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    };
    // every scroller between the words and the document back to its start (the reply card scrolls inside itself)
    const top = () => evaluate(p, `(()=>{for(let e=document.getElementById('words');e;e=e.parentElement)e.scrollTop=0;scrollTo(0,0);return true})()`);
    const card = () => evaluate(p, `(()=>{const x=document.getElementById('cmdx'),f=x.querySelector('[data-old-updater-fix]'),raw=x.querySelector('[data-old-updater-raw]');
      const run=f&&f.querySelector('.bar .runbtn'),cp=f&&f.querySelector('.bar .copy'),r=run&&run.getBoundingClientRect(),c=cp&&cp.getBoundingClientRect(),pre=f&&f.querySelector('pre'),code=f&&f.querySelector('code');
      const vis=(e)=>{if(!e)return false;const q=e.getBoundingClientRect();if(!(q.width>0&&q.height>0))return false;const hit=(y)=>{const n=document.elementFromPoint(q.left+Math.min(q.width/2,20),y);return !!n&&(n===e||e.contains(n)||n.contains(e)&&n.closest('#cmdx,#words')!==null)};return hit(q.top+2)&&hit(q.bottom-2)};
      const a=x.querySelector('a[href]');
      return {words:document.getElementById('words').textContent,fix:!!f,runFlag:f?f.dataset.run:null,code:code?code.textContent:null,lang:pre?pre.dataset.lang:null,
        codeLines:code?Math.round(code.getBoundingClientRect().height/parseFloat(getComputedStyle(code).lineHeight)):0,
        run:!!run,runH:r?Math.round(r.height):0,runW:r?Math.round(r.width):0,runAria:run?run.getAttribute('aria-label'):'',danger:!!(run&&run.classList.contains('term-command--danger')),
        leftOf:!!(r&&c&&r.right<=c.left+0.5),gap:r&&c?Math.round(c.left-r.right):null,copy:!!cp,copyH:c?Math.round(c.height):0,
        raw:raw?{open:raw.open,label:raw.querySelector('summary').textContent,text:raw.querySelector('p').textContent,sumH:Math.round(raw.querySelector('summary').getBoundingClientRect().height)}:null,
        link:a?{href:a.href,text:a.textContent}:null,links:x.querySelectorAll('a[href]').length,repair:x.querySelectorAll('[data-repair]').length,
        seen:{code:vis(pre),copy:vis(cp),run:run?vis(run):null,link:vis(a),raw:vis(raw&&raw.querySelector('summary')),words:vis(document.querySelector('#words p'))},runTitle:run?run.title:'',
        box:(()=>{const b=x.querySelector('.runbox');return b?{head:b.querySelector('.runhead')?.textContent||'',acts:[...b.querySelectorAll('.runact')].map(e=>e.textContent||e.getAttribute('aria-label'))}:null})(),
        over:document.documentElement.scrollWidth>innerWidth+1}})()`);
    try {
      if (w === 390) await p.send('Emulation.setUserAgentOverride', { userAgent: PHONE_UA });
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      await evaluate(p, `if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click(); document.getElementById('phone-env-dismiss')?.click(); true`);
      const credId = randomBytes(16); H.fa = b64u(credId);
      const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
      await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
        rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p0371-fixture').toString('base64'), signCount: 0 } });
      const says0 = fake.st.says.length;

      // ---- 1. no web terminal on this remote: plain words, copy only, the host's text folded
      const turn = await fake.addTurn({ k: 'cmd', text: '/update' }, HOST[lang], 'done', { cmd: 'update', kind: 'error', repair: 'reconnect', system_name: 'AgentJ' });
      await waitFor(p, `!!document.querySelector('#cmdx [data-old-updater-fix]')`, 8000);
      await sleep(300); await top();
      const a = await card();
      check(a.words.includes(s.likely) && a.words.includes('0.30.65') && !a.words.includes(s.scary) && !/UPGRADE_RESULT|st_x/.test(a.words), '1 the card says the plain words, not the host\'s sentence ' + tag + JSON.stringify(a.words));
      check(s.does.every((x) => a.words.includes(x)), '1 the words say what the line does: the cache folder, update + restart now, tasks cut ' + tag);
      check(a.fix && a.code === LINE && a.lang === 'sh' && a.codeLines === 1 && a.copy && a.copyH >= 44, '1 the fixed line: one line in a code block with copy ' + tag + JSON.stringify(a));
      check(!a.run && a.runFlag === '0' && a.repair === 0, '1 no terminal here → no 「执行」; no 「修复连接」 on this card ' + tag + JSON.stringify(a));
      check(a.raw && !a.raw.open && a.raw.label === s.raw && a.raw.text === HOST[lang] && a.raw.sumH >= 44, '1 the host\'s own text is whole inside the fold ' + tag + JSON.stringify(a.raw));
      check(a.links === 1 && a.link.href === 'https://agentj.app/docs/install/#upgrade-failed', '1 one repair guide link ' + tag + JSON.stringify(a.link));
      check(!a.over, '1 no horizontal overflow ' + tag);
      if (w === 390) check(a.seen.words && (lang === 'en' || a.seen.code && a.seen.copy), '1 390 first screen: the whole paragraph (zh: the line and copy too) ' + tag + JSON.stringify(a.seen));
      if (!on) {
        // the reader opens with the words on screen, and stays so
        await evaluate(p, `document.getElementById('readBtn').click(); true`); await sleep(500);
        const rd = await evaluate(p, `({open:!document.getElementById('rd').hidden,text:document.getElementById('rdWords').textContent})`);
        check(rd.open && rd.text.includes(s.likely) && !rd.text.includes(s.scary) && rd.text.trim() === a.words.trim(), '1 the reader shows the same words as the card ' + tag + JSON.stringify(rd));
        await fake.send(fake.prefsMsg()); await sleep(400);   // a redraw of the page under the reader
        const rd2 = await evaluate(p, `document.getElementById('rdWords').textContent`);
        check(rd2 === rd.text, '1 the reader does not flip on a redraw ' + tag);
        await evaluate(p, `history.back(); true`); await sleep(400);
        check(await evaluate(p, `document.getElementById('rd').hidden`), '1 reader closed ' + tag);
        await top();
      }

      // ---- 2. the web terminal may be used here
      fake.st.terminal = BLOCK({ kind: w === 390 ? 'phone' : 'desktop' }); await fake.send(fake.prefsMsg()); await sleep(300);
      await evaluate(p, `import('./js/settings.js').then(m=>{m.render();return true})`);
      const usable = JSON.parse(await evaluate(p, `import('./js/termhook.js').then(m=>JSON.stringify(m._debug()))`));
      check(usable.state === 'ok', '2 the web terminal may be used on this remote ' + tag + JSON.stringify(usable));
      if (!on) {
        await sleep(500); await top();
        const b0 = await card();
        check(b0.fix && !b0.run && b0.runFlag === '0' && b0.copy && !H.lines.length, '2 as shipped (switch off): copy only even with a usable terminal, nothing sent ' + tag + JSON.stringify(b0));
        await save('copy', true);
        if (lang === 'zh' && w === 390) {
          await evaluate(p, `(()=>{const d=document.querySelector('#cmdx [data-old-updater-raw]');d.open=true;d.scrollIntoView({block:'end'});return true})()`); await sleep(250);
          await save('expanded', true);
          await evaluate(p, `(()=>{document.querySelector('#cmdx [data-old-updater-raw]').open=false;return true})()`); await top();
        }
        // ---- 5. the host comes back on 0.30.69
        fake.setUp(false); await sleep(800);
        fake.st.version = '0.30.69'; fake.setUp(true);
        await waitFor(p, `document.body.dataset.conn === 'on'`, 20000).catch(() => {});
        await fake.send(fake.prefsMsg()); await sleep(800);
        await evaluate(p, `document.getElementById('offLater')?.click(); true`); await sleep(200); await top();
        const k = await card();
        check(k.fix && k.raw && k.raw.text === HOST[lang] && !k.run && k.repair === 0 && k.words.includes(s.likely) && k.words.includes('0.30.65') && !k.words.includes(s.scary),
          '5 after the update this page keeps the plain words (drawn for 0.30.65) ' + tag + JSON.stringify(k));
        await fake.addTurn({ k: 'cmd', text: '/update' }, HOST[lang], 'done', { cmd: 'update', kind: 'error', repair: 'reconnect', system_name: 'AgentJ' });
        await waitFor(p, `!document.querySelector('#cmdx [data-old-updater-fix]') && document.getElementById('words').textContent.includes(${JSON.stringify(s.scary)})`, 10000)
          .catch(async (e) => fails.push('5 a new failure page on 0.30.69 still shows the fixed line ' + tag + ' ' + e.message + JSON.stringify(await card())));
        const n = await card();
        check(!n.fix && !n.raw && n.repair === 1, '5 a new failure page on 0.30.69 is the host\'s own card, 「修复连接」 as before ' + tag + JSON.stringify(n));
        check(!H.lines.length && fake.st.says.length === says0 && !H.cmds.length, '5 switch off: nothing was ever sent ' + tag);
      } else {
      await waitFor(p, `!!document.querySelector('#cmdx [data-old-updater-fix] .runbtn')`, 8000).catch((e) => fails.push('2 「执行」 never shown ' + tag + ' ' + e.message));
      await top();
      const b = await card();
      check(b.run && b.runFlag === '1' && b.leftOf && b.runH >= 44 && b.runW >= 44 && b.gap >= 8 && b.runAria.startsWith(s.run) && b.runTitle === s.run && !b.danger, '2 「执行并升级」 left of copy, ≥44 px, no red ' + tag + JSON.stringify(b));
      if (w === 390) check(b.seen.words && (lang === 'en' || b.seen.code && b.seen.run && b.seen.copy), '2 390 first screen: the whole paragraph (zh: the line, 执行 and copy too) ' + tag + JSON.stringify(b.seen));
      await save('run', lang === 'zh');
      await clickSel('#cmdx [data-old-updater-fix] .runbtn');
      await waitFor(p, `document.getElementById('term-sheetwrap')?.dataset.kind === 'intro' && !document.getElementById('term-sheetwrap').hidden && !document.getElementById('term-sh-yes').disabled`, 10000)
        .catch((e) => fails.push('2 intro sheet never shown ' + tag + ' ' + e.message));
      await clickSel('#term-sh-yes');
      await waitFor(p, `document.getElementById('term-sheetwrap')?.dataset.kind === 'confirm' && !document.getElementById('term-sheetwrap').hidden`, 10000)
        .catch((e) => fails.push('2 confirm card never shown ' + tag + ' ' + e.message + JSON.stringify(H.lines)));
      const l1 = H.lines[0];
      check(H.lines.length === 1 && l1.line === LINE && l1.run === 1 && l1.from === turn.id && /^[A-Za-z0-9_-]{22}$/.test(l1.bang || '') && !l1.signed && l1.keys === 'bang,from,id,line,run,seq,t',
        '2 exactly one unsigned term_line {the constant, bang, run:1, from: page id}, no other field ' + tag + JSON.stringify(H.lines));
      const sheet = await evaluate(p, `document.getElementById('term-sheetwrap').textContent`);
      check(sheet.includes(LINE) && sheet.includes(s.why), '2 the host\'s confirm card names the line and its why ' + tag + JSON.stringify(sheet.slice(0, 300)));
      if (lang === 'zh' && w === 390) await save('confirm', theme === 'light');
      await clickSel('#term-sh-no');
      await sleep(600);
      if (await evaluate(p, `!!document.getElementById('term') && !document.getElementById('term').hidden`)) await evaluate(p, `document.getElementById('term-exit')?.click(); true`);
      await sleep(300);
      const c1 = await card();
      check(H.lines.length === 1 && c1.box && c1.box.head.startsWith(s.refused), '2 取消: not run, said so, nothing more sent ' + tag + JSON.stringify([H.lines.length, c1.box]));
      await evaluate(p, `document.querySelector('#cmdx .runbox .runact').setAttribute('data-qa','again')`);
      await clickSel('[data-qa="again"]');
      await waitFor(p, `document.getElementById('term-sheetwrap')?.dataset.kind === 'confirm' && !document.getElementById('term-sheetwrap').hidden`, 10000)
        .catch((e) => fails.push('2 confirm card on 再跑一次 never shown ' + tag + ' ' + e.message));
      await clickSel('#term-sh-yes');
      for (let i = 0; i < 100 && !H.lines.some((x) => x.signed); i++) await sleep(50);
      const sgn = H.lines.find((x) => x.signed);
      check(H.lines.length === 3 && sgn && sgn.line === LINE && sgn.run === 1 && sgn.sigOk && !sgn.faSent && sgn.bang === H.lines[1].bang,
        '2 运行 → one signed resend of the same line (uv 0: no Face ID) ' + tag + JSON.stringify(H.lines));
      check(fake.st.says.length === says0 && !H.cmds.length && !H.other.length, '2 never a chat say, a cmd_run or another terminal frame ' + tag + JSON.stringify([fake.st.says.length - says0, H.cmds, H.other]));

      // ---- 3. the service restarts under the terminal
      await sleep(400);
      fake.setUp(false);
      await waitFor(p, `(document.querySelector('#cmdx .runbox .runhead')?.textContent||'').startsWith(${JSON.stringify(s.cut)})`, 10000)
        .catch(async (e) => fails.push('3 the box never said the disconnect is expected ' + tag + ' ' + e.message + JSON.stringify((await card()).box)));
      if (await evaluate(p, `!!document.getElementById('term') && !document.getElementById('term').hidden`)) await evaluate(p, `document.getElementById('term-exit')?.click(); true`);
      if (lang === 'zh' && w === 390 && theme === 'light') { await sleep(300); await evaluate(p, `document.querySelector('#cmdx .runbox').scrollIntoView({block:'center'})`); await save('cut'); }

      // ---- 4. the host is back on 0.30.69: the card stays in plain words, without 「执行」
      fake.st.version = '0.30.69'; fake.st.terminal = BLOCK({ kind: w === 390 ? 'phone' : 'desktop' });
      fake.setUp(true);
      await waitFor(p, `document.body.dataset.conn === 'on'`, 20000).catch(() => {});
      await fake.send(fake.prefsMsg());
      await waitFor(p, `!document.querySelector('#cmdx [data-old-updater-fix] .runbtn')`, 20000)
        .catch(async (e) => fails.push('4 a 0.30.69 host still offers 「执行」 ' + tag + ' ' + e.message + JSON.stringify(await card())));
      const d = await card();
      check(d.fix && d.raw && !d.run && d.repair === 0 && d.words.includes(s.likely) && !d.words.includes(s.scary), '4 0.30.69: the same page keeps the plain words, no 「执行」 ' + tag + JSON.stringify(d));
      check(H.lines.length === 3, '4 nothing was sent after the restart ' + tag);
      }

      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + JSON.stringify([problems.slice(0, 5), p.offsite.slice(0, 3)]));
      check(!H.errors.length, 'fake host saw no malformed message ' + tag + H.errors.join(' | '));
      console.log((fails.length ? 'DONE-WITH-FAILS ' : 'PASS ') + 'p0371 ' + tag + ' ' + sw);
    } catch (e) { fails.push('EXCEPTION ' + tag + ' ' + sw + ' ' + (e && e.stack || e)); await save('zz-exception').catch(() => {}); }
    finally { await p.dispose(); }
  }
  fake.st.onApp = null; fake.st.terminal = null; fake.st.version = null;
  console.log(`p0371: ${checks} checks passed, ${fails.length} failed; shots → ${out}`);
  for (const f of fails) console.log('FAIL ' + f);
  writeFileSync(resolve(out, 'browser.json'), JSON.stringify({ checks, fails, at: new Date().toISOString() }, null, 2));
  assert.equal(fails.length, 0, fails.join('\n'));
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP0371({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
