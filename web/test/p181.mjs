#!/usr/bin/env node
// P181 · the load card's five resources, long-press shortcuts, the source card's double tap, press-to-talk cancel and the
// double-tap lock. Real page + local fake host (signatures checked like the real host), own headless Chromium (never :9222).
//   a) load card: opening it asks the computer to sample (res_watch on), closing stops it (off); five rows, unknown = 「—」
//   b) long-press the header name → rename dialog (rule hint, too long refused here, signed agent_name, refusal + suggestions, Esc)
//   c) robots card: 「换个样子」 sends the theme preset as the owner; a long press on the robots sends it without opening the card
//   d) ≡: a long press sends the commands preset; the list has /customize (tap → the same); typing /customize + Send → the same
//   e) the source card: double click / double tap on its words = 「…」 / O (open ⇄ close), never the full-screen reader
//   f) hold 文件 → the clipboard's text at the cursor (≤ 2000) or a .txt attachment (longer); hold 相册 → the clipboard image
//   g) press to talk: a wobble (40 px) or leaving the button by 60 px does not cancel; 120 px away does; coming back un-cancels
//   h) double-tap lock: 20 real CDP touch double taps (gaps 60–420 ms) ≥ 19 locked; the same with WebKit-like event orders
//      (lostpointercapture before pointerup, pointercancel instead of pointerup, a window blur between the taps, touchend only)
//   i) 追加 2: the reply card's header (name · load line · robots) stays pinned while a long reply scrolls; gestures still work
//   j) 追加 3: quick flicks turn pages (10/10); a hold (> 300 ms) then a drag, a drag that starts on a selection (its handles),
//      and a selection that appears mid-touch never do (0/10 each)
// Shots (zh/en × 390/1440) → reports/qa/p181/screens/*.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, rect, swipe, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p181/screens/';
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P181_ONLY || '').split(',').filter(Boolean);
const rec = parityRecorder('web/test/p181.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const $ = (id) => `document.getElementById(${JSON.stringify(id)})`;
const shown = (p, id) => ev(p, `!!${$(id)} && !${$(id)}.hidden`);
const results = {};

async function paired({ w = 390, h = 844, scheme = 'light', lang = 'zh' } = {}) {
  fake.reset();
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await p.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `(()=>{${$('phone-env-dismiss')}?.click(); const n=${$('confirm-no')}; if(n&&!${$('confirm')}.hidden)n.click(); const a=${$('a2hs-ok')}; if(a&&!${$('a2hs')}.hidden)a.click();})()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await sleep(200);
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
/** A press of `ms` at the centre of `sel`: a finger on a touch page, the mouse otherwise. */
async function hold(p, sel, ms = 750, touch = true) {
  const r = await rect(p, sel);
  if (touch) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
    await sleep(ms);
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } else {
    await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await sleep(ms);
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  }
  await sleep(150);
}
const says = () => fake.st.says.map((m) => m.text || '');
const dlgAudit = (id) => `(() => { const sq = ${$(id)}.querySelector('.sq'); const r = sq.getBoundingClientRect();
  const over = [...sq.querySelectorAll('*')].filter((e) => { const b = e.getBoundingClientRect(); return b.width && (b.right > r.right + 1 || b.left < r.left - 1); }).map((e) => e.className || e.tagName);
  const small = [...sq.querySelectorAll('button')].filter((b) => b.getClientRects().length && b.getBoundingClientRect().height < 44).map((b) => b.id);
  return { over, small, inView: r.top >= 0 && r.bottom <= innerHeight + 1 }; })()`;

// ---------------------------------------------------------------- a) load card
await C('load-card-five-resources', async () => {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const p = await paired({ w, h, lang });
    await fake.send({ t: 'busy', tasks: [], by: 'Agent J', load: 0.42 });
    fake.st.res = { cpu: 0.27, mem: 0.63, gpu: { pct: 0.91, name: 'NVIDIA GeForce RTX 3070 Ti Laptop GPU' }, disk: { busy: 0.04, free: 0.75, free_gb: 761.8 }, net: { rx: 1234567, tx: 45678 }, load: 0.42 };
    await ev(p, `${$('loadHit')}.click()`);
    await waitFor(p, `!${$('loadDlg')}.hidden`);
    await waitFor(p, `${$('resList')}.querySelector('[data-k=cpu] .v').textContent === '27%'`);
    assert.deepEqual(fake.st.resWatch, [true], 'opening the card asks for samples');
    const rows = await ev(p, `[...${$('resList')}.children].map((li) => [li.dataset.k, li.querySelector('.v').textContent, li.querySelector('small').hidden ? '' : li.querySelector('small').textContent, li.dataset.hot])`);
    assert.deepEqual(rows.map((r) => r[1]), ['27%', '63%', '91%', '4%', '↓ 1.2 MB/s  ↑ 46 KB/s']);
    assert.equal(rows[2][3], '1', 'a share ≥ 85 % is marked hot');
    assert.match(rows[3][2], lang === 'zh' ? /剩余 762 GB（75%）/ : /762 GB free \(75%\)/);
    const a = await ev(p, dlgAudit('loadDlg'));
    assert.deepEqual(a.over, [], 'nothing sticks out of the square');
    assert.deepEqual(a.small, [], 'every button ≥ 44 px');
    writeFileSync(`${OUT}${lang}-${w}-load-card.png`, await shoot(p, ''));
    // a computer without a GPU reader / disk busy: 「—（未检测到）」, never a number
    await fake.send({ t: 'res', cpu: 0.1, mem: 0.5, disk: { free: 0.5, free_gb: 12 } });
    await sleep(100);
    const none = await ev(p, `[...${$('resList')}.children].map((li) => li.querySelector('.v').textContent)`);
    assert.equal(none[2], lang === 'zh' ? '—（未检测到）' : '— (not detected)');
    assert.equal(none[3], '—');
    assert.equal(none[4], '—');
    if (lang === 'zh' && w === 390) writeFileSync(`${OUT}zh-390-load-card-nogpu.png`, await shoot(p, ''));
    await key(p, 'Escape');
    await waitFor(p, `${$('loadDlg')}.hidden`);
    await sleep(100);
    // §22.1 renews every 10s while open; a loaded screenshot run may legitimately renew several times.
    assert.equal(fake.st.resWatch.at(-1), false, 'closing the card stops the sampling');
    assert.ok(fake.st.resWatch.slice(0, -1).every(on => on === true), 'only renewals preceded the close');
    if (lang === 'zh' && w === 390) {
      const stopped = fake.st.resWatch.length;
      await sleep(11000); // One full protocol renewal period after close: no subscription may resume.
      assert.equal(fake.st.resWatch.length, stopped, 'no renewal after closing');
    }
    noProblems(p);
    await p.dispose();
  }
});

// ---------------------------------------------------------------- b) rename from the header
await C('long-press-name-renames', async () => {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const p = await paired({ w, h, lang });
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await ev(p, `${$('agent')}.click()`);
    await sleep(150);
    assert.equal(await shown(p, 'nameDlg'), false, 'a tap does nothing new');
    await hold(p, '#agent', 750, w < 500);
    await waitFor(p, `!${$('nameDlg')}.hidden`);
    assert.equal(await ev(p, `${$('nameInput')}.value`), 'Agent J');
    assert.equal(await ev(p, `document.activeElement.id`), 'nameInput');
    await ev(p, `(() => { const i = ${$('nameInput')}; i.value = '一二三四五六七八九'; i.dispatchEvent(new Event('input')); })()`);
    assert.equal(await ev(p, `${$('nameSave')}.disabled`), true, 'over the short-name rule: not sent');
    await ev(p, `(() => { const i = ${$('nameInput')}; i.value = '小助手'; i.dispatchEvent(new Event('input')); })()`);
    writeFileSync(`${OUT}${lang}-${w}-rename.png`, await shoot(p, ''));
    const a = await ev(p, dlgAudit('nameDlg'));
    assert.deepEqual(a.over, []); assert.deepEqual(a.small, []);
    fake.st.renameWhy = 'name_taken';
    await ev(p, `${$('nameSave')}.click()`);
    await waitFor(p, `${$('nameHint')}.dataset.bad === '1' && /小J/.test(${$('nameHint')}.textContent)`);
    assert.equal(await shown(p, 'nameDlg'), true, 'a refusal keeps the dialog with the reason + suggestions');
    await ev(p, `${$('nameSave')}.click()`);
    await waitFor(p, `${$('nameDlg')}.hidden`);
    await waitFor(p, `${$('agent')}.textContent === '小助手'`);
    assert.deepEqual(fake.st.renames.map((x) => [x.name, x.sigOk]), [['小助手', true], ['小助手', true]], 'signed with this remote\'s key');
    await hold(p, '#agent', 750, w < 500);
    await waitFor(p, `!${$('nameDlg')}.hidden`);
    await key(p, 'Escape');
    await waitFor(p, `${$('nameDlg')}.hidden`);
    assert.equal(fake.st.renames.length, 2, 'Esc sends nothing');
    noProblems(p);
    await p.dispose();
  }
});

// ---------------------------------------------------------------- c) robots → theme preset
await C('robots-card-theme-button', async () => {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const p = await paired({ w, h, lang });
    await fake.send({ t: 'busy', by: 'Agent J', tasks: [{ id: 'a', who: '掌枢', kind: 'ceo', label: '发布收尾', c: 0 }, { id: 'b', who: null, kind: 'temp', label: 'Explore the repo', c: 1 }] });
    await waitFor(p, `!${$('busyDots')}.hidden`);
    await ev(p, `${$('busyDots')}.click()`);
    await waitFor(p, `!${$('busyDlg')}.hidden`);
    assert.equal(await ev(p, `${$('busyTheme')}.textContent`), lang === 'zh' ? '换个样子' : 'Change the look');
    const a = await ev(p, dlgAudit('busyDlg'));
    assert.deepEqual(a.over, []); assert.deepEqual(a.small, []);
    writeFileSync(`${OUT}${lang}-${w}-robots-card.png`, await shoot(p, ''));
    const n0 = says().length;
    await ev(p, `${$('busyTheme')}.click()`);
    await waitFor(p, `${$('busyDlg')}.hidden`);
    await waitFor(p, 'true'); await sleep(300);
    const t1 = says().slice(n0);
    assert.equal(t1.length, 1);
    assert.match(t1[0], /agentj-theme/); assert.match(t1[0], /agentj theme validate/); assert.match(t1[0], lang === 'zh' ? /先问我想怎么改/ : /first ask me/);
    // long press on the robots: the same message, the card does not open
    await hold(p, '#busyDots', 750, w < 500);
    await sleep(300);
    assert.equal(await shown(p, 'busyDlg'), false, 'a hold never also opens the card');
    assert.equal(says().length, n0 + 2);
    assert.equal(says().at(-1), t1[0]);
    noProblems(p);
    await p.dispose();
  }
});

// ---------------------------------------------------------------- d) ≡ → commands preset, /customize
await C('menu-customize', async () => {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const p = await paired({ w, h, lang });
    const n0 = says().length;
    await hold(p, '#slashBtn', 750, w < 500);
    await sleep(300);
    assert.equal(await shown(p, 'menu'), false, 'a hold does not open the menu');
    assert.equal(says().length, n0 + 1);
    assert.match(says().at(-1), /agentj-commands/);
    await ev(p, `${$('slashBtn')}.click()`);
    await waitFor(p, `!${$('menu')}.hidden`);
    const item = await ev(p, `(() => { const b = [...${$('menu')}.querySelectorAll('button')].find((x) => x.dataset.prompt === 'cmds'); return b ? [b.querySelector('b').textContent, b.querySelector('span').textContent] : null; })()`);
    assert.deepEqual(item, ['/customize', lang === 'zh' ? '定制命令：增删改你自己的 slash 命令' : 'Customise your own commands']);
    writeFileSync(`${OUT}${lang}-${w}-menu-customize.png`, await shoot(p, ''));
    await ev(p, `[...${$('menu')}.querySelectorAll('button')].find((x) => x.dataset.prompt === 'cmds').click()`);
    await sleep(300);
    assert.equal(says().length, n0 + 2);
    // type-ahead + typed /customize sends the preset, not the bare word
    await ev(p, `(() => { const i = ${$('input')}; i.focus(); i.value = '/cus'; i.dispatchEvent(new Event('input')); })()`);
    await waitFor(p, `!${$('sug')}.hidden && /customize/.test(${$('sug')}.textContent)`);
    await ev(p, `(() => { const i = ${$('input')}; i.value = '/customize'; i.dispatchEvent(new Event('input')); })()`);
    await ev(p, `${$('send')}.click()`);
    await sleep(400);
    assert.equal(says().length, n0 + 3);
    assert.match(says().at(-1), /agentj-commands/);
    assert.equal(await ev(p, `${$('input')}.value`), '', 'the field is cleared like any sent message');
    noProblems(p);
    await p.dispose();
  }
});

// ---------------------------------------------------------------- e) source card double tap = O
await C('source-card-double-tap', async () => {
  for (const touch of [false, true]) {
    const [w, h] = touch ? [390, 844] : [1440, 900];
    const p = await paired({ w, h });
    const long = Array.from({ length: 30 }, (_, i) => `第 ${i + 1} 行：这是一条很长的原话，用来测试双击展开。`).join('\n');
    await ev(p, `(() => { const i = ${$('input')}; i.value = ${JSON.stringify(long)}; i.dispatchEvent(new Event('input')); })()`);
    await ev(p, `${$('send')}.click()`);
    await waitFor(p, `!${$('om')}.hidden && ${$('omText')}.textContent.length > 50`, 8000);
    await sleep(400);
    const open = () => ev(p, `${$('om')}.classList.contains('open')`);
    assert.equal(await open(), false);
    const r = await rect(p, '#omText');
    const dbl = async () => {
      if (touch) {
        for (let i = 0; i < 2; i++) {
          await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.top + 8 }] });
          await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
          await sleep(90);
        }
      } else {
        for (const n of [1, 2]) {
          await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.top + 8, button: 'left', clickCount: n });
          await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.top + 8, button: 'left', clickCount: n });
        }
      }
      await sleep(450);
    };
    await dbl();
    assert.equal(await open(), true, 'double tap opens the card like O / …');
    assert.equal(await ev(p, `!!document.querySelector('.rd:not([hidden])') || document.body.classList.contains('reading')`), false, 'never the full-screen reader');
    assert.equal(await ev(p, `String(getSelection())`), '', 'a double click does not select a word first');
    if (!touch) writeFileSync(`${OUT}zh-${w}-source-open.png`, await shoot(p, ''));
    await dbl();
    assert.equal(await open(), false, 'again → closed');
    noProblems(p);
    await p.dispose();
  }
});

// ---------------------------------------------------------------- f) clipboard long presses
await C('clipboard-long-press', async () => {
  for (const touch of [true, false]) {
    const p = await paired(touch ? { w: 390, h: 844 } : { w: 1440, h: 900 });
    await ev(p, `navigator.clipboard.writeText('剪贴板里的一句话')`);
    await ev(p, `(() => { const i = ${$('input')}; i.focus(); i.value = '前缀|后缀'; i.setSelectionRange(3, 3); })()`);
    await hold(p, '#tDoc', 750, touch);
    await waitFor(p, `${$('input')}.value === '前缀|剪贴板里的一句话后缀'`);
    assert.equal(await ev(p, `${$('fDoc')}.files.length`), 0, 'the file picker did not open (no tap)');
    const big = 'x'.repeat(2500);
    await ev(p, `navigator.clipboard.writeText(${JSON.stringify(big)})`);
    await hold(p, '#tDoc', 750, touch);
    const atts = () => ev(p, `import('./js/relay.js').then((m) => m.debug().atts.map((a) => a.name))`);
    await waitFor(p, `import('./js/relay.js').then((m) => m.debug().atts.some((a) => /\\.txt$/.test(a.name)))`, 5000);
    // an image on the clipboard → hold 相册
    await ev(p, `(async () => { const c = document.createElement('canvas'); c.width = c.height = 8; const b = await new Promise((r) => c.toBlob(r, 'image/png'));
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': b })]); })()`);
    const n1 = (await atts()).length;
    await hold(p, '#tPhoto', 750, touch);
    await waitFor(p, `import('./js/relay.js').then((m) => m.debug().atts.length > ${n1})`, 5000);
    // no image → a light hint, nothing added
    await ev(p, `navigator.clipboard.writeText('只有文字')`);
    const n2 = (await atts()).length;
    await hold(p, '#tPhoto', 750, touch);
    await sleep(400);
    assert.equal((await atts()).length, n2);
    assert.match(await ev(p, `document.getElementById('toast')?.textContent || ''`), /剪贴板里是文字|剪贴板里没有图片/);
    noProblems(p);
    await p.dispose();
  }
});

// ---------------------------------------------------------------- g) press-to-talk cancel tolerance
await C('ptt-cancel-tolerance', async () => {
  const p = await paired({ w: 1440, h: 900 });
  const r = await rect(p, '#mic');
  const st = () => ev(p, `import('./js/relay.js').then((m) => m.debug().ptt)`);
  const mv = (x, y) => p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'left', buttons: 1 });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await sleep(700);
  const seen = [];
  for (const [dx, dy, want, label] of [[18, -12, false, 'wobble inside'], [0, -40, false, '40 px up'], [r.w / 2 + 60, 0, false, '60 px right of the edge'],
    [0, -(r.h / 2 + 50), false, 'just above, less than a button height + 24'], [r.w / 2 + 120, 0, true, '120 px away'],
    [r.w / 2 + 70, 0, true, 'back to 70 px: still cancelled (hysteresis)'], [r.w / 2 + 30, 0, false, 'back near: un-cancelled'],
    [0, -(r.h + 40), true, 'slid up more than a button height + 24']]) {
    await mv(r.x + dx, r.y + dy); await sleep(60);
    const s = await st();
    seen.push([label, s && s.cancel]);
    assert.ok(s, 'still recording: ' + label);
    assert.equal(s.cancel, want, label);
  }
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y - r.h - 40, button: 'left', clickCount: 1 });
  await sleep(300);
  assert.equal(await st(), null, 'released in the cancel zone → cancelled');
  results.cancel = seen;
  // pure rule (the exported function), finger thresholds
  const z = await ev(p, `import('./js/relay.js').then((m) => { const R = {left: 100, right: 156, top: 500, bottom: 556, width: 56, height: 56};
    return [m.cancelZone(R, 128, 528, 128 + 28 + 90, 528, true), m.cancelZone(R, 128, 528, 128 + 28 + 100, 528, true),
            m.cancelZone(R, 128, 528, 128, 528 - 80, true), m.cancelZone(R, 128, 528, 128, 528 - 90, true),
            m.cancelZone(R, 128, 528, 128 + 28 + 79, 528, false), m.cancelZone(R, 128, 528, 128 + 28 + 80, 528, false)]; })`);
  assert.deepEqual(z, [false, true, false, true, false, true]);
  noProblems(p);
  await p.dispose();
});

// ---------------------------------------------------------------- h) double-tap lock, 20 tries per way
await C('double-tap-lock-20x', async () => {
  const p = await paired({ w: 390, h: 844 });
  const r = await rect(p, '#mic');
  const locks = () => ev(p, `import('./js/relay.js').then((m) => m.debug().lockCount)`);
  const pttNow = () => ev(p, `import('./js/relay.js').then((m) => m.debug().ptt)`);
  const endTake = async () => {     // a locked take: one more tap ends it (sends nothing useful: the fake mic is short)
    if (await pttNow()) {
      await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
      await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    }
    await sleep(700);                // past LOCK_REARM_MS and the transcription toast
    await ev(p, `(() => { const i = ${$('input')}; i.value = ''; i.dispatchEvent(new Event('input')); })()`);
  };
  // real touches through CDP, the gap between the two taps varied 60 … 420 ms (iOS allows ~350 ms)
  const gaps = Array.from({ length: 20 }, (_, i) => 60 + Math.round((i * 360) / 19));
  let ok = 0;
  for (const gap of gaps) {
    const before = await locks();
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
    await sleep(70);
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await sleep(gap);
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x + 3, y: r.y - 2 }] });
    await sleep(60);
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await sleep(120);
    if ((await locks()) === before + 1 && (await pttNow())?.locked) ok++;
    await endTake();
  }
  results.cdp = { ok, of: gaps.length, gaps };
  assert.ok(ok >= 19, `CDP touch double taps locked ${ok}/20`);
  // WebKit-like orders, dispatched to the real handlers (synthetic PointerEvents; the WebKit run records the real order)
  const ORDERS = {
    'lostpointercapture-before-pointerup': ['pointerdown', 'lostpointercapture', 'pointerup'],
    'pointercancel-instead-of-pointerup': ['pointerdown', 'pointercancel'],
    'blur-between-taps': ['pointerdown', 'pointerup', 'lostpointercapture', 'blur'],
    'touchend-only': ['pointerdown', 'touchend'],
  };
  results.orders = {};
  for (const [name, first] of Object.entries(ORDERS)) {
    let n = 0;
    for (let i = 0; i < 20; i++) {
      const before = await locks();
      const gap = 60 + ((i * 37) % 240);      // first tap's last event → second tap: 60 … 345 ms in all
      await ev(p, `(async () => {
        const m = ${$('mic')}, r = m.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, id = 900 + ${i};
        const pe = (t, pid) => m.dispatchEvent(new PointerEvent(t, {bubbles: true, cancelable: true, pointerId: pid, isPrimary: true, button: 0, buttons: t === 'pointerdown' ? 1 : 0, pointerType: 'touch', clientX: x, clientY: y}));
        const te = () => m.dispatchEvent(new TouchEvent('touchend', {bubbles: true, cancelable: true, touches: [], changedTouches: []}));
        for (const t of ${JSON.stringify(first)}) { if (t === 'blur') window.dispatchEvent(new Event('blur')); else if (t === 'touchend') te(); else pe(t, id); await new Promise((s) => setTimeout(s, 15)); }
        await new Promise((s) => setTimeout(s, ${gap}));
        pe('pointerdown', id + 50); await new Promise((s) => setTimeout(s, 50)); pe('pointerup', id + 50); pe('lostpointercapture', id + 50);
      })()`);
      await sleep(80);
      if ((await locks()) === before + 1) n++;
      await endTake();
    }
    results.orders[name] = n;
    assert.ok(n >= 19, `${name}: ${n}/20`);
  }
  noProblems(p);
  await p.dispose();
});

// ---------------------------------------------------------------- 追加 2) the reply card's header stays pinned
await C('header-sticky', async () => {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const p = await paired({ w, h, lang });
    const long = Array.from({ length: 40 }, (_, i) => (lang === 'zh' ? `第 ${i + 1} 段：页眉吸顶，正文从它下面滚过去。` : `Paragraph ${i + 1}: the header stays put while the words scroll under it.`)).join('\n\n');
    await fake.say(long, 'agent');
    await waitFor(p, `${$('words')}.textContent.includes(${JSON.stringify(lang === 'zh' ? '第 40 段' : 'Paragraph 40')})`);
    await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J' });
    await fake.send({ t: 'busy', by: 'Agent J', tasks: [{ id: 'a', who: 'Explore', what: lang === 'zh' ? '读代码' : 'reading code' }], load: 0.4 });
    await sleep(300);
    const geo = () => ev(p, `(() => { const m = ${$('main')}, top = ${$('rmTop')}.getBoundingClientRect(), mr = m.getBoundingClientRect(),
      a = ${$('agent')}.getBoundingClientRect(), d = ${$('busyDots')}.getBoundingClientRect(), bg = getComputedStyle(${$('rmTop')}).backgroundColor;
      // the first paragraph whose top is above the header's bottom must be hidden under it (the header paints over it)
      const hit = document.elementFromPoint(mr.left + mr.width / 2, top.top + top.height / 2);
      return { st: m.scrollTop, max: m.scrollHeight - m.clientHeight, topY: top.top, topH: top.height, mainY: mr.top, agentY: a.top, dotsY: d.top,
               bg, under: !!hit && ${$('rmTop')}.contains(hit) || hit === ${$('rmTop')} }; })()`);
    const g0 = await geo();
    assert.ok(g0.max > 200, `long reply scrolls (${g0.max})`);
    for (const frac of [0.3, 0.7, 1]) {
      await ev(p, `${$('main')}.scrollTop = ${Math.round(g0.max * frac)}`);
      await sleep(120);
      const g = await geo();
      assert.ok(g.st > 0, 'scrolled');
      assert.ok(Math.abs(g.topY - g.mainY) <= 1, `header pinned at the scroller's top (${g.topY} vs ${g.mainY})`);
      assert.ok(Math.abs(g.agentY - g0.agentY) <= 1, `name does not move (${g.agentY} vs ${g0.agentY})`);
      assert.ok(Math.abs(g.dotsY - g0.dotsY) <= 1, 'robots do not move');
      assert.ok(g.under, 'the header paints over the words');
      assert.notEqual(g.bg, 'rgba(0, 0, 0, 0)', 'opaque header');
    }
    // gestures still work while pinned: a tap on the robots opens their card, a long press on the name opens the rename
    await ev(p, `${$('busyDots')}.click()`);
    await waitFor(p, `!${$('busyDlg')}.hidden`);
    await key(p, 'Escape'); await waitFor(p, `${$('busyDlg')}.hidden`);
    await hold(p, '#agent', 750, w < 500);
    await waitFor(p, `!${$('nameDlg')}.hidden`);
    await key(p, 'Escape'); await waitFor(p, `${$('nameDlg')}.hidden`);
    await ev(p, `${$('main')}.scrollTop = ${Math.round(g0.max * 0.5)}`); await sleep(150);
    await ev(p, 'document.activeElement && document.activeElement.blur()');
    writeFileSync(`${OUT}${lang}-${w}-header-sticky.png`, await shoot(p, ''));
    noProblems(p);
    await p.dispose();
  }
});

// ---------------------------------------------------------------- 追加 3) a long press selects text, it never turns the page
await C('swipe-vs-select', async () => {
  const p = await paired({ w: 390, h: 844 });
  for (let i = 1; i <= 4; i++) await fake.addTurn({ k: 'phone', dev: 'other', text: `问题 ${i}` }, `回复 ${i}：这是一段可以长按选中的文字，用来测试横滑和选字互不干扰。`);
  await waitFor(p, `${$('pg')}.textContent.trim().endsWith('/ ' + ${fake.st.turns.length})`);
  if (await ev(p, `${$('pgCorner')}.dataset.unread === '1'`)) await ev(p, `${$('pgLatest')}.click()`);
  await sleep(300);
  const pg = async () => (await ev(p, `${$('pg')}.textContent.trim()`));
  const r = await rect(p, '#words');
  const flick = (dir) => swipe(p, r.x - dir * 90, r.y, r.x + dir * 90, r.y + 4, 6, 90);
  const out = { flick: 0, hold: 0, handle: 0, midSel: 0 };
  // quick flicks: alternate older / newer, every one must turn the page
  for (let i = 0; i < 10; i++) {
    const before = await pg();
    await flick(i % 2 ? -1 : 1);
    try { await waitFor(p, `${$('pg')}.textContent.trim() !== ${JSON.stringify(before)}`, 1500); out.flick++; } catch {}
    await sleep(250);
  }
  const drag = async (x0, y0, x1, y1, holdMs = 0, mid = null) => {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y: y0 }] });
    if (holdMs) await sleep(holdMs);
    for (let k = 1; k <= 6; k++) {
      if (k === 2 && mid) await ev(p, mid);
      await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + (x1 - x0) * k / 6, y: y0 + (y1 - y0) * k / 6 }] });
      await sleep(20);
    }
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await sleep(400);
  };
  const selectWords = `(() => { const w = ${$('words')}, tn = document.createTreeWalker(w, NodeFilter.SHOW_TEXT).nextNode();
    const rg = document.createRange(); rg.setStart(tn, 2); rg.setEnd(tn, Math.min(tn.length, 12)); const s = getSelection(); s.removeAllRanges(); s.addRange(rg); })()`;
  const clearSel = `getSelection().removeAllRanges()`;
  for (let i = 0; i < 10; i++) {
    const dir = i % 2 ? -1 : 1;
    // 1) long press (450 ms, past the 300 ms window) then a horizontal drag — what selecting looks like
    let before = await pg();
    await drag(r.x - dir * 60, r.y, r.x + dir * 120, r.y + 3, 450);
    if ((await pg()) === before && !(await ev(p, `${$('deck')}.style.transform`))) out.hold++;
    // 2) a selection exists and the finger lands on its end (the handle) and drags quickly sideways
    await ev(p, selectWords);
    const hr = await ev(p, `(() => { const rs = getSelection().getRangeAt(0).getClientRects(); const l = rs[rs.length - 1]; return { x: l.right, y: (l.top + l.bottom) / 2 }; })()`);
    before = await pg();
    await drag(hr.x, hr.y, hr.x + dir * 150, hr.y + 2);
    if ((await pg()) === before) out.handle++;
    await ev(p, clearSel);
    // 3) the selection appears after the finger already started moving (iOS grows it under the finger)
    before = await pg();
    await drag(r.x - dir * 60, r.y, r.x + dir * 140, r.y + 3, 0, selectWords);
    if ((await pg()) === before && !(await ev(p, `${$('deck')}.style.transform`))) out.midSel++;
    await ev(p, clearSel);
  }
  results.swipeVsSelect = out;
  assert.equal(out.flick, 10, `quick flicks turn pages: ${out.flick}/10`);
  assert.equal(out.hold, 10, `hold then drag never turns: ${10 - out.hold} turned`);
  assert.equal(out.handle, 10, `dragging a selection handle never turns: ${10 - out.handle} turned`);
  assert.equal(out.midSel, 10, `a selection mid-touch vetoes the turn: ${10 - out.midSel} turned`);
  noProblems(p);
  await p.dispose();
});

writeFileSync(ROOT + 'reports/qa/p181/chromium.json', JSON.stringify(results, null, 1));
rec.write();
await B.close(); await fake.stop(); await web.stop();
console.log(failed ? `P181: ${failed} case(s) failed` : 'P181: all cases passed');
process.exit(failed ? 1 : 0);
