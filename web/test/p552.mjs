#!/usr/bin/env node
// P552 (FEEDBACK #325) Settings → 安全: the approval switch that lapsed silently, and one menu switch.
//   ① host.approval.unsigned (the remote that signed 「全放行」 / 「设了目标就不弹窗」 is unpaired or re-keyed) → the row says
//     「未生效：<why>」 + 「用本机重新确认」; no key here → the host's no_passkey → 「在本机设置」 (P547); with a key → Face ID →
//     in force again, the line goes. The host's `approval_resign` (a new key on this remote) → a toast with the same button.
//   ③ three options: 安全 / 中等 / 全放行 (P556 name; a released `full` shows as 全放行), the background line.
// The real web client against fakehost.mjs, own headless Chromium (never :9222); 390 × 844 and 1440 × 900, zh / en, light / dark.
//   → reports/qa/p552/after/<shot>-<w>-<scheme>-<lang>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, tap, rect } from './browser.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const OUT = fileURLToPath(new URL('../../reports/qa/p552/after/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
let checks = 0;
const check = (v, label) => { assert.ok(v, label); checks++; };
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
const VIEWS = [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [1440, 900, 'dark', 'en']];
const LAPSED = [{ key: 'approval.level', value: 'harness', why: 'unpaired', by: 'Mac M1' },
  { key: 'approval.goal_nocard', value: true, why: 'rekeyed', by: 'Mac M1' }];

async function paired(w, h, scheme, lang, before) {
  fake.reset();
  fake.st.prefs = structuredClone(PREFS);
  fake.st.prefs.appearance.language = lang;
  if (before) before(fake.st);
  const p = await newPage(B, w, h, scheme, { touch: w < 500, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await sleep(250);
  return p;
}
async function openSafety(p) {
  await click(p, '#setBtn');
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await ev(p, `document.getElementById('set-h-safety').scrollIntoView({block:'start'})`);
  await sleep(300);
}
const pressed = (p) => ev(p, `[...document.querySelectorAll('[data-set-level]')].filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.dataset.setLevel).join()`);
const shown = (p, id) => ev(p, `!document.getElementById(${JSON.stringify(id)}).hidden`);
const text = (p, id) => ev(p, `document.getElementById(${JSON.stringify(id)}).textContent`);
// the two rows stay inside their card (no overflow at 390 px), every button ≥ 44 px tall
const LAYOUT = `(()=>{const bad=[];for(const id of ['set-level-row','set-gnc-row']){const row=document.getElementById(id);if(!row||row.hidden)continue;const R=row.getBoundingClientRect();
  for(const e of row.querySelectorAll('*')){const r=e.getBoundingClientRect();if(!r.width)continue;if(r.left<R.left-1||r.right>R.right+1)bad.push(id+' overflow '+(e.id||e.tagName));
   if(e.matches('button')&&r.height<44)bad.push(id+' tap '+(e.dataset.setLevel||e.id)+' '+r.height.toFixed(0));}
  if(row.scrollWidth>row.clientWidth+1)bad.push(id+' scrollWidth');}return bad;})()`;

try {
  for (const [w, h, scheme, lang] of VIEWS) {
    const name = `${w}-${scheme}-${lang}`;
    const L = (zh, en) => (lang === 'en' ? en : zh);
    // ③ the merged menu, a released `full` in force (the host already answers `harness`)
    {
      const p = await paired(w, h, scheme, lang, (st) => { st.prefs.approval = { level: 'harness' }; });
      await openSafety(p);
      check(await ev(p, `[...document.querySelectorAll('#set-level-row [data-set-level]')].map(b=>b.dataset.setLevel).join()`) === 'safe,standard,harness', 'three options ' + name);
      check(await ev(p, `document.querySelector('[data-set-level="harness"]').textContent`) === L('全放行', 'Full access'), 'one name ' + name);
      check(await pressed(p) === 'harness', 'in force → 全放行 pressed ' + name);
      check((await text(p, 'set-level-note')).startsWith(L('全放行：Agent J 不再弹审批卡', 'Full access: Agent J shows no approval cards')), 'merged note ' + name);
      check((await text(p, 'set-level-note')).includes(L('AI 编程工具可以直接改文件、跑命令、联网', 'the AI coding tool can change files, run commands and go online')), 'note says the AI coding tool acts on its own (P556) ' + name);
      // P559 naming: headless Chromium is Linux (touch → Android family) → Touch ID; Face ID only on iPhone / iPad
      check((await text(p, 'set-level-note')).includes(L('仍要 Touch ID', 'still need Touch ID')), 'note keeps the Face ID kinds (P556) ' + name);
      check(!(await shown(p, 'set-level-unsigned')) && !(await shown(p, 'set-gnc-unsigned')), 'nothing lapsed → no line ' + name);
      writeFileSync(OUT + `1-menu-${name}.png`, await shoot(p, ''));
      await p.dispose();
    }
    // ① lapsed: the row says why, one button; no key here → no_passkey → 「在本机设置」
    {
      const p = await paired(w, h, scheme, lang, (st) => { st.approvalUnsigned = true; st.approvalUnsignedList = structuredClone(LAPSED); st.prefs.approval = { level: 'full', goal_nocard: true }; });
      await openSafety(p);
      check(await pressed(p) === 'standard', 'not in force → 中等 pressed ' + name);
      check(await shown(p, 'set-level-unsigned') && await shown(p, 'set-gnc-unsigned'), 'both rows say not in force ' + name);
      const why = await text(p, 'set-level-unsigned-why');
      check(why.startsWith(L('未生效：', 'Not in force:')) && why.includes('Mac M1') && why.includes(L('已经解绑', 'no longer paired')), 'why (unpaired) ' + name + ' :: ' + why);
      check((await text(p, 'set-gnc-unsigned-why')).includes(L('换了钥匙', 'has a new key')), 'why (rekeyed) ' + name);
      check(await text(p, 'set-level-resign') === L('用本机重新确认', 'Confirm again on this device'), 'resign button ' + name);
      const bad = await ev(p, LAYOUT);
      check(!bad.length, 'layout ' + name + ' ' + bad.join(' · '));
      writeFileSync(OUT + `2-lapsed-${name}.png`, await shoot(p, ''));
      await click(p, '#set-level-resign');
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      check((await text(p, 'confirm-title')).includes(L('全放行', 'Full access')), 'confirm names the merged switch ' + name);
      if (w === 390 && scheme === 'light') writeFileSync(OUT + `3-resign-confirm-${name}.png`, await shoot(p, ''));
      await click(p, '#confirm-yes');
      await waitFor(p, `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${JSON.stringify(L('还没有设置 Touch ID', 'no Touch ID'))})`);
      check(fake.st.approvalSets.length === 1 && fake.st.approvalSets[0].value === 'harness' && fake.st.approvalSets[0].sigOk, 'resign signs the merged value ' + name);
      check(await ev(p, `[...document.querySelectorAll('#toast button')].some(b=>b.textContent.length>0)`), 'no key → one button to set it up here (P547) ' + name);
      if (w === 390 && scheme === 'light') writeFileSync(OUT + `4-no-passkey-${name}.png`, await shoot(p, ''));
      check(await shown(p, 'set-level-unsigned'), 'still not in force ' + name);
      check(!p.problems.length, 'no console errors ' + name + ' ' + p.problems.join(' | '));
      await p.dispose();
    }
  }
  // ① with a key here: Face ID → in force again, the line goes; the host's approval_resign toast offers the same tap
  for (const lang of ['zh', 'en']) {
    const L = (zh, en) => (lang === 'en' ? en : zh), name = `390-light-${lang}`;
    const p = await paired(390, 844, 'light', lang, (st) => { st.approvalPasskey = true; st.approvalUnsigned = true; st.approvalUnsignedList = structuredClone(LAPSED.slice(0, 1)); st.prefs.approval = { level: 'harness' }; });
    await ev(p, `navigator.credentials.get = (o) => { const b = (s) => new TextEncoder().encode(s).buffer; return Promise.resolve({ rawId: b('cred-1'), response: { clientDataJSON: b('{}'), authenticatorData: b('ad'), signature: b('sig') } }); }`);
    await fake.send({ t: 'approval_resign', text: L('🔐 这台遥控器的钥匙已经好了。「全放行」现在没有生效——点「用本机重新确认」就能恢复。', '🔐 This remote has its key now. 「Full access」 is not in force — tap 「Confirm again on this device」 to turn it back on.') });
    await waitFor(p, `document.getElementById('toast').classList.contains('on') && [...document.querySelectorAll('#toast button')].some(b=>b.textContent===${JSON.stringify(L('用本机重新确认', 'Confirm again on this device'))})`);
    writeFileSync(OUT + `5-resign-toast-${name}.png`, await shoot(p, ''));
    await ev(p, `[...document.querySelectorAll('#toast button')].find(b=>b.textContent===${JSON.stringify(L('用本机重新确认', 'Confirm again on this device'))}).click()`);
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    const yes = async () => { const r = await rect(p, '#confirm-yes'); await tap(p, r.x, r.y); };
    await yes();
    await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent===${JSON.stringify(L('用 Touch ID 确认', 'Confirm with Touch ID'))}`);
    await yes();
    await waitFor(p, `document.querySelector('[data-set-level="harness"]').getAttribute('aria-pressed')==='true'`);
    const [s1, s2] = fake.st.approvalSets;
    check(s1 && !s1.fa && s2 && s2.fa && s2.n === s1.n && s2.value === 'harness' && s2.sigOk, 'toast → the signed switch + Face ID ' + name);
    await openSafety(p);
    check(!(await shown(p, 'set-level-unsigned')), 'in force again → the line is gone ' + name);
    writeFileSync(OUT + `6-restored-${name}.png`, await shoot(p, ''));
    check(!p.problems.length, 'no console errors (resign) ' + name + ' ' + p.problems.join(' | '));
    await p.dispose();
  }
  const rec = parityRecorder('web/test/p552.mjs');
  rec.record('p552-approval-lapse-and-merged-menu', true);
  rec.write();
  console.log(`p552: ${checks} checks passed — shots in ${OUT}`);
} catch (e) {
  console.error('p552 FAILED:', e && e.stack || e);
  process.exitCode = 1;
} finally {
  await B.close();
  await fake.stop();
  await web.stop();
}
