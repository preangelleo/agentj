// P561 追加 13 (#338, Leo 10-28, Mac Safari): renaming a remote opened the full-screen editor, and its ✓ only went back to
// the form (a second tap on 保存 was needed). Real Chromium, isolated fake host + local relay (own headless browser, never :9222):
//   a) Settings → 遥控器 → 改名: the name field stays inline (not read-only, no full screen); Enter saves in one step (signed dev_name)
//   b) the Agent name dialog: inline, no full screen; Enter saves (signed agent_name)
//   c) a single-line settings field (mail address) is typed in place, no full screen
//   d) long text still goes full screen: ✓ submits the field's form in one tap (P563: also when the form holds other fields)
// zh/en × 390 / 1440, light + dark shots. `before` runs the baseline tree (P561_BEFORE_PUBLIC) and only records what it does.
// Then the same a/b/d in Safari's engine (Playwright WebKit, 390 + 1440, zh) unless AJ_P561_WEBKIT=0. Not physical Mac acceptance.
//   node web/test/p561-rename.mjs [before]
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { createRequire } from 'node:module';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate as ev, navigate, waitFor, waitState, shoot, key, sleep } from './browser.mjs';

const before = process.argv.includes('before');
const out = resolve(process.env.P561_RENAME_OUT || 'reports/qa/p561-rename', before ? 'before' : 'after');
mkdirSync(out, { recursive: true });
const publicDir = before ? process.env.P561_BEFORE_PUBLIC : undefined;
if (before) assert.ok(publicDir && existsSync(publicDir), 'P561_BEFORE_PUBLIC = the baseline web/public');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(publicDir ? { publicDir } : {}) });
const fake = await startFakeHost();
const B = await launch();
let checks = 0, failed = 0;
const notes = [];
const ok = (v, m) => { assert.ok(v, m); checks++; };
const NOW = Math.floor(Date.now() / 1000);
const DEVICES = () => [
  { id: 'ME', name: 'iPhone Air', paired_at: NOW - 86400 * 12, seen: NOW, online: true, source: 'local', me: true },
  { id: 'Kx3pQ9wLm2Vb7Tz1', name: 'MacBook · Safari', paired_at: NOW - 86400 * 30, seen: NOW - 3600, online: true, source: 'account' },
  { id: 'Rf8sN2cHq5Yd4Jk6', name: 'Pixel 9', paired_at: NOW - 86400 * 5, seen: NOW - 86400 * 2, online: false, source: 'local' },
];
const FS_OPEN = `!document.getElementById('fsEd').hidden`;
const RM = `document.querySelector('.rmrename input')`;

async function paired(w, h, lang, scheme) {
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: scheme } };
  fake.remotes({ limit: 10, items: DEVICES(), link: 'https://m.agentj.app/#p=' + '2'.repeat(40), qr: '', code: '482913' });
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  return p;
}
const shot = async (p, n) => writeFileSync(join(out, n + '.png'), await shoot(p, ''));
const typeInto = (p, sel, v) => ev(p, `(() => { const i = ${sel}; i.focus(); i.value = ${JSON.stringify(v)}; i.dispatchEvent(new Event('input', { bubbles: true })); return document.activeElement === i; })()`);

try {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 1000]]) for (const scheme of ['light', 'dark']) {
    const tag = `${lang}-${w}-${scheme}`;
    const p = await paired(w, h, lang, scheme);
    try {
      // a) Settings → 遥控器 → 改名
      await ev(p, `document.getElementById('setBtn').click()`); await waitFor(p, `!document.getElementById('settings').hidden`);
      await ev(p, `document.getElementById('set-remotes-open').click()`); await waitFor(p, `!document.getElementById('set-remotes').hidden`);
      await waitFor(p, `!!document.querySelector('#rm-list .rmitem[data-id="Rf8sN2cHq5Yd4Jk6"] [data-act="rename"]')`);
      await ev(p, `document.querySelector('#rm-list .rmitem[data-id="Rf8sN2cHq5Yd4Jk6"] [data-act="rename"]').click()`);
      await waitFor(p, `!!${RM}`); await sleep(150);
      const newName = lang === 'zh' ? '书房 Mac Safari' : 'Leo Mac M1 Safari';
      if (before) {
        // what the owner met: the field is read-only; a tap on it (or Enter) puts it in full screen
        const ro = await ev(p, `${RM}.readOnly`);
        await ev(p, `${RM}.click()`); await sleep(200);
        const fs = await ev(p, FS_OPEN);
        await ev(p, `(() => { const t = document.getElementById('fsText'); t.value = ${JSON.stringify(newName)}; t.dispatchEvent(new Event('input')); })()`);
        await shot(p, tag + '-remote-rename');
        const n0 = (fake.st.remotes.writes || []).length;
        await ev(p, `document.getElementById('fsSend').click()`); await sleep(400);
        const sent = (fake.st.remotes.writes || []).length - n0;
        await shot(p, tag + '-remote-rename-after-check');
        notes.push(`${tag}: rename field readOnly=${ro}, tap → full screen=${fs}, ✓ sent dev_name=${sent} (form still open=${await ev(p, `!!${RM}`)})`);
      } else {
        ok(await ev(p, `!${RM}.readOnly && !${RM}.dataset.textField`), tag + ' remote name field is a plain inline input');
        await ev(p, `${RM}.click()`); await sleep(150);
        ok(!(await ev(p, FS_OPEN)), tag + ' tapping the remote name field does not open full screen');
        ok(await typeInto(p, RM, newName), tag + ' remote name typed in place');
        await shot(p, tag + '-remote-rename');
        const n0 = (fake.st.remotes.writes || []).length;
        await key(p, 'Enter', { text: '\r' });
        await waitFor(p, `!document.querySelector('.rmrename')`);
        const wr = fake.st.remotes.writes.slice(n0);
        ok(wr.length === 1 && wr[0].t === 'dev_name' && wr[0].name === newName && wr[0].sigOk, tag + ' Enter = one signed dev_name');
        ok(!(await ev(p, FS_OPEN)), tag + ' never went full screen');
        await waitFor(p, `document.querySelector('#rm-list .rmitem[data-id="Rf8sN2cHq5Yd4Jk6"]').textContent.includes(${JSON.stringify(newName)})`);
        await sleep(600);   // the list refresh after a rename re-renders it; start the next edit after it lands
        // the Save button is also one step
        await ev(p, `document.querySelector('#rm-list .rmitem[data-id="Kx3pQ9wLm2Vb7Tz1"] [data-act="rename"]').click()`);
        await waitFor(p, `!!${RM}`); await typeInto(p, RM, 'Office iMac');
        await ev(p, `document.querySelector('.rmrename button[type=submit]').click()`);
        await waitFor(p, `!document.querySelector('.rmrename')`);
        ok(fake.st.remotes.writes.at(-1).name === 'Office iMac' && !(await ev(p, FS_OPEN)), tag + ' Save = one signed dev_name');
        await shot(p, tag + '-remote-renamed');
      }
      await ev(p, `document.getElementById('set-back').click()`); await sleep(150);

      // c) a single-line settings field: typed in place
      if (!before) {
        const inline = await ev(p, `(() => { const i = document.getElementById('mail-addr'); i.click(); return !i.readOnly && !i.dataset.textField; })()`);
        await sleep(150);
        ok(inline && !(await ev(p, FS_OPEN)), tag + ' single-line settings field (mail address) stays inline');
      }

      // d) long text: a settings textarea that is all its form saves
      await ev(p, `(() => { window.p561Submits = 0; const f = document.createElement('form'); f.id = 'p561-form'; f.hidden = true;
        f.addEventListener('submit', (e) => { e.preventDefault(); window.p561Submits++; window.p561Saved = f.querySelector('#p561-long').value; });
        const t = document.createElement('textarea'); t.id = 'p561-long'; t.value = 'line 1\\nline 2'; t.setAttribute('aria-label', 'Long text');
        const s = document.createElement('button'); s.type = 'submit'; s.textContent = 'Save';
        f.append(t, s); document.getElementById('settings').append(f); })()`);
      await waitFor(p, `document.getElementById('p561-long').type === 'hidden'`);
      await ev(p, `document.querySelector('#p561-form .text-editor-field').click()`);
      await waitFor(p, FS_OPEN);
      await ev(p, `(() => { const t = document.getElementById('fsText'); t.value = 'line 1\\nline 2\\nline 3'; t.dispatchEvent(new Event('input')); })()`);
      await shot(p, tag + '-long-text-editor');
      await ev(p, `document.getElementById('fsSend').click()`); await sleep(300);
      const sub = await ev(p, `window.p561Submits`), closed = !(await ev(p, FS_OPEN));
      if (before) notes.push(`${tag}: long text ✓ → closed=${closed}, form submitted=${sub}`);
      else {
        ok(closed && sub === 1 && (await ev(p, `window.p561Saved`)) === 'line 1\nline 2\nline 3', tag + ' long text ✓ saves in one tap (form submitted once)');
        // P563 追加 3: a field among others — ✓ is still Save: the form is submitted (with the other fields as they stand)
        await ev(p, `(() => { const f = document.getElementById('p561-form'); const i = document.createElement('input'); i.id = 'p561-other'; f.prepend(i); })()`);
        await ev(p, `document.querySelector('#p561-form .text-editor-field').click()`); await waitFor(p, FS_OPEN);
        await ev(p, `(() => { const t = document.getElementById('fsText'); t.value = 'four'; t.dispatchEvent(new Event('input')); })()`);
        await ev(p, `document.getElementById('fsSend').click()`); await sleep(300);
        ok(!(await ev(p, FS_OPEN)) && (await ev(p, `window.p561Submits`)) === 2 && (await ev(p, `window.p561Saved`)) === 'four',
          tag + ' a field among others: ✓ saves too (form submitted, P563)');
      }
      await ev(p, `document.getElementById('p561-form').remove()`);
      await ev(p, `import('./js/settings.js').then(m => m.close())`); await sleep(150);

      // b) Agent name dialog (opened as its long press does)
      await ev(p, `import('./js/agentname.js').then(m => m.open())`);
      await waitFor(p, `!document.getElementById('nameDlg').hidden`);
      const agentName = lang === 'zh' ? '小杰' : 'Jay';
      await typeInto(p, `document.getElementById('nameInput')`, agentName); await sleep(100);
      await shot(p, tag + '-agent-name');
      if (before) notes.push(`${tag}: agent name dialog full screen=${await ev(p, FS_OPEN)}`);
      else {
        ok(!(await ev(p, FS_OPEN)) && !(await ev(p, `document.getElementById('nameInput').readOnly`)), tag + ' Agent name inline, no full screen');
        await key(p, 'Enter', { text: '\r' });
        await waitFor(p, `document.getElementById('nameDlg').hidden`);
        ok(fake.log.some((m) => m.t === 'agent_name' && m.name === agentName), tag + ' Enter = one signed agent_name');
      }
      ok(p.problems.length === 0, tag + ' no page errors: ' + p.problems.join(' | '));
    } catch (e) { failed++; console.log(`  FAIL ${tag}: ${e.stack || e}`); await shot(p, tag + '-FAIL').catch(() => {}); }
    await p.close?.();
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
for (const n of notes) console.log('  ' + n);
console.log(`P561 rename (Chromium) ${before ? 'baseline recorded' : checks + ' checks PASS'}${failed ? `, ${failed} FAILED` : ''} → ${out}`);

// ---- WebKit (Safari's engine)
let wkFailed = 0, wkChecks = 0;
if (!before && process.env.AJ_P561_WEBKIT !== '0') {
  const require = createRequire(import.meta.url);
  const { webkit } = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
  const web2 = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake2 = await startFakeHost();
  const wb = await webkit.launch();
  const wok = (v, m) => { assert.ok(v, m); wkChecks++; };
  try {
    for (const [w, h] of [[390, 844], [1440, 1000]]) {
      const tag = `webkit-zh-${w}`;
      fake2.reset(); fake2.st.prefs = { appearance: { language: 'zh', theme: 'light' } };
      fake2.remotes({ limit: 10, items: DEVICES(), link: 'https://m.agentj.app/#p=' + '2'.repeat(40), qr: '', code: '482913' });
      const ctx = await wb.newContext({ viewport: { width: w, height: h }, hasTouch: w < 500 });
      // this headless WebKit (WPE) build has no media backend: an HTMLAudioElement (unlocked on any pointerdown) crashes the
      // page process — audio stubbed exactly as p181-webkit.mjs does; nothing under test here plays sound
      await ctx.addInitScript(() => { window.Audio = undefined;
        for (const k of ['play', 'pause', 'load']) HTMLMediaElement.prototype[k] = function () { return k === 'play' ? Promise.resolve() : undefined; }; });
      const page = await ctx.newPage();
      const errs = [];
      page.on('pageerror', (e) => errs.push(String(e)));
      try {
        // pairing in WPE WebKit under a loaded gate run can take > 15 s (precheck 10-28 04:4x); the checks below are the point
        await page.goto(fake2.newPairing(web2.url.replace('127.0.0.1', 'localhost')));
        await page.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 30000 });
        await fake2.approve();
        await page.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 30000 });
        await fake2.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
        await page.evaluate(() => document.getElementById('phone-env-dismiss')?.click());
        await page.evaluate(() => document.getElementById('setBtn').click());
        await page.evaluate(() => document.getElementById('set-remotes-open').click());
        await page.waitForSelector('#rm-list .rmitem[data-id="Rf8sN2cHq5Yd4Jk6"] [data-act="rename"]');
        await page.evaluate(() => document.querySelector('#rm-list .rmitem[data-id="Rf8sN2cHq5Yd4Jk6"] [data-act="rename"]').click());
        await page.waitForSelector('.rmrename input');
        await page.click('.rmrename input'); await page.waitForTimeout(200);
        wok(await page.evaluate(() => document.getElementById('fsEd').hidden), tag + ' click on the remote name field: no full screen');
        await page.fill('.rmrename input', '书房 Mac Safari');
        writeFileSync(join(out, tag + '-remote-rename.png'), await page.screenshot());
        const n0 = (fake2.st.remotes.writes || []).length;
        await page.press('.rmrename input', 'Enter');
        await page.waitForSelector('.rmrename', { state: 'detached' });
        const wr = fake2.st.remotes.writes.slice(n0);
        wok(wr.length === 1 && wr[0].name === '书房 Mac Safari' && wr[0].sigOk, tag + ' Enter = one signed dev_name');
        await page.evaluate(() => { const f = document.createElement('form'); f.id = 'p561-form'; window.p561Submits = 0;
          f.addEventListener('submit', (e) => { e.preventDefault(); window.p561Submits++; });
          const t = document.createElement('textarea'); t.id = 'p561-long'; t.value = 'a\nb'; f.append(t); document.getElementById('settings').append(f); });
        await page.waitForFunction(() => document.getElementById('p561-long').type === 'hidden');
        await page.evaluate(() => document.querySelector('#p561-form .text-editor-field').click());
        await page.waitForFunction(() => !document.getElementById('fsEd').hidden);
        await page.evaluate(() => document.getElementById('fsSend').click());
        await page.waitForFunction(() => document.getElementById('fsEd').hidden && window.p561Submits === 1);
        wok(true, tag + ' long text ✓ submits its form in one tap');
        await page.evaluate(() => { document.getElementById('p561-form').remove(); return import('./js/settings.js').then((m) => m.close()); });
        await page.evaluate(() => import('./js/agentname.js').then((m) => m.open()));
        await page.waitForSelector('#nameDlg:not([hidden])');
        await page.fill('#nameInput', '小杰');
        wok(await page.evaluate(() => document.getElementById('fsEd').hidden), tag + ' Agent name: no full screen');
        await page.press('#nameInput', 'Enter');
        await page.waitForSelector('#nameDlg', { state: 'hidden' });
        wok(fake2.log.some((m) => m.t === 'agent_name' && m.name === '小杰'), tag + ' Enter = one signed agent_name');
        wok(errs.length === 0, tag + ' no page errors: ' + errs.join(' | '));
      } catch (e) { wkFailed++; console.log(`  FAIL ${tag}: ${e.stack || e}`); }
      await ctx.close();
    }
  } finally { await wb.close(); await fake2.stop(); await web2.stop(); }
  console.log(`P561 rename (WebKit) ${wkChecks} checks PASS${wkFailed ? `, ${wkFailed} FAILED` : ''}`);
}
if (failed || wkFailed) process.exit(1);
