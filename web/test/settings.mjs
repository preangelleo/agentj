#!/usr/bin/env node
// F13 (0.15, P44) · the Settings panel against the fake host, in an independent headless Chromium (never :9222).
//   node web/test/settings.mjs        → cases in parity format (reports/qa/parity/web-…settings….json) +
//                                                   reports/qa/release-0.15/settings-{zh,en}-{desktop,phone}.png
// Cases: gear in the header (the bulb is gone), s / ⌘, / Ctrl+, / ? shortcuts, pref_set round trip (contract C6 /
// Amendment A1), an older host (no pref_res → the command), reply text size (persisted, reply card follows), update app
// (SW unregistered + caches cleared + reload), Add to Home Screen (Android prompt, iPhone guide, hidden when standalone),
// read-only safety rows with their commands, versions, unpair from Settings, layout audit (no overflow, taps ≥ 44 px).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, sleep } from './browser.mjs';
import { chatWithLongReplies } from './density_shots.mjs';
import VERSION from '../public/version.js';

const OUT = fileURLToPath(new URL('../../../reports/qa/release-0.15/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const OUT_P156 = fileURLToPath(new URL('../../../reports/qa/p156/', import.meta.url));
const ONLY = (process.env.AJ_SETTINGS_ONLY || '').split(',').filter(Boolean);
const rec = parityRecorder('web/test/settings.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: true, session_mode: 'shared' } };
let failed = 0;

const ev = (p, s) => evaluate(p, s);
const isOpen = (p) => ev(p, `!document.getElementById('settings').hidden`);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
const blur = (p) => ev(p, `document.activeElement && document.activeElement.blur()`);

const chat = (w = 1440, h = 900, opts = {}) => chatWithLongReplies(B, fake, web.url, w, h, opts);
/** A paired chat page; `before` runs on the fake host before pairing (prefs / version / prefSet). */
async function paired({ w = 1440, h = 900, ua, touch, lang, before, init } = {}) {
  fake.reset();
  if (before) before(fake.st);
  const p = await newPage(B, w, h, 'light', { ua, touch, allow: [web.url, fake.relay + '/'] });
  if (init) await p.send('Page.addScriptToEvaluateOnNewDocument', { source: init });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  return p;
}
async function C(name, fn) {
  if (ONLY.length && !ONLY.includes(name)) return;
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };

try {
  await C('settings-gear', async () => {
    const p = await paired();
    try {
      const g = await ev(p, `(() => { const b = document.getElementById('setBtn'), r = b.getBoundingClientRect(); return { inTop: !!b.closest('.top'), w: r.width, h: r.height, label: b.getAttribute('aria-label'), bulbInTop: !!document.querySelector('.top #keysBtn') }; })()`);
      assert.ok(g.inTop && g.w >= 44 && g.h >= 44, JSON.stringify(g));
      assert.equal(g.label, '设置');
      assert.equal(g.bulbInTop, false, 'the lightbulb left the header');
      await click(p, '#setBtn');
      await waitFor(p, `!document.getElementById('settings').hidden`);
      assert.equal(await ev(p, `document.activeElement.classList.contains('setbox')`), true, 'focus moves into the dialog');
      await click(p, '#setBtn');
      assert.equal(await isOpen(p), false);
      await click(p, '#setBtn');
      await key(p, 'Escape', { code: 'Escape' });
      assert.equal(await isOpen(p), false, 'Esc closes');
      await ev(p, `document.getElementById('input').value = 'keep me'`);
      await click(p, '#setBtn'); await key(p, 'Escape', { code: 'Escape' }); await key(p, 'Escape', { code: 'Escape' });
      // the first Esc closed the panel; it never counted toward relay's Esc Esc (which would clear the composer)
      assert.equal(await ev(p, `document.getElementById('input').value`), 'keep me');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('settings-shortcuts', async () => {
    const p = await paired();
    try {
      await blur(p);
      await key(p, 's', { code: 'KeyS' });
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await key(p, 'Escape', { code: 'Escape' });
      assert.equal(await isOpen(p), false);
      // Ctrl+, works even with the cursor in the composer (and types nothing)
      await ev(p, `document.getElementById('input').focus()`);
      await key(p, ',', { code: 'Comma', ctrl: true, keyCode: 188 });
      await waitFor(p, `!document.getElementById('settings').hidden`);
      assert.equal(await ev(p, `document.getElementById('input').value`), '');
      await click(p, '#setBtn');
      await blur(p);
      await key(p, ',', { code: 'Comma', meta: true, keyCode: 188 });
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await key(p, ',', { code: 'Comma', meta: true, keyCode: 188 });
      assert.equal(await isOpen(p), false, '⌘, again closes it');
      // `s` typed into the field is just a letter
      await ev(p, `document.getElementById('input').focus()`);
      await key(p, 's', { code: 'KeyS' });
      assert.equal(await isOpen(p), false);
      await ev(p, `document.getElementById('input').value = ''`); await blur(p);
      // ? still opens the shortcut sheet, which lists s / Ctrl+,
      await key(p, '?', { code: 'Slash', shift: true, text: '?' });
      await waitFor(p, `!document.getElementById('keys').hidden`);
      assert.match(await ev(p, `document.getElementById('keys').textContent`), /打开设置/);
      await key(p, 'Escape', { code: 'Escape' });
      // the second-level item in Settings opens the same page ABOVE Settings (P187): its back icon returns there
      await click(p, '#setBtn'); await click(p, '#keysBtn');
      await waitFor(p, `!document.getElementById('keys').hidden`);
      assert.equal(await isOpen(p), true);
      await click(p, '#keysClose');
      assert.equal(await ev(p, `document.getElementById('keys').hidden`), true);
      assert.equal(await isOpen(p), true, 'back from the shortcuts = Settings');
      await click(p, '#setBtn');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('settings-pref-roundtrip', async () => {
    const p = await paired({ before: (st) => { st.prefs = structuredClone(PREFS); st.version = '0.15.0a1'; } });
    try {
      await click(p, '#setBtn');
      assert.equal(await ev(p, `document.getElementById('set-speak').getAttribute('aria-checked')`), 'false');
      await click(p, '#set-speak');
      await waitFor(p, `(document.getElementById('toast').classList.contains('on') ? document.getElementById('toast').textContent : '') === '已保存'`);
      assert.deepEqual(fake.st.prefSets.map((m) => [m.t, m.key, m.value]), [['pref_set', 'voice.speak_replies', true]]);
      assert.ok(typeof fake.st.prefSets[0].r === 'string' && fake.st.prefSets[0].r.length > 0, 'request id r');
      await waitFor(p, `document.getElementById('set-speak').getAttribute('aria-checked') === 'true'`);
      await click(p, '#set-wake');
      await waitFor(p, `document.getElementById('set-wake').getAttribute('aria-checked') === 'true' && (document.getElementById('toast').classList.contains('on') ? document.getElementById('toast').textContent : '') === '已保存'`);
      assert.equal(fake.st.prefs.voice.wake_enabled, true);
      // theme: the page follows at once and the host stores it
      await click(p, '[data-set-theme="dark"]');
      await waitFor(p, `document.documentElement.getAttribute('data-theme') === 'dark' && document.querySelector('[data-set-theme="dark"]').getAttribute('aria-pressed') === 'true'`);
      await waitFor(p, `(document.getElementById('toast').classList.contains('on') ? document.getElementById('toast').textContent : '') === '已保存'`);
      assert.equal(fake.st.prefs.appearance.theme, 'dark');
      assert.equal(await ev(p, `localStorage.getItem('aj.theme')`), 'dark', 'persisted through the page\'s own theme switch');
      await click(p, '[data-set-theme="system"]');
      await waitFor(p, `!document.documentElement.hasAttribute('data-theme')`);
      // language: ONE value (Amendment A1) — the page switches and the host's appearance.language follows
      await click(p, '[data-set-lang="en"]');
      await waitFor(p, `document.documentElement.lang === 'en' && document.getElementById('set-title').textContent === 'Settings'`);
      await waitFor(p, `(document.getElementById('toast').classList.contains('on') ? document.getElementById('toast').textContent : '') === 'Saved'`);
      assert.equal(fake.st.prefs.appearance.language, 'en');
      assert.match(await ev(p, `document.querySelector('#set-lang-row .setnote').textContent`), /emails we send you/);
      // a value the host refuses → its problem is shown, nothing changes
      const r = await ev(p, `import('./js/settings.js').then((m) => m.setPref('appearance.font_scale', 2))`);
      assert.equal(r, false, 'phone-local keys are never sent');
      assert.equal(fake.st.prefSets.some((m) => m.key === 'appearance.font_scale'), false);
      await click(p, '[data-set-lang="zh"]');
      await waitFor(p, `document.documentElement.lang === 'zh-CN'`);
      await waitFor(p, `(document.getElementById('toast').classList.contains('on') ? document.getElementById('toast').textContent : '') === '已保存'`);
      // the host's own broadcast (e.g. `agentj config set …` on the Agent J host) re-renders the open panel
      await fake.send(fake.prefsMsg({ ...fake.st.prefs, voice: { speak_replies: false, wake_enabled: false } }));
      await waitFor(p, `document.getElementById('set-speak').getAttribute('aria-checked') === 'false' && document.getElementById('set-wake').getAttribute('aria-checked') === 'false'`);
      assert.equal(fake.st.prefSets.every((m) => ['appearance.language', 'appearance.theme', 'voice.speak_replies', 'voice.wake_enabled'].includes(m.key)), true);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('settings-pref-old-host', async () => {
    const p = await paired({ before: (st) => { st.prefSet = false; } });   // no preferences at all: the request times out
    try {
      await click(p, '#setBtn');
      await click(p, '#set-wake');
      await waitFor(p, `!document.getElementById('set-pref-cmd').hidden`, 12000);
      assert.equal(await ev(p, `document.querySelector('#set-pref-cmd code').textContent`), 'agentj config set voice.wake_enabled true');
      assert.equal(await ev(p, `document.getElementById('set-wake').getAttribute('aria-checked')`), 'false', 'not shown as on when nothing saved');
      noProblems(p);
    } finally { await p.dispose(); }
    const q = await paired({ before: (st) => { st.prefs = structuredClone(PREFS); st.prefSet = false; } });   // 0.12–0.14: preferences, no host block
    try {
      await click(q, '#setBtn');
      await click(q, '[data-set-lang="en"]');
      await waitFor(q, `!document.getElementById('set-lang-cmd').hidden`, 2000);   // at once, no 8 s wait
      assert.equal(await ev(q, `document.querySelector('#set-lang-cmd code').textContent`), 'agentj config set appearance.language en');
      assert.equal(fake.st.prefSets.length, 0, 'nothing sent to a host that cannot take it');
      await click(q, '[data-set-lang="zh"]');
      noProblems(q);
    } finally { await q.dispose(); }
  });

  await C('settings-font-scale', async () => {
    const p = await chat(390, 844, { touch: true });
    try {
      const base = await ev(p, `parseFloat(getComputedStyle(document.getElementById('words')).fontSize)`);
      assert.equal(base, 16, 'phone default reply size 16 px');
      await click(p, '#setBtn');
      assert.equal(await ev(p, `document.getElementById('set-font-val').textContent`), '100%');
      await click(p, '#set-font-plus'); await click(p, '#set-font-plus');
      assert.equal(await ev(p, `document.getElementById('set-font-val').textContent`), '125%');
      assert.equal(await ev(p, `parseFloat(getComputedStyle(document.getElementById('words')).fontSize)`), 20, 'the reply card follows');
      assert.equal(await ev(p, `localStorage.getItem('aj.fontScale')`), '1.25');
      for (let i = 0; i < 8; i++) await click(p, '#set-font-minus');
      assert.equal(await ev(p, `document.getElementById('set-font-val').textContent`), '80%');
      assert.equal(await ev(p, `document.getElementById('set-font-minus').disabled`), true);
      for (let i = 0; i < 8; i++) await click(p, '#set-font-plus');
      assert.equal(await ev(p, `document.getElementById('set-font-val').textContent`), '160%');
      const big = await ev(p, `parseFloat(getComputedStyle(document.getElementById('words')).fontSize)`);
      assert.ok(big > 17 && 16 * 0.8 < 17, `range goes below and above the 0.14 phone size (17 px): ${big}`);
      // persisted: a reload keeps it (the page starts with it, before the panel is ever opened)
      await navigate(p, web.url);
      await waitState(p, 'ready');
      await waitFor(p, `getComputedStyle(document.documentElement).getPropertyValue('--rs').trim() === '1.6'`);
      assert.equal(await ev(p, `parseFloat(getComputedStyle(document.getElementById('words')).fontSize)`), 25.6);
      await ev(p, `document.getElementById('setBtn').click(); document.getElementById('set-font-reset').click()`);
      assert.equal(await ev(p, `localStorage.getItem('aj.fontScale')`), '1');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('settings-update-app', async () => {
    // spy (test code only): every unregister() of a service worker registration is counted in localStorage
    const spy = `(() => { const u = ServiceWorkerRegistration.prototype.unregister; ServiceWorkerRegistration.prototype.unregister = function () { localStorage.setItem('test.unreg', String(Number(localStorage.getItem('test.unreg') || 0) + 1)); return u.call(this); }; const up = ServiceWorkerRegistration.prototype.update; ServiceWorkerRegistration.prototype.update = function () { localStorage.setItem('test.update', '1'); return up.call(this); }; })()`;
    const p = await paired({ init: spy });
    try {
      await waitFor(p, `navigator.serviceWorker.getRegistrations().then((r) => r.length > 0)`);
      await ev(p, `caches.open('aj-old-shell').then((c) => c.put('/x', new Response('old')))`);
      assert.deepEqual(await ev(p, `caches.keys()`), ['aj-old-shell']);
      await ev(p, `window.__beforeRefresh = 1`);
      await click(p, '#setBtn');
      await click(p, '#set-refresh');
      await waitFor(p, `window.__beforeRefresh === undefined`, 10000);
      await waitFor(p, `document.readyState === 'complete'`);
      assert.deepEqual(await ev(p, `caches.keys()`), [], 'Cache Storage emptied');
      assert.ok(Number(await ev(p, `localStorage.getItem('test.unreg')`)) >= 1, 'service worker unregistered');
      assert.equal(await ev(p, `localStorage.getItem('test.update')`), '1', 'asked for a fresh worker first');
      assert.equal(await ev(p, `performance.getEntriesByType('navigation')[0].type`), 'reload');
      await waitState(p, 'ready');                    // still paired after the reload
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('settings-a2hs-android', async () => {
    const p = await paired({ w: 412, h: 915, touch: true, ua: 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36' });
    try {
      await click(p, '#setBtn');
      assert.equal(await ev(p, `document.getElementById('set-a2hs').hidden`), true, 'no prompt yet → the browser-menu hint');
      assert.match(await ev(p, `document.getElementById('set-a2hs-text').textContent`), /安装应用/);
      await ev(p, `(() => { const e = new Event('beforeinstallprompt', { cancelable: true }); e.prompt = async () => { window.__prompted = (window.__prompted || 0) + 1; }; e.userChoice = Promise.resolve({ outcome: 'accepted' }); window.__bip = e; dispatchEvent(e); })()`);
      assert.equal(await ev(p, `window.__bip.defaultPrevented`), true, 'the mini-infobar is kept for our button');
      await waitFor(p, `!document.getElementById('set-a2hs').hidden && document.getElementById('set-a2hs').textContent === '添加'`);
      await click(p, '#set-a2hs');
      await waitFor(p, `window.__prompted === 1 && (document.getElementById('toast').classList.contains('on') ? document.getElementById('toast').textContent : '').includes('已添加')`);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('settings-a2hs-ios', async () => {
    const p = await paired({ w: 390, h: 844, touch: true, ua: IPHONE });
    try {
      await click(p, '#setBtn');
      await waitFor(p, `document.getElementById('set-a2hs').textContent === '怎么添加'`);
      await click(p, '#set-a2hs');
      await waitFor(p, `!document.getElementById('set-ios').hidden && document.getElementById('set-main').hidden`);
      assert.equal(await ev(p, `document.querySelectorAll('#set-ios .iossteps li').length`), 3);
      assert.equal(await ev(p, `document.getElementById('set-title').textContent`), '在 iPhone 上添加到主屏幕');
      writeFileSync(OUT + 'settings-ios-guide-phone.png', await shoot(p, 'unused'));
      await key(p, 'Escape', { code: 'Escape' });
      await waitFor(p, `document.getElementById('set-ios').hidden && !document.getElementById('set-main').hidden`);
      assert.equal(await isOpen(p), true, 'Esc in the guide goes back, not out');
      await click(p, '#setBtn');
    } finally { await p.dispose(); }
    // opened from the Home Screen: nothing to add, the row is gone
    const q = await paired({ w: 390, h: 844, touch: true, ua: IPHONE, init: `Object.defineProperty(navigator, 'standalone', { value: true })` });
    try {
      await click(q, '#setBtn');
      assert.equal(await ev(q, `document.getElementById('set-a2hs-row').hidden`), true);
      noProblems(q);
    } finally { await q.dispose(); }
  });

  await C('settings-readonly-rows', async () => {
    const p = await paired({ before: (st) => { st.prefs = structuredClone(PREFS); } });
    try {
      await click(p, '#setBtn');
      await waitFor(p, `document.getElementById('set-risk').getAttribute('aria-checked') === 'true'`);
      assert.equal(await ev(p, `document.getElementById('set-risk-cmd').textContent`), 'agentj config set agent.high_risk_warnings false');
      assert.equal(await ev(p, `document.getElementById('set-mode').getAttribute('aria-checked')`), 'true');   // P207: two-way switch, right = 共享
      assert.equal(await ev(p, `document.getElementById('set-mode-cmd').textContent`), 'agentj config set agent.session_mode independent');
      // P294: shared mode → a line under 隔离运行 says the open computer session is not covered (the switch stays usable)
      assert.equal(await ev(p, `document.querySelector('[data-i18n="set.iso"]').textContent`), '隔离运行');
      assert.equal(await ev(p, `!document.getElementById('set-iso-shared').hidden && !document.getElementById('set-iso').disabled`), true);
      // nothing on these rows can write: no switch, no pref_set
      assert.equal(await ev(p, `document.querySelectorAll('#set-risk-cmd, #set-mode-cmd').length === 2 && !!document.querySelector('#settings [data-pref^="agent."]')`), true);
      fake.st.prefs = { ...PREFS, agent: { high_risk_warnings: false, session_mode: 'independent' } };
      await fake.send(fake.prefsMsg(fake.st.prefs));
      await waitFor(p, `document.getElementById('set-risk').getAttribute('aria-checked') === 'false'`);
      assert.equal(await ev(p, `document.getElementById('set-risk-cmd').textContent`), 'agentj config set agent.high_risk_warnings true');
      assert.equal(await ev(p, `document.getElementById('set-mode-cmd').textContent`), 'agentj config set agent.session_mode shared');
      await ev(p, `document.querySelector('[data-copy="set-risk-cmd"]').click()`);
      await waitFor(p, `(document.getElementById('toast').classList.contains('on') ? document.getElementById('toast').textContent : '') === '已复制'`);
      assert.equal(await ev(p, `navigator.clipboard.readText()`), 'agentj config set agent.high_risk_warnings true');
      await click(p, '#set-risk');
      await waitFor(p, `document.getElementById('set-risk').getAttribute('aria-checked') === 'true'`);
      assert.equal(fake.st.prefSets.at(-1).key, 'agent.high_risk_warnings');
      await click(p, '#set-mode');
      await waitFor(p, `document.getElementById('set-mode').getAttribute('aria-checked') === 'true'`);
      assert.equal(fake.st.prefSets.at(-1).key, 'agent.session_mode');
      // P174: a host that sends no session_mode shows the factory default (independent) and offers shared
      fake.st.prefs = { ...PREFS, agent: { high_risk_warnings: false } };
      await fake.send(fake.prefsMsg(fake.st.prefs));
      await waitFor(p, `document.getElementById('set-mode').getAttribute('aria-checked') === 'false' && document.querySelector('#set-mode ~ .tw-lab, .tw-lab[data-tw="set-mode"][data-side="left"]').getAttribute('aria-current') === 'true'`);
      assert.equal(await ev(p, `document.getElementById('set-mode-cmd').textContent`), 'agentj config set agent.session_mode shared');
      // F14 (P45b): isolation (default on) and docker (default off) switch from the phone
      assert.equal(await ev(p, `document.getElementById('set-iso').getAttribute('aria-checked')`), 'true');
      assert.equal(await ev(p, `document.getElementById('set-iso-cmd').textContent`), 'agentj config set agent.isolation false');
      assert.equal(await ev(p, `document.getElementById('set-iso-shared').hidden`), true);   // P294: independent → no shared note
      assert.equal(await ev(p, `document.getElementById('set-docker').getAttribute('aria-checked')`), 'false');
      assert.equal(await ev(p, `document.getElementById('set-docker-off').hidden && !document.getElementById('set-docker').disabled`), true);
      await click(p, '#set-docker');
      await waitFor(p, `document.getElementById('set-docker').getAttribute('aria-checked') === 'true'`);
      assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['agent.allow_docker', true]);
      await click(p, '#set-iso');
      await waitFor(p, `document.getElementById('set-iso').getAttribute('aria-checked') === 'false'`);
      assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['agent.isolation', false]);
      // P294: Docker depends on isolation → greyed, disabled, says why; a click writes nothing
      assert.equal(await ev(p, `!document.getElementById('set-docker-off').hidden && document.getElementById('set-docker').disabled && document.getElementById('set-docker').closest('.setrow').classList.contains('setoff')`), true);
      const n = fake.st.prefSets.length;
      await click(p, '#set-docker');
      await sleep(200);
      assert.equal(fake.st.prefSets.length, n);
      // account / help: the dashboard and the feedback mailbox (P168), new tab
      assert.equal(await ev(p, `document.getElementById('set-account').href`), 'https://agentj.app/account/');
      assert.ok(await ev(p, `!!document.querySelector('#settings a[href="mailto:feedback@agentj.app"]')`));
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('fleet-upgrade-mode-and-read', async () => {
    const p = await paired({ before: (st) => { st.prefs = structuredClone(PREFS); } });
    try {
      await click(p, '#setBtn');
      // P203 + P207: the two-way 升级方式 (left 自动 · right 先问我) writes both keys — updates.auto_install + updates.mode
      await waitFor(p, `document.getElementById('set-updates').getAttribute('aria-checked') === 'false'`);   // left = 自动
      await click(p, '#set-updates');
      await waitFor(p, `document.getElementById('set-updates').getAttribute('aria-checked') === 'true'`);
      for (let i = 0; i < 50 && fake.st.prefSets.at(-1)?.key !== 'updates.mode'; i++) await sleep(100);
      assert.deepEqual(fake.st.prefSets.slice(-2).map((m) => [m.key, m.value]), [['updates.auto_install', false], ['updates.mode', 'ask']]);
      await click(p, '#set-updates');
      await waitFor(p, `document.getElementById('set-updates').getAttribute('aria-checked') === 'false'`);
      for (let i = 0; i < 50 && fake.st.prefSets.at(-1)?.value !== 'auto'; i++) await sleep(100);
      assert.equal(fake.st.prefSets.at(-1).value, 'auto');
      await click(p, '#setBtn');
      const id = 'N'.repeat(22);
      await fake.addTurn({ k:'sys', notice_id:id, text:'Agent J 官方' }, '官方升级消息，仅作为数据。');
      for (let i=0;i<50 && !fake.log.some(m=>m.t==='notice_read');i++) await sleep(100);
      assert.equal(fake.log.filter(m=>m.t==='notice_read' && m.id===id).length,1);
      await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
      await sleep(100);
      assert.equal(fake.log.filter(m=>m.t==='notice_read' && m.id===id).length,1, 'foreground receipt deduplicated');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('settings-heartbeat', async () => {           // P156: switch + idle interval + cost note
    const p = await paired({ before: (st) => { st.prefs = { ...structuredClone(PREFS), heartbeat: { enabled: true, interval_minutes: 60 } }; } });
    try {
      await click(p, '#setBtn');
      await waitFor(p, `!document.getElementById('set-hb-row').hidden`);
      assert.equal(await ev(p, `document.getElementById('set-hb').getAttribute('aria-checked')`), 'true');
      assert.equal(await ev(p, `document.getElementById('set-hb-every').value + ' ' + document.getElementById('set-hb-every').selectedOptions[0].textContent`), '60 空闲 1 小时后');   // P207: a dropdown
      assert.match(await ev(p, `document.querySelector('#set-hb-row .setnote').textContent`), /每次心跳都会用掉一次模型调用/);
      await ev(p, `document.getElementById('set-hb-row').scrollIntoView({block:'center'})`);
      mkdirSync(OUT_P156, { recursive: true });
      writeFileSync(OUT_P156 + 'settings-heartbeat-zh.png', await shoot(p));
      await ev(p, `(() => { const s = document.getElementById('set-hb-every'); s.value = '120'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
      await waitFor(p, `document.getElementById('set-hb-every').value === '120'`);
      assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['heartbeat.interval_minutes', 120]);
      await click(p, '#set-hb');
      await waitFor(p, `document.getElementById('set-hb').getAttribute('aria-checked') === 'false'`);
      assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['heartbeat.enabled', false]);
      assert.equal(await ev(p, `document.getElementById('set-hb-every').disabled`), true, 'off: no interval to pick');
      const r = await ev(p, `JSON.stringify(document.getElementById('set-hb').getBoundingClientRect())`);
      assert.ok(JSON.parse(r).height >= 24, 'the switch is visible');
      noProblems(p);
    } finally { await p.dispose(); }
    const old = await paired({ before: (st) => { st.prefs = structuredClone(PREFS); st.prefSet = false; } });
    try {                                                  // an older host cannot set it: no row
      await click(old, '#setBtn');
      await sleep(200);
      assert.equal(await ev(old, `document.getElementById('set-hb-row').hidden`), true);
    } finally { await old.dispose(); }
  });

  await C('settings-imessage', async () => {           // P274: macOS-only card, parameters via pref_set, rules read-only
    const OUT_P274 = fileURLToPath(new URL('../../../reports/qa/p274/', import.meta.url));
    mkdirSync(OUT_P274, { recursive: true });
    const IM = { enabled: true, interval_minutes: 5, delivery: 'instant_digest', digest_at: '09:00,18:00', quiet_hours: '', strictness: 'standard',
      codes: 'show', groups: true, llm: 'off', vip: ['+15550100001'], mute: [], instructions: '家人的消息都要马上提醒。\n快递短信只进摘要。' };
    const lin = await paired({ before: (st) => { st.prefs = { ...structuredClone(PREFS), imessage: structuredClone(IM) }; } });
    try {                                                  // Linux / Windows: no card at all
      await click(lin, '#setBtn');
      await sleep(300);
      assert.equal(await ev(lin, `document.getElementById('set-imsg-nav').hidden`), true, 'not on Linux');
    } finally { await lin.dispose(); }
    for (const [w, h, lang, theme] of [[390, 844, 'zh', 'light'], [1440, 900, 'zh', 'light'], [390, 844, 'en', 'dark'], [1440, 900, 'en', 'dark']]) {
      const p = await paired({ w, h, lang, before: (st) => { st.platform = 'macos'; st.prefs = { ...structuredClone(PREFS), appearance: { language: lang, theme }, imessage: structuredClone(IM) };
        st.imsg = { status: 'no_fda', grant_to: 'python3.13', alerts_today: 0, blocked_today: 0, next_digest: '18:00' }; } });
      try {
        await ev(p, `document.documentElement.setAttribute('data-theme', ${JSON.stringify(theme)})`);
        await click(p, '#setBtn');
        await waitFor(p, `!document.getElementById('set-imsg-nav').hidden`);
        await click(p, '#set-imsg-nav');
        await waitFor(p, `!document.getElementById('set-imessage').hidden`);
        assert.equal(await ev(p, `document.getElementById('imsg-on').getAttribute('aria-checked')`), 'true');
        assert.equal(await ev(p, `document.getElementById('imsg-fda').hidden`), false, '待授权: the three-step guide');
        assert.equal(await ev(p, `document.getElementById('imsg-grant').textContent`), 'python3.13');
        assert.equal(await ev(p, `document.getElementById('imsg-rules').tagName + '|' + document.getElementById('imsg-rules').isContentEditable`), 'PRE|false', 'rules are read-only');
        assert.match(await ev(p, `document.getElementById('imsg-rules').textContent`), /快递短信只进摘要/);
        writeFileSync(OUT_P274 + `settings-imessage-${lang}-${w}-${theme}.png`, await shoot(p));
        await ev(p, `document.getElementById('imsg-assist').scrollIntoView({ block: 'end' })`);
        writeFileSync(OUT_P274 + `settings-imessage-rules-${lang}-${w}-${theme}.png`, await shoot(p));
        await ev(p, `document.getElementById('set-imessage').scrollTop = 0`);
        if (w !== 390 || lang !== 'zh') { noProblems(p); continue; }
        assert.match(await ev(p, `document.getElementById('imsg-status').textContent`), /待授权/);
        await click(p, '#imsg-fda-open');
        for (let i = 0; i < 50 && !fake.st.imsgCalls.includes('imsg_fda'); i++) await sleep(100);
        await waitFor(p, `/已在Agent J 主机上打开/.test(document.getElementById('imsg-msg').textContent)`);
        await click(p, '#imsg-check');
        await waitFor(p, `/运行中/.test(document.getElementById('imsg-status').textContent)`);
        assert.equal(await ev(p, `document.getElementById('imsg-fda').hidden`), true, 'authorised: the guide goes');
        await ev(p, `(() => { const s = document.getElementById('imsg-every'); s.value = '15'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
        for (let i = 0; i < 50 && fake.st.prefSets.at(-1)?.key !== 'imessage.interval_minutes'; i++) await sleep(100);
        assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['imessage.interval_minutes', 15]);
        await ev(p, `(() => { const s = document.getElementById('imsg-quiet'); s.value = '22:00-07:00'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
        for (let i = 0; i < 50 && fake.st.prefSets.at(-1)?.key !== 'imessage.quiet_hours'; i++) await sleep(100);
        assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['imessage.quiet_hours', '22:00-07:00']);
        await click(p, '#imsg-groups');
        for (let i = 0; i < 50 && fake.st.prefSets.at(-1)?.key !== 'imessage.groups'; i++) await sleep(100);
        assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['imessage.groups', false]);
        // a value set on the Agent J host (agentj imessage set interval_minutes 7) shows as its own option
        fake.st.prefs.imessage.interval_minutes = 7;
        await fake.send({ t: 'preferences', value: fake.st.prefs, problem: null, host: { version: '0.20.4a1', language_at: 0, settable: ['imessage.enabled', 'imessage.interval_minutes', 'imessage.groups', 'imessage.quiet_hours'], platform: 'macos', imessage: fake.st.imsg } });
        await waitFor(p, `document.getElementById('imsg-every').value === '7'`);
        assert.match(await ev(p, `document.getElementById('imsg-every').selectedOptions[0].textContent`), /每 7 分钟（在Agent J 主机上设的）/);
        await key(p, 'Escape', { code: 'Escape' });
        await waitFor(p, `document.getElementById('set-imessage').hidden && !document.getElementById('set-main').hidden`);
        await click(p, '#set-imsg-nav');
        await waitFor(p, `!document.getElementById('set-imessage').hidden`);
        const says = fake.st.says.length;
        await click(p, '#imsg-assist');                    // 「让 Agent J 帮我改」: a draft in the chat box, never sent
        await waitFor(p, `document.getElementById('settings').hidden`);
        assert.match(await ev(p, `document.getElementById('input').value`), /^\/agentj-imessage\n\[来自设置页按钮：iMessage 助手\]/);
        await sleep(300);
        assert.equal(fake.st.says.length, says, 'not sent');
        await click(p, '#setBtn'); await click(p, '#set-imsg-nav');
        await waitFor(p, `!document.getElementById('set-imessage').hidden`);
        await click(p, '#imsg-on');
        for (let i = 0; i < 50 && fake.st.prefSets.at(-1)?.key !== 'imessage.enabled'; i++) await sleep(100);
        assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['imessage.enabled', false]);
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });

  await C('settings-versions', async () => {
    const p = await paired({ before: (st) => { st.prefs = structuredClone(PREFS); st.version = '0.15.0a1'; } });
    try {
      await click(p, '#setBtn');
      assert.equal(await ev(p, `document.getElementById('set-webver').textContent`), `网页版：${VERSION.version}`);
      assert.equal(await ev(p, `document.getElementById('set-hostver').textContent`), 'Agent J 主机端程序：0.15.0a1');
      assert.match(await ev(p, `document.getElementById('set-computer').textContent`), /Agent J · 已连上/);
      // the phones the Agent J host paired (host metadata), this one marked — P171: on the 遥控器管理 page; a host without §24
      // (this fake answers no dev_list) → the same list from the preferences host block, read-only
      await click(p, '#set-remotes-open');
      await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 1 && !document.getElementById('rm-legacy').hidden`, 9000);
      assert.equal(await ev(p, `document.querySelector('#rm-list .rmtag').textContent`), '这台');
      assert.match(await ev(p, `document.querySelector('#rm-list .rmmeta').textContent`), /^在线/);
    } finally { await p.dispose(); }
    const q = await paired({ before: (st) => { st.prefs = structuredClone(PREFS); st.prefSet = false; } });   // a host before 0.15: preferences without the host block
    try {
      await click(q, '#setBtn');
      assert.equal(await ev(q, `document.getElementById('set-hostver').textContent`), 'Agent J 主机端程序：没有报告版本号');
      noProblems(q);
    } finally { await q.dispose(); }
  });

  await C('settings-telegram-pair', async () => {   // P536 #296: editable main-Agent setup/manage request
    const p = await paired();
    try {
      await click(p, '#setBtn');
      assert.equal(await ev(p, `document.getElementById('set-tgpair').textContent`), '开通或管理');
      assert.doesNotMatch(await ev(p, `document.getElementById('settings').textContent`), /userinfobot/i);
      await click(p, '#set-tgpair');
      await waitFor(p, `document.getElementById('input').value.includes('agentj-config')`);
      assert.match(await ev(p, `document.getElementById('input').value`), /Telegram/);
      assert.equal(fake.log.filter((m) => m.t === 'slash' && m.cmd === 'telegram-pair').length, 0, 'no immediate command; owner may add details before sending');
      assert.equal(await isOpen(p), false, 'settings close so the code card is in view');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('settings-unpair', async () => {
    const p = await paired();
    try {
      await click(p, '#setBtn');
      await click(p, '#unpair');
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      assert.equal(await isOpen(p), false);
      await click(p, '#confirm-no');
      assert.equal(await ev(p, `window.__ajState`), 'ready', 'cancelled: still paired');
      await click(p, '#setBtn'); await click(p, '#unpair'); await click(p, '#confirm-yes');
      await waitFor(p, `document.body.dataset.view === 'pair'`);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  // F13 density (Leo 10-05): smaller reply type, tighter chrome, the space handed to the reply card; capped and centred on wide
  // screens; phones keep 44 px taps. Numbers from 0.14 at the same sizes: desktop card 1328 × 405, 19 px; phone 358 × 489, 17 px.
  await C('density-reply-area', async () => {
    const { GEOMETRY } = await import('./density_shots.mjs');
    for (const [w, h, opts] of [[1440, 900, {}], [390, 844, { touch: true }], [360, 800, { touch: true }]]) {
      const p = await chat(w, h, opts);
      try {
        assert.equal(await ev(p, `document.getElementById('toast').textContent`), '', 'initial notification is empty');
        assert.equal(await ev(p, `document.getElementById('toast').getBoundingClientRect().height`), 0, 'empty notification reserves no reply space');
        const g = await ev(p, GEOMETRY);
        const words = parseFloat(g.words);
        if (w > 1000) {
          // P412 (#174, Leo 10-20: 「拉多宽撑多宽」): no 960 px cap — the card fills the window between the paging columns
          assert.ok(g.deck.w >= w - 2 * 120 && g.deck.w <= w, `card ${g.deck.w} px wide`);
          const left = await ev(p, `document.getElementById('deck').getBoundingClientRect().left`);
          assert.ok(Math.abs(left - (w - g.deck.w) / 2) <= 2, 'centred');
          assert.ok(words >= 16 && words <= 18, `desktop reply ${words} px`);
          assert.ok(g.rm.h >= 540, `reply card ${g.rm.h} px tall`);
          assert.ok(g.composer.h <= 160, `composer ${g.composer.h} px`);
        } else {
          assert.equal(words, 16);
          assert.ok(g.rm.h >= (h > 820 ? 510 : 470), `reply card ${g.rm.h} px tall`);
          assert.ok(g.tool.h >= 44 && g.field.h >= 44, 'touch targets');
          assert.equal(await ev(p, `document.documentElement.scrollWidth`), w, 'no sideways scroll');
        }
        assert.ok(g.top.h <= 60, `header ${g.top.h} px`);
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });

  // P170: Settings → 邮件通道 — preset by domain, save, app password card request, allowlist add / remove, test, layout
  await C('settings-mail', async () => {
    for (const lang of ['zh', 'en']) {
      for (const [dev, w, h] of [['desktop', 1440, 900], ['phone', 390, 844]]) {
        const p = await paired({ w, h, touch: dev === 'phone', lang, before: (st) => { st.prefs = structuredClone({ ...PREFS, appearance: { language: lang, theme: 'system' } }); st.mail = null; st.mailMsgs = []; } });
        try {
          await click(p, '#setBtn');
          await click(p, '#set-mail-nav');
          await waitFor(p, `document.getElementById('mail-preset').options.length === 3`);
          assert.equal(await ev(p, `document.getElementById('set-main').hidden && !document.getElementById('set-mail').hidden`), true);
          await ev(p, `(() => { const a = document.getElementById('mail-addr'); a.value = 'agent@163.com'; a.dispatchEvent(new Event('change')); })()`);
          assert.equal(await ev(p, `document.getElementById('mail-preset').value`), '163', 'the provider follows the address');
          await click(p, '#mail-save');
          await waitFor(p, `document.getElementById('mail-status').textContent.includes('agent@163.com')`);
          const set = fake.st.mailMsgs.find((x) => x.t === 'mail_set');
          assert.equal(set.cfg.preset, '163'); assert.equal(set.cfg.address, 'agent@163.com');
          assert.equal(JSON.stringify(fake.st.mailMsgs).includes('password'), false, 'no password field ever travels in mail_* messages');
          await ev(p, `document.getElementById('mail-add-addr').value = 'me@qq.com'`);
          await click(p, '#mail-add');
          await waitFor(p, `document.querySelectorAll('#mail-allow li').length === 1`);
          await click(p, '#mail-blocked li button');      // one tap allowlists a refused sender
          await waitFor(p, `document.querySelectorAll('#mail-allow li').length === 2`);
          await click(p, '#mail-key');
          await waitFor(p, `!document.getElementById('mail-key').disabled && document.getElementById('mail-key').textContent === ${JSON.stringify(lang === 'en' ? 'Set · Change' : '已设置 · 更换')}`);
          await click(p, '#mail-test');
          await waitFor(p, `document.getElementById('mail-sample').textContent.includes('mx.qq.com')`);
          await ev(p, `document.getElementById('mail-auth').value = 'off'; document.getElementById('mail-auth').dispatchEvent(new Event('change'))`);
          assert.equal(await ev(p, `document.getElementById('mail-auth-off').hidden`), false, 'turning the check off warns');
          const audit = await ev(p, `(() => {
            const box = document.getElementById('set-mail');
            const bad = [...box.querySelectorAll('button:not([hidden]), a, select, input, summary')].filter((b) => b.offsetParent && b.getBoundingClientRect().height < 44).map((b) => b.id || b.textContent.trim().slice(0, 20));
            const wide = [...box.querySelectorAll('*')].filter((n) => n.offsetParent && n.getBoundingClientRect().right > box.getBoundingClientRect().right + 1).map((n) => n.id || n.className || n.tagName);
            return { overflowX: box.scrollWidth > box.clientWidth || document.documentElement.scrollWidth > innerWidth, small: bad, wide: wide.slice(0, 5) };
          })()`);
          assert.equal(audit.overflowX, false, JSON.stringify(audit));
          assert.deepEqual(audit.wide, [], JSON.stringify(audit));
          assert.deepEqual(audit.small, [], `taps < 44 px: ${audit.small}`);
          writeFileSync(OUT + `settings-mail-${lang}-${dev}.png`, await shoot(p, 'unused'));
          await blur(p);
          await key(p, 'Escape', { code: 'Escape' });
          assert.equal(await ev(p, `!document.getElementById('set-main').hidden`), true, 'Esc goes back to the main settings');
          await key(p, 'Escape', { code: 'Escape' });
          assert.equal(await isOpen(p), false, 'a second Esc closes');
          noProblems(p);
        } finally { await p.dispose(); }
      }
    }
  });

  // screenshots (zh / en × desktop / phone) + a layout audit of the open panel
  await C('settings-screens', async () => {
    for (const lang of ['zh', 'en']) {
      for (const [dev, w, h] of [['desktop', 1440, 900], ['phone', 390, 844]]) {
        const p = await paired({ w, h, touch: dev === 'phone', lang, before: (st) => { st.prefs = structuredClone({ ...PREFS, appearance: { language: lang, theme: 'system' } }); st.version = '0.15.0a1'; } });
        try {
          await fake.addTurn({ k: 'phone', dev: 'other', text: lang === 'en' ? 'Make it a table' : '做成表格' }, lang === 'en' ? 'Done. The list is in `restock.md`.' : '好了，清单在 `restock.md` 里。');
          await click(p, '#setBtn');
          await sleep(300);
          const audit = await ev(p, `(() => {
            const box = document.querySelector('#settings .box'), main = document.getElementById('set-main');
            const bad = [...document.querySelectorAll('#settings button:not([hidden]), #settings a')].filter((b) => b.offsetParent && b.getBoundingClientRect().height < 44).map((b) => b.id || b.textContent.trim().slice(0, 20));
            const wide = [...main.querySelectorAll('*')].filter((n) => n.getBoundingClientRect().right > main.getBoundingClientRect().right + 1).map((n) => n.id || n.className || n.tagName);
            const scrolls=[...main.querySelectorAll('*')].filter(n=>n.offsetParent && n.scrollWidth>n.clientWidth+1).map(n=>({id:n.id,cl:n.className,sw:n.scrollWidth,cw:n.clientWidth,space:getComputedStyle(n).whiteSpace}));
            return { scrolls, overflowX: main.scrollWidth > main.clientWidth || document.documentElement.scrollWidth > innerWidth, small: bad, wide: wide.slice(0, 5), boxW: Math.round(box.getBoundingClientRect().width) };
          })()`);
          assert.equal(audit.overflowX, false, JSON.stringify(audit));
          assert.deepEqual(audit.wide, [], JSON.stringify(audit));
          assert.deepEqual(audit.small, [], `taps < 44 px: ${audit.small}`);
          if (dev === 'phone') assert.equal(audit.boxW, w, 'the whole screen on a phone');
          writeFileSync(OUT + `settings-${lang}-${dev}.png`, await shoot(p, 'unused'));
          await ev(p, `document.getElementById('set-main').scrollTop = 1e6`);
          writeFileSync(OUT + `settings-${lang}-${dev}-end.png`, await shoot(p, 'unused'));
          noProblems(p);
        } finally { await p.dispose(); }
      }
    }
  });
} finally {
  rec.write();
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `settings: ${failed} case(s) FAILED` : 'settings PASS: gear, shortcuts, pref_set round trip + older host, font size, update app, A2HS (Android / iPhone / standalone), read-only rows, versions, unpair, zh/en desktop/phone screens');
process.exit(failed ? 1 : 0);
