// P361 (PROTOCOL §34) Settings → 紧急修复 → 「电脑上的命令」 on the real page against the local fake host (real Noise; the §8
// control signature checked with wire.js like the host does), own headless Chromium (never :9222) with a CDP virtual platform
// authenticator as Face ID's stand-in. Checks:
//   · rows only for the host's `run_cmds` (unknown names dropped); an older host without it → the block stays hidden
//   · each row: `agentj …` as copyable code, a plain note, 「执行」 (≥ 44 px, named for screen readers)
//   · 执行 → signed cmd_run {cmd} (verified) → passkey_required → the Face ID sheet names the command → a real WebAuthn
//     assertion over the host's challenge → ctl_pending → running → the output as text (HTML stays text), exit code line,
//     the 「已遮住」 note, cut note; exit ≠ 0 says 「有需要处理的项」 and still shows the output
//   · cancel on the sheet → nothing runs; no passkey → the 「设置 Face ID」 offer, nothing runs; busy → plain words
//   · zh / en × light / dark at 390 px + zh 1440 px; shots → reports/qa/p361/web/ (P361_OUT)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect } from './browser.mjs';

const OUT = process.env.P361_OUT || 'reports/qa/p361/web';
const NAMES = ['doctor', 'reconnect', 'status', 'service_status', 'rescue_status', 'update_check'];
const L = {
  zh: { title: '电脑上的命令', run: '执行', fa: '用 Face ID 确认', ok: '执行完成（退出码 0）', fail: '执行完成，有需要处理的项（退出码 1）', masked: '结果里像密码、密钥的内容已经在电脑上遮住了。',
    busy: '上一条命令还在执行，请等它结束。', nopk: '执行命令前要先用 Face ID 确认，这台手机还没设置 Face ID。', cut: '结果太长，只显示前面一部分。' },
  en: { title: 'Commands on the computer', run: 'Run', fa: 'Confirm with Face ID', ok: 'Done (exit code 0)', fail: 'Done, with items that need attention (exit code 1)',
    masked: 'Anything that looks like a password or key was hidden on the computer.', busy: 'The previous command is still running; wait for it to finish.',
    nopk: 'Running a command needs Face ID first, and this phone has not set it up yet.', cut: 'The result is long; only the first part is shown.' },
};
const DOCTOR = '✓ version    agentj 0.23.0a1\n✓ relay      wss://relay.agentj.app (TLS + WebSocket)\n! asr        speech-to-text not installed\n✓ serve      running · relay connected\nOPENROUTER_API_KEY=[REDACTED]\n<b id="inj">x</b>\n';
const $ = (id) => `document.getElementById('${id}')`;
const b64u = (b) => Buffer.from(b).toString('base64url');

export async function runP361({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');   // WebAuthn wants a domain rp id
  const credId = randomBytes(16), fa = b64u(credId);
  const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
  // the host side: §34 — signature, two-step Face ID (the assertion's clientData must carry OUR challenge), pending, result
  const H = { calls: [], ran: [], passkey: true, hold: null, busy: false, next: null };
  fake.st.runCmds = [...NAMES, 'rm_rf'];
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'cmd_run') return false;
    const sigOk = await fake.verifyControl(c, 'cmd_run', m, { cmd: m.cmd });
    H.calls.push({ cmd: m.cmd, sigOk, fa: !!m.fa });
    const res = (x) => send({ t: 'ctl_res', r: m.r, action: 'cmd_run', cmd: m.cmd, ...x });
    if (!sigOk) return res({ ok: false, why: 'bad_signature' }), true;
    if (!H.passkey) return res({ ok: false, why: 'no_passkey' }), true;
    const ch = b64u(Buffer.from('p361-challenge-' + m.n));
    if (!m.fa) return res({ ok: false, why: 'passkey_required', fa, fa_ch: ch }), true;
    let cd = {};
    try { cd = JSON.parse(Buffer.from(m.fa.cd, 'base64url').toString()); } catch {}
    if (cd.type !== 'webauthn.get' || cd.challenge !== ch || m.fa.id !== fa) return res({ ok: false, why: 'passkey_failed' }), true;
    if (H.busy) return res({ ok: false, why: 'busy' }), true;
    H.ran.push(m.cmd);
    await send({ t: 'ctl_pending', r: m.r, action: 'cmd_run', timeout_ms: 150000 });
    if (H.hold) await H.hold.p;
    const x = H.next || { ok: true, code: 0, out: DOCTOR, ms: 1800 };
    H.next = null;
    await res(x);
    return true;
  };
  const gate = () => { let open; const p = new Promise((r) => { open = r; }); H.hold = { p, open }; };
  const release = () => { const g = H.hold; H.hold = null; g?.open(); };

  const combos = [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 390], ['zh', 'light', 1440], ['zh', 'dark', 1440]];
  for (const [lang, theme, w] of combos) {
    const s = L[lang], tag = `${lang}-${theme}-${w}`, full = w === 390 && theme === 'light';
    fake.reset(); fake.setUp(true); fake.st.runCmds = [...NAMES, 'rm_rf']; fake.st.prefs = { appearance: { language: lang, theme } }; H.calls.length = 0; H.ran.length = 0; H.passkey = true;
    const p = await newPage(B, w, w === 390 ? 844 : 900, theme, { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
    await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
      rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p361-fixture').toString('base64'), signCount: 0 } });
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${name}.png`), await shoot(p, ''));
    const view = (id, block = 'start') => evaluate(p, `${$(id)}.scrollIntoView({block:'${block}'}); true`).then(() => sleep(250));
    const click = async (id) => {
      await evaluate(p, `${$(id)}.scrollIntoView({block:'center'})`); await sleep(80);
      const r = await rect(p, '#' + id);
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    };
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      await evaluate(p, `import('./js/settings.js').then(m=>{m.openRescue();return true})`);
      await waitFor(p, `!${$('settings')}.hidden && !${$('rs-cmds')}.hidden`);

      // ---- the rows: the host's whitelist only, each copyable + 「执行」
      const rows = await evaluate(p, `[...document.querySelectorAll('#rs-cmd-list .rscmd')].map(li=>({n:li.dataset.cmd,code:li.querySelector('code').textContent,cc:li.querySelector('code').hasAttribute('data-cc'),note:li.querySelector('small').textContent,b:li.querySelector('.rscmdgo').textContent,aria:li.querySelector('.rscmdgo').getAttribute('aria-label')}))`);
      check(rows.map((r) => r.n).join() === NAMES.join(), 'rows = the host whitelist, unknown name dropped ' + tag);
      check(rows[0].code === 'agentj doctor' && rows[1].code === 'agentj doctor --reconnect' && rows.every((r) => r.cc && r.note.length > 4 && r.b === s.run && r.aria.includes(r.code)), 'code copyable, note, 执行 named ' + tag);
      check(await evaluate(p, `${$('rs-cmds-h')}.textContent`) === s.title, 'block title ' + tag);
      check(await evaluate(p, `!!${$('rs-cmds')}.closest('#set-s-rescue')`), 'inside 紧急修复 ' + tag);
      const audit = async (label) => {
        const a = await evaluate(p, `(()=>{const b=document.getElementById('rs-cmds');const small=[...b.querySelectorAll('button')].filter(e=>e.getClientRects().length).map(e=>{const r=e.getBoundingClientRect();return {id:e.id||e.className,w:Math.round(r.width),h:Math.round(r.height)}}).filter(x=>x.h<44||x.w<44);return {small,over:document.getElementById('set-main').scrollWidth>document.getElementById('set-main').clientWidth+1}})()`);
        check(!a.small.length && !a.over, `${label}: taps ≥ 44 px, no overflow ${tag} ${JSON.stringify(a)}`);
      };
      await audit('idle');
      await view('rs-cmds'); await save('1-list');

      // ---- 执行 doctor → Face ID sheet → assertion → pending (held: running) → output
      gate();
      await click('rs-cmd-doctor');
      await waitFor(p, `!${$('confirm')}.hidden`);
      check(await evaluate(p, `${$('confirm')}.textContent.includes('agentj doctor') && ${$('confirm')}.textContent.includes(${JSON.stringify(s.fa)})`), 'the sheet names the command ' + tag);
      check(H.calls.length === 1 && H.calls[0].sigOk && !H.calls[0].fa && !H.ran.length, 'first a signed request without Face ID; nothing ran ' + tag);
      if (full) await save('2-faceid-sheet');
      await click('confirm-yes');
      await waitFor(p, `!!document.querySelector('#rs-cmd-out .rsnow')`, 10000);
      for (let i = 0; i < 100 && !H.ran.length; i++) await sleep(50);   // the host side is held at ctl_pending
      check(H.calls.length === 2 && H.calls[1].sigOk && H.calls[1].fa && H.ran.join() === 'doctor', 'resent with a real assertion over the host challenge → ran ' + tag);
      check(await evaluate(p, `[...document.querySelectorAll('.rscmdgo')].every(b=>b.disabled)`), 'one at a time: buttons wait ' + tag);
      await view('rs-cmd-out', 'center'); if (full || w === 1440) await save('3-running');
      release();
      await waitFor(p, `!!${$('rs-cmd-out-text')}`);
      const res = await evaluate(p, `({head:${$('rs-cmd-head')}.textContent,ok:document.querySelector('#rs-cmd-out .rsok')?.textContent,out:${$('rs-cmd-out-text')}.textContent,inj:!!document.getElementById('inj'),notes:[...document.querySelectorAll('#rs-cmd-out .rsnote')].map(n=>n.textContent),cc:!!document.querySelector('#rs-cmd-out [data-cc]')})`);
      check(res.head === 'agentj doctor' && res.ok === s.ok, 'result head + exit line ' + tag);
      check(res.out === DOCTOR && !res.inj, 'output shown as text (HTML stays text) ' + tag);
      check(res.notes.includes(s.masked) && res.cc, 'masked note + copyable output ' + tag);
      check(!(await evaluate(p, `JSON.stringify(localStorage).includes('OPENROUTER_API_KEY')`)), 'output not stored ' + tag);
      await audit('result');
      await view('rs-cmd-out', 'end'); await save('4-doctor-done');

      // ---- exit ≠ 0: still shows the output, says what it means; a cut result says so
      H.next = { ok: false, code: 1, out: '✗ relay      reconnecting\n    → agentj doctor --reconnect\n', cut: true, ms: 900 };
      await click('rs-cmd-reconnect'); await waitFor(p, `!${$('confirm')}.hidden`); await click('confirm-yes');
      await waitFor(p, `${$('rs-cmd-head')}?.textContent==='agentj doctor --reconnect' && !!${$('rs-cmd-out-text')}`, 10000);
      check(await evaluate(p, `document.querySelector('#rs-cmd-out .rsfail').textContent`) === s.fail, 'exit 1 → 「有需要处理的项」 ' + tag);
      check(await evaluate(p, `[...document.querySelectorAll('#rs-cmd-out .rsnote')].some(n=>n.textContent===${JSON.stringify(s.cut)})`), 'cut note ' + tag);
      if (full) { await view('rs-cmd-out', 'end'); await save('5-reconnect-exit1'); }

      // ---- cancel on the sheet: nothing runs, the result block goes
      const ran = H.ran.length;
      await click('rs-cmd-status'); await waitFor(p, `!${$('confirm')}.hidden`); await click('confirm-no');
      await waitFor(p, `${$('confirm')}.hidden && ${$('rs-cmd-out')}.hidden`);
      check(H.ran.length === ran, 'cancel → nothing ran ' + tag);

      // ---- busy → plain words
      H.busy = true;
      await click('rs-cmd-status'); await waitFor(p, `!${$('confirm')}.hidden`); await click('confirm-yes');
      await waitFor(p, `document.querySelector('#rs-cmd-out .rsfail')?.textContent===${JSON.stringify(s.busy)}`, 10000);
      H.busy = false;
      check(H.ran.length === ran, 'busy → nothing ran ' + tag);

      // ---- no passkey on this phone: the 「设置 Face ID」 offer, nothing runs
      H.passkey = false;
      await click('rs-cmd-update_check');
      await waitFor(p, `document.querySelector('#rs-cmd-out .rsfail')?.textContent===${JSON.stringify(s.nopk)}`);
      check(await evaluate(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.nopk)}) && !!${$('toast')}.querySelector('button')`), 'no passkey → 设置 Face ID offer ' + tag);
      check(H.ran.length === ran && !(await evaluate(p, `!${$('confirm')}.hidden`)), 'no passkey → no sheet, nothing ran ' + tag);
      if (full) { await view('rs-cmd-out', 'end'); await save('6-no-passkey'); }
      H.passkey = true;

      // ---- an older host without run_cmds: the block stays hidden
      if (full) {
        fake.st.runCmds = null; fake.kick(); await waitState(p, 'ready', 20000);
        await evaluate(p, `import('./js/settings.js').then(m=>{m.openRescue();return true})`);
        await waitFor(p, `${$('rs-cmds')}.hidden`, 10000);
        check(true, 'older host → no block ' + tag);
      }
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS P361 ' + tag);
    } finally { release(); await p.dispose(); }
  }
  fake.st.onApp = null; fake.st.runCmds = null;
  console.log(`P361: ${checks} checks; shots → ${out}`);
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP361({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
