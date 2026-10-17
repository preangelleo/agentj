// P365 `agentj notify` on the phone, in Chromium against fakehost.mjs: (a) an automation's page (src k "auto") — the quiet
// system card 「来自你的自动化：name」, the Markdown title + text, its image as an ordinary §13 file card; the main Agent's
// follow-up (agent via "auto") 「关于自动化：name」; (b) Settings → 自动化消息 — hidden on an older host, one script row on and
// one task row off from notify_list, switch → notify_set, 移除 → confirm → allow:null → empty state, the master switch =
// pref_set notify.enabled. 390 × 844 and 1440 × 900, zh / en, light / dark → reports/qa/p365/.
// `P365_SHOTS=<dir>` also copies the two review shots (zh, 390, light) there.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync, copyFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate as ev, shoot, sleep, key } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p365/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const IMG = readFileSync(new URL('../public/brand/img/icon-512.png', import.meta.url));
const MID = 'Q'.repeat(22);
const ITEM = { mid: MID, name: 'login-qr.png', mime: 'image/png', kind: 'image', bytes: IMG.length, sha256: createHash('sha256').update(IMG).digest('hex'), ref: '' };
const TEXT = { zh: '**视频号登录已掉线**\n\n请在 10 分钟内扫码', en: '**Channels login dropped**\n\nScan the code within 10 minutes' };
const LABEL = { zh: '来自你的自动化：wechat-login-push', en: 'From your automation: wechat-login-push' };
const ABOUT = { zh: '关于自动化：wechat-login-push', en: 'About your automation: wechat-login-push' };
const NOTIFY = () => ({ automations: [{ id: 'wechat-login-push', allow: true }, { id: 'task.daily-report', allow: false }],
  tasks: [{ id: 'daily-report', title: { zh: '每日报告', en: 'Daily report' }, declared: true }] });

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
let failed = 0;
const shots = [];
const save = async (p, name) => { const f = OUT + name + '.png'; writeFileSync(f, await shoot(p, '')); shots.push(f); return f; };

function serveMedia(st) {
  st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    if (m.mid !== MID) { await send({ t: 'media_err', mid: m.mid, why: 'gone' }); return true; }
    for (let o = m.o; o < IMG.length; o += 48 * 1024) {
      const d = IMG.subarray(o, Math.min(IMG.length, o + 48 * 1024));
      await send({ t: 'media_chunk', mid: m.mid, o, d: Buffer.from(d).toString('base64url'), last: o + d.length >= IMG.length });
    }
    return true;
  };
}
async function paired(w, h, lang, scheme, { notify = true } = {}) {
  fake.reset();
  serveMedia(fake.st);
  fake.st.prefs = { appearance: { language: lang, theme: scheme }, notify: { enabled: true } };
  if (notify) fake.st.notify = NOTIFY();
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `document.documentElement.setAttribute('data-theme', ${JSON.stringify(scheme)})`);
  await ev(p, `document.getElementById('a2hs-ok') && document.getElementById('a2hs-ok').click()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await fake.send(fake.prefsMsg());
  return p;
}
async function C(name, fn) {
  try { await fn(); console.log(`  ✓ ${name}`); } catch (e) { failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
}
const clean = (p) => { assert.deepEqual(p.problems, [], 'console / page errors'); assert.deepEqual(p.offsite, [], 'off-origin requests'); };
const SIZES = [[390, 844], [1440, 900]];

try {
  for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of SIZES) {
    const tag = `${lang}-${w}-${scheme}`;
    await C(`chat page from an automation ${tag}`, async () => {
      const p = await paired(w, h, lang, scheme);
      try {
        await fake.addTurn({ k: 'phone', dev: null, text: lang === 'zh' ? '早上好' : 'Morning' }, lang === 'zh' ? '早上好，董事长。' : 'Good morning.', 'done');
        const turn = await fake.addTurn({ k: 'auto', name: 'wechat-login-push', text: '' }, TEXT[lang], 'done');
        await fake.updateTurn(turn.id, { media: [ITEM] });
        await waitFor(p, `document.getElementById('om').dataset.src === 'sys' && document.getElementById('omLabel').textContent.includes(${JSON.stringify(LABEL[lang])})`, 10000);
        await waitFor(p, `!!document.querySelector('[data-mid="${MID}"]')`, 10000);   // an unreferenced file = an ordinary §13 card
        await ev(p, `document.querySelector('[data-mid="${MID}"]').scrollIntoView({ block: 'end' })`);
        await waitFor(p, `!!document.querySelector('[data-mid="${MID}"][data-state=ready] img')`, 15000);
        await ev(p, `(() => { for (let n = document.getElementById('words'); n; n = n.parentElement) n.scrollTop = 0; })()`);   // back to the title for the shot
        await sleep(200);
        assert.match(await ev(p, `document.querySelector('#words strong, #words b')?.textContent || ''`), lang === 'zh' ? /视频号登录已掉线/ : /Channels login dropped/, 'the **title** is bold');
        assert.match(await ev(p, `document.getElementById('words').textContent`), lang === 'zh' ? /请在 10 分钟内扫码/ : /within 10 minutes/);
        await save(p, `chat-auto-${tag}`);
        // the main Agent's own follow-up about it
        await fake.addTurn({ k: 'agent', via: 'auto', name: 'wechat-login-push', text: '' }, lang === 'zh' ? '我已经提醒你扫码了。' : 'I have reminded you to scan.', 'done');
        await waitFor(p, `document.getElementById('om').dataset.src === 'agent' && document.getElementById('omLabel').textContent.includes(${JSON.stringify(ABOUT[lang])})`, 10000);
        if (w === 390) await save(p, `chat-agent-about-${tag}`);
        clean(p);
      } finally { await p.dispose(); }
    });
    await C(`Settings → automation messages ${tag}`, async () => {
      const p = await paired(w, h, lang, scheme);
      try {
        await click(p, '#setBtn');
        await waitFor(p, `!document.getElementById('set-notify-nav').hidden`);
        await click(p, '#set-notify-nav');
        await waitFor(p, `!document.getElementById('set-notify').hidden && document.querySelectorAll('#ntf-autos .ntfrow').length === 1 && document.querySelectorAll('#ntf-tasks .ntfrow').length === 1`);
        assert.equal(await ev(p, `document.getElementById('set-title').textContent`), lang === 'zh' ? '自动化消息' : 'Automation messages');
        assert.equal(await ev(p, `document.querySelector('#ntf-autos .ntfrow .tgl').getAttribute('aria-checked')`), 'true');
        assert.equal(await ev(p, `document.querySelector('#ntf-tasks .ntfrow .tgl').getAttribute('aria-checked')`), 'false');
        assert.equal(await ev(p, `document.querySelector('#ntf-tasks .ntfname').textContent`), lang === 'zh' ? '每日报告' : 'Daily report');
        assert.ok(await ev(p, `!!document.querySelector('#ntf-tasks .ntfnote')`), 'the override of the task declaration is noted');
        assert.equal(await ev(p, `document.getElementById('ntf-on').getAttribute('aria-checked')`), 'true');
        // every target ≥ 44 px, nothing wider than the screen
        const bad = await ev(p, `JSON.stringify([...document.querySelectorAll('#set-notify button')].filter(b => b.offsetParent).map(b => { const r = b.getBoundingClientRect(); return [b.id || b.className, Math.round(r.width), Math.round(r.height)]; }).filter(([, bw, bh]) => bh < 44 && bw < 44))`);
        assert.equal(bad, '[]', 'tap targets');
        assert.ok(await ev(p, `document.getElementById('set-notify').scrollWidth <= document.getElementById('set-notify').clientWidth + 1`), 'no sideways overflow');
        await save(p, `settings-notify-${tag}`);
        if (w !== 390 || scheme !== 'light') { clean(p); return; }
        // a script switch → notify_set {id, allow:false}; the answer repaints
        await click(p, '#ntf-autos .ntfrow .tgl');
        await waitFor(p, `document.querySelector('#ntf-autos .ntfrow .tgl').getAttribute('aria-checked') === 'false'`);
        assert.deepEqual(fake.st.notify.sets.at(-1), { id: 'wechat-login-push', allow: false });
        // a task switch → id task.<id>
        await click(p, '#ntf-tasks .ntfrow .tgl');
        await waitFor(p, `document.querySelector('#ntf-tasks .ntfrow .tgl').getAttribute('aria-checked') === 'true' && !document.querySelector('#ntf-tasks .ntfnote')`);
        assert.deepEqual(fake.st.notify.sets.at(-1), { id: 'task.daily-report', allow: true });
        // a refused save says why and keeps the saved state
        fake.st.notify.refuse = true;
        await click(p, '#ntf-autos .ntfrow .tgl');
        await waitFor(p, `document.getElementById('ntf-msg').textContent.length > 0`);
        assert.equal(await ev(p, `document.querySelector('#ntf-autos .ntfrow .tgl').getAttribute('aria-checked')`), 'false');
        // 移除 → the confirm sheet (Esc = no), then yes → allow:null → the empty state
        const n = fake.st.notify.sets.length;
        await click(p, '#ntf-autos .ntfrm');
        await waitFor(p, `!document.getElementById('confirm').hidden`);
        await key(p, 'Escape', { code: 'Escape' });
        await waitFor(p, `document.getElementById('confirm').hidden`);
        await sleep(200);
        assert.equal(fake.st.notify.sets.length, n, 'Esc sends nothing');
        if (await ev(p, `document.getElementById('set-notify').hidden`)) { await click(p, '#set-notify-nav'); await waitFor(p, `document.querySelectorAll('#ntf-autos .ntfrow').length === 1`); }
        await click(p, '#ntf-autos .ntfrm');
        await waitFor(p, `!document.getElementById('confirm').hidden`);
        await click(p, '#confirm-yes');
        await waitFor(p, `document.querySelectorAll('#ntf-autos .ntfrow').length === 0 && !document.getElementById('ntf-autos-empty').hidden`);
        assert.deepEqual(fake.st.notify.sets.at(-1), { id: 'wechat-login-push', allow: null });
        assert.match(await ev(p, `document.getElementById('ntf-autos-empty').textContent`), lang === 'zh' ? /agentj notify --allow <名字>/ : /agentj notify --allow <name>/);
        await save(p, `settings-notify-empty-${tag}`);
        // the master switch = pref_set notify.enabled
        await click(p, '#ntf-on');
        for (let i = 0; i < 50 && fake.st.prefSets.at(-1)?.key !== 'notify.enabled'; i++) await sleep(100);
        assert.deepEqual([fake.st.prefSets.at(-1).key, fake.st.prefSets.at(-1).value], ['notify.enabled', false]);
        await waitFor(p, `document.getElementById('ntf-on').getAttribute('aria-checked') === 'false' && !document.getElementById('ntf-off').hidden`);
        await save(p, `settings-notify-off-${tag}`);
        clean(p);
      } finally { await p.dispose(); }
    });
  }
  await C('an older host (no notify.enabled in settable): no entry', async () => {
    const p = await paired(390, 844, 'zh', 'light', { notify: false });
    try {
      await click(p, '#setBtn');
      await sleep(400);
      assert.equal(await ev(p, `document.getElementById('set-notify-nav').hidden`), true);
      clean(p);
    } finally { await p.dispose(); }
  });
} finally { await B.close(); await fake.stop(); await web.stop(); }

if (process.env.P365_SHOTS) {
  mkdirSync(process.env.P365_SHOTS, { recursive: true });
  for (const [from, to] of [['chat-auto-zh-390-light', 'p365-chat-auto.png'], ['settings-notify-zh-390-light', 'p365-settings-notify.png']])
    copyFileSync(OUT + from + '.png', process.env.P365_SHOTS.replace(/\/?$/, '/') + to);
}
console.log(`${shots.length} shots → ${OUT}`);
if (failed) { console.log(`FAIL P365: ${failed} case(s)`); process.exit(1); }
console.log('PASS P365 phone: automation pages + Settings → 自动化消息');
