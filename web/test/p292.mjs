// P292: one copy component for every code / pre (js/copycode.js) — the runtime half of "never forget again" (the static half is
// p292_copy.test.mjs). Real page + local fake host (real Noise), own headless Chromium (never :9222). For each screen below it
// asserts that EVERY visible code / pre is wired (copycode audit() is empty) and really clicks (touch tap at 390, mouse at 1440)
// once: the clipboard (navigator.clipboard.readText, with what writeText / execCommand received as the witness) holds exactly
// the code's text, the icon turns ✓ with 「已复制」 in place. Screens:
//   offline   the P168 disconnected card: `agentj doctor --reconnect` gets ⧉ + one click; keyboard (Tab target, Enter);
//             a press that selected text does not copy (⌘C stays the system's); the 44 px target keeps the line
//   approval  the approval sheet's command (a click on it = the title's ⧉, the whole command)
//   reply     the reply's inline code (P154: one click, no icon, 「已拷贝」) and a code block (a click on it = 拷贝)
//   settings  Settings: the read-only rows' commands (a click = their ⧉) and `agentj name <新名字>` in the help text
//   elevate   the sudo card's command row (⧉ inside the row)
//   confirm   解除配对？ — `agentj pair` in the dialog text
//   toast     a toast naming a command (r.say.err.no_agent) stays tappable for 6 s with ⧉
//   login     Settings → AI 登录救援: Codex's one-time device code (a click = its 复制)
// + a positive control: an unwired <code> put on the page must make audit() go red.
// zh: every screen at 390 + 1440 × light + dark; en: the offline card in all four, the rest at 390 light + 1440 dark.
// Shots → reports/qa/p292/screens/<lang>-<theme>-<w>-<screen>.png (fake data only: no account, key or address).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key } from './browser.mjs';
import { b64u } from '../../protocol/wire.js';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.P292_SHOTS || join(ROOT, 'reports/qa/p292/screens');
const $ = (id) => `document.getElementById('${id}')`;
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const S = {
  zh: { copied: '已复制', copyCmd: '复制命令', inline: '已拷贝', md: '已复制',
    reply: '先跑 `git status -sb` 看看改了什么，再跑测试：\n\n```bash\nnpm test\nnpm run build\n```\n\n都过了再告诉我。' },
  en: { copied: 'Copied', copyCmd: 'Copy command', inline: 'Copied', md: 'Copied',
    reply: 'First run `git status -sb` to see what changed, then the tests:\n\n```bash\nnpm test\nnpm run build\n```\n\nTell me when both pass.' },
};
const ONLY = (process.env.AJ_P292_ONLY || '').split(',').filter(Boolean);

let checks = 0;
const failures = [];
const check = (ok, label) => { checks++; if (!ok) failures.push(label); assert.ok(ok, label); };

const audit = (p, scope = 'document') => evaluate(p, `import('./js/copycode.js').then(m=>m.audit(${scope}).map(n=>n.outerHTML.slice(0,140)))`);
async function wrapClip(p) {
  await evaluate(p, `(()=>{if(window.__ccw)return true;window.__ccw=1;window.__cc=[];const c=navigator.clipboard;const w=c.writeText.bind(c);
    c.writeText=async(t)=>{window.__cc.push(t);return w(t)};const ex=document.execCommand.bind(document);
    document.execCommand=(k,...a)=>{if(k==='copy'){const s=document.activeElement;window.__cc.push(s&&s.value!==undefined?s.value:String(getSelection()))}return ex(k,...a)};return true})()`);
}
const copies = (p) => evaluate(p, `window.__cc.length`);
async function clip(p) {
  const witness = await evaluate(p, `window.__cc[window.__cc.length-1] ?? null`);
  const real = await evaluate(p, `navigator.clipboard.readText().then(t=>t,()=>null)`);
  return { witness, real };
}
const box = (p, sel, first = false) => evaluate(p, `(()=>{const e=typeof ${JSON.stringify(sel)}==='string'?document.querySelector(${JSON.stringify(sel)}):null;if(!e)return null;const r=${first}?e.getClientRects()[0]:e.getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);
async function press(p, sel, touch) {
  await evaluate(p, `document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`); await sleep(120);
  const r = await box(p, sel, true); assert.ok(r, 'no ' + sel);   // the first line's box: a wrapped code is two
  const x = Math.round(r.l + Math.min(r.w / 2, 24)), y = Math.round(r.t + Math.min(r.h / 2, 12));
  if (touch) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } else {
    await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  }
}
/** a click on `sel` copies exactly `want` (real clipboard where the headless browser lets us read it; the witness always). */
async function copiesExactly(p, sel, want, touch, tag) {
  const n = await copies(p);
  await press(p, sel, touch);
  await waitFor(p, `window.__cc.length > ${n}`, 4000);
  await sleep(120);
  const c = await clip(p);
  check(c.witness === want, `${tag}: copied ${JSON.stringify(c.witness)} = ${JSON.stringify(want)}`);
  if (c.real !== null) check(c.real === want, `${tag}: the real clipboard holds ${JSON.stringify(c.real)}`);
  stats.real += c.real !== null ? 1 : 0; stats.clicks++;
}
const stats = { real: 0, clicks: 0, screens: [] };
async function wired(p, tag, scope) {
  const bad = await audit(p, scope);
  check(bad.length === 0, `${tag}: every visible code / pre is wired — not: ${JSON.stringify(bad)}`);
}

async function paired(B, web, fake, lang, w, h, theme) {
  fake.reset(); fake.setUp(true);
  fake.st.prefs = { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: false, wake_enabled: false },
    agent: { high_risk_warnings: false, session_mode: 'independent', isolation: true, allow_docker: false },
    heartbeat: { enabled: true, interval_minutes: 60, daily_at: '' }, updates: { mode: 'auto' }, approval: { goal_auto: true, jev_threshold: 0.7 } };
  const touch = w < 500;
  const p = await newPage(B, w, h, theme, { touch, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `${$('phone-env-dismiss')}?.click(); if(${$('a2hs')} && !${$('a2hs')}.hidden) ${$('a2hs-ok')}?.click()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await wrapClip(p);
  await sleep(200);
  return p;
}

async function offline(p, fake, s, tag, touch, save) {
  fake.setUp(false);
  await waitState(p, 'waiting-host');
  await evaluate(p, `import('./js/offline-card.js').then(m=>{m._skew(7000);return true})`);
  await waitFor(p, `!${$('offDlg')}.hidden`, 6000);
  await sleep(350);
  await wired(p, tag + ' offline');
  const btn = '#offTip2 .cc-btn', code = '#offTip2 code';
  check(await evaluate(p, `!!document.querySelector(${JSON.stringify(btn)})`), tag + ': ⧉ beside `agentj doctor --reconnect`');
  const b = await box(p, btn), c = await box(p, code);
  check(b.w >= 43.5 && b.h >= 43.5, `${tag}: a ≥ 44 px target (${b.w}×${b.h})`);
  check(Math.abs((b.t + b.h / 2) - (c.t + c.h / 2)) < 4 || (b.t + b.h / 2 > c.t && b.t + b.h / 2 < c.b), `${tag}: ⧉ on the code's line (${JSON.stringify([b, c])})`);
  check(b.l >= c.r - 10 && b.l <= c.r + 2, `${tag}: ⧉ right after the code, not over it (${b.l} vs ${c.r})`);
  check(await evaluate(p, `document.querySelector('#offDlg .sq').scrollWidth <= document.querySelector('#offDlg .sq').clientWidth + 1`), tag + ': no overflow in the card');
  check(await evaluate(p, `document.querySelector(${JSON.stringify(btn)}).getAttribute('aria-label')`) === s.copyCmd, tag + ': aria-label 复制命令');
  check(await evaluate(p, `getComputedStyle(document.querySelector(${JSON.stringify(code)})).userSelect`) !== 'none', tag + ': the code stays selectable');
  await save('offline');
  // a press that selected text is the owner's own copy, not ours
  const n0 = await copies(p);
  await evaluate(p, `(()=>{const c=document.querySelector(${JSON.stringify(code)});const r=document.createRange();r.selectNodeContents(c);getSelection().removeAllRanges();getSelection().addRange(r);c.click();})()`);
  await sleep(250);
  check(await copies(p) === n0, tag + ': a click that ends a selection does not copy');
  await evaluate(p, `getSelection().removeAllRanges()`);
  // one real click on the code itself
  await copiesExactly(p, code, 'agentj doctor --reconnect', touch, tag + ' offline code');
  await waitFor(p, `document.querySelector(${JSON.stringify(btn)}).dataset.done==='1'`, 2000);
  check(await evaluate(p, `document.querySelector(${JSON.stringify(btn)}).dataset.tip`) === s.copied, tag + ': 「已复制」 in place');
  await save('offline-copied');
  await sleep(1600);
  check(!(await evaluate(p, `document.querySelector(${JSON.stringify(btn)}).dataset.done`)), tag + ': back to ⧉ after ~1.4 s');
  // keyboard: the icon is a button (Tab target); Enter copies
  const n1 = await copies(p);
  await evaluate(p, `document.querySelector(${JSON.stringify(btn)}).focus()`);
  check(await evaluate(p, `document.activeElement===document.querySelector(${JSON.stringify(btn)})`), tag + ': ⧉ takes focus');
  await key(p, 'Enter', { text: '\r' });
  await waitFor(p, `window.__cc.length > ${n1}`, 3000);
  check((await clip(p)).witness === 'agentj doctor --reconnect', tag + ': Enter on ⧉ copies');
  fake.setUp(true);
  await waitState(p, 'ready', 15000);
  await waitFor(p, `${$('offDlg')}.hidden`, 4000);
}

async function approval(p, fake, tag, touch, save) {
  const id = hex(16);
  await fake.ask({ id, tool: 'Bash', summary: 'git status -sb', ttl: 120 });
  await waitFor(p, `document.body.dataset.sheet==='1' && ${$('cmd')}.textContent==='git status -sb'`);
  await sleep(400);
  await wired(p, tag + ' approval');
  await copiesExactly(p, '#cmd', 'git status -sb', touch, tag + ' approval command');
  await save('approval');
  await evaluate(p, `${$('apprDeny')}.click()`);
  await waitFor(p, `document.body.dataset.sheet!=='1'`, 4000).catch(() => {});
  await sleep(300);
}

async function reply(p, fake, s, tag, touch, save) {
  await fake.say(s.reply, 'agent');
  await waitFor(p, `${$('words')}.querySelector('code.icode') && ${$('words')}.querySelector('.codeblock pre code')`);
  await sleep(300);
  await wired(p, tag + ' reply');
  check(await evaluate(p, `${$('words')}.querySelector('code.icode').dataset.cc`) === 'bare', tag + ': the reply\'s inline code is the bare variant (P154: no icon)');
  await copiesExactly(p, '#words code.icode', 'git status -sb', touch, tag + ' inline code');
  await waitFor(p, `${$('toast')}.textContent===${JSON.stringify(s.inline)}`, 2000);
  await save('reply-inline-copied');
  await sleep(1000);
  await copiesExactly(p, '#words .codeblock pre code', 'npm test\nnpm run build', touch, tag + ' code block');
  await waitFor(p, `${$('words')}.querySelector('.codeblock .copy').classList.contains('done')`, 2000);
  check(await evaluate(p, `${$('words')}.querySelector('.codeblock .copy').textContent`) === s.md, tag + ': the block\'s 拷贝 says 已复制');
  await save('reply-block-copied');
}

async function settings(p, s, tag, touch, save) {
  await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`); await sleep(400);
  await waitFor(p, `${$('set-risk-cmd')}.textContent.length > 0`, 4000).catch(() => {});
  await evaluate(p, `${$('set-risk-cmd')}.scrollIntoView({block:'center'})`); await sleep(200);
  await wired(p, tag + ' settings', `document.getElementById('settings')`);
  const want = await evaluate(p, `${$('set-risk-cmd')}.textContent`);
  check(/^agentj config set /.test(want), tag + ': a real command in the row');
  await copiesExactly(p, '#set-risk-cmd', want, touch, tag + ' settings command');
  await waitFor(p, `document.querySelector('#settings .setcopy[data-copy="set-risk-cmd"]').dataset.done==='1'`, 2000);
  await save('settings-command');
  // the help text's `agentj name <新名字>` (dictionary code → ⧉)
  const sel = '#settings [data-i18n="set.computerNote"] code';
  if (await evaluate(p, `!!document.querySelector(${JSON.stringify(sel)}) && document.querySelector(${JSON.stringify(sel)}).getClientRects().length>0`)) {
    await evaluate(p, `document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`); await sleep(150);
    await copiesExactly(p, sel, await evaluate(p, `document.querySelector(${JSON.stringify(sel)}).textContent`), touch, tag + ' settings help');
    await save('settings-help-copied');
  }
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await waitFor(p, `${$('settings')}.hidden`, 3000).catch(() => {});
  await sleep(250);
}

async function elevate(p, fake, tag, touch, save) {
  const kp = await webcrypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
  const epk = new Uint8Array(await webcrypto.subtle.exportKey('raw', kp.publicKey));
  const m = { t: 'elev', id: hex(16), kind: 'sudo', n: hex(16), epk: b64u(epk), ttl: 120, tries: 3, cmd: 'apt-get install -y ffmpeg', why: 'mp3', effect: '' };
  await fake.send(m);
  await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`);
  await sleep(350);
  await wired(p, tag + ' elevate');
  check(await evaluate(p, `!!document.querySelector('#elev dd.mono.cc-in > .cc-btn')`), tag + ': ⧉ inside the command row');
  await copiesExactly(p, '#elev dd.mono', 'apt-get install -y ffmpeg', touch, tag + ' sudo command');
  await save('elevate-copied');
  await fake.send({ t: 'elev_done', id: m.id, result: 'denied' });
  await waitFor(p, `document.getElementById('elev').hidden`, 4000).catch(() => {});
  await sleep(250);
}

async function confirmDlg(p, tag, touch, save) {
  await evaluate(p, `Promise.all([import('./js/ui.js'),import('./js/t.js')]).then(([u,T])=>{u.confirmSheet(T.t('menu.unpairTitle'),T.t('menu.unpairText'),T.t('menu.unpairYes'),true);return true})`);
  await waitFor(p, `!${$('confirm')}.hidden`); await sleep(300);
  await wired(p, tag + ' confirm');
  check(!(await evaluate(p, `${$('confirm-text')}.textContent.includes('\`')`)), tag + ': no raw backticks in the dialog');
  await copiesExactly(p, '#confirm-text code', 'agentj pair', touch, tag + ' confirm command');
  await save('confirm-copied');
  await evaluate(p, `${$('confirm-no')}.click()`); await sleep(250);
}

async function toastCmd(p, tag, touch, save) {
  await evaluate(p, `Promise.all([import('./js/ui.js'),import('./js/t.js')]).then(([u,T])=>{u.toast(T.t('r.say.err.no_agent'),3000);return true})`);
  await waitFor(p, `${$('toast')}.classList.contains('on') && !!${$('toast')}.querySelector('code')`); await sleep(300);
  await wired(p, tag + ' toast');
  check(await evaluate(p, `getComputedStyle(${$('toast')}).pointerEvents`) === 'auto', tag + ': the command toast can be tapped');
  await copiesExactly(p, '#toast code', 'agentj agent claude', touch, tag + ' toast command');
  await save('toast-copied');
  check(await evaluate(p, `${$('toast')}.classList.contains('on')`), tag + ': still there after the tap (6 s)');
  await evaluate(p, `import('./js/ui.js').then(u=>{u.toastOff();return true})`);
}

async function login(p, fake, tag, touch, save) {
  const row = (kind, extra = {}) => ({ kind, label: { claude: 'Claude Code', codex: 'Codex' }[kind], installed: true, current: kind === 'codex', state: 'ok', routes: [], ...extra });
  const view = { dot: false, harnesses: [row('codex', { state: 'login' }), row('claude')],
    flow: { kind: 'codex', route: 'device', stage: 'device', url: 'https://auth.openai.com/codex/device', code: 'AB12-CD34E', expires_at: Math.floor(Date.now() / 1000) + 899 } };
  fake.st.onApp = async (c, m, send) => { if (!String(m.t).startsWith('auth_')) return false; await send({ t: 'auth_res', r: m.r, ok: true, view }); return true; };
  await fake.send({ t: 'auth_rescue', view });
  await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
  if (!(await evaluate(p, `!!${$('set-auth-nav')}`))) { stats.screens.push(tag + ' login: no set-auth-nav'); return; }
  await evaluate(p, `${$('set-auth-nav')}.click()`); await waitFor(p, `!${$('set-auth')}.hidden`);
  await waitFor(p, `document.querySelector('#ar-flow .arcode')?.textContent==='AB12-CD34E'`, 5000);
  await sleep(300);
  await wired(p, tag + ' login', `document.getElementById('settings')`);
  await copiesExactly(p, '#ar-flow .arcode', 'AB12-CD34E', touch, tag + ' device code');
  await save('login-code-copied');
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }); await sleep(200);
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }); await sleep(200);
}

async function negative(p, tag) {
  await evaluate(p, `(()=>{const d=document.createElement('p');d.id='cc-neg';d.style.cssText='position:fixed;left:8px;bottom:8px;z-index:999';d.textContent='run ';const c=document.createElement('code');c.textContent='rogue --cmd';d.append(c);document.body.append(d);return true})()`);
  const bad = await audit(p);
  check(bad.length === 1 && /rogue --cmd/.test(bad[0]), `${tag}: positive control — an unwired <code> is caught (${JSON.stringify(bad)})`);
  await evaluate(p, `document.getElementById('cc-neg').remove()`);
  check((await audit(p)).length === 0, tag + ': and clean again without it');
}

export async function runP292() {
  mkdirSync(OUT, { recursive: true });
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  const combos = [];
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) for (const w of [390, 1440]) {
    const full = lang === 'zh' || (theme === 'light' && w === 390) || (theme === 'dark' && w === 1440);
    combos.push({ lang, theme, w, h: w < 500 ? 844 : 900, full });
  }
  let negDone = false;
  try {
    for (const { lang, theme, w, h, full } of combos) {
      const tag = `${lang}-${theme}-${w}`, s = S[lang], touch = w < 500;
      if (ONLY.length && !ONLY.includes(tag)) continue;
      const p = await paired(B, web, fake, lang, w, h, theme);
      const save = async (name) => { const f = join(OUT, `${tag}-${name}.png`); writeFileSync(f, await shoot(p, '')); stats.screens.push(f); };
      try {
        await offline(p, fake, s, tag, touch, save);
        if (!negDone) { await negative(p, tag); negDone = true; }
        if (full) {
          await reply(p, fake, s, tag, touch, save);
          await approval(p, fake, tag, touch, save);
          await settings(p, s, tag, touch, save);
          await elevate(p, fake, tag, touch, save);
          await confirmDlg(p, tag, touch, save);
          await toastCmd(p, tag, touch, save);
          if ((lang === 'zh' && theme === 'light') || (lang === 'en' && theme === 'dark' && w === 1440)) await login(p, fake, tag, touch, save);
        }
        const problems = p.problems.filter((x) => !/favicon|ERR_CONNECTION_REFUSED|WebSocket connection/.test(x));
        check(problems.length === 0, `${tag}: no console errors (${problems.join(' | ')})`);
        check(p.offsite.length === 0, `${tag}: no off-site requests (${p.offsite.join(' ')})`);
      } finally { await p.dispose(); }
    }
  } finally { await B.close?.(); await fake.stop(); await web.stop(); }
  const out = { checks, clicks: stats.clicks, realClipboardReads: stats.real, screens: stats.screens.length };
  writeFileSync(join(OUT, '..', 'p292-browser.json'), JSON.stringify({ ...out, files: stats.screens.map((f) => f.replace(ROOT, '')) }, null, 2) + '\n');
  return out;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runP292().then((r) => { console.log(`p292: ${r.checks} checks ok, ${r.clicks} real clicks (${r.realClipboardReads} read back from the system clipboard), ${r.screens} shots → ${OUT}`); process.exit(0); },
    (e) => { console.error('p292 FAILED:', e && e.stack || e); process.exit(1); });
}
