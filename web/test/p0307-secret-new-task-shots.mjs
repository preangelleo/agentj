#!/usr/bin/env node
// 0.30.7 N4b inside Settings (full-screen page again since the 0308 hotfix; the secret card is the centred popup): Settings → 邮件 → 「设置邮箱授权码」 (mail_key). The host shows the
// secret card (AGENTJ_MAIL_PASSWORD); a new task starts before the write → the card ends `elev_done {result:'unknown', why}`
// and the Settings request ends `mail_res {ok:false, why}` (host serve.py mail_key → answer(detail or why)), why =
// new_task | new_task_during_validation (approval_special.LiveContextMoved.REASONS). The page must say
// p0307.secret.newTask / p0307.secret.newTaskValidated — in the mail panel line (#mail-msg) and as the card's notice —
// visibly, inside the overlay, not hidden behind it.
//  n1 mail page        Settings → 邮件, address + provider saved, password not yet set
//  n2 secret card      after 「设置邮箱授权码」: the host's secret card over the overlay
//  n3 new_task         the card ended + Settings answer: the in-panel line says it ONCE — no notice repeating it on top
//                      (0.30.7 candidate, chairman 11-11: the notice and the overlay said the same sentence for 8 s)
//  n3b new_task later  8 s on: still no notice, the in-panel line stays
//  n4 validated        why = new_task_during_validation: in-panel line once, no repeating notice
// zh + en × light + dark × 390 × 844 and 1440 × 900 → $AJ_P0307_N4B_SHOTS (default reports/qa/0307-secret-new-task-shots/).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { randomBytes, webcrypto } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P0307_N4B_SHOTS || join(ROOT, 'reports/qa/0307-secret-new-task-shots/');
const ONLY = process.env.AJ_P0307_ONLY || '';
mkdirSync(OUT, { recursive: true });
const PY = process.env.AJ_PYTHON || join(ROOT, 'host/.venv/bin/python');
const fakeHome = join(process.env.TMPDIR || join(homedir(), '.cache'), 'p0307-n4b-home');
mkdirSync(fakeHome, { recursive: true });
// the mailbox view and the card's purpose text from the real host functions (synthetic address)
const HOSTV = JSON.parse(execFileSync(PY, ['-I', '-c', `
import sys, json; sys.path.insert(0, ${JSON.stringify(join(ROOT, 'agentjarvis/host'))})
from agentj import mail
o = {}
for lang in ('zh', 'en'):
    c = mail.apply_preset(mail.blank_config(), 'qq', 'agent@example.com')
    o[lang] = {'view': {'config': c, 'presets': mail.preset_list(lang), 'has_password': False, 'today': {}, 'blocked': []},
               'purpose': mail.password_purpose(c, lang)}
print(json.dumps(o, ensure_ascii=False))
`], { encoding: 'utf8', env: { PATH: process.env.PATH, HOME: fakeHome, XDG_CONFIG_HOME: join(fakeHome, '.config') } }));

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
const J = JSON.stringify;
const hex = () => randomBytes(16).toString('hex');
const b64u = (b) => Buffer.from(b).toString('base64url');
let checks = 0;
const check = (v, label) => { assert.ok(v, label); checks++; };
const tr = (p, k) => ev(p, `import('./js/t.js').then(m=>m.t(${J(k)}))`);
const K = { new_task: 'p0307.secret.newTask', new_task_during_validation: 'p0307.secret.newTaskValidated' };

const S = { lang: 'zh', card: null, keyR: null, conn: null, keys: 0 };
const handler = async (c, m, send) => {
  if (typeof m.t !== 'string' || !m.t.startsWith('mail_')) return false;
  const v = HOSTV[S.lang].view;
  if (m.t === 'mail_key') {
    S.keys++; S.keyR = m.r; S.send = send;
    const pair = await webcrypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    S.card = { t: 'elev', id: hex(), n: hex(), kind: 'secret', epk: b64u(new Uint8Array(await webcrypto.subtle.exportKey('raw', pair.publicKey))),
      ttl: 600, tries: 3, name: 'AGENTJ_MAIL_PASSWORD', purpose: HOSTV[S.lang].purpose,
      dest: S.lang === 'zh' ? 'Agent J 邮件通道（本机加密）' : 'Agent J mail channel (encrypted on the host)', verify: 'IMAP + SMTP login', request: m.r };
    await send(S.card);
    return true;
  }
  await send({ t: 'mail_res', r: m.r, ok: true, view: v });
  return true;
};
async function end(why) {
  await fake.send({ t: 'elev_done', id: S.card.id, result: 'unknown', why });
  await S.send({ t: 'mail_res', r: S.keyR, ok: false, why, card: 'unknown', view: HOSTV[S.lang].view });
}

async function open(w, h, scheme, lang) {
  fake.reset(); fake.setUp(true);
  fake.st.onApp = handler; S.lang = lang; S.keys = 0; S.card = null;
  fake.st.prefSet = true;
  fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
  const p = await newPage(B, w, h, scheme, { touch: w < 500, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();true`);
  await sleep(250);
  await ev(p, `document.getElementById('setBtn').click();true`);
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await ev(p, `document.getElementById('set-mail-nav').click();true`);
  await waitFor(p, `document.getElementById('mail-addr')?.value==='agent@example.com' && !document.getElementById('mail-key').disabled`);
  await sleep(300);
  return p;
}
// (the notice is pointer-events:none; the stacking probe lifts that for one elementFromPoint and restores it)
// the overlay frame, the notice and the in-panel line: centered, inside the viewport, the line not covered by anything
const GEOM = (key) => `(async()=>{const {t}=await import('./js/t.js');const want=t(${J(key)});
  const box=document.querySelector('#settings>.setbox').getBoundingClientRect(),vw=innerWidth,vh=innerHeight;
  const msg=document.getElementById('mail-msg');msg.scrollIntoView({block:'center'});await new Promise(r=>setTimeout(r,250));
  const m=msg.getBoundingClientRect(),hit=document.elementFromPoint(m.left+Math.min(m.width/2,40),m.top+m.height/2);
  const n=document.getElementById('toast'),tr=n.getBoundingClientRect();
  return {centered:box.width>=0.9*vw&&box.height>=0.9*vh,inView:box.x>=0&&box.y>=0&&box.right<=vw+1&&box.bottom<=vh+1,
    line:msg.textContent===want,lineInBox:m.left>=box.left-1&&m.right<=box.right+1&&m.top>=box.top&&m.bottom<=box.bottom,
    lineClipped:msg.scrollWidth>msg.clientWidth+1,lineOnTop:!!hit&&(hit===msg||msg.contains(hit)),
    toastOn:n.classList.contains('on'),toastText:n.textContent.includes(want),toastInView:tr.left>=0&&tr.right<=vw+1&&tr.top>=0&&tr.bottom<=vh+1,
    toastAbove:n.classList.contains('on')?(()=>{const pe=n.style.pointerEvents;n.style.pointerEvents='auto';const h=document.elementFromPoint(tr.left+tr.width/2,tr.top+tr.height/2);n.style.pointerEvents=pe;return !!h&&(h===n||n.contains(h))})():null};})()`;
const index = [];
const save = async (p, state, name, note) => { const f = `${state}-${name}.png`; writeFileSync(OUT + f, await shoot(p, '')); index.push([state, f, note]); };

const VIEWS = [];
for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) VIEWS.push([w, h, scheme, lang]);
try {
  for (const [w, h, scheme, lang] of VIEWS) {
    const name = `${w}-${scheme}-${lang}`;
    if (ONLY && ONLY !== name) continue;
    for (const why of ['new_task', 'new_task_during_validation']) {
      const p = await open(w, h, scheme, lang);
      if (why === 'new_task') await save(p, 'n1-mail-page', name, 'Settings → 邮件 in the centered overlay, before the password');
      await ev(p, `document.getElementById('mail-key').click();true`);
      await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`, 15000);
      check(S.keys === 1, 'one mail_key ' + why + ' ' + name);
      await sleep(350);
      if (why === 'new_task') await save(p, 'n2-secret-card', name, 'the host secret card (AGENTJ_MAIL_PASSWORD) over the overlay');
      await ev(p, `(()=>{window.__echo=false;window.__echoT=setInterval(()=>{const n=document.getElementById('toast'),m=document.getElementById('mail-msg');if(n.classList.contains('on')&&m.textContent&&n.textContent.trim()===m.textContent.trim())window.__echo=true},50);return true})()`);
      await end(why);
      await waitFor(p, `document.getElementById('elev').hidden`);
      const want = await tr(p, K[why]);
      await waitFor(p, `document.getElementById('mail-msg').textContent===${J(want)}`);
      await sleep(600);
      const echo = await ev(p, `(()=>{clearInterval(window.__echoT);return window.__echo})()`);
      const asIs = await ev(p, `(()=>{const r=document.getElementById('mail-msg').getBoundingClientRect(),b=document.querySelector('#settings>.setbox').getBoundingClientRect();return r.height>0&&r.top>=b.top&&r.bottom<=b.bottom})()`);
      index.push([(why === 'new_task' ? 'n3' : 'n4') + '-note', '', `${name}: in-panel line visible without scrolling right after the answer: ${asIs}`]);
      await save(p, (why === 'new_task' ? 'n3a-new-task' : 'n4a-new-task-validated') + '-as-answered', name, `why=${why}: exactly what the page shows when the answer lands — the sentence once, in the panel line (no scrolling; line ${asIs ? 'in view' : 'NOT in view'}; repeated notice seen: ${echo})`);
      const g = await ev(p, GEOM(K[why]));
      check(g.line, `${why}: in-panel line is the N4b sentence ${name}`);
      check(g.centered && g.inView, `${why}: Settings still full-screen (0308) and inside the viewport ${name} ${J(g)}`);
      check(g.lineInBox && !g.lineClipped, `${why}: line inside the overlay frame, not clipped ${name} ${J(g)}`);
      check(g.lineOnTop, `${why}: line not covered ${name} ${J(g)}`);
      check(!(g.toastOn && g.toastText), `${why}: no notice repeating the in-panel sentence while the overlay is open ${name} ${J(g)}`);
      check(echo === false, `${why}: the sentence was never on screen twice (sampled every 50 ms from the card's end) ${name} ${J(echo)}`);
      check(!(await ev(p, `document.getElementById('mail-msg').textContent.includes(${J((await tr(p, 'mail.keyFail')).slice(0, 6))})`)), `${why}: not the generic keyFail line ${name}`);
      await sleep(150);
      await save(p, why === 'new_task' ? 'n3-new-task' : 'n4-new-task-validated', name, `why=${why}: scrolled to the in-panel line; line only, no repeating notice (${K[why]})`);
      if (why === 'new_task') {
        await sleep(8200);
        const g2 = await ev(p, GEOM(K[why]));
        check(g2.line && g2.lineOnTop && !g2.toastOn, `${why}: after the notice the line stays ${name}`);
        await save(p, 'n3b-new-task-line-stays', name, '8 s later: still no notice; the panel line stays');
      }
      check(!p.problems.length, 'no console errors ' + why + ' ' + name + ' ' + p.problems.join(' | '));
      await p.dispose();
    }
    console.log('ok', name);
  }
  writeFileSync(OUT + 'browser.json', JSON.stringify({ pass: true, checks, shots: index.filter((x) => x[1]).length, index }, null, 2));
  console.log(`p0307-secret-new-task-shots: ${checks} checks passed, ${index.filter((x) => x[1]).length} shots → ${OUT}`);
} catch (e) {
  console.error('p0307-secret-new-task-shots FAILED:', e && e.stack || e);
  process.exitCode = 1;
} finally {
  await B.close();
  await fake.stop();
  await web.stop();
}
