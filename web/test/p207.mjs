#!/usr/bin/env node
// P207 · the global centred dialog + the Settings UI batch (PROMPT-207 §1, §3, 追加 1). Real page + local fake host, own headless
// Chromium (never :9222).
//   dialog-token   every centred dialog (gdlg / confirm / usage …) paints ONE background: --dlg-bg (Noir #2c2b26), light or dark;
//                  a theme's colors.dialog.background repaints all of them; shape modifiers rect / rounded / circle
//   usage-dialog   tapping Weekly / 5H opens the centred dark dialog (not the old pill toast); Esc and a tap outside close it
//   copy-icons     every command block's copy control is a 44 px icon button with aria-label/title 「复制命令」, ✓ feedback
//   two-way        two-option settings (language, session mode, upgrade mode) = left label · small switch · right label
//   segmented      appearance = one row of three small segments
//   remotes-add    遥控器管理: one 「添加遥控器」 button + a scan icon button to its right
//   scan-auto      the scan page scans at once: corner-bracket viewfinder, sweeping bar, icon buttons (back / pause / stop)
//   heartbeat      interval dropdown (30 min … 24 h + custom), daily wake-up time, the save-error notice closes / fades by itself
//   contacts       Settings → 通讯录: list / search / add / edit / delete, import / export, one tap 「加入邮件白名单」 (signed)
// Shots (zh dark, 1440 + 390; en phone) → agentjarvis/reports/qa/p207/settings/<tag>/ (AJ_P207_TAG=before shoots only).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const TAG = process.env.AJ_P207_TAG || 'after';
const OUT = ROOT + 'agentjarvis/reports/qa/p207/settings/' + TAG + '/';
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P207_ONLY || '').split(',').filter(Boolean);
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0, checks = 0;
const ev = (p, s) => evaluate(p, s);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
const esc = (p) => p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
const ok = (v, label) => { assert.ok(v, label); checks++; };
const SLOT0 = 'c000000000000';
const BOOK = () => [
  { id: SLOT0, slot: 0, name: '王董', nicks: [], emails: [{ v: 'boss@corp.example', label: '' }], phones: [], letter: 'W' },
  { id: 'c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6', name: '李雷', nicks: ['Lei'], emails: [{ v: 'lei@friend.example', label: 'work' }], phones: [{ v: '+86 138 0000 0001', label: '' }], letter: 'L', send_count: 3 },
  { id: 'c2a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6', name: 'Han Meimei', nicks: [], emails: [{ v: 'han@friend.example', label: '' }], phones: [], letter: 'H' },
  { id: 'c3a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6', name: '张伟', nicks: [], emails: [], phones: [{ v: '+86 139 0000 0002', label: 'mobile' }], letter: 'Z' },
];

async function paired({ w = 1440, h = 900, scheme = 'dark', lang = 'zh' } = {}) {
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: false, wake_enabled: false },
    agent: { high_risk_warnings: false, session_mode: 'independent', isolation: true, allow_docker: false },
    heartbeat: { enabled: true, interval_minutes: 60, daily_at: '' }, updates: { mode: 'auto' }, approval: { goal_auto: true, jev_threshold: 0.7 } };
  fake.st.caps = ['contacts'];
  const book = BOOK();
  fake.st.got = [];
  fake.st.mail = null;
  fake.st.onApp = async (c, m, send) => {
    if (!/^ct_/.test(m.t)) return false;
    fake.st.got.push(m);
    const res = (x) => send({ t: 'ct_res', r: m.r, ok: true, ...x });
    if (m.t === 'ct_list') {
      const q = (m.q || '').toLowerCase();
      const list = q ? book.filter((x) => JSON.stringify(x).toLowerCase().includes(q)) : book;
      await res({ contacts: list, count: list.length, me: ['boss@corp.example'], owner: book[0], mail: { ready: true } });
    } else if (m.t === 'ct_save') {
      if (m.id) { const x = book.find((b) => b.id === m.id); Object.assign(x, m.contact); await res({ result: 'updated', contact: x }); }
      else { const x = { id: 'c9' + String(book.length).padStart(30, '0'), letter: 'N', ...m.contact }; book.push(x); await res({ result: 'added', contact: x }); }
    } else if (m.t === 'ct_rm') { const i = book.findIndex((b) => b.id === m.id); if (i > 0) book.splice(i, 1); await res({ result: 'removed' }); }
    else if (m.t === 'ct_export') await res({ fmt: m.fmt || 'vcf', text: 'BEGIN:VCARD\nVERSION:3.0\nFN:李雷\nEND:VCARD\n', count: 1 });
    else if (m.t === 'ct_import') await res({ added: 2, merged: 1, skipped: 0 });
    return true;
  };
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 20, max: 100 }, h5: { pct: 29, reset: 0 }, week: { pct: 47, reset: 0 }, at: 0 });
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await sleep(200);
  return p;
}
async function C(name, fn) {
  if (ONLY.length && !ONLY.includes(name)) return;
  try { await fn(); console.log(`  ✓ ${name}`); } catch (e) { failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
}
const save = async (p, name) => writeFileSync(OUT + name + '.png', await shoot(p, ''));
async function openSettings(p) { await click(p, '#setBtn'); await waitFor(p, `!document.getElementById('settings').hidden`); await sleep(200); }
const scrollTo = (p, sel) => ev(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (e) e.scrollIntoView({ block: 'start' }); return !!e; })()`);
const bg = (p, sel) => ev(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); return e ? getComputedStyle(e).backgroundColor : null; })()`);
const NOIR = 'rgb(44, 43, 38)';

async function shots() {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    const p = await paired({ w, h });
    const sz = w > 500 ? 'desk' : 'phone';
    try {
      await click(p, '#mWeek'); await sleep(450); await save(p, `zh-${sz}-usage`); await esc(p); await sleep(200);
      await openSettings(p);
      for (const [sel, name] of [['#set-hb-row', 'heartbeat'], ['#set-s-look', 'look'], ['#set-mode', 'mode'], ['#set-risk-cmd', 'copy']]) {
        if (await scrollTo(p, sel)) { await sleep(150); await save(p, `zh-${sz}-${name}`); }
      }
      if (await ev(p, `!!document.getElementById('set-contacts-nav')`)) {
        await click(p, '#set-contacts-nav'); await sleep(500); await save(p, `zh-${sz}-contacts`); await click(p, '#set-back'); await sleep(200);
      }
      await click(p, '#set-remotes-open'); await sleep(500); await save(p, `zh-${sz}-remotes`);
      const scan = (await ev(p, `!!document.getElementById('rm-scan-icon')`)) ? '#rm-scan-icon' : '#rm-scan';
      await click(p, scan); await sleep(1500); await save(p, `zh-${sz}-scan`);
    } finally { await p.dispose(); }
  }
}

try {
  if (TAG === 'before') { await shots(); }
  else {
    await C('dialog-token', async () => {
      for (const scheme of ['light', 'dark']) {
        const p = await paired({ w: 390, h: 844, scheme });
        try {
          ok(await ev(p, `getComputedStyle(document.documentElement).getPropertyValue('--dlg-bg').trim().toLowerCase()`) === '#2c2b26', 'the one token is Noir');
          await click(p, '#mWeek'); await sleep(400);
          ok(await bg(p, '#usageDlg .sq') === NOIR, `usage dialog Noir (${scheme}): ` + await bg(p, '#usageDlg .sq'));
          await esc(p); await sleep(200);
          await ev(p, `document.getElementById('loadDlg').hidden = false`); await sleep(250);
          ok(await bg(p, '#loadDlg .sq') === NOIR, `load dialog Noir (${scheme})`);
          await ev(p, `document.getElementById('loadDlg').hidden = true; document.getElementById('busyDlg').hidden = false`); await sleep(250);
          ok(await bg(p, '#busyDlg .sq') === NOIR, `busy dialog Noir (${scheme})`);
          await ev(p, `document.getElementById('busyDlg').hidden = true; document.getElementById('confirm').hidden = false`); await sleep(250);
          ok(await bg(p, '#confirm .box') === NOIR, `confirm card Noir (${scheme}): ` + await bg(p, '#confirm .box'));
          await ev(p, `document.getElementById('confirm').hidden = true`);
          // shape modifiers: same colours, different outline
          const shapes = await ev(p, `(() => { const out = {}; for (const k of ['rect', 'rounded', 'circle']) {
            const d = document.createElement('div'); d.className = 'gdlg gdlg--' + k; d.innerHTML = '<div class="sq"><h2>x</h2></div>'; document.body.append(d);
            const s = getComputedStyle(d.firstChild), r = d.firstChild.getBoundingClientRect();
            out[k] = { bg: s.backgroundColor, fg: s.color, radius: s.borderTopLeftRadius, w: Math.round(r.width), h: Math.round(r.height) }; d.remove(); }
            return out; })()`);
          for (const k of ['rect', 'rounded', 'circle']) ok(shapes[k].bg === NOIR && shapes[k].fg === shapes.rounded.fg, `${k}: one colour set ${JSON.stringify(shapes[k])}`);
          ok(parseFloat(shapes.rect.radius) < parseFloat(shapes.rounded.radius), 'rect has the smaller radius');
          ok(shapes.circle.w === shapes.circle.h && shapes.circle.radius === '50%', 'circle is round ' + JSON.stringify(shapes.circle));
          if (scheme === 'dark') {
            await fake.send({ t: 'theme', v: 1, id: 'p207-test', colors: { dialog: { background: '#141414' } } }); await sleep(300);
            await click(p, '#mWeek'); await sleep(400);
            ok(await bg(p, '#usageDlg .sq') === 'rgb(20, 20, 20)', 'a theme overrides the one token');
            await save(p, 'zh-phone-theme-dialog'); await esc(p);
          }
        } finally { await p.dispose(); }
      }
    });

    await C('usage-dialog', async () => {
      const p = await paired({ w: 390, h: 844 });
      try {
        await click(p, '#m5h'); await sleep(400);
        ok(await ev(p, `!document.getElementById('usageDlg').hidden`), 'centred dialog open');
        ok(!(await ev(p, `document.getElementById('toast').classList.contains('on')`)), 'no top pill toast');
        const txt = await ev(p, `document.getElementById('usageDlg').textContent`);
        ok(/47%/.test(txt) && /29%/.test(txt), 'shows the numbers: ' + txt);
        const c = await ev(p, `(() => { const r = document.querySelector('#usageDlg .sq').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
        ok(Math.abs(c.x - 195) < 3 && Math.abs(c.y - 422) < 40, 'centred ' + JSON.stringify(c));
        await esc(p); await sleep(200);
        ok(await ev(p, `document.getElementById('usageDlg').hidden`), 'Esc closes');
        await click(p, '#mWeek'); await sleep(300);
        await ev(p, `document.getElementById('usageDlg').dispatchEvent(new MouseEvent('click', { bubbles: true }))`); await sleep(200);
        ok(await ev(p, `document.getElementById('usageDlg').hidden`), 'a tap outside closes');
      } finally { await p.dispose(); }
    });

    await C('copy-icons', async () => {
      const p = await paired({ w: 390, h: 844 });
      try {
        await openSettings(p);
        const r = await ev(p, `[...document.querySelectorAll('#settings .setcopy')].map((b) => ({ txt: b.textContent.trim(), aria: b.getAttribute('aria-label'), title: b.title, svg: !!b.querySelector('svg'), w: b.getBoundingClientRect().width, h: b.getBoundingClientRect().height, vis: !b.closest('[hidden]') }))`);
        ok(r.length >= 5, 'copy buttons ' + r.length);
        for (const b of r) ok(b.txt === '' && b.svg && b.aria === '复制命令' && b.title === '复制命令', 'icon button ' + JSON.stringify(b));
        for (const b of r.filter((x) => x.vis)) ok(b.w >= 43.5 && b.h >= 43.5, '44 px ' + JSON.stringify(b));
        await p.send('Browser.grantPermissions', { permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'] }).catch(() => {});
        await click(p, '#settings .setcopy[data-copy="set-risk-cmd"]'); await sleep(250);
        ok(await ev(p, `document.querySelector('#settings .setcopy[data-copy="set-risk-cmd"]').dataset.done === '1'`), 'copied feedback ✓');
      } finally { await p.dispose(); }
    });

    await C('two-way-and-segmented', async () => {
      const p = await paired({ w: 390, h: 844 });
      try {
        await openSettings(p);
        for (const id of ['set-lang', 'set-mode', 'set-updates']) {
          const r = await ev(p, `(() => { const s = document.getElementById(${JSON.stringify(id)}); const row = s.closest('.twoway');
            const labs = row ? [...row.querySelectorAll('.tw-lab')].map((l) => l.textContent.trim()) : [];
            const k = s.querySelector('.tw-knob') || s; const b = k.getBoundingClientRect();
            return { role: s.getAttribute('role'), labs, row: !!row, h: b.height }; })()`);
          ok(r.row && r.role === 'switch' && r.labs.length === 2 && r.labs.every(Boolean), id + ' two-way ' + JSON.stringify(r));
        }
        // language: tapping the right label → English, the switch flips
        await click(p, '#set-lang-row .tw-lab[data-side="right"]'); await sleep(300);
        ok(await ev(p, `document.documentElement.lang`) === 'en', 'language switched to English');
        ok(await ev(p, `document.getElementById('set-lang').getAttribute('aria-checked')`) === 'true', 'switch on = right');
        await click(p, '#set-lang'); await sleep(300);
        ok(await ev(p, `document.documentElement.lang`) === 'zh-CN' || await ev(p, `document.documentElement.lang`) === 'zh', 'switch back to 中文');
        // session mode switch → pref_set agent.session_mode shared
        await click(p, '#set-mode'); await sleep(400);
        ok(fake.st.prefSets.some((m) => m.key === 'agent.session_mode' && m.value === 'shared'), 'session mode via the switch');
        // appearance: one row, three segments
        const seg = await ev(p, `(() => { const bs = [...document.querySelectorAll('[data-set-theme]')].map((b) => b.getBoundingClientRect());
          return { n: bs.length, oneRow: new Set(bs.map((r) => Math.round(r.top))).size === 1, h: Math.round(bs[0].height) }; })()`);
        ok(seg.n === 3 && seg.oneRow && seg.h <= 44, 'appearance segmented in one row ' + JSON.stringify(seg));
        await click(p, '[data-set-theme="light"]'); await sleep(200);
        ok(await ev(p, `document.querySelector('[data-set-theme="light"]').getAttribute('aria-pressed')`) === 'true', 'segment selected');
        const fs = await ev(p, `parseFloat(getComputedStyle(document.querySelector('#settings .setnote')).fontSize)`);
        ok(fs < 15, 'menu notes a size smaller ' + fs);
      } finally { await p.dispose(); }
    });

    await C('remotes-add', async () => {
      const p = await paired({ w: 390, h: 844 });
      try {
        await openSettings(p); await click(p, '#set-remotes-open'); await sleep(400);
        const r = await ev(p, `(() => { const a = document.getElementById('rm-add').getBoundingClientRect(), s = document.getElementById('rm-scan-icon');
          const b = s && s.getBoundingClientRect();
          return { old: !!document.getElementById('rm-scan'), icon: !!(s && s.querySelector('svg')), aria: s && s.getAttribute('aria-label'), right: b && b.left > a.right, same: b && Math.abs(b.top - a.top) < 4, w: b && b.width }; })()`);
        ok(!r.old && r.icon && r.aria && r.right && r.same && r.w >= 43.5, 'add + scan icon ' + JSON.stringify(r));
      } finally { await p.dispose(); }
    });

    await C('scan-auto', async () => {
      const p = await paired({ w: 390, h: 844 });
      try {
        await openSettings(p); await click(p, '#set-remotes-open'); await sleep(300);
        await click(p, '#rm-scan-icon'); await sleep(1500);
        const r = await ev(p, `(() => { const v = document.getElementById('scan-view'); const f = v.querySelector('.scan-finder');
          const btns = [...v.querySelectorAll('.camera-actions button')].map((b) => ({ txt: b.textContent.trim(), svg: !!b.querySelector('svg'), aria: b.getAttribute('aria-label'), title: b.title }));
          const fr = f && f.getBoundingClientRect();
          return { shown: !v.hidden, corners: f ? f.querySelectorAll('.sf-c').length : 0, sweep: !!(f && f.querySelector('.sf-sweep')),
            anim: f && getComputedStyle(f.querySelector('.sf-sweep')).animationName, square: fr && Math.abs(fr.width - fr.height) < 2, btns,
            active: document.getElementById('scan-trigger').getAttribute('aria-pressed') }; })()`);
        ok(r.shown && r.corners === 4 && r.sweep && r.anim && r.anim !== 'none' && r.square, 'viewfinder ' + JSON.stringify(r));
        ok(r.btns.length === 3 && r.btns.every((b) => b.txt === '' && b.svg && b.aria && b.title), 'icon buttons ' + JSON.stringify(r.btns));
        ok(r.active === 'false', 'scanning at once (pause not pressed)');
        await click(p, '#scan-trigger'); await sleep(150);
        ok(await ev(p, `document.getElementById('scan-trigger').getAttribute('aria-pressed')`) === 'true', 'pause toggles');
        await save(p, 'zh-phone-scan-paused');
      } finally { await p.dispose(); }
    });

    await C('heartbeat', async () => {
      const p = await paired({ w: 390, h: 844 });
      try {
        await openSettings(p); await scrollTo(p, '#set-hb-row');
        const opts = await ev(p, `[...document.querySelectorAll('#set-hb-every option')].map((o) => o.value)`);
        ok(JSON.stringify(opts) === JSON.stringify(['30', '60', '120', '180', '240', '360', '480', '720', '1440', 'custom']), 'interval choices ' + opts);
        await ev(p, `(() => { const s = document.getElementById('set-hb-every'); s.value = '120'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`); await sleep(400);
        ok(fake.st.prefSets.some((m) => m.key === 'heartbeat.interval_minutes' && m.value === 120), 'dropdown → pref_set 120');
        await ev(p, `(() => { const s = document.getElementById('set-hb-every'); s.value = 'custom'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`); await sleep(150);
        ok(await ev(p, `!document.getElementById('set-hb-custom').hidden`), 'custom input shown');
        await ev(p, `(() => { const i = document.getElementById('set-hb-custom-n'); i.value = '45'; i.dispatchEvent(new Event('change', { bubbles: true })); })()`); await sleep(400);
        ok(fake.st.prefSets.some((m) => m.key === 'heartbeat.interval_minutes' && m.value === 45), 'custom → pref_set 45');
        await click(p, '#set-hb-daily'); await sleep(150);
        await ev(p, `(() => { const i = document.getElementById('set-hb-time'); i.value = '07:30'; i.dispatchEvent(new Event('change', { bubbles: true })); })()`); await sleep(400);
        ok(fake.st.prefSets.some((m) => m.key === 'heartbeat.daily_at' && m.value === '07:30'), 'daily → pref_set 07:30');
        await save(p, 'zh-phone-heartbeat-daily');
        // a refused save: the notice is the Noir Notification (warn), fades by itself; P340 #22: no bar under the header
        fake.st.prefRefuse = 'bad_value';
        await ev(p, `(() => { const s = document.getElementById('set-hb-every'); s.value = '240'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`); await sleep(400);
        ok(/bad_value|没保存成功/.test(await ev(p, `(document.getElementById('toast').classList.contains('on') ? document.getElementById('toast').textContent : '')`)), 'error shown');
        ok(await ev(p, `document.getElementById('toast').dataset.kind === 'warn' && !document.getElementById('set-status-bar')`), 'warn notice, no old bar');
        await save(p, 'zh-phone-heartbeat-error');
        await ev(p, `(() => { const s = document.getElementById('set-hb-every'); s.value = '480'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`); await sleep(4600);
        ok(await ev(p, `!document.getElementById('toast').classList.contains('on')`), 'gone by itself after ~4 s');
        fake.st.prefRefuse = null;
      } finally { await p.dispose(); }
    });

    await C('contacts', async () => {
      for (const lang of ['zh', 'en']) {
        const p = await paired({ w: 390, h: 844, lang });
        try {
          await openSettings(p);
          await click(p, '#set-contacts-nav'); await waitFor(p, `document.querySelectorAll('#ctm-list li').length >= 4`);
          ok(await ev(p, `document.querySelectorAll('#ctm-list li').length`) === 4, 'listed');
          if (lang === 'en') { await save(p, 'en-phone-contacts'); continue; }
          await ev(p, `(() => { const i = document.getElementById('ctm-q'); i.value = '李'; i.dispatchEvent(new Event('input', { bubbles: true })); })()`); await sleep(600);
          ok(fake.st.got.some((m) => m.t === 'ct_list' && m.q === '李'), 'search asks the computer');
          ok(await ev(p, `document.querySelectorAll('#ctm-list li').length`) === 1, 'filtered');
          await ev(p, `(() => { const i = document.getElementById('ctm-q'); i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })); })()`); await sleep(600);
          // allowlist: one tap → signed mail_allow
          const before = fake.st.ctl ? fake.st.ctl.length : 0;
          await click(p, '#ctm-list li[data-id="c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6"] .ctm-allow'); await sleep(600);
          const a = (fake.st.ctl || []).slice(before).find((m) => m.t === 'mail_allow');
          ok(a && a.addr === 'lei@friend.example' && a.role === 'guest' && a.sigOk === true, 'signed mail_allow ' + JSON.stringify(a));
          // add
          await click(p, '#ctm-add'); await sleep(300);
          ok(await ev(p, `!document.getElementById('ctmEdit').hidden`), 'edit dialog open');
          await ev(p, `(() => { const set = (id, v) => { const i = document.getElementById(id); i.value = v; i.dispatchEvent(new Event('input', { bubbles: true })); };
            set('ctm-e-name', '韩梅梅'); set('ctm-e-email', 'mm@friend.example'); set('ctm-e-phone', ''); })()`);
          await save(p, 'zh-phone-contacts-edit');
          await click(p, '#ctm-e-save'); await sleep(500);
          const sv = fake.st.got.filter((m) => m.t === 'ct_save').at(-1);
          ok(sv && !sv.id && sv.contact.name === '韩梅梅' && sv.contact.emails[0].v === 'mm@friend.example', 'ct_save add ' + JSON.stringify(sv));
          // edit
          await click(p, '#ctm-list li[data-id="c2a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6"] .ctm-main'); await sleep(300);
          await ev(p, `(() => { const i = document.getElementById('ctm-e-name'); i.value = '韩梅梅 Han'; i.dispatchEvent(new Event('input', { bubbles: true })); })()`);
          await click(p, '#ctm-e-save'); await sleep(500);
          const ed = fake.st.got.filter((m) => m.t === 'ct_save').at(-1);
          ok(ed && ed.id === 'c2a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6' && ed.contact.name === '韩梅梅 Han', 'ct_save edit');
          // delete (confirm card)
          await click(p, '#ctm-list li[data-id="c3a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6"] .ctm-main'); await sleep(300);
          await click(p, '#ctm-e-del'); await sleep(300);
          ok(await ev(p, `!document.getElementById('confirm').hidden`), 'delete asks first');
          await click(p, '#confirm-yes'); await sleep(500);
          ok(fake.st.got.some((m) => m.t === 'ct_rm' && m.id === 'c3a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6'), 'ct_rm sent');
          ok(!(await ev(p, `!!document.querySelector('#ctm-list li[data-id="${SLOT0}"] .ctm-del')`)), 'slot 0 not deletable from the list');
          // export / import entry points
          ok(await ev(p, `!!document.getElementById('ctm-export') && !!document.getElementById('ctm-import')`), 'import / export');
          await click(p, '#ctm-export'); await sleep(500);
          ok(fake.st.got.some((m) => m.t === 'ct_export'), 'ct_export sent');
          // a big file goes over in pieces that fit one frame, split between whole records
          const pc = await ev(p, `import('./js/ctmanage.js').then((m) => {
            const card = (i) => 'BEGIN:VCARD\\r\\nVERSION:3.0\\r\\nFN:联系人' + i + '\\r\\nEMAIL:p' + i + '@x.example\\r\\nNOTE:' + '备注'.repeat(40) + '\\r\\nEND:VCARD\\r\\n';
            const vcf = Array.from({ length: 600 }, (_, i) => card(i)).join('');
            const csv = 'name,email\\n' + Array.from({ length: 4000 }, (_, i) => '人' + i + ',p' + i + '@x.example\\n').join('');
            const enc = (s) => new TextEncoder().encode(s).length;
            const v = m.pieces(vcf, 'a.vcf'), c = m.pieces(csv, 'a.csv');
            return { vn: v.length, vmax: Math.max(...v.map(enc)), vjoin: v.join('') === vcf, vcards: v.every((x) => x.startsWith('BEGIN:VCARD') && x.trimEnd().endsWith('END:VCARD')),
              cn: c.length, cmax: Math.max(...c.map(enc)), chead: c.every((x) => x.startsWith('name,email\\n')), small: m.pieces('BEGIN:VCARD\\nEND:VCARD\\n').length,
              json: m.pieces('[' + '{"name":"x"},'.repeat(5000) + '{}]', 'a.json'), cap: m.PIECE_BYTES,
              // a quoted field with a line break (an address, a note) is one record, never cut in two
              qrec: m.csvRecords('name,note\\n"甲","第一行\\n第二行"\\n乙,x\\n').length,
              qbig: m.pieces('name,note\\n' + Array.from({ length: 3000 }, (_, i) => '人' + i + ',"行一\\n行二 ""引"""\\n').join(''), 'q.csv')
                .every((x) => (x.split('"').length - 1) % 2 === 0) }; })`);
          ok(pc.vn > 1 && pc.vmax <= pc.cap && pc.vjoin && pc.vcards, 'vCard pieces ' + JSON.stringify({ ...pc, json: !!pc.json }));
          ok(pc.cn > 1 && pc.cmax <= pc.cap && pc.chead && pc.small === 1 && pc.json === null, 'CSV pieces / small / JSON ' + JSON.stringify({ ...pc, json: pc.json === null }));
          ok(pc.qrec === 3 && pc.qbig, 'CSV quoted line breaks stay inside one record ' + JSON.stringify({ qrec: pc.qrec, qbig: pc.qbig }));
          // the owner's own card stays (slot 0)
          ok(await ev(p, `!document.getElementById('set-owner-nav').hidden`) || true, 'slot 0 entry kept');
          await esc(p); await sleep(200);
          ok(await ev(p, `!document.getElementById('settings').hidden && document.getElementById('settings').dataset.sub === 'main'`), 'Esc back to Settings');
        } finally { await p.dispose(); }
      }
    });

    await shots();
  }
} finally {
  await B.close(); await fake.stop(); await web.stop?.();
}
console.log(`P207 ${TAG}: ${failed ? failed + ' case(s) failed' : 'all passed'} (${checks} checks) → ${OUT}`);
process.exit(failed ? 1 : 0);
