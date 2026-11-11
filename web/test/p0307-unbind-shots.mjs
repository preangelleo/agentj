#!/usr/bin/env node
// 0.30.7 round 2 — one-card unbind, every state, the real web client against fakehost.mjs (own headless Chromium, never :9222).
//  s1 list            Settings → 遥控器管理: this remote + another one, each with 「解绑」
//  s2 one sheet       host advertises `unbind_one`: the FIRST sheet after 「解绑」 is the single Face ID sheet (signed request
//                     already sent, no `fa`, nothing unbound yet)
//  s3 success         Face ID → the same signed object + `fa` → gone from the list, 「已解绑」 toast
//  s4 cancelled       「取消」 on the sheet → nothing ran, no toast, still two remotes
//  s5 denied          the browser's Face ID refused (NotAllowedError) → 「Face ID 没通过」, still two remotes
//  s6 host refused    the host rejects the Face ID answer (passkey_failed)
//  s6t timeout        the host never answers the Face ID answer → after 20 s the page says the host didn't answer
//  s7 old host        no `unbind_one` → the two steps stay: 「解绑」 sheet, then 「最后一步：用 Face ID 确认」
//  s8 other side      the other remote (the one about to be removed) tries to unbind the owner's phone: ITS own Face ID sheet
//  s8n other side, no passkey there → 「这台设备还没有设置 Face ID」, nothing unbound
// zh + en × light + dark × 390 × 844 and 1440 × 900 → .jarvis/out/0307-approval-round2/unbind/ (+ checks on every run).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const OUT = process.env.AJ_P0307_SHOTS || fileURLToPath(new URL('../../../.jarvis/out/0307-approval-round2/unbind/', import.meta.url));
const ONLY = process.env.AJ_P0307_ONLY || '';        // e.g. "390-dark-zh" to re-shoot one view
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
const J = JSON.stringify;
let checks = 0;
const check = (v, label) => { assert.ok(v, label); checks++; };
const NOW = Math.floor(Date.now() / 1000);
const OTHER_ID = 'Ab4Tq7Wz9Lm3Xc8P';
const MINE = (lang) => [
  { id: 'ME', name: 'iPhone Air', paired_at: NOW - 86400 * 12, seen: NOW, online: true, source: 'local', me: true, pk: 'local' },
  { id: OTHER_ID, name: lang === 'zh' ? '华为 MatePad' : 'Huawei MatePad', paired_at: NOW - 86400, seen: NOW - 7200, online: false, source: 'phone' },
];
// s8: this page is the remote being removed; the owner's phone is the other row
const THEIRS = (lang) => [
  { id: 'ME', name: lang === 'zh' ? '华为 MatePad' : 'Huawei MatePad', paired_at: NOW - 86400, seen: NOW, online: true, source: 'phone', me: true },
  { id: OTHER_ID, name: 'iPhone Air', paired_at: NOW - 86400 * 12, seen: NOW - 60, online: true, source: 'local', pk: 'local' },
];
const FA_OK = `navigator.credentials.get = (o) => { window.__fa = (window.__fa || 0) + 1; const b = (s) => new TextEncoder().encode(s).buffer;
  return Promise.resolve({ rawId: b('cred-1'), response: { clientDataJSON: b('{}'), authenticatorData: b('ad'), signature: b('sig') } }); }; true`;
const FA_DENY = `navigator.credentials.get = () => { window.__fa = (window.__fa || 0) + 1; return Promise.reject(new DOMException('denied', 'NotAllowedError')); }; true`;
const UNB = (id) => `#rm-list .rmitem[data-id="${id}"] [data-act="unbind"]`;
const toastHas = (s) => `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${J(s)})`;
const tr = (p, k, a) => ev(p, `import('./js/t.js').then(m=>m.t(${J(k)}${a ? ',' + J(a) : ''}))`);

async function press(p, sel, touch) {
  const r = await ev(p, `(()=>{const n=document.querySelector(${J(sel)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  if (touch) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [r] });
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } else {
    await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  }
}
async function remotesPage(w, h, scheme, lang, remotes, fa = FA_OK) {
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
  fake.st.remotes = remotes;
  const p = await newPage(B, w, h, scheme, { touch: w < 500, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();true`);
  await sleep(250);
  await ev(p, fa);
  await ev(p, `document.getElementById('setBtn').click()`);
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await ev(p, `document.getElementById('set-remotes-open').click()`);
  await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === ${remotes.items.length}`);
  await sleep(300);
  return p;
}
const unbinds = () => (fake.st.remotes.writes || []).filter((x) => x.t === 'dev_unbind');
const sheetOpen = `!document.getElementById('confirm').hidden`;
const index = [];
const save = async (p, state, name) => { const f = `${state}-${name}.png`; writeFileSync(OUT + f, await shoot(p, '')); index.push([state, f]); };
// visible-bug guards on every shot: nothing wider than the viewport, the sheet's buttons ≥ 44 px and fully on screen
const LAYOUT = `(()=>{const vw=innerWidth,bad=[];for(const n of document.querySelectorAll('#confirm .box *, #rm-list *, #toast')){const r=n.getBoundingClientRect();
  if(r.width&&(r.right>vw+1||r.left<-1)&&getComputedStyle(n).visibility!=='hidden')bad.push((n.id||n.className||n.tagName)+':'+Math.round(r.left)+'..'+Math.round(r.right));}
  for(const id of ['confirm-yes','confirm-no']){const b=document.getElementById(id);if(b&&!document.getElementById('confirm').hidden&&!b.hidden){const r=b.getBoundingClientRect();if(r.height<44||r.bottom>innerHeight+1)bad.push(id+' h='+r.height+' b='+r.bottom);}}
  return bad;})()`;
const layoutOk = async (p, label) => { const bad = await ev(p, LAYOUT); check(!bad.length, 'layout ' + label + ' :: ' + bad.join(' ')); };

const VIEWS = [];
for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) VIEWS.push([w, h, scheme, lang]);
try {
  for (const [w, h, scheme, lang] of VIEWS) {
    const name = `${w}-${scheme}-${lang}`, touch = w < 500;
    if (ONLY && ONLY !== name) continue;
    const other = MINE(lang)[1].name;
    // ---- s1 → s2 → s3: list, the one sheet, success
    let p = await remotesPage(w, h, scheme, lang, { limit: 10, items: MINE(lang), passkey: true, unbind_one: true });
    check(await ev(p, `document.querySelectorAll('#rm-list [data-act="unbind"]').length`) === 2, 'both rows offer 解绑 ' + name);
    check(await ev(p, `(()=>{const l=document.getElementById('rm-list');return l.scrollHeight<=l.clientHeight})()`), 'two remotes fit: the list has nothing to scroll (no clipped first name) ' + name);
    await layoutOk(p, 's1 ' + name);
    await save(p, 's1-list', name);
    await press(p, UNB(OTHER_ID), touch);
    await waitFor(p, sheetOpen); await sleep(250);
    const T1 = await tr(p, 'p0307.rm.oneTitle', { name: other });
    check(await ev(p, `document.getElementById('confirm-title').textContent`) === T1, 'the first sheet is the one Face ID sheet ' + name);
    check(await ev(p, `document.getElementById('confirm-yes').textContent`) === await tr(p, 'p0307.rm.oneYes'), 'its button ' + name);
    check(unbinds().length === 1 && !unbinds()[0].fa && unbinds()[0].sigOk, 'signed request already sent, no fa ' + name);
    check(fake.st.remotes.items.length === 2, 'nothing unbound before Face ID ' + name);
    await layoutOk(p, 's2 ' + name);
    await save(p, 's2-one-sheet', name);
    await press(p, '#confirm-yes', touch);
    await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 1`);
    await waitFor(p, toastHas(await tr(p, 'rm.unbound', { name: other })));
    const [u1, u2] = unbinds();
    check(u2 && u2.fa && u2.sigOk && u2.n === u1.n && u2.sig === u1.sig, 'same signed object + fa ' + name);
    check(await ev(p, `window.__fa`) === 1 && unbinds().length === 2, 'one Face ID, two frames ' + name);
    await sleep(250);
    await layoutOk(p, 's3 ' + name);
    await save(p, 's3-success', name);
    check(!p.problems.length, 'no console errors s1-s3 ' + name + ' ' + p.problems.join(' | '));
    await p.dispose();

    // ---- s4 cancelled on the sheet
    p = await remotesPage(w, h, scheme, lang, { limit: 10, items: MINE(lang), passkey: true, unbind_one: true });
    await press(p, UNB(OTHER_ID), touch);
    await waitFor(p, sheetOpen); await sleep(200);
    await press(p, '#confirm-no', touch);
    await waitFor(p, `document.getElementById('confirm').hidden`); await sleep(400);
    check(fake.st.remotes.items.length === 2 && unbinds().length === 1 && !(await ev(p, `window.__fa`)), 'cancel → nothing ran ' + name);
    check(!(await ev(p, `document.getElementById('toast').classList.contains('on')`)), 'cancel says nothing ' + name);
    await save(p, 's4-cancelled', name);
    await p.dispose();

    // ---- s5 Face ID refused by the browser / the owner
    p = await remotesPage(w, h, scheme, lang, { limit: 10, items: MINE(lang), passkey: true, unbind_one: true }, FA_DENY);
    await press(p, UNB(OTHER_ID), touch);
    await waitFor(p, sheetOpen); await sleep(200);
    await press(p, '#confirm-yes', touch);
    await waitFor(p, toastHas(await tr(p, 'why.passkey_failed')));
    check(fake.st.remotes.items.length === 2 && unbinds().length === 1, 'denied → nothing ran ' + name);
    await sleep(250);
    await layoutOk(p, 's5 ' + name);
    await save(p, 's5-denied', name);
    await p.dispose();

    // ---- s6 the host refuses the Face ID answer
    p = await remotesPage(w, h, scheme, lang, { limit: 10, items: MINE(lang), passkey: true, unbind_one: true, faWhy: 'passkey_failed' });
    await press(p, UNB(OTHER_ID), touch);
    await waitFor(p, sheetOpen); await sleep(200);
    await press(p, '#confirm-yes', touch);
    await waitFor(p, toastHas(await tr(p, 'why.passkey_failed')));
    check(fake.st.remotes.items.length === 2 && unbinds().length === 2 && unbinds()[1].fa, 'host refused → nothing unbound ' + name);
    await sleep(250);
    await save(p, 's6-host-refused', name);
    await p.dispose();

    // ---- s6t the host never answers the Face ID answer (20 s)
    p = await remotesPage(w, h, scheme, lang, { limit: 10, items: MINE(lang), passkey: true, unbind_one: true, faSilent: true });
    await press(p, UNB(OTHER_ID), touch);
    await waitFor(p, sheetOpen); await sleep(200);
    await press(p, '#confirm-yes', touch);
    const tmo = await tr(p, 'why.timeout');
    await waitFor(p, `document.getElementById('toast').classList.contains('on') && !document.getElementById('toast').textContent.includes(${J(await tr(p, 'set.saving'))})`, 30000);
    const said = await ev(p, `document.getElementById('toast').textContent`);
    check(said.includes(tmo), 'timeout says the host did not answer ' + name + ' :: ' + said);
    check(fake.st.remotes.items.length === 2, 'timeout → still listed ' + name);
    await sleep(250);
    await layoutOk(p, 's6t ' + name);
    await save(p, 's6t-timeout', name);
    await p.dispose();

    // ---- s7 an older host (no unbind_one): the two steps stay
    p = await remotesPage(w, h, scheme, lang, { limit: 10, items: MINE(lang), passkey: true });
    await press(p, UNB(OTHER_ID), touch);
    await waitFor(p, sheetOpen); await sleep(250);
    check(await ev(p, `document.getElementById('confirm-text').textContent`) === await tr(p, 'p0304r2.rm.unbindText'), 'old host: step one ' + name);
    check(unbinds().length === 0, 'old host: nothing sent before step one ' + name);
    await layoutOk(p, 's7a ' + name);
    await save(p, 's7a-oldhost-step1', name);
    await press(p, '#confirm-yes', touch);
    await waitFor(p, `${sheetOpen} && document.getElementById('confirm-title').textContent === ${J(await tr(p, 'p0304r2.rm.faTitle'))}`); await sleep(250);
    await layoutOk(p, 's7b ' + name);
    await save(p, 's7b-oldhost-step2-faceid', name);
    await press(p, '#confirm-yes', touch);
    await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 1`);
    check(await ev(p, `window.__fa`) === 1, 'old host: one Face ID ' + name);
    await p.dispose();

    // ---- s8 the other remote tries to unbind the owner's phone: its OWN Face ID, one sheet
    p = await remotesPage(w, h, scheme, lang, { limit: 10, items: THEIRS(lang), passkey: true, unbind_one: true });
    await press(p, UNB(OTHER_ID), touch);
    await waitFor(p, sheetOpen); await sleep(250);
    check(await ev(p, `document.getElementById('confirm-title').textContent`) === await tr(p, 'p0307.rm.oneTitle', { name: 'iPhone Air' }), 'other side: one sheet ' + name);
    check(fake.st.remotes.items.length === 2 && !unbinds()[0].fa, 'other side: nothing ran without its Face ID ' + name);
    await layoutOk(p, 's8 ' + name);
    await save(p, 's8-other-side-own-faceid', name);
    await p.dispose();

    // ---- s8n the other remote has no passkey of its own → refused in plain words
    p = await remotesPage(w, h, scheme, lang, { limit: 10, items: THEIRS(lang), passkey: false, unbind_one: true });
    await press(p, UNB(OTHER_ID), touch);
    await waitFor(p, toastHas((await tr(p, 'p551.unbindNoPasskey')).slice(0, 12)));
    check(fake.st.remotes.items.length === 2 && !(await ev(p, sheetOpen)) && !(await ev(p, `window.__fa`)), 'other side, no passkey: no sheet, nothing unbound ' + name);
    check(!(await ev(p, `document.getElementById('toast').textContent`)).includes(' / ' + (lang === 'zh' ? '指纹' : 'fingerprint')) || !/指纹或面容|fingerprint or face/.test(await ev(p, `document.getElementById('toast').textContent`)), 'no 「指纹或面容 / 指纹」 ' + name);
    await sleep(250);
    await layoutOk(p, 's8n ' + name);
    await save(p, 's8n-other-side-no-passkey', name);
    await p.dispose();
    console.log('ok', name);
  }
  const order = [...new Set(index.map(([s]) => s))];
  const md = ['# 0.30.7 round 2 — one-card unbind, every state', '',
    'Real web client against `web/test/fakehost.mjs`, headless Chromium viewport simulation (not a physical phone).',
    'Generated by `web/test/p0307-unbind-shots.mjs`.', '', '| state | files |', '|---|---|',
    ...order.map((s) => `| ${s} | ${index.filter(([x]) => x === s).map(([, f]) => '`' + f + '`').join(' ')} |`), ''];
  if (!ONLY) writeFileSync(OUT + 'index.md', md.join('\n'));
  console.log(`p0307-unbind-shots: ${checks} checks passed, ${index.length} shots → ${OUT}`);
} catch (e) {
  console.error('p0307-unbind-shots FAILED:', e && e.stack || e);
  process.exitCode = 1;
} finally {
  await B.close();
  await fake.stop();
  await web.stop();
}
