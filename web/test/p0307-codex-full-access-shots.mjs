#!/usr/bin/env node
// Restored from f71be54d73. Current-only fixture coverage; no installed service or real account/configuration.
// 0.30.7 — 「改回全放行并重启」 / "Back to full access & restart" on a Codex sandbox refusal page, every state, the real web client
// against fakehost.mjs (own headless Chromium, never :9222; CDP virtual platform authenticator = Face ID stand-in, rp localhost).
// The host side is the shape of host/agentj/cmdrun.py `_full_access` + codex_full_access.py (signed cmd_run {cmd}, passkey_required
// → assertion over the host challenge → ctl_res {ok, code, restart, why?} → agent_notice for refusal_text / the receipt).
// System-page texts are produced by the REAL host functions (agent_codex.POLICY_NOTICE + CONFIG_NOTICE, codex_full_access
// receipt_text / refusal_text, system_text.remote) through the host venv; nothing is hand-written here.
//  f1 button        k:"sys" refusal page with card {"fix":"codex_full_access"} → the button
//  f2 no field      the same refusal text without card.fix (offered() false: desktop thread / symlink / profile) → no button
//  f3 faceid        tap → one Face ID sheet with p0307.fix.faText (signed request already sent, nothing ran)
//  f4 restarting    Face ID → ok, restart "requested" → toast 「已改好，Agent J 正在重启…」
//  f5 receipt       serve back (link dropped + resumed) → the receipt system page 「已回到全放行：…」
//  f6 cancelled     「取消」 on the sheet → nothing ran, no toast
//  f7 unchanged     ok + why "unchanged" → 「已经是全放行，没有要改的。」
//  f8 manual        ok + restart "manual" (service not installed) → toast + the host's manual-restart page
//  f9 profile       ok:false why "profile_restricts" → 「没有改成…」 toast + the profile page
//  f10 symlink      ok:false why "config_invalid" detail not_regular → toast + the symlink page
//  f11 unsupported  host does not list codex_full_access in run_cmds → button disabled + the update note
// zh + en × light + dark × 390 × 844 and 1440 × 900 → $AJ_P0307_FIX_SHOTS (default reports/qa/0307-codex-full-access-shots/).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { randomBytes, generateKeyPairSync } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P0307_FIX_SHOTS || process.env.OLARES_RUN_OUT || join(ROOT, 'reports/qa/0307-codex-full-access-shots/');
const ONLY = process.env.AJ_P0307_ONLY || '';
mkdirSync(OUT, { recursive: true });

// ---- the real host texts (isolated HOME / XDG / CODEX_HOME; codex_perm reads stubbed so no real config.toml is read)
const PY = process.env.AJ_PYTHON || 'python3';
const fakeHome = mkdtempSync(join(tmpdir(), 'p0307-fix-home-'));
mkdirSync(fakeHome, { recursive: true });
const TEXT = JSON.parse(execFileSync(PY, ['-I', '-c', `
import sys, json; sys.path.insert(0, ${JSON.stringify(join(ROOT, 'agentjarvis/host'))})
from agentj import agent_codex as ac, codex_full_access as cfa, codex_perm, system_text
o = {'refusal': system_text.remote(ac.POLICY_NOTICE + ac.CONFIG_NOTICE.format(mode='workspace-write')
      + '（批准后命令会在 Codex 沙箱外运行，而它不只是读取：运行 pip install requests）')}
codex_perm.config_info = lambda: {'top': None}
codex_perm.profile_restricts = lambda: None
for lang in ('zh', 'en'):
    o['ok_' + lang] = system_text.remote(cfa.receipt_text({'restart': 'requested'}, lang)[1])
    o['manual_' + lang] = system_text.remote(cfa.receipt_text({'restart': 'manual'}, lang)[1])
    o['profile_' + lang] = system_text.remote(cfa.refusal_text({'why': 'profile_restricts', 'profile': {'profile': 'safe', 'mode': 'workspace-write'}}, lang))
    o['symlink_' + lang] = system_text.remote(cfa.refusal_text({'why': 'config_invalid', 'detail': 'not_regular'}, lang))
print(json.dumps(o, ensure_ascii=False))
`], { encoding: 'utf8', env: { PATH: process.env.PATH, HOME: fakeHome, XDG_CONFIG_HOME: join(fakeHome, '.config'), CODEX_HOME: join(fakeHome, '.codex') } }));

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const PAGE = web.url.replace('127.0.0.1', 'localhost');     // WebAuthn wants a domain rp id
const ev = (p, s) => evaluate(p, s);
const J = JSON.stringify;
const b64u = (b) => Buffer.from(b).toString('base64url');
let checks = 0;
const check = (v, label) => { assert.ok(v, label); checks++; };
const credId = randomBytes(16), FA = b64u(credId);
const PRIV = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
const RUN_CMDS = ['doctor', 'reconnect', 'status', 'codex_full_access'];
const FIX = { fix: 'codex_full_access' };
const BTN = '#cmdx [data-fix="codex_full_access"]';
const tr = (p, k, a) => ev(p, `import('./js/t.js').then(m=>m.t(${J(k)}${a ? ',' + J(a) : ''}))`);
const toastHas = (s) => `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${J(s)})`;
const sheetOpen = `!document.getElementById('confirm').hidden`;

// the host: §34 cmd_run — signature, passkey_required + challenge, assertion over OUR challenge, then the result H.mode
const H = { calls: [], ran: 0, mode: 'requested', lang: 'zh' };
fake.st.onApp = null;
const handler = async (c, m, send) => {
  if (m.t !== 'cmd_run') return false;
  const sigOk = await fake.verifyControl(c, 'cmd_run', m, { cmd: m.cmd });
  H.calls.push({ cmd: m.cmd, sigOk, fa: !!m.fa });
  const res = (x) => send({ t: 'ctl_res', r: m.r, action: 'cmd_run', cmd: m.cmd, ...x });
  if (!sigOk || m.cmd !== 'codex_full_access') return res({ ok: false, why: 'bad_signature' }), true;
  const ch = b64u(Buffer.from('p0307-fix-challenge-' + m.n));
  if (!m.fa) return res({ ok: false, why: 'passkey_required', fa: FA, fa_ch: ch }), true;
  let cd = {};
  try { cd = JSON.parse(Buffer.from(m.fa.cd, 'base64url').toString()); } catch { /* bad */ }
  if (cd.type !== 'webauthn.get' || cd.challenge !== ch || m.fa.id !== FA) return res({ ok: false, why: 'passkey_failed' }), true;
  H.ran++;
  const base = { code: 0, out: '', ms: 0 };
  if (H.mode === 'requested') await res({ ok: true, ...base, restart: 'requested' });
  else if (H.mode === 'unchanged') await res({ ok: true, ...base, restart: 'none', why: 'unchanged' });
  else if (H.mode === 'manual') { await res({ ok: true, ...base, restart: 'manual' }); await sleep(120); await fake.addTurn({ k: 'sys', text: '', local: true }, TEXT['manual_' + H.lang]); }
  else if (H.mode === 'profile') { await res({ ok: false, ...base, code: 2, restart: 'none', why: 'profile_restricts' }); await sleep(120); await fake.addTurn({ k: 'sys', text: '' }, TEXT['profile_' + H.lang]); }
  else if (H.mode === 'symlink') { await res({ ok: false, ...base, code: 2, restart: 'none', why: 'config_invalid' }); await sleep(120); await fake.addTurn({ k: 'sys', text: '' }, TEXT['symlink_' + H.lang]); }
  return true;
};

async function press(p, sel, touch) {
  const r = await ev(p, `(()=>{const n=document.querySelector(${J(sel)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  await sleep(80);
  if (touch) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [r] });
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } else {
    await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  }
}
async function open(w, h, scheme, lang, { runCmds = RUN_CMDS } = {}) {
  fake.reset(); fake.setUp(true);
  fake.st.onApp = handler; fake.st.runCmds = runCmds; H.lang = lang; H.calls.length = 0; H.ran = 0;
  fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
  const p = await newPage(B, w, h, scheme, { touch: w < 500, allow: [web.url, PAGE, fake.relay + '/'] });
  await p.send('WebAuthn.enable', { enableUI: false });
  const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal',
    hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
    rpId: 'localhost', privateKey: PRIV, userHandle: Buffer.from('p0307-fix-fixture').toString('base64'), signCount: 0 } });
  await navigate(p, fake.newPairing(PAGE + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'codex', name: 'Agent J' });
  await ev(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal(${J(scheme)});return true})`);
  await ev(p, `document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();true`);
  await sleep(300);
  return p;
}
async function refusal(p, card = FIX) {
  await fake.addTurn({ k: 'sys', text: '', local: true }, TEXT.refusal, 'done', card);
  await waitFor(p, `document.body.innerText.includes('config.toml')`);
  await sleep(350);
}
const LAYOUT = `(()=>{const vw=innerWidth,vh=innerHeight,bad=[];
  for(const n of document.querySelectorAll('#cmdx, #cmdx *, #confirm .box *, #toast')){const r=n.getBoundingClientRect();
    if(r.width&&(r.right>vw+1||r.left<-1)&&getComputedStyle(n).visibility!=='hidden'&&!n.closest('[hidden]'))bad.push((n.id||n.className||n.tagName)+':'+Math.round(r.left)+'..'+Math.round(r.right));}
  const b=document.querySelector(${J(BTN)});if(b&&!b.closest('[hidden]')){const r=b.getBoundingClientRect();if(r.height<44)bad.push('fix h='+r.height);if(b.scrollWidth>b.clientWidth+1)bad.push('fix text clipped');}
  for(const id of ['confirm-yes','confirm-no']){const e=document.getElementById(id);if(e&&!document.getElementById('confirm').hidden&&!e.hidden){const r=e.getBoundingClientRect();if(r.height<44||r.bottom>vh+1)bad.push(id+' h='+r.height+' b='+r.bottom);}}
  return bad;})()`;
const layoutOk = async (p, label) => { const bad = await ev(p, LAYOUT); check(!bad.length, 'layout ' + label + ' :: ' + bad.join(' ')); };
const index = [];
const save = async (p, state, name, note) => { const f = `${state}-${name}.png`; writeFileSync(join(OUT, f), await shoot(p, '')); index.push([state, f, note]); };
const toastOff = (p) => ev(p, `!document.getElementById('toast').classList.contains('on')`);
async function tapAndConfirm(p, touch) {
  await press(p, BTN, touch);
  await waitFor(p, sheetOpen); await sleep(250);
  await press(p, '#confirm-yes', touch);
}

const VIEWS = [];
for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) VIEWS.push([w, h, scheme, lang]);
try {
  for (const [w, h, scheme, lang] of VIEWS) {
    const name = `${w}-${scheme}-${lang}`, touch = w < 500;
    if (ONLY && ONLY !== name) continue;
    // ---- f2 no field, then f1 button → f3 Face ID → f4 restarting → f5 receipt
    H.mode = 'requested';
    let p = await open(w, h, scheme, lang);
    await refusal(p, null);
    check(!(await ev(p, `!!document.querySelector(${J(BTN)})`)), 'no card.fix → no button ' + name);
    await layoutOk(p, 'f2 ' + name);
    await save(p, 'f2-no-field', name, 'same refusal text without card.fix → no button');
    await refusal(p);
    await waitFor(p, `!!document.querySelector(${J(BTN)}) && !document.querySelector(${J(BTN)}).disabled`);
    check(await ev(p, `document.querySelector(${J(BTN)}).textContent`) === await tr(p, 'p0307.fix.button'), 'button label ' + name);
    check(await ev(p, `!document.getElementById('cmdx').hidden && document.getElementById('cmdx').getClientRects().length>0`), 'button box shown ' + name);
    await layoutOk(p, 'f1 ' + name);
    // visible = what is actually painted at the button's centre is the button (the page card clips its own scroll area)
    const fold = await ev(p, `(()=>{const b=document.querySelector(${J(BTN)}),r=b.getBoundingClientRect();if(r.bottom>innerHeight||r.top<0)return false;const h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return !!h&&(h===b||b.contains(h))})()`);
    index.push(['f1-note', '', `button inside the first view without scrolling: ${fold}`]);
    await save(p, 'f1-button', name, 'refusal page as it opens (first view; button ' + (fold ? 'visible' : 'below the fold') + ')');
    await ev(p, `document.querySelector(${J(BTN)}).scrollIntoView({block:'center'});true`); await sleep(300);
    await save(p, 'f1b-button-in-view', name, 'same page scrolled to 「改回全放行并重启」');
    await press(p, BTN, touch);
    await waitFor(p, sheetOpen); await sleep(300);
    check((await ev(p, `document.getElementById('confirm').textContent`)).includes(await tr(p, 'p0307.fix.faText')), 'Face ID sheet text ' + name);
    check(H.calls.length === 1 && H.calls[0].sigOk && !H.calls[0].fa && H.ran === 0, 'signed request sent, nothing ran before Face ID ' + name);
    await layoutOk(p, 'f3 ' + name);
    await save(p, 'f3-faceid-sheet', name, 'one Face ID / passkey sheet (p0307.fix.faText), nothing ran yet');
    await press(p, '#confirm-yes', touch);
    await waitFor(p, toastHas(await tr(p, 'p0307.fix.restarting')));
    check(H.ran === 1 && H.calls[1]?.fa && H.calls[1]?.sigOk, 'one signed run with the assertion ' + name);
    await sleep(300);
    await layoutOk(p, 'f4 ' + name);
    await save(p, 'f4-restarting', name, 'restart requested → toast');
    // serve restarts: the link drops and comes back, the new serve posts the receipt once
    fake.kick(1001);
    await sleep(600);
    await ev(p, `import('./js/session.js').then(s=>s.reconnectNow?.())`);
    await waitState(p, 'ready', 20000);
    await fake.addTurn({ k: 'sys', text: '' }, TEXT['ok_' + lang]);
    await waitFor(p, `document.body.innerText.includes(${J(TEXT['ok_' + lang].slice(0, 12))})`);
    await waitFor(p, `!document.getElementById('toast').classList.contains('on')`, 12000).catch(() => {});
    await sleep(300);
    await layoutOk(p, 'f5 ' + name);
    await save(p, 'f5-receipt-back-to-full', name, 'after restart: the receipt system page 「已回到全放行」');
    check(!p.problems.length, 'no console errors f1-f5 ' + name + ' ' + p.problems.join(' | '));
    await p.dispose();

    // ---- f6 cancelled on the sheet
    p = await open(w, h, scheme, lang);
    await refusal(p);
    await waitFor(p, `!!document.querySelector(${J(BTN)}) && !document.querySelector(${J(BTN)}).disabled`);
    await press(p, BTN, touch);
    await waitFor(p, sheetOpen); await sleep(200);
    await press(p, '#confirm-no', touch);
    await waitFor(p, `document.getElementById('confirm').hidden`); await sleep(500);
    check(H.ran === 0 && H.calls.length === 1 && await toastOff(p), 'cancel → nothing ran, nothing said ' + name);
    check(await ev(p, `!document.querySelector(${J(BTN)}).disabled`), 'button usable again after cancel ' + name);
    await save(p, 'f6-cancelled', name, '「取消」 → nothing ran, no toast, button back');
    await p.dispose();

    // ---- f7..f10 the other host answers
    for (const [mode, state, key, note] of [
      ['unchanged', 'f7-unchanged', 'p0307.fix.already', 'already full access (why unchanged)'],
      ['manual', 'f8-manual', 'p0307.fix.manual', 'service not installed → restart manual: toast + host page'],
      ['profile', 'f9-profile-restricts', 'p0307.fix.failed', 'profile_restricts: toast + host page naming [profiles.safe]'],
      ['symlink', 'f10-config-symlink', 'p0307.fix.failed', 'config_invalid / not_regular: toast + host page'],
    ]) {
      H.mode = mode;
      p = await open(w, h, scheme, lang);
      await refusal(p);
      await waitFor(p, `!!document.querySelector(${J(BTN)}) && !document.querySelector(${J(BTN)}).disabled`);
      await tapAndConfirm(p, touch);
      await waitFor(p, toastHas(await tr(p, key)));
      check(H.ran === 1, mode + ': ran once after Face ID ' + name);
      if (mode !== 'unchanged') {
        const page = TEXT[{ manual: 'manual_', profile: 'profile_', symlink: 'symlink_' }[mode] + lang];
        await waitFor(p, `document.body.innerText.includes(${J(page.slice(0, 14))})`);
      }
      await sleep(400);
      await layoutOk(p, state + ' ' + name);
      await save(p, state, name, note);
      await p.dispose();
    }

    // ---- f11 an older host (no codex_full_access in run_cmds): the button is there but disabled, with the update note
    p = await open(w, h, scheme, lang, { runCmds: ['doctor', 'reconnect', 'status'] });
    await refusal(p);
    await waitFor(p, `!!document.querySelector(${J(BTN)})`);
    check(await ev(p, `document.querySelector(${J(BTN)}).disabled`), 'unsupported → disabled ' + name);
    check((await ev(p, `document.getElementById('cmdx').textContent`)).includes(await tr(p, 'p0307.fix.unsupported')), 'unsupported note ' + name);
    await press(p, BTN, touch); await sleep(300);
    check(H.calls.length === 0, 'disabled button never sends ' + name);
    await layoutOk(p, 'f11 ' + name);
    await save(p, 'f11-unsupported-host', name, 'host without the name → disabled + 「先升级 Agent J」 note');
    await p.dispose();
    console.log('ok', name);
  }
  const lines = ['# 0.30.7 — 「改回全放行并重启」 Codex sandbox refusal button, every state', '',
    'Real web client against `web/test/fakehost.mjs`; CDP virtual authenticator for Face ID; host texts from the real host functions.',
    'Generated by `web/test/p0307-codex-full-access-shots.mjs`.', '', '| state | file | note |', '|---|---|---|',
    ...index.map(([s, f, n]) => `| ${s} | ${f ? '`' + f + '`' : '—'} | ${n} |`), ''];
  if (!ONLY) writeFileSync(join(OUT, 'index.generated.md'), lines.join('\n'));
  writeFileSync(join(OUT, 'browser.json'), JSON.stringify({ pass: true, checks, shots: index.filter((x) => x[1]).length, foldNotes: index.filter((x) => !x[1]), fixture: 'fakehost + virtual authenticator; texts via host venv' }, null, 2));
  console.log(`p0307-codex-full-access-shots: ${checks} checks passed, ${index.filter((x) => x[1]).length} shots → ${OUT}`);
} catch (e) {
  writeFileSync(join(OUT, 'browser.json'), JSON.stringify({pass:false,checks,index,error:String(e.stack||e)},null,2));
  console.error('p0307-codex-full-access-shots FAILED:', e && e.stack || e);
  process.exitCode = 1;
} finally {
  await B.close();
  await fake.stop();
  await web.stop();
  rmSync(fakeHome,{recursive:true,force:true});
}
