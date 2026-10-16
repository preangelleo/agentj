#!/usr/bin/env node
// P171 · Settings and the ≡ menu merged into ONE full-screen page; 遥控器管理 is its own second-level page (PROTOCOL §24).
// Real page + local fake host (signatures checked like the real host), independent headless Chromium (never :9222).
//   a) the ≡ menu is gone; every one of its rows (and the old Settings rows) lives in the page — parity list below
//   b) full screen at 1440 × 900 and 390 × 844: side navigation + two columns of cards on wide, one column on narrow,
//      no horizontal overflow, every tap ≥ 44 px, buttons side by side ≥ 12 px apart (global --space-control)
//   c) Esc: sub-page → Settings → closed; the gear on other paired views too; a row that opens a view closes Settings
//   d) 遥控器管理: 已用 N / 10, this device marked, rename / unbind (danger confirm) signed; add → QR + link, the new
//      device's code typed here: wrong code ends it, the right code adds it; an older host → read-only + commands
// Shots (zh/en × light/dark × 1440/390) → reports/qa/p171/screens/*.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p171/screens/';
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P171_ONLY || '').split(',').filter(Boolean);
const rec = parityRecorder('web/test/p171.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
const isOpen = (p) => ev(p, `!document.getElementById('settings').hidden`);

// A real pairing QR from the host's own code (segno), so the screenshot shows what a customer sees.
const LINK = 'https://m.agentj.app/#p=' + '2'.repeat(240);
let QR = '';
try {
  const py = [ROOT + '../host/.venv/bin/python', ROOT + 'host/.venv/bin/python', process.env.AJ_PYTHON || ''];
  for (const exe of py) {
    try {
      QR = execFileSync(exe, ['-c', `import sys; sys.path.insert(0, ${JSON.stringify(ROOT + 'agentjarvis/host')}); from agentj import wire; print(wire.pairing_qr(sys.argv[1]).png_data_uri(scale=6, border=2, dark="#141414", light="#faf9f5"))`, LINK], { encoding: 'utf8' }).trim();
      if (QR) break;
    } catch { /* next interpreter */ }
  }
} catch { /* no QR: the page still works, the shot just shows an empty frame */ }
const NOW = Math.floor(Date.now() / 1000);
const DEVICES = () => [
  { id: 'ME', name: 'iPhone Air', paired_at: NOW - 86400 * 12, seen: NOW, online: true, source: 'local', me: true },
  { id: 'Kx3pQ9wLm2Vb7Tz1', name: 'MacBook · Chrome', paired_at: NOW - 86400 * 30, seen: NOW - 3600, online: true, source: 'account' },
  { id: 'Rf8sN2cHq5Yd4Jk6', name: 'Pixel 9', paired_at: NOW - 86400 * 5, seen: NOW - 86400 * 2, online: false, source: 'local' },
  { id: 'Ab4Tq7Wz9Lm3Xc8P', name: '华为 MatePad', paired_at: NOW - 86400, seen: NOW - 7200, online: false, source: 'phone' },
];
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: true, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'shared' } };

async function paired({ w = 1440, h = 900, scheme = 'light', lang = 'zh', remotes = true, silent = false } = {}) {
  fake.reset();
  fake.st.prefs = structuredClone(PREFS);
  fake.st.prefs.appearance.language = lang;   // A1: the computer's language wins over ?lang=
  if (remotes) fake.remotes({ limit: 10, items: DEVICES(), link: LINK, qr: QR, code: '482913', silent });
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
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
async function openSettings(p) { await click(p, '#setBtn'); await waitFor(p, `!document.getElementById('settings').hidden`); await sleep(150); }
/** Layout audit of what is visible in the page: overflow, taps < 44 px, neighbouring buttons closer than 12 px. */
const AUDIT = `(() => {
  const box = document.querySelector('#settings .setbox');
  const vis = (e) => e.getClientRects().length && !e.closest('[hidden]') && getComputedStyle(e).visibility !== 'hidden';
  const small = [], tight = [];
  for (const e of box.querySelectorAll('button, a[href], input, [role=switch]')) {
    if (!vis(e) || e.closest('.setlinks') && e.tagName === 'A' && e.getBoundingClientRect().height >= 44) continue;
    const r = e.getBoundingClientRect();
    if (e.type !== 'range' && (r.height < 43.5 || r.width < 43.5)) small.push((e.id || e.className || e.tagName) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
  }
  for (const row of box.querySelectorAll('.setacts')) {
    const bs = [...row.children].filter((b) => b.matches('button, .aj-btn') && vis(b)).map((b) => b.getBoundingClientRect());
    for (let i = 1; i < bs.length; i++) {
      const a = bs[i - 1], b = bs[i];
      const sameLine = Math.abs(a.top - b.top) < 4;
      const gap = sameLine ? b.left - a.right : b.top - a.bottom;
      if (gap < 11.5) tight.push(Math.round(gap));
    }
  }
  const r = box.getBoundingClientRect();
  return { w: Math.round(r.width), h: Math.round(r.height), vw: innerWidth, vh: innerHeight,
    overflowX: document.documentElement.scrollWidth > innerWidth || [...box.querySelectorAll('.setmain,.setsub')].some((m) => vis(m) && m.scrollWidth > m.clientWidth + 1),
    small, tight };
})()`;

// Every row the ≡ menu had (id or selector) and every Settings row — all must be inside #settings now.
const PARITY = ['#brand-name', '#open-bots', '#open-friends', '#open-models', '#setup-faceid', '#open-setup', '#open-mem', '#open-act', '#open-tasks',
  '#set-push', '[data-set-lang="zh"]', '[data-set-lang="en"]', '[data-set-theme="system"]', '#menu-about', 'a[href$="/account/#invite"]',
  'a[href$="/docs/phone/"]', 'a[href$="/security/"]', 'a[href$="/privacy/"]', 'a[href$="/terms/"]', '#scan-repair', '#unpair',
  '[data-i18n="menu.foot"]', '#set-font-plus', '#set-sfx', '#set-speak', '#set-wake', '#set-shot', '#set-risk', '#set-mode', '#set-iso',
  '#set-docker', '#set-updates', '#set-refresh', '#set-a2hs', '#keysBtn', '#set-account', 'a[href^="mailto:"]', '#set-remotes-open'];

try {
  await C('menu-merged', async () => {
    const p = await paired();
    try {
      assert.equal(await ev(p, `!!document.getElementById('aj-menu') || !!document.querySelector('.ajmenu, details summary.ajmenu__btn')`), false, 'the ≡ menu is gone');
      const missing = await ev(p, `${JSON.stringify(PARITY)}.filter((s) => !document.querySelector('#settings ' + s))`);
      assert.deepEqual(missing, [], 'rows not in Settings: ' + missing);
      // nothing duplicated: one language switch, one theme switch, one unpair, one push control on the whole page
      assert.equal(await ev(p, `document.querySelectorAll('[data-aj-theme-switch]').length + document.querySelectorAll('#settings [data-set-theme="dark"]').length`), 1);
      assert.equal(await ev(p, `document.querySelectorAll('#unpair, #set-unpair').length`), 1);
      assert.equal(await ev(p, `document.querySelectorAll('#push-on, #push-row').length`), 0);
      // the header's top-right holds the gear only (plus the 网页版 chip)
      assert.equal(await ev(p, `[...document.querySelector('header.top').children].filter((e) => e.matches('details, summary')).length`), 0);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('fullscreen-layout', async () => {
    for (const [w, h] of [[1440, 900], [390, 844]]) {
      const p = await paired({ w, h });
      try {
        await openSettings(p);
        const a = await ev(p, AUDIT);
        assert.equal(a.w, w, `full width at ${w}: ${JSON.stringify(a)}`);
        assert.ok(Math.abs(a.h - h) <= 1, `full height at ${w}: ${JSON.stringify(a)}`);
        assert.equal(a.overflowX, false, JSON.stringify(a));
        assert.deepEqual(a.small, [], `taps < 44 px at ${w}: ${a.small}`);
        assert.deepEqual(a.tight, [], `buttons closer than 12 px at ${w}`);
        const lay = await ev(p, `(() => {
          const side = document.querySelector('.setside'); const cards = [...document.querySelectorAll('#set-main .setcard')].filter((c) => !c.hidden);
          return { side: side.getClientRects().length > 0, cols: new Set(cards.map((c) => Math.round(c.getBoundingClientRect().left))).size, n: cards.length };
        })()`);
        if (w >= 900) { assert.equal(lay.side, true, 'side navigation on wide'); assert.equal(lay.cols, 2, 'two columns of cards on wide'); }
        else { assert.equal(lay.side, false, 'no side navigation on a phone'); assert.equal(lay.cols, 1, 'one column on a phone'); }
        assert.equal(lay.n, 10, 'ten sections when paired (P165: + P170 沟通渠道, P332: + 紧急修复)');
        if (w >= 900) {          // the side navigation jumps and marks the section
          await click(p, '[data-set-jump="set-s-help"]');
          await sleep(700);
          assert.equal(await ev(p, `document.querySelector('[data-set-jump="set-s-help"]').getAttribute('aria-current')`), 'true');
        }
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });

  await C('esc-and-views', async () => {
    const p = await paired();
    try {
      await openSettings(p);
      await click(p, '#set-remotes-open');
      await waitFor(p, `!document.getElementById('set-remotes').hidden`);
      await key(p, 'Escape');
      assert.equal(await ev(p, `document.getElementById('set-remotes').hidden && !document.getElementById('set-main').hidden`), true, 'Esc: sub-page → Settings');
      assert.equal(await isOpen(p), true);
      await key(p, 'Escape');
      assert.equal(await isOpen(p), false, 'Esc again closes');
      assert.equal(await ev(p, `document.activeElement === document.getElementById('setBtn')`), true, 'focus back on the gear');
      // a row that opens a view closes Settings; the gear is there on that view too and opens Settings again
      await openSettings(p);
      await click(p, '#open-mem');
      await waitFor(p, `document.body.dataset.view === 'mem'`);
      assert.equal(await isOpen(p), false);
      assert.equal(await ev(p, `document.getElementById('setBtn').getClientRects().length > 0`), true, 'gear on the memory page');
      await openSettings(p);
      assert.equal(await ev(p, `document.getElementById('keysBtn').hidden`), true, 'keyboard shortcuts only from the chat');
      await key(p, 'Escape');
      // the security panel opens above Settings and closing it returns there
      await click(p, '#setBtn');
      await click(p, '#menu-about');
      assert.equal(await ev(p, `(() => { const b = document.getElementById('badge-panel'); const r = b.querySelector('.box').getBoundingClientRect(); return !b.hidden && document.elementFromPoint(r.left + r.width / 2, r.top + 20).closest('#badge-panel') !== null; })()`), true, 'badge above Settings');
      await key(p, 'Escape');
      assert.equal(await ev(p, `document.getElementById('badge-panel').hidden`), true);
      assert.equal(await isOpen(p), true, 'still in Settings');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('remotes-manage', async () => {
    const p = await paired({ w: 390, h: 844 });
    try {
      await openSettings(p);
      await waitFor(p, `document.getElementById('set-remotes-count').textContent.startsWith('已用 4 / 10')`);
      await click(p, '#set-remotes-open');
      await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 4`);
      assert.equal(await ev(p, `document.getElementById('rm-used').textContent + '/' + document.getElementById('rm-limit').textContent`), '4/10');
      assert.equal(await ev(p, `document.getElementById('set-title').textContent`), '遥控器管理');
      // this device first, marked
      assert.equal(await ev(p, `document.querySelector('#rm-list .rmitem').dataset.me === '1' && !!document.querySelector('#rm-list .rmitem .rmtag')`), true);
      // rename (signed)
      await click(p, '#rm-list .rmitem[data-id="Rf8sN2cHq5Yd4Jk6"] [data-act="rename"]');
      await waitFor(p, `!!document.querySelector('.rmrename input')`);
      await ev(p, `(() => { const i = document.querySelector('.rmrename input'); i.value = '  Pixel 9 Pro  '; document.querySelector('.rmrename').requestSubmit(); })()`);
      await waitFor(p, `[...document.querySelectorAll('.rmname__t')].some((n) => n.textContent === 'Pixel 9 Pro')`);
      let w = fake.st.remotes.writes.at(-1);
      assert.equal(w.t, 'dev_name'); assert.equal(w.sigOk, true); assert.equal(w.name, 'Pixel 9 Pro');
      // unbind another remote: a destructive confirm, then signed
      await click(p, '#rm-list .rmitem[data-id="Ab4Tq7Wz9Lm3Xc8P"] [data-act="unbind"]');
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      assert.equal(await ev(p, `document.getElementById('confirm-yes').classList.contains('aj-btn--danger')`), true, 'unbind reads as destructive');
      assert.match(await ev(p, `document.getElementById('confirm-title').textContent`), /华为 MatePad/);
      await click(p, '#confirm-no');
      assert.equal(fake.st.remotes.items.length, 4, 'cancel = nothing sent');
      await click(p, '#rm-list .rmitem[data-id="Ab4Tq7Wz9Lm3Xc8P"] [data-act="unbind"]');
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      await click(p, '#confirm-yes');
      await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 3`);
      w = fake.st.remotes.writes.at(-1);
      assert.equal(w.t, 'dev_unbind'); assert.equal(w.sigOk, true); assert.equal(w.device, 'Ab4Tq7Wz9Lm3Xc8P');
      assert.equal(await ev(p, `document.getElementById('rm-used').textContent`), '3');
      // add: QR + link; the new device's code — wrong first (pairing ends), then a fresh code and the right one
      await click(p, '#rm-add');
      await waitFor(p, `!document.getElementById('rm-addbox').hidden && document.getElementById('rm-qr').getAttribute('src') !== null`);
      assert.equal(fake.st.remotes.writes.at(-1).t, 'dev_add'); assert.equal(fake.st.remotes.writes.at(-1).sigOk, true);
      assert.match(await ev(p, `document.getElementById('rm-expire').textContent`), /[45]:[0-5][0-9]/);
      if (QR) assert.equal(await ev(p, `document.getElementById('rm-qr').naturalWidth > 100`), true, 'a real QR image');
      await fake.remotePending('NEWDEVICEaaaaaaa', 'Galaxy S26');
      await waitFor(p, `!document.getElementById('rm-pending').hidden`);
      assert.match(await ev(p, `document.getElementById('rm-pend-name').textContent`), /Galaxy S26/);
      assert.equal(await ev(p, `document.getElementById('rm-approve').disabled`), true, 'Approve waits for 6 digits');
      await ev(p, `(() => { const i = document.getElementById('rm-code'); i.value = '12a3456'; i.dispatchEvent(new Event('input')); })()`);
      assert.equal(await ev(p, `document.getElementById('rm-code').value`), '123456', 'digits only');
      await click(p, '#rm-approve');
      await waitFor(p, `!document.getElementById('rm-again').hidden`);
      assert.match(await ev(p, `document.getElementById('rm-expire').textContent`), /安全码不对/);
      assert.equal(fake.st.remotes.writes.at(-1).t, 'dev_confirm');
      await click(p, '#rm-again');
      await waitFor(p, `document.getElementById('rm-again').hidden && document.getElementById('rm-pending').hidden && !document.getElementById('rm-addbox').hidden`);
      await fake.remotePending('NEWDEVICEaaaaaaa', 'Galaxy S26');
      await waitFor(p, `!document.getElementById('rm-pending').hidden`);
      await ev(p, `(() => { const i = document.getElementById('rm-code'); i.value = '482913'; i.dispatchEvent(new Event('input')); })()`);
      await click(p, '#rm-approve');
      await waitFor(p, `document.getElementById('rm-addbox').hidden && document.querySelectorAll('#rm-list .rmitem').length === 4`);
      const last = fake.st.remotes.writes.at(-1);
      assert.equal(last.t, 'dev_confirm'); assert.equal(last.sigOk, true); assert.equal(last.code, '482913');
      // add, then cancel → dev_cancel
      await click(p, '#rm-add');
      await waitFor(p, `!document.getElementById('rm-addbox').hidden`);
      await click(p, '#rm-cancel');
      await waitFor(p, `document.getElementById('rm-addbox').hidden`);
      await waitFor(p, `true`);
      await sleep(200);
      assert.equal(fake.st.remotes.cancels, 1, 'cancel tells the computer');
      const a = await ev(p, AUDIT);
      assert.deepEqual(a.small, [], 'taps: ' + a.small); assert.deepEqual(a.tight, []); assert.equal(a.overflowX, false);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('remotes-refusals', async () => {
    const p = await paired();
    try {
      await openSettings(p);
      await click(p, '#set-remotes-open');
      await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 4`);
      fake.st.remotes.why = 'disabled';
      await click(p, '#rm-add');
      await waitFor(p, `document.getElementById('toast').classList.contains('on')`);
      assert.match(await ev(p, `document.getElementById('toast').textContent`), /agentj remote-pair on/);
      assert.equal(await ev(p, `document.getElementById('rm-addbox').hidden && !document.getElementById('rm-add').disabled`), true);
      // unbinding THIS device = a signed unbind of itself, then the page forgets the computer (pairing screen)
      await click(p, '#rm-list .rmitem[data-me="1"] [data-act="unbind"]');
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      assert.match(await ev(p, `document.getElementById('confirm-title').textContent`), /这台设备/);
      await click(p, '#confirm-yes');
      await waitFor(p, `window.__ajState === 'idle' || document.body.dataset.view === 'pair'`);
      assert.equal(await isOpen(p), false);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('remotes-older-host', async () => {
    const p = await paired({ silent: true });
    try {
      await openSettings(p);
      await click(p, '#set-remotes-open');
      await waitFor(p, `!document.getElementById('rm-legacy').hidden`, 9000);
      assert.equal(await ev(p, `document.getElementById('rm-add').hidden && !document.querySelector('#rm-list [data-act]')`), true, 'read-only');
      assert.match(await ev(p, `document.getElementById('rm-legacy').textContent`), /agentj revoke/);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('screens', async () => {
    for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[1440, 900], [390, 844]]) {
      const tag = `${lang}-${scheme}-${w}`;
      const p = await paired({ w, h, scheme, lang });
      try {
        await openSettings(p);
        writeFileSync(OUT + `settings-${tag}.png`, await shoot(p));
        if (w < 900) {
          await ev(p, `document.getElementById('set-main').scrollTop = document.getElementById('set-s-read').offsetTop - 16`);
          writeFileSync(OUT + `settings-${tag}-2.png`, await shoot(p));
          await ev(p, `document.getElementById('set-main').scrollTop = 1e6`);
          writeFileSync(OUT + `settings-${tag}-end.png`, await shoot(p));
        } else {
          await click(p, '[data-set-jump="set-s-safety"]'); await sleep(700);
          writeFileSync(OUT + `settings-${tag}-safety.png`, await shoot(p));
        }
        await ev(p, `document.getElementById('set-main').scrollTop = 0`);
        await click(p, '#set-remotes-open');
        await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 4`);
        writeFileSync(OUT + `remotes-${tag}.png`, await shoot(p));
        await click(p, '#rm-add');
        await waitFor(p, `!document.getElementById('rm-addbox').hidden`);
        await sleep(300);
        writeFileSync(OUT + `remotes-${tag}-add.png`, await shoot(p));
        await fake.remotePending('NEWDEVICEaaaaaaa', lang === 'en' ? 'Galaxy S26' : '小米 15', { evict: undefined });
        await waitFor(p, `!document.getElementById('rm-pending').hidden`);
        await ev(p, `(() => { const i = document.getElementById('rm-code'); i.value = '4829'; i.dispatchEvent(new Event('input')); })()`);
        await ev(p, `document.getElementById('rm-pending').scrollIntoView({ block: 'center' })`);
        writeFileSync(OUT + `remotes-${tag}-code.png`, await shoot(p));
        await click(p, '#rm-cancel');
        await click(p, '#rm-list .rmitem[data-id="Rf8sN2cHq5Yd4Jk6"] [data-act="rename"]');
        await waitFor(p, `!!document.querySelector('.rmrename input')`);
        writeFileSync(OUT + `remotes-${tag}-rename.png`, await shoot(p));
        const a = await ev(p, AUDIT);
        assert.deepEqual(a.small, [], tag + ' taps: ' + a.small); assert.deepEqual(a.tight, [], tag + ' gaps'); assert.equal(a.overflowX, false, tag);
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });
} finally {
  rec.write();
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p171: ${failed} case(s) FAILED` : 'p171 PASS: ≡ merged into one full-screen Settings, layout 1440/390, Esc, 遥控器管理 (rename / unbind / add with code / refusals / older host), shots');
process.exit(failed ? 1 : 0);
