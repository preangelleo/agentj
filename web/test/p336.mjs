#!/usr/bin/env node
// P336 Settings → 安全: 「审批档位」 (安全 / 中等 / 全放行) and 「设了目标就不弹窗」 — both change ONLY by the signed
// `approval_set` control (never pref_set), shown only when the host lists them in `host.signed_prefs`. Plus the
// `auto` toast for by:"full" and the activity lines for auto by level_full / goal_nocard and a parked (owner offline) card.
// The real web client against fakehost.mjs, own headless Chromium (never :9222); 390 × 844 and 1440 × 900, zh / en,
// light / dark. `node test/p336.mjs before` = shots only (main, no rows yet); no tag = shots + checks.
//   → reports/qa/p336/<tag>/<shot>-<w>-<scheme>-<lang>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, key, tap, rect } from './browser.mjs';

const tag = process.argv[2] || 'after', CHECK = tag === 'after';
const OUT = fileURLToPath(new URL(`../../reports/qa/p336/${tag}/`, import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
let checks = 0;
const check = (v, label) => { if (!CHECK) return; assert.ok(v, label); checks++; };
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
const VIEWS = [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [390, 844, 'dark', 'en'], [1440, 900, 'light', 'zh'], [1440, 900, 'dark', 'en']];

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
const confirmOpen = (p) => ev(p, `!document.getElementById('confirm').hidden`);
const pressed = (p) => ev(p, `[...document.querySelectorAll('[data-set-level]')].filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.dataset.setLevel).join()`);
// every visible element of the two rows stays inside the row (no overflow at 390 px) and every control is ≥ 44 px tall
const LAYOUT = `(()=>{const bad=[];for(const id of ['set-level-row','set-gnc-row']){const row=document.getElementById(id);if(!row||row.hidden)continue;const R=row.getBoundingClientRect();
  for(const e of row.querySelectorAll('*')){const r=e.getBoundingClientRect();if(!r.width)continue;if(r.left<R.left-1||r.right>R.right+1)bad.push(id+' overflow '+(e.id||e.tagName)+' '+r.left.toFixed(0)+'-'+r.right.toFixed(0));
   if(e.matches('button')&&r.height<44)bad.push(id+' tap '+(e.dataset.setLevel||e.id)+' '+r.height.toFixed(0));}
  if(row.scrollWidth>row.clientWidth+1)bad.push(id+' scrollWidth');}return bad;})()`;

try {
  // ---- an older host (no signed_prefs): the rows stay hidden ----
  {
    const p = await paired(390, 844, 'light', 'zh', (st) => { st.signedPrefs = false; });
    await openSafety(p);
    check(await ev(p, `document.getElementById('set-level-row').hidden && document.getElementById('set-gnc-row').hidden && document.getElementById('set-goal-safe').hidden`), 'older host: both rows + the goal note hidden');
    writeFileSync(OUT + '0-old-host-390-light-zh.png', await shoot(p, ''));
    await p.dispose();
  }
  // ---- the configuration says full but no live signature backs it: the phone shows what is IN FORCE (standard) ----
  if (CHECK) {
    const p = await paired(390, 844, 'light', 'zh', (st) => { st.approvalUnsigned = true; st.prefs.approval = { level: 'full', goal_nocard: true }; });
    await openSafety(p);
    check(await pressed(p) === 'standard', 'unsigned full in the file → 中等 pressed');
    check(await ev(p, `document.getElementById('set-gnc').getAttribute('aria-checked')`) === 'false', 'unsigned goal switch → off');
    await p.dispose();
  }
  for (const [w, h, scheme, lang] of VIEWS) {
    const name = `${w}-${scheme}-${lang}`;
    const L = (zh, en) => (lang === 'en' ? en : zh);
    const p = await paired(w, h, scheme, lang);
    await openSafety(p);
    writeFileSync(OUT + `1-safety-${name}.png`, await shoot(p, ''));
    if (!CHECK) { await p.dispose(); continue; }
    check(!(await ev(p, `document.getElementById('set-level-row').hidden`)) && !(await ev(p, `document.getElementById('set-gnc-row').hidden`)), 'rows shown with signed_prefs ' + name);
    check(await pressed(p) === 'standard', 'default level = standard ' + name);
    check((await ev(p, `document.getElementById('set-level-note').textContent`)).startsWith(L('中等（默认）', 'Standard (default)')), 'the selected option\'s note ' + name);
    check(await ev(p, `document.getElementById('set-gnc').getAttribute('aria-checked')==='false'`), 'goal switch off by default ' + name);
    const bad = await ev(p, LAYOUT);
    check(!bad.length, 'layout ' + name + ' ' + bad.join(' · '));

    // 全放行: the red-line confirm, then ONE signed approval_set (no pref_set)
    await click(p, '[data-set-level="full"]');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    await sleep(200);
    writeFileSync(OUT + `2-full-confirm-${name}.png`, await shoot(p, ''));
    const txt = await ev(p, `document.getElementById('confirm-text').textContent`);
    check(txt.includes(L('疑似被攻击', 'suspected attacks')) && txt.includes(L('花钱', 'spending money')) && txt.includes(L('改价', 'price changes')), 'full confirm names the red lines ' + name);
    check(txt.includes(L('定时任务', 'scheduled tasks')) && txt.includes(L('直到你切回来', 'until you switch back')), 'full confirm: everything until switched back ' + name);
    check(await ev(p, `document.getElementById('confirm-yes').classList.contains('aj-btn--danger')`), 'full confirm button reads as dangerous ' + name);
    check(fake.st.approvalSets.length === 0, 'nothing sent before the confirm ' + name);
    await click(p, '#confirm-yes');
    await waitFor(p, `document.querySelector('[data-set-level="full"]').getAttribute('aria-pressed')==='true'`);
    check(fake.st.approvalSets.length === 1 && fake.st.approvalSets[0].key === 'approval.level' && fake.st.approvalSets[0].value === 'full' && fake.st.approvalSets[0].sigOk, 'signed approval_set level=full ' + name);
    check(!fake.st.prefSets.some((m) => String(m.key).startsWith('approval.')), 'never pref_set for approval.* ' + name);
    { const nt = await ev(p, `document.getElementById('set-level-note').textContent`); check(nt.includes('--dangerously-skip-permissions'), 'full note shown ' + name + ' :: ' + nt); }
    await sleep(300);
    writeFileSync(OUT + `3-safety-full-${name}.png`, await shoot(p, ''));

    // 安全: a short confirm; Esc cancels, nothing sent
    await click(p, '[data-set-level="safe"]');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    check(!(await ev(p, `document.getElementById('confirm-yes').classList.contains('aj-btn--danger')`)), 'safe confirm is not red ' + name);
    await key(p, 'Escape'); await sleep(250);
    check(!(await confirmOpen(p)) && fake.st.approvalSets.length === 1 && await pressed(p) === 'full', 'Esc cancels the switch ' + name);
    check(!(await ev(p, `document.getElementById('settings').hidden`)), 'Esc on the confirm leaves Settings open ' + name);

    // 设了目标就不弹窗: confirm → a boolean approval_set (the shot waits for the 「已保存」 ✓ to fade first)
    check(!(await ev(p, `document.getElementById('set-goal-safe').hidden`)), 'goal countdown note names the Safe level ' + name);
    if (w === 390) await sleep(2500);
    await click(p, '#set-gnc');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    if (w === 390) writeFileSync(OUT + `4-goal-confirm-${name}.png`, await shoot(p, ''));
    await click(p, '#confirm-yes');
    await waitFor(p, `document.getElementById('set-gnc').getAttribute('aria-checked')==='true'`);
    const g = fake.st.approvalSets[1];
    check(g && g.key === 'approval.goal_nocard' && g.value === true && g.sigOk, 'signed approval_set goal_nocard=true (boolean) ' + name);

    // a refused write: a toast with the reason, the UI follows the host (still full)
    fake.st.approvalWhy = 'rejected';
    await click(p, '[data-set-level="standard"]');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    await click(p, '#confirm-yes');
    await waitFor(p, `document.getElementById('toast').classList.contains('on')&&document.getElementById('toast').textContent.includes(${JSON.stringify(L('Agent J 主机没有接受这个值', "didn't accept"))})`);
    check(await pressed(p) === 'full', 'refused → level unchanged ' + name);
    await key(p, 'Escape'); await sleep(300);   // close Settings

    // the by:"full" auto toast
    await fake.send({ t: 'auto', by: 'full', tool: 'Bash', summary: 'rm -rf build/' });
    await waitFor(p, `document.getElementById('toast').textContent.includes(${JSON.stringify(L('全放行：已自动批准 Bash rm -rf build/', 'Full access: approved Bash rm -rf build/'))})`);
    check(true, 'auto by:full toast ' + name);
    await sleep(150);
    if (w === 390) writeFileSync(OUT + `5-auto-toast-${name}.png`, await shoot(p, ''));

    // the activity page: auto by level_full / goal_nocard and a parked card read as words
    const now = Date.now();
    fake.answer('act_list', () => ({ t: 'act_page', more: false, items: [
      { k: 'auto', ts: now - 10e3, tool: 'Bash', summary: 'git push origin main', by: 'level_full' },
      { k: 'auto', ts: now - 20e3, tool: 'Bash', summary: 'trash-put old.log', by: 'goal_nocard' },
      { k: 'decision', ts: now - 30e3, tool: 'Bash', result: 'parked', reason: 'offline', cats: ['delete'], summary: 'rm -rf dist/' },
      { k: 'auto', ts: now - 40e3, tool: 'Bash', summary: 'rm -rf dist/', by: 'parked' },
    ] }));
    await ev(p, `import(new URL('js/controls.js', document.baseURI).href).then(m=>m.openPanel('act'))`);
    await waitFor(p, `document.querySelectorAll('#act-list .act-item').length===4`);
    const lines = await ev(p, `[...document.querySelectorAll('#act-list .act-text')].map(e=>e.textContent)`);
    check(lines[0].startsWith(L('全放行，自动批准', 'Full access, approved automatically')) && !lines[0].includes('level_full'), 'activity: level_full ' + name + ' ' + lines[0]);
    check(lines[1].startsWith(L('设了目标不弹窗，自动批准', 'Goal mode without cards, approved')) && !lines[1].includes('goal_nocard'), 'activity: goal_nocard ' + name + ' ' + lines[1]);
    check(lines[2].startsWith(L('主人不在线，卡片留在手机上，已告诉 Agent', 'Owner offline: card kept on the phone, Agent told')), 'activity: parked offline ' + name + ' ' + lines[2]);
    check(lines[3].startsWith(L('离线时留下的卡你已批准，重试直接执行', 'Approved on the card kept while you were offline — the retry ran')) && !lines[3].includes('parked'), 'activity: parked retry ' + name + ' ' + lines[3]);
    if (w === 390) writeFileSync(OUT + `6-activity-${name}.png`, await shoot(p, ''));
    check(!p.problems.length, 'no console errors ' + name + ' ' + p.problems.join(' | '));
    await p.dispose();
  }
  // ---- addendum: widening on a phone with a passkey → 「用 Face ID 确认」; the tap starts Face ID synchronously, then the SAME
  // signed object goes again with `fa`; narrowing never asks; cancel = nothing; passkey_failed = the toast
  for (const lang of CHECK ? ['zh', 'en'] : ['zh']) {
    const L = (zh, en) => (lang === 'en' ? en : zh), name = `390-light-${lang}`;
    const p = await paired(390, 844, 'light', lang, (st) => { st.approvalPasskey = true; st.prefs.approval = { level: 'safe' }; });
    // the stub records whether get() ran inside the click (window.event) — the iOS user-gesture rule
    await ev(p, `navigator.credentials.get = (o) => { window.__fa = (window.__fa || []).concat([{ inTap: !!(window.event && window.event.type === 'click'), ch: o.publicKey.challenge.byteLength }]);
      const b = (s) => new TextEncoder().encode(s).buffer; return window.__faReject ? Promise.reject(new Error('NotAllowed')) : Promise.resolve({ rawId: b('cred-1'), response: { clientDataJSON: b('{}'), authenticatorData: b('ad'), signature: b('sig') } }); }`);
    await openSafety(p);
    const yes = async () => { const r = await rect(p, '#confirm-yes'); await tap(p, r.x, r.y); };
    // narrowing (safe → standard): one send, no Face ID
    await click(p, '[data-set-level="standard"]'); await waitFor(p, `!document.getElementById('confirm').hidden`); await yes();
    await waitFor(p, `document.querySelector('[data-set-level="standard"]').getAttribute('aria-pressed')==='true'`);
    check(fake.st.approvalSets.length === 1 && !fake.st.approvalSets[0].fa && !(await ev(p, `window.__fa`)), 'narrowing never asks for Face ID ' + name);
    await sleep(2500);
    // widening to full: red confirm → host says passkey_required → the Face ID sheet
    await click(p, '[data-set-level="full"]'); await waitFor(p, `!document.getElementById('confirm').hidden`); await yes();
    await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent===${JSON.stringify(L('用 Face ID 确认', 'Confirm with Face ID'))}`);
    await sleep(250);
    writeFileSync(OUT + `7-faceid-sheet-${name}.png`, await shoot(p, ''));
    check(fake.st.approvalSets.length === 2 && !fake.st.approvalSets[1].fa, 'first widening send has no fa ' + name);
    await yes();
    await waitFor(p, `document.querySelector('[data-set-level="full"]').getAttribute('aria-pressed')==='true'`);
    const fa = await ev(p, `window.__fa`), [s1, s2] = fake.st.approvalSets.slice(1);
    check(fa && fa.length === 1 && fa[0].inTap && fa[0].ch > 0, 'Face ID started synchronously inside the tap ' + name);
    check(s2 && s2.n === s1.n && s2.sigOk && s2.fa && s2.fa.id && s2.fa.sig && s2.value === 'full', 'resent the SAME signed object (same n) + fa ' + name);
    await sleep(2500);
    // goal on → Face ID sheet → 取消: nothing changes, no resend
    await click(p, '#set-gnc'); await waitFor(p, `!document.getElementById('confirm').hidden`); await yes();
    await waitFor(p, `document.getElementById('confirm-title').textContent===${JSON.stringify(L('用 Face ID 确认', 'Confirm with Face ID'))}`);
    await click(p, '#confirm-no'); await sleep(400);
    check(fake.st.approvalSets.length === 4 && (await ev(p, `window.__fa.length`)) === 1 && await ev(p, `document.getElementById('set-gnc').getAttribute('aria-checked')==='false'`), 'cancel on the Face ID sheet: nothing changes ' + name);
    // goal on → Face ID → the host says passkey_failed → the toast, switch stays off
    fake.st.approvalFaFail = true;
    await click(p, '#set-gnc'); await waitFor(p, `!document.getElementById('confirm').hidden`); await yes();
    await waitFor(p, `document.getElementById('confirm-title').textContent===${JSON.stringify(L('用 Face ID 确认', 'Confirm with Face ID'))}`);
    await yes();
    await waitFor(p, `document.getElementById('toast').textContent.includes(${JSON.stringify(L('Face ID 没通过，请再试一次', "Face ID didn't go through — try again"))})`);
    check(await ev(p, `document.getElementById('set-gnc').getAttribute('aria-checked')==='false'`), 'passkey_failed → toast, switch stays off ' + name);
    // Face ID itself refused on the phone → the same toast, no resend
    await sleep(4300);
    await ev(p, `window.__faReject = true`);
    const before = fake.st.approvalSets.length;
    await click(p, '#set-gnc'); await waitFor(p, `!document.getElementById('confirm').hidden`); await yes();
    await waitFor(p, `document.getElementById('confirm-title').textContent===${JSON.stringify(L('用 Face ID 确认', 'Confirm with Face ID'))}`);
    await yes();
    await waitFor(p, `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${JSON.stringify(L('Face ID 没通过', "Face ID didn't"))})`);
    check(fake.st.approvalSets.length === before + 1, 'Face ID refused on the phone: no resend ' + name);
    check(!p.problems.length, 'no console errors (Face ID) ' + name + ' ' + p.problems.join(' | '));
    await p.dispose();
  }
  console.log(CHECK ? `p336: ${checks} checks passed — shots in ${OUT}` : `p336 ${tag}: shots in ${OUT}`);
} catch (e) {
  console.error('p336 FAILED:', e && e.stack || e);
  process.exitCode = 1;
} finally {
  await B.close();
  await fake.stop();
  await web.stop();
}
