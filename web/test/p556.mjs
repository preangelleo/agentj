#!/usr/bin/env node
// P556 (chairman 10-27) Settings → 安全 → 审批档位: the third level is 「全放行」 / Full access again and it also starts the AI
// coding tool at its widest permissions. Checks:
//   ① three labels 安全 / 中等 / 全放行 · Safe / Standard / Full access (stored value `harness`; a released `full` = the same)
//   ② the copy (10-27): each level is ONE plain paragraph of one or two sentences (全放行 word for word), no
//      technical words, no per-tool line / background paragraph even when the host sends host.approval.max, + 「了解更多」 → docs
//   ③ layout at 390 and 360 px (zh / en, light / dark) and 1440: the three buttons share one row, nothing overflows, and every
//      selected pill (::after) sits ≥ 3 px inside its group's border (::before) on every side; the group keeps off the card edge
// The real web client against fakehost.mjs, own headless Chromium (never :9222).
//   → reports/qa/p556/after/<shot>-<w>-<scheme>-<lang>.png
//   P556_BEFORE=1 P556_OUT=<dir> node p556.mjs   shots only (no checks) — run from a HEAD export for the 「before」 set
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const BEFORE = process.env.P556_BEFORE === '1';
const OUT = process.env.P556_OUT ? process.env.P556_OUT.replace(/\/?$/, '/') : fileURLToPath(new URL('../../reports/qa/p556/after/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
let checks = 0;
const check = (v, label) => { if (BEFORE) { if (!v) console.log('before: ' + label); return; } assert.ok(v, label); checks++; };
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
const BYPASS = { kind: 'claude', shared: false, how: 'answer' };

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
const text = (p, id) => ev(p, `document.getElementById(${JSON.stringify(id)}).textContent`);
const shown = (p, sel) => ev(p, `(()=>{const e=document.querySelector(${JSON.stringify(sel)});return !!e&&!e.hidden&&!!e.offsetParent;})()`);
// what the owner reads under the three buttons: every visible note paragraph of the level row (the lapsed line excluded)
const notes = (p) => ev(p, `[...document.querySelectorAll('#set-level-row p.setnote')].filter(e=>!e.hidden&&e.offsetParent&&!e.closest('.setunsigned')).map(e=>e.textContent)`);
const NOTE = {
  zh: { harness: '全放行：Agent J 不再弹审批卡，AI 编程工具可以直接改文件、跑命令、联网。只有花钱、删除你的数据、解绑或转移席位、密钥外发，以及有人想改动 Agent J 本身时，仍要 Face ID。风险由你自己承担，换来最快的速度。' },
  en: { harness: 'Full access: Agent J shows no approval cards, and the AI coding tool can change files, run commands and go online on its own. Only spending money, deleting your data, unbinding or moving a seat, sending a key out, and anyone trying to change Agent J itself still need Face ID. You take the risk in exchange for the fastest speed.' },
};
/** host.approval.max changes → the next `preferences` broadcast carries it (as the real host does on every change). */
async function setMax(p, max) {
  fake.st.approvalMax = max;
  await fake.send(fake.prefsMsg());
  await sleep(200);
}
const TECH = /钩子|参数|重启|全局配置|沙箱|Claude Code|Codex|harness|hook|parameter|restart|config|sandbox|danger-full-access|approval_policy/i;
const sentences = (s) => s.split(/[。！？]|[.!?](?=\s|$)/).filter((x) => x.trim()).length;

// ③ the layout: every .segrow in Settings (审批档位 + 外观) — one row, no overflow, the pill ≥ 3 px inside the border,
// the group ≥ 8 px inside its card; the three level buttons ≥ 44 px tall and not wrapped.
const LAYOUT = `(()=>{const bad=[],px=(v)=>parseFloat(v)||0;
  for(const g of document.querySelectorAll('#settings .segrow')){if(!g.offsetParent)continue;const id=g.closest('.setrow')?.id||g.getAttribute('aria-label');
    const G=g.getBoundingClientRect(),gs=getComputedStyle(g,'::before');
    const O={l:G.left+px(gs.left),t:G.top+px(gs.top),r:G.right-px(gs.right),b:G.bottom-px(gs.bottom)};
    const card=g.closest('.setcard').getBoundingClientRect();
    if(O.l<card.left+8||O.r>card.right-8)bad.push(id+' group touches the card edge '+(O.l-card.left).toFixed(1)+'/'+(card.right-O.r).toFixed(1));
    const row=g.closest('.setrow').getBoundingClientRect();if(G.left<row.left-1||G.right>row.right+1)bad.push(id+' group overflows its row');
    const bs=[...g.querySelectorAll('button')];
    if(new Set(bs.map(b=>b.offsetTop)).size!==1)bad.push(id+' not one row '+bs.map(b=>b.offsetTop).join('/'));
    for(const b of bs){const r=b.getBoundingClientRect(),n=b.dataset.setLevel||b.dataset.setTheme;
      if(r.left<G.left-1||r.right>G.right+1)bad.push(id+' overflow '+n);
      if(b.scrollWidth>b.clientWidth+1)bad.push(id+' clipped '+n);
      if(r.height<44||r.width<44)bad.push(id+' tap '+n+' '+r.width.toFixed(0)+'x'+r.height.toFixed(0));
      if(b.dataset.setLevel&&r.height>44.5)bad.push(id+' wrapped '+n+' '+r.height.toFixed(1));
      if(b.getAttribute('aria-pressed')!=='true')continue;
      const a=getComputedStyle(b,'::after');if(a.content==='none'){bad.push(id+' no pill '+n);continue;}
      const P={l:r.left+px(a.left),t:r.top+px(a.top),r:r.right-px(a.right),b:r.bottom-px(a.bottom)};
      const gap={left:P.l-O.l,top:P.t-O.t,right:O.r-P.r,bottom:O.b-P.b};
      for(const [k,v] of Object.entries(gap))if(v<3)bad.push(id+' pill '+n+' '+k+' gap '+v.toFixed(1));}}
  if(document.documentElement.scrollWidth>innerWidth)bad.push('page scroll');return bad;})()`;

try {
  // ① ③ + the screenshots: 全放行 selected (Claude Code, answered by the host), every width / scheme / language that matters
  const VIEWS = [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [390, 844, 'dark', 'en'],
    [360, 780, 'light', 'zh'], [360, 780, 'dark', 'zh'], [360, 780, 'light', 'en'], [360, 780, 'dark', 'en'], [1440, 900, 'dark', 'en']];
  for (const [w, h, scheme, lang] of VIEWS) {
    const name = `${w}-${scheme}-${lang}`, L = (zh, en) => (lang === 'en' ? en : zh);
    const p = await paired(w, h, scheme, lang, (st) => { st.prefs.approval = { level: 'harness' }; st.approvalMax = structuredClone(BYPASS); });
    await openSafety(p);
    check(await ev(p, `[...document.querySelectorAll('#set-level-row [data-set-level]')].map(b=>b.dataset.setLevel+'='+b.textContent).join()`) ===
      L('safe=安全,standard=中等,harness=全放行', 'safe=Safe,standard=Standard,harness=Full access'), 'three labels ' + name);
    check(await ev(p, `document.querySelector('[data-set-level="harness"]').getAttribute('aria-pressed')`) === 'true', '全放行 pressed ' + name);
    check((await text(p, 'set-level-note')).startsWith(L('全放行：Agent J 不再弹审批卡', 'Full access: Agent J shows no approval cards')), 'risk note ' + name);
    check(JSON.stringify(await notes(p)) === JSON.stringify([NOTE[lang].harness]), 'one paragraph, word for word ' + name + ' :: ' + JSON.stringify(await notes(p)));
    check(await shown(p, '#set-level-more') && (await ev(p, `document.getElementById('set-level-more').getAttribute('href')`)) === 'https://agentj.app/docs/phone/#approval-level'
      && (await text(p, 'set-level-more')) === L('了解更多', 'Learn more'), '了解更多 → docs ' + name);
    const bad = await ev(p, LAYOUT);
    check(!bad.length, 'layout ' + name + ' :: ' + bad.join(' · '));
    writeFileSync(OUT + `1-full-${name}.png`, await shoot(p, ''));
    check(!p.problems.length, 'no console errors ' + name + ' ' + p.problems.join(' | '));
    await p.dispose();
  }
  // 中等 selected: one short paragraph (the host still sends max) — and the layout holds with the middle pill
  for (const [w, h, scheme, lang] of [[390, 844, 'light', 'zh'], [360, 780, 'light', 'en'], [390, 844, 'dark', 'en']]) {
    const name = `${w}-${scheme}-${lang}`;
    const p = await paired(w, h, scheme, lang, (st) => { st.prefs.approval = { level: 'standard' }; st.approvalMax = structuredClone(BYPASS); });
    await openSafety(p);
    check(await ev(p, `document.querySelector('[data-set-level="standard"]').getAttribute('aria-pressed')`) === 'true', '中等 pressed ' + name);
    check((await notes(p)).length === 1, '中等 → one paragraph ' + name);
    const bad = await ev(p, LAYOUT);
    check(!bad.length, 'layout (中等) ' + name + ' :: ' + bad.join(' · '));
    writeFileSync(OUT + `2-standard-${name}.png`, await shoot(p, ''));
    await p.dispose();
  }
  // ② every level, zh and en: one paragraph of one or two sentences (全放行: three, word for word), nothing technical;
  // a released `full` = the same switch → the same paragraph; the old per-tool / background paragraphs are gone from the page
  if (!BEFORE) for (const lang of ['zh', 'en']) {
    const name = `390-light-${lang}`;
    const p = await paired(390, 844, 'light', lang, (st) => { st.prefs.approval = { level: 'harness' }; st.approvalMax = { kind: 'codex', shared: false, how: 'no_sandbox' }; });
    await openSafety(p);
    check(!(await ev(p, `!!document.getElementById('set-level-max')||!!document.getElementById('set-level-bg')`)), 'no per-tool / background paragraph ' + name);
    for (const lv of ['safe', 'standard', 'full', 'harness']) {
      fake.st.prefs.approval = { level: lv };
      await setMax(p, { kind: 'codex', shared: false, how: 'no_sandbox' });
      const sel = lv === 'full' ? 'harness' : lv, ns = await notes(p);
      check(await ev(p, `document.querySelector('[data-set-level="${sel}"]').getAttribute('aria-pressed')`) === 'true', lv + ' pressed ' + name);
      check(ns.length === 1, lv + ' → one paragraph ' + name + ' :: ' + JSON.stringify(ns));
      if (sel === 'harness') check(ns[0] === NOTE[lang].harness, lv + ' → the 全放行 words ' + name);
      else check(sentences(ns[0]) <= 2, lv + ' → one or two sentences ' + name + ' :: ' + ns[0]);
      check(!TECH.test(ns[0]), lv + ' → no technical words ' + name + ' :: ' + ns[0]);
      check(await shown(p, '#set-level-more'), lv + ' → 了解更多 ' + name);
    }
    check(!p.problems.length, 'no console errors (notes) ' + name + ' ' + p.problems.join(' | '));
    await p.dispose();
  }
  if (!BEFORE) {
    const rec = parityRecorder('web/test/p556.mjs');
    rec.record('p556-full-access-menu', true);
    rec.write();
  }
  console.log(BEFORE ? `p556: before shots in ${OUT}` : `p556: ${checks} checks passed — shots in ${OUT}`);
} catch (e) {
  console.error('p556 FAILED:', e && e.stack || e);
  process.exitCode = 1;
} finally {
  await B.close();
  await fake.stop();
  await web.stop();
}
