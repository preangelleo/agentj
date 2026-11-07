// #584 「回复里的终端命令可一键执行」 — QA e2e on the real page against the local fake host (real Noise; §8 control signatures
// checked like the host does), own headless Chromium (never :9222), a CDP virtual platform authenticator as Face ID's stand-in.
// Checks (per combo zh/en × light/dark × 390/1440):
//   1 own settled reply: ```sh blocks get `.term-command.runbtn` in the bar, LEFT of `.copy` (rects); a ```python block gets none;
//     friend_report / channel (telegram) / quoted / blockquote-inside-reply / streaming pages → no run button, copy still there
//   2 `agentj --version` → the plain-words `.fixbtn` (the command folded in details.fixcmd) → ONE signed cmd_run {cmd:'version'}
//     even on a double tap → the black box: output, 「完成 · 退出码 0」 + time
//   3 `ls -la` on a terminal-usable page (390 = a phone UA + host kind phone; 1440 = a computer) → intro → Face ID → exactly one term_line {bang, run:1, from:<page id>} →
//     term_confirm card → 取消 → 「没有执行：你选了取消」, no second term_line → 再跑一次 → card → 运行 → signed resend carries
//     run:1 → term_bang {run:1, out:'… [已隐藏] …', hidden:1} → masked text + the hidden note; 3d a line the host judges
//     direct result fixture → 再跑一次 = one unsigned line, no card, the result.
//     This FakeHost branch checks browser transport only, NOT H1 execution authority.
//     Current bb19 Host authority is tested separately in test_p584_h1_profile; ls requires confirmation.
//   4 `sudo pacman -Syu` → 「在终端里打开」 → the terminal shows the line in its input, nothing sent
//   5 host down → buttons .off / aria-disabled, a tap sends nothing, the offline toast
//   6 问 Agent → one say with reply_to = the page id, the text holds the command and the output
//   7 no console errors, nothing in storage, no off-site requests
// Shots → reports/qa/p584 (P584_OUT). P584_FIRST_ONLY=1 runs one combo.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateKeyPairSync, randomBytes, webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect } from './browser.mjs';
import { b64u } from '../../protocol/wire.js';

const subtle = webcrypto.subtle;
const enc = new TextEncoder();
const OUT = process.env.P584_OUT || 'reports/qa/p584';
const hex = (n) => randomBytes(n).toString('hex');
const PROMPT = 'owner@box:~/work$ ';
const PHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const BLOCK = (x = {}) => ({ mobile: true, resume: true, prefill: true, kind: 'desktop', on: true, os: true, me: true, web_ok: true, sudo: 'local', minutes: 15, idle_s: 300, open: false, modes: ['line', 'pty'], ...x });
const VERSION_OUT = 'agentj 0.28.5\n';
const LS_OUT = 'total 8\ndrwxr-xr-x 2 owner owner 4096 Nov  7 18:00 .\n-rw------- 1 owner owner   42 Nov  7 18:00 .env  OPENAI_API_KEY=[已隐藏]\n';
const REPLY = (lang) => (lang === 'zh' ? '可以这样看：\n\n' : 'Here is how:\n\n') +
  '```sh\nls -la\n```\n\n' + '```sh\nagentj --version\n```\n\n' + '```sh\nsudo pacman -Syu\n```\n\n' + '```python\nprint(1)\n```\n';
const L = {
  zh: { run: '执行', inTerm: '在终端里打开', ok: '完成 · 退出码 0', took: '用时', refused: '没有执行：你选了取消', hidden: '已隐藏', offline: 'Agent J 主机离线', version: '查看版本', ask: '问 ' },
  en: { run: 'Run', inTerm: 'Open in terminal', ok: 'Done · exit code 0', took: '', refused: 'Not run', hidden: 'hidden', offline: 'offline', version: 'version', ask: 'Ask ' },
};

export async function runP584({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const fails = [];
  const check = (ok, label) => { if (!ok) fails.push(label); else checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');
  const H = {};
  const resetH = () => Object.assign(H, { fa: null, ch: new Map(), calls: [], lines: [], cmds: [], hold: null, confirmNext: false, confirm: null, id: null, errors: [], other: [] });
  resetH();
  const faOk = (fa, ch) => { try { const cd = JSON.parse(Buffer.from(fa.cd, 'base64url').toString()); return cd.type === 'webauthn.get' && cd.challenge === ch && fa.id === H.fa; } catch { return false; } };
  fake.st.onApp = async (c, m, send) => {
    try {
      if (m.t === 'term_open' && !m.sig) {
        H.calls.push({ step: 1, mode: m.mode || 'line' });
        const n = hex(16), ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('p584-open-' + n))));
        H.ch.set(n, { ch, used: false, mode: m.mode || 'line' });
        return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'passkey_required', n, fa: H.fa, fa_ch: ch, salt: b64u(randomBytes(32)), epk: b64u(randomBytes(32)) }), true;
      }
      if (m.t === 'term_open') {
        const x = H.ch.get(m.n);
        const sigOk = !!x && await fake.verifyControl(c, 'term_open', m, { mode: x.mode });
        const fa = !!x && faOk(m.fa, x.ch);
        H.calls.push({ step: 2, sigOk, fa, mode: x?.mode });
        if (!x || x.used || !sigOk || !fa) return send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'bad_signature' }), true;
        x.used = true;
        await send({ t: 'ctl_pending', r: m.r, action: 'term_open', timeout_ms: 180000 });
        H.id = hex(8);
        await send({ t: 'term_ready', r: m.r, id: H.id, expires_at: Math.floor(Date.now() / 1000) + 900, idle_s: 300, sudo: 'local', cwd: '~/work' });
        await send({ t: 'term_out', id: H.id, seq: 1, data: PROMPT, prompt: true });
        return true;
      }
      if (m.t === 'term_line') {
        const signed = typeof m.sig === 'string';
        const rec = { line: m.line, seq: m.seq, bang: m.bang || null, run: m.run, from: m.from, signed };
        if (signed) {
          const x = H.confirm;
          rec.sigOk = !!x && m.n === x.n && await fake.verifyControl(c, 'term_line', m, { id: m.id, line: m.line });
          rec.fa = !!x && faOk(m.fa, x.fa_ch); rec.faSent = !!m.fa;
          H.confirm = null;
        }
        H.lines.push(rec);
        if (!signed && H.confirmNext) {   // Browser transport fixture only; final Host authorization is tested independently.
          H.confirm = { n: hex(16) }; H.confirm.fa_ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode('confirm-' + H.confirm.n))));
          await send({ t: 'term_confirm', id: m.id, seq: m.seq, line: m.line, why: 'Agent 填写的命令', why_en: 'filled in by the Agent', n: H.confirm.n, fa_ch: H.confirm.fa_ch, agent: 1, uv: 0 });
          return true;
        }
        await send({ t: 'term_out', id: m.id, seq: 9, data: LS_OUT + PROMPT, prompt: true });
        if (m.bang && m.run === 1) await send({ t: 'term_bang', id: m.id, sid: m.bang, ok: true, run: 1, state: 'done', out: LS_OUT, cut: false, hidden: 1, ms: 840 });
        return true;
      }
      if (m.t === 'cmd_run') {
        const sigOk = await fake.verifyControl(c, 'cmd_run', m, { cmd: m.cmd });
        H.cmds.push({ cmd: m.cmd, sigOk, fa: !!m.fa });
        const res = (x) => send({ t: 'ctl_res', r: m.r, action: 'cmd_run', cmd: m.cmd, ...x });
        if (!sigOk) return res({ ok: false, why: 'bad_signature' }), true;
        await send({ t: 'ctl_pending', r: m.r, action: 'cmd_run', timeout_ms: 150000 });
        if (H.hold) await H.hold.p;
        await res({ ok: true, code: 0, out: VERSION_OUT, ms: 420 });
        return true;
      }
      if (m.t && /^term_|^cmd_/.test(m.t)) H.other.push(m.t);
    } catch (e) { H.errors.push(String(e && e.stack || e)); }
    return false;
  };
  const gate = () => { let open; const p = new Promise((r) => { open = r; }); H.hold = { p, open }; };
  const release = () => { const g = H.hold; H.hold = null; g?.open(); };

  const combos = [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 390],
    ['zh', 'light', 1440], ['zh', 'dark', 1440], ['en', 'light', 1440], ['en', 'dark', 1440]];
  for (const [lang, theme, w] of (process.env.P584_FIRST_ONLY ? combos.slice(0, 1) : combos)) {
    const s = L[lang], tag = `${lang}-${theme}-${w}`;
    fake.reset(); fake.setUp(true);
    fake.st.prefs = { appearance: { language: lang, theme } }; fake.st.terminal = null; fake.st.autoReply = false;
    fake.st.runCmds = ['doctor', 'reconnect', 'status', 'version'];
    resetH();
    const p = await newPage(B, w, w === 390 ? 844 : 900, theme, { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => { const W = window.__w = { ls: [], ss: [], idb: 0 };
      const si = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { (this === sessionStorage ? W.ss : W.ls).push(String(k) + '=' + String(v).slice(0, 400)); return si.call(this, k, v); };
      for (const f of ['put', 'add']) { const o = IDBObjectStore.prototype[f]; IDBObjectStore.prototype[f] = function (...a) { W.idb++; try { W.ls.push('idb:' + JSON.stringify(a[0]).slice(0, 400)); } catch {} return o.apply(this, a); }; } })()` });
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${name}.png`), await shoot(p, ''));
    const clickSel = async (sel, n = 1) => {
      await evaluate(p, `document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`); await sleep(80);
      const r = await rect(p, sel);
      await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
      for (let i = 0; i < n; i++) {
        await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: i + 1 });
        await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: i + 1 });
      }
    };
    const blockSel = (i) => `#words .codeblock:nth-of-type(1)`;   // unused helper placeholder
    void blockSel;
    // the code block whose code text is `cmd` → a data marker for selectors
    const mark = (cmd) => evaluate(p, `(()=>{document.querySelectorAll('[data-qa]').forEach(e=>e.removeAttribute('data-qa'));const w=[...document.querySelectorAll('#words .codeblock')].find(b=>b.querySelector('code')?.textContent.replace(/\\n$/,'')===${JSON.stringify(cmd)});if(!w)return false;w.setAttribute('data-qa','blk');return true})()`);
    const showTurn = async (turnId) => waitFor(p, `(async()=>{return true})()`).then(() => sleep(1));
    void showTurn;
    try {
      // 17:58: phone and computer alike — the 390 combos are a phone (phone user agent, host kind 'phone')
      if (w === 390) await p.send('Emulation.setUserAgentOverride', { userAgent: PHONE_UA });
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      await evaluate(p, `if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click(); document.getElementById('phone-env-dismiss')?.click(); true`);
      const credId = randomBytes(16); H.fa = b64u(credId);
      const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
      await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
        rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p584-fixture').toString('base64'), signCount: 0 } });
      fake.st.terminal = BLOCK({ kind: w === 390 ? 'phone' : 'desktop' }); await fake.send(fake.prefsMsg()); await sleep(250);
      await evaluate(p, `import('./js/settings.js').then(m=>{m.render();return true})`);
      const st0 = JSON.parse(await evaluate(p, `import('./js/termhook.js').then(m=>JSON.stringify(m._debug()))`));
      check(st0.state === 'ok', 'terminal usable on this page (phone and computer alike) ' + tag + JSON.stringify(st0));
      if (w === 390) check(await evaluate(p, `import('./js/termhook.js').then(m=>!m.computerBrowser())`), '0 the 390 page is judged a phone ' + tag);
      const dev = fake.devs[0];
      const says0 = fake.st.says.length;

      // ---- 1. own reply
      const own = await fake.addTurn({ k: 'phone', dev: dev.id, name: '', text: lang === 'zh' ? '怎么看？' : 'How?' }, REPLY(lang), 'done');
      await waitFor(p, `[...document.querySelectorAll('#words .codeblock')].length >= 4`, 8000);
      await sleep(200);
      const lay = await evaluate(p, `(()=>{const out=[];for(const w of document.querySelectorAll('#words .codeblock')){const code=w.querySelector('code').textContent.replace(/\\n$/,'');const bar=w.querySelector('.bar');const rb=bar.querySelector('.runbtn'),cp=bar.querySelector('.copy');const r=rb&&rb.getBoundingClientRect(),c=cp&&cp.getBoundingClientRect();out.push({code,run:!!rb,text:rb?.textContent||'',aria:rb?.getAttribute('aria-label')||'',copy:!!cp,leftOf:!!(r&&c&&r.right<=c.left+0.5&&Math.abs((r.top+r.height/2)-(c.top+c.height/2))<6),order:rb?[...bar.children].indexOf(rb)<[...bar.children].indexOf(cp):null,h:r?Math.round(r.height):0,gap:r&&c?c.left-r.right:null,inFix:!!w.closest('details.fixcmd')})}return {out,fix:[...document.querySelectorAll('#words .fixbtn')].map(b=>b.getAttribute('aria-label')),over:document.documentElement.scrollWidth>innerWidth+1}})()`);
      const byCode = Object.fromEntries(lay.out.map((x) => [x.code, x]));
      check(byCode['ls -la']?.run && byCode['ls -la'].leftOf && byCode['ls -la'].order && byCode['ls -la'].text === '' && !!byCode['ls -la'].aria && byCode['ls -la'].h >= 44 && byCode['ls -la'].gap >= 8, '1 ls -la: run button left of copy, ≥44 px ' + tag + JSON.stringify(byCode['ls -la']));
      check(byCode['sudo pacman -Syu']?.run && byCode['sudo pacman -Syu'].leftOf && byCode['sudo pacman -Syu'].text === '' && byCode['sudo pacman -Syu'].aria.includes(s.inTerm), '4 sudo: 「在终端里打开」 left of copy ' + tag + JSON.stringify(byCode['sudo pacman -Syu']));
      check(byCode['print(1)'] && !byCode['print(1)'].run && byCode['print(1)'].copy, '1 python block: copy only ' + tag);
      check(byCode['agentj --version']?.inFix && lay.fix.some((t) => t.includes(s.version)), '2 agentj --version: plain-words fixbtn, command folded ' + tag + JSON.stringify(lay.fix));
      check(await evaluate(p, `[...document.querySelectorAll('#words .runbtn,#words .fixbtn')].every(b=>b.textContent===''&&b.querySelector('path')?.getAttribute('d')==='m8 5 11 7-11 7Z'&&getComputedStyle(b.querySelector('svg')).fill==='none')`), 'execution icons match Settings and have no visible text ' + tag);
      check(!lay.over, '1 no horizontal overflow ' + tag);
      await mark('ls -la'); await evaluate(p, `document.querySelector('[data-qa]').scrollIntoView({block:'center'})`);
      await save('1-own-reply');
      if (process.env.P584_PROBE) console.log('PROBE', await evaluate(p, `JSON.stringify({bg:getComputedStyle(document.documentElement).getPropertyValue('--bg'),theme:document.documentElement.dataset.theme,top:[...document.elementsFromPoint(10,${w === 390 ? 700 : 600})].slice(0,4).map(e=>e.id||e.className),scrims:[...document.querySelectorAll('.scrim,[class*=scrim]')].filter(e=>getComputedStyle(e).opacity>0&&e.getClientRects().length).map(e=>e.id||e.className)})`));

      // ---- 2. agentj --version → one cmd_run on a double tap
      gate();
      await clickSel('#words .fixbtn', 2);
      await sleep(300);
      const midBusy = await evaluate(p, `(()=>{const b=document.querySelector('#words .fixbtn');return {t:b.textContent,dis:b.disabled,box:!!document.querySelector('#words .runbox')}})()`);
      for (let i = 0; i < 40 && !H.cmds.length; i++) await sleep(50);
      await clickSel('#words .fixbtn', 1);    // a third tap while running
      await sleep(200);
      if (w === 390 && theme === 'light') await save('2a-version-running');
      release();
      await waitFor(p, `!!document.querySelector('#words .runbox .bangout') && document.querySelector('#words .runbox .bangout').textContent.includes('agentj 0.28.5')`, 8000)
        .catch((e) => { fails.push('2 version result never shown ' + tag + ' ' + e.message + JSON.stringify(H)); });
      const vbox = await evaluate(p, `(()=>{const b=document.querySelector('#words .runbox');return b&&{head:b.querySelector('.runhead')?.textContent,out:b.querySelector('.bangout')?.textContent,acts:[...b.querySelectorAll('.runact')].map(x=>x.textContent)}})()`);
      check(H.cmds.length === 1 && H.cmds[0].cmd === 'version' && H.cmds[0].sigOk, '2 exactly one signed cmd_run {cmd:version} for a double tap + a tap while running ' + tag + JSON.stringify(H.cmds));
      check(midBusy.dis || /运行中|Running/.test(midBusy.t), '2 button shows running / disabled while running ' + tag + JSON.stringify(midBusy));
      check(vbox && vbox.head.startsWith(s.ok) && /\d/.test(vbox.head.slice(s.ok.length)) && vbox.out === VERSION_OUT, '2 box: 完成 · 退出码 0 + time, output ' + tag + JSON.stringify(vbox));
      await evaluate(p, `document.querySelector('#words .runbox').scrollIntoView({block:'center'})`);
      await save('2-version-result');

      // ---- 3. ls -la via the terminal path
      H.confirmNext = true;
      await mark('ls -la');
      await clickSel('[data-qa] .runbtn');
      await waitFor(p, `document.getElementById('term-sheetwrap')?.dataset.kind === 'intro' && !document.getElementById('term-sheetwrap').hidden && !document.getElementById('term-sh-yes').disabled`, 10000)
        .catch((e) => fails.push('3 intro sheet never shown ' + tag + ' ' + e.message));
      if (w === 390 && theme === 'light') await save('3a-intro');
      await clickSel('#term-sh-yes');
      await waitFor(p, `document.getElementById('term-sheetwrap')?.dataset.kind === 'agent' && !document.getElementById('term-sheetwrap').hidden`, 10000)
        .catch((e) => fails.push('3 confirm card never shown ' + tag + ' ' + e.message + JSON.stringify({ calls: H.calls, lines: H.lines })));
      const l1 = H.lines[0];
      check(H.lines.length === 1 && l1.line === 'ls -la' && l1.run === 1 && l1.from === own.id && /^[A-Za-z0-9_-]{22}$/.test(l1.bang || '') && !l1.signed, '3 one term_line {bang, run:1, from:page id} ' + tag + JSON.stringify(H.lines));
      check(fake.st.says.length === says0, '3 no chat say for a run ' + tag);
      const card = await evaluate(p, `(()=>{const w=document.getElementById('term-sheetwrap');return {kind:w.dataset.kind,text:w.textContent.slice(0,300)}})()`);
      check(card.kind === 'agent' && card.text.includes('ls -la') && (lang === 'zh' ? card.text.includes('Agent 填写') : /filled in by the Agent/i.test(card.text)), '3 card = 「这条命令由 Agent 填写」, names the line ' + tag + JSON.stringify(card));
      await save('3-confirm-card');
      await clickSel('#term-sh-no');
      await sleep(600);
      // the terminal page may be up (collapsed after ready); look at the box in the chat
      const cbox = await evaluate(p, `(()=>{const w=document.querySelector('[data-qa]');const b=w&&w.nextElementSibling;return b&&b.classList.contains('runbox')?{head:b.querySelector('.runhead')?.textContent,acts:[...b.querySelectorAll('.runact')].map(x=>x.textContent)}:null})()`);
      check(H.lines.length === 1, '3 cancel: no second term_line ' + tag + JSON.stringify(H.lines));
      check(cbox && cbox.head.startsWith(s.refused), '3 cancel: box says not run ' + tag + JSON.stringify(cbox));
      const termShown = await evaluate(p, `!!document.getElementById('term') && !document.getElementById('term').hidden`);
      if (termShown) await evaluate(p, `document.getElementById('term-exit')?.click(); true`);
      await sleep(300);
      await mark('ls -la'); await evaluate(p, `document.querySelector('[data-qa]').scrollIntoView({block:'center'})`);
      await save('3b-cancelled');
      // 再跑一次 → confirm → 运行
      await evaluate(p, `(()=>{const b=document.querySelector('[data-qa]').nextElementSibling;[...b.querySelectorAll('.runact')][0].setAttribute('data-qa2','again');return true})()`);
      await clickSel('[data-qa2="again"]');
      await waitFor(p, `document.getElementById('term-sheetwrap')?.dataset.kind === 'agent' && !document.getElementById('term-sheetwrap').hidden`, 10000)
        .catch((e) => fails.push('3 confirm card on 再跑一次 never shown ' + tag + ' ' + e.message + JSON.stringify({ lines: H.lines })));
      await clickSel('#term-sh-yes');
      for (let i = 0; i < 100 && !H.lines.some((x) => x.signed); i++) await sleep(50);
      const sgn = H.lines.find((x) => x.signed);
      check(H.lines.length === 3 && H.lines[1].run === 1 && !H.lines[1].signed && sgn && sgn.run === 1 && sgn.bang === H.lines[1].bang && sgn.from === own.id && sgn.sigOk && !sgn.faSent,
        '3 再跑一次 → one new line; 运行 → signed resend (uv 0: no Face ID) carries run:1/bang/from ' + tag + JSON.stringify(H.lines));
      await waitFor(p, `(()=>{const w=document.querySelector('[data-qa]');const b=w&&w.nextElementSibling;return !!(b&&b.querySelector('.bangout'))})()`, 8000)
        .catch((e) => fails.push('3 run result never shown ' + tag + ' ' + e.message));
      if (await evaluate(p, `!!document.getElementById('term') && !document.getElementById('term').hidden`)) await evaluate(p, `document.getElementById('term-exit')?.click(); true`);
      await sleep(300);
      const rbox = await evaluate(p, `(()=>{const b=document.querySelector('[data-qa]').nextElementSibling;return b&&{head:b.querySelector('.runhead')?.textContent,out:b.querySelector('.bangout')?.textContent,note:b.querySelector('.bangnote')?.textContent||'',acts:[...b.querySelectorAll('.runact')].map(x=>x.textContent)}})()`);
      check(rbox && rbox.out === LS_OUT && rbox.out.includes('[已隐藏]') && !rbox.out.includes('sk-') && rbox.note.includes(s.hidden), '3 masked output + hidden note ' + tag + JSON.stringify(rbox));
      check(fake.st.says.length === says0, '3 still no chat say ' + tag);
      await evaluate(p, `document.querySelector('[data-qa]').nextElementSibling.scrollIntoView({block:'center'})`);
      await save('3c-run-result');
      // 3d synthetic direct-result transport: not a claim that the real Host authorizes ls directly.
      H.confirmNext = false;
      const nd = H.lines.length;
      await evaluate(p, `(()=>{const b=document.querySelector('[data-qa]').nextElementSibling;[...b.querySelectorAll('.runact')][0].setAttribute('data-qa2','again');return true})()`);
      await clickSel('[data-qa2="again"]');
      await waitFor(p, `(()=>{const w=document.querySelector('[data-qa]');const b=w&&w.nextElementSibling;return !!(b&&b.querySelector('.bangout')&&!/运行中|Running/.test(b.querySelector('.runhead').textContent))})()`, 8000)
        .catch((e) => fails.push('3d direct result never shown ' + tag + ' ' + e.message));
      const cardUp = await evaluate(p, `document.getElementById('term-sheetwrap')?.hidden === false && document.getElementById('term-sheetwrap').dataset.kind === 'agent'`);
      check(H.lines.length === nd + 1 && H.lines[nd].run === 1 && !H.lines[nd].signed && !cardUp, '3d synthetic Host direct-result transport, no card ' + tag + JSON.stringify(H.lines.slice(nd)));
      if (await evaluate(p, `!!document.getElementById('term') && !document.getElementById('term').hidden`)) await evaluate(p, `document.getElementById('term-exit')?.click(); true`);
      await sleep(300);
      if (theme === 'light') { await mark('ls -la'); await evaluate(p, `document.querySelector('[data-qa]').nextElementSibling.scrollIntoView({block:'center'})`); await save('3d-direct-result'); }

      // ---- 4. sudo → 在终端里打开, prefilled, nothing sent
      const nl = H.lines.length, nc = H.cmds.length;
      await mark('sudo pacman -Syu');
      await clickSel('[data-qa] .runbtn');
      await waitFor(p, `!!document.getElementById('term') && !document.getElementById('term').hidden`, 8000).catch((e) => fails.push('4 terminal never shown ' + tag + ' ' + e.message));
      await sleep(400);
      const pre = await evaluate(p, `(()=>{const i=document.getElementById('term-input');return {v:i?i.value:null,sheet:document.getElementById('term-sheetwrap')?.hidden===false?document.getElementById('term-sheetwrap').dataset.kind:''}})()`);
      check(pre.v === 'sudo pacman -Syu', '4 terminal input prefilled with the line ' + tag + JSON.stringify(pre));
      await sleep(300);
      check(H.lines.length === nl && H.cmds.length === nc && fake.st.says.length === says0, '4 nothing sent ' + tag);
      await save('4-sudo-prefilled');
      await evaluate(p, `(()=>{const i=document.getElementById('term-input');if(i)i.value='';document.getElementById('term-exit')?.click();return true})()`);
      await sleep(300);

      // ---- 6. 问 Agent on the ls box
      await mark('ls -la');
      await evaluate(p, `(()=>{const b=document.querySelector('[data-qa]').nextElementSibling;const a=[...b.querySelectorAll('.runact')].find(x=>x.textContent.startsWith(${JSON.stringify(s.ask)}));if(a)a.setAttribute('data-qa2','ask');return !!a})()`);
      const nSay = fake.st.says.length;
      if (await evaluate(p, `!!document.querySelector('[data-qa2="ask"]')`)) {
        await clickSel('[data-qa2="ask"]');
        for (let i = 0; i < 60 && fake.st.says.length === nSay; i++) await sleep(50);
        await sleep(300);
        const sy = fake.st.says.slice(nSay);
        check(sy.length === 1 && sy[0].reply_to === own.id && sy[0].text.includes('ls -la') && sy[0].text.includes('[已隐藏]'), '6 问 Agent → one say, reply_to page id, command + output ' + tag + JSON.stringify(sy.map((x) => ({ r: x.reply_to, t: x.text?.slice(0, 200) }))));
      } else fails.push('6 no 问 Agent button ' + tag);

      // ---- 1b. non-own pages
      const variants = [
        ['friend', { k: 'friend_report', friend: { id: 'AJ-FRGG-6PG6-V1WK-AAQA', name: 'Bob' }, text: 'report' }, 'done', '```sh\nls -la\n```\n'],
        ['channel', { k: 'telegram', text: 'hi', name: 'Bob' }, 'done', '```sh\nls -la\n```\n'],
        ['quoted', { k: 'phone', dev: dev.id, name: '', text: 'q', quote: { id: own.id, who: 'Agent', ts: Date.now(), text: 'x' } }, 'done', '```sh\nls -la\n```\n'],
        ['blockquote', { k: 'phone', dev: dev.id, name: '', text: 'b' }, 'done', '> quoted:\n>\n> ```sh\n> ls -la\n> ```\n\n' + 'ok\n'],
        ['streaming', { k: 'phone', dev: dev.id, name: '', text: 's' }, 'open', '```sh\nls -la\n```\n'],
      ];
      for (const [name, src, end, reply] of variants) {
        const tr = await fake.addTurn(src, reply, end);
        await sleep(700);
        const v = await evaluate(p, `({blocks:document.querySelectorAll('#words .codeblock').length,copy:document.querySelectorAll('#words .codeblock .copy').length,run:document.querySelectorAll('#words .runbtn,#words .fixbtn,#words .term-command').length,txt:document.getElementById('words').textContent.slice(0,120)})`);
        check(v.run === 0 && (name === 'friend' ? true : v.blocks >= 1 && v.copy >= 1), `1 ${name} page (turn ${tr.id}): copy only, no run button ` + tag + JSON.stringify(v));
        if (name === 'friend' || name === 'blockquote') await save('5-no-run-' + name);
        if (end === 'open') await fake.updateTurn(tr.id, { end: 'done', reply: { text: 'done' } });
      }
      // host-written page (k sys) naming a whitelisted repair → fixbtn
      await fake.addTurn({ k: 'sys', text: '' }, (lang === 'zh' ? '连接断了，运行 ' : 'The link dropped; run ') + '`agentj doctor --reconnect`', 'done');
      await sleep(700);
      const hp = await evaluate(p, `[...document.querySelectorAll('#words .fixbtn')].map(b=>b.getAttribute('aria-label'))`);
      check(hp.length === 1 && /修复连接|connection/i.test(hp[0]), '8 host page: 「修复连接」 fixbtn ' + tag + JSON.stringify(hp));
      await save('6-host-fix');

      // ---- 5. offline: back to the own reply (newest turn again), host down
      const off = await fake.addTurn({ k: 'phone', dev: dev.id, name: '', text: 'again' }, REPLY(lang), 'done');
      await waitFor(p, `document.querySelectorAll('#words .runbtn').length >= 2`, 8000).catch(() => {});
      const nl5 = H.lines.length, nc5 = H.cmds.length, ns5 = fake.st.says.length;
      fake.setUp(false);
      await waitFor(p, `[...document.querySelectorAll('#words .runbtn')].every(b=>b.classList.contains('off')&&b.getAttribute('aria-disabled')==='true')&&document.querySelectorAll('#words .runbtn').length>0`, 8000)
        .catch(() => fails.push('5 offline: buttons never turned .off/aria-disabled after the host went down (no repaint?) ' + tag));
      const offInfo = await evaluate(p, `[...document.querySelectorAll('#words .runbtn,#words .fixbtn')].map(b=>({c:b.className,a:b.getAttribute('aria-disabled'),t:b.textContent,o:getComputedStyle(b).opacity}))`);
      await sleep(1500);
      await save('7a-offline-dialog');
      if (await evaluate(p, `!!document.getElementById('offLater') && document.getElementById('offLater').getClientRects().length>0`)) await clickSel('#offLater');   // the owner taps 稍后
      await sleep(4500);   // let the terminal's own 「连接断了」 toast fade first
      await mark('ls -la');
      const hit = await evaluate(p, `(()=>{const b=document.querySelector('[data-qa] .runbtn');b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();const h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {hit:h&&(h.id||h.className),ok:b.contains(h),toastOn:document.getElementById('toast')?.className}})()`);
      if (await evaluate(p, `!!document.querySelector('[data-qa] .runbtn')`)) await clickSel('[data-qa] .runbtn');
      await sleep(300);
      const toastT = await evaluate(p, `document.getElementById('toast')?.className+' | '+(document.getElementById('toast')?.textContent||'')`) + ' hit=' + JSON.stringify(hit);
      check(H.lines.length === nl5 && H.cmds.length === nc5 && fake.st.says.length === ns5, '5 offline tap sends nothing ' + tag);
      check(toastT.includes(s.offline), '5 offline toast ' + tag + JSON.stringify({ toastT, offInfo }));
      await save('7-offline');
      fake.setUp(true);
      void off;

      const stor = await evaluate(p, `({ls:window.__w.ls.filter(k=>/ls -la|agentj 0\\.28|已隐藏|pacman|runbox|p584/i.test(k)),ss:window.__w.ss.filter(k=>/ls -la|agentj 0\\.28|已隐藏|pacman/i.test(k)),local:JSON.stringify(localStorage).match(/ls -la|agentj 0\\.28|已隐藏|pacman/)?.[0]||''})`);
      check(!stor.ls.length && !stor.ss.length && !stor.local, '7 nothing about runs in storage ' + tag + JSON.stringify(stor));
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, '7 no console errors / off-site requests ' + tag + JSON.stringify([problems.slice(0, 5), p.offsite.slice(0, 3)]));
      check(!H.errors.length, 'fake host saw no malformed message ' + tag + H.errors.join(' | '));
      console.log((fails.length ? 'DONE-WITH-FAILS ' : 'PASS ') + 'p584 ' + tag);
    } catch (e) { fails.push('EXCEPTION ' + tag + ' ' + (e && e.stack || e)); await save('zz-exception').catch(() => {}); }
    finally { await p.dispose(); }
  }
  fake.st.onApp = null; fake.st.terminal = null;
  console.log(`p584: ${checks} checks passed, ${fails.length} failed; shots → ${out}`);
  for (const f of fails) console.log('FAIL ' + f);
  writeFileSync(resolve(out, 'browser.json'), JSON.stringify({ checks, fails, at: new Date().toISOString() }, null, 2));
  assert.equal(fails.length, 0, fails.join('\n'));
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP584({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
