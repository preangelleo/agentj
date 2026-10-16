// P340 A group, the real page against the local fake host (no production / QA account, own headless Chromium):
//   #27 hold the gear (a real CDP pointer / touch gesture) → the screenshot is in the tray AND on the clipboard (read back
//       and decoded); refused / missing clipboard → the warn notice with its picture, 「复制图片」 (a fresh tap that succeeds)
//       and 「分享」 (navigator.share({files}) where canShare says yes); Alt+Shift+Y does the same from the keyboard.
//   #22 a Settings save shows only the Noir ✓ circle: no bar under the header.
//   #23 → P349: the manifest's theme_color / background_color = Sooty in both schemes (the nav bar under any page colour).
// zh/en × light/dark × 390 / 360 / 350 (fold outer) / 690 (fold inner) / 1440 → reports/qa/p340/a/.
// AJ_P340_TAG=before + AJ_P340_PUBLIC=<old public/>: the same flow on the old page, shots only (no assertions).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitState, waitFor, rect, shoot, sleep } from './browser.mjs';

const TAG = process.env.AJ_P340_TAG || 'after', BEFORE = TAG === 'before';
const PUB = process.env.AJ_P340_PUBLIC || PUBLIC_DIR;
const out = fileURLToPath(new URL(`../../../reports/qa/p340/a/${TAG}/`, import.meta.url)); mkdirSync(out, { recursive: true });
const manifest = JSON.parse(readFileSync(PUB + '/manifest.webmanifest', 'utf8'));
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: true, session_mode: 'shared' } };
const SIZES = (process.env.AJ_P340_SIZES || '390,360,350,690,1440').split(',').map(Number);
const ev = (p, s) => evaluate(p, s);
const toHex = (c) => { const k = c.startsWith('color(') ? 255 : 1; return '#' + c.replace(/^[^(]*\(\s*(srgb\s+)?/, '').match(/\d*\.?\d+/g).slice(0, 3).map((v) => Math.round(Math.min(255, +v * k)).toString(16).padStart(2, '0')).join(''); };
let web, fake, B, checks = 0;
const ok = (v, m) => { if (!BEFORE) { assert.ok(v, m); checks++; } };
const eq = (a, b, m) => { if (!BEFORE) { assert.deepEqual(a, b, m); checks++; } };
const results = [];

// a real held gesture on the gear: mouse on wide pages, touch on phones (CDP input = trusted events, real user activation)
async function holdGear(p, touch) {
  const r = await rect(p, '#setBtn');
  if (touch) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
    await sleep(700);
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } else {
    await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await sleep(700);
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  }
}
async function tapSel(p, sel, touch) {
  const r = await rect(p, sel);
  if (touch) { await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] }); await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); }
  else { await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 }); await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 }); }
}
const clipPng = (p) => ev(p, `navigator.clipboard.read().then(async items => { for (const it of items) { const ty = it.types.find(x => x === 'image/png'); if (ty) { const b = await it.getType(ty); const bm = await createImageBitmap(b); return {size: b.size, w: bm.width, h: bm.height}; } } return null; }).catch(e => 'ERR ' + e.name)`);
const clearClip = (p) => ev(p, `navigator.clipboard.writeText('-').then(() => true, () => false)`);
const toastState = (p) => ev(p, `(() => { const t = document.getElementById('toast'); return {on: t.classList.contains('on'), kind: t.dataset.kind, text: t.textContent, circle: t.classList.contains('circle'), media: !!t.querySelector('img.nt-media'), btns: [...t.querySelectorAll('.nt-acts button')].map(b => b.textContent), bar: !!document.getElementById('set-status-bar') && !document.getElementById('set-status-bar').hidden, h: t.getBoundingClientRect().height, w: t.getBoundingClientRect().width, right: t.getBoundingClientRect().right}; })()`);

try {
  web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: PUB });
  fake = await startFakeHost(); B = await launch();
  for (const scheme of ['light', 'dark']) for (const lang of ['zh', 'en']) for (const w of SIZES) {
    const touch = w < 800, h = w >= 1440 ? 900 : w > 600 ? 900 : 800, name = `${lang}-${scheme}-${w}`;
    fake.reset(); fake.st.prefs = structuredClone({ ...PREFS, appearance: { language: lang, theme: 'system' } });
    const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch });
    await p.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
    await ev(p, `document.documentElement.dataset.theme=${JSON.stringify(scheme)}`); await sleep(200);
    const row = { name };

    // #23 → P349: the installed app's navigation bar takes the manifest colour — Sooty, whatever the page colour or scheme
    const pg = toHex(await ev(p, `getComputedStyle(document.body).backgroundColor`));
    const want = manifest.theme_color;
    row.page = pg; row.manifest = want; eq(want, '#141414', `${name}: manifest ${want} = Sooty`);
    eq(manifest.background_color, want, `${name}: background_color = theme_color`);

    // #22: a Settings save → only the Noir circle
    await ev(p, `document.getElementById('setBtn').click()`); await waitFor(p, `!document.getElementById('settings').hidden`);
    await ev(p, `document.getElementById('set-speak').scrollIntoView({block:'center'})`); await sleep(100);
    await ev(p, `document.getElementById('set-speak').click()`);
    await waitFor(p, `document.getElementById('toast').classList.contains('circle') && document.getElementById('toast').classList.contains('on')`, 4000).catch(() => {});
    await sleep(120);
    writeFileSync(`${out}settings-saved-${name}.png`, await shoot(p));
    const s1 = await toastState(p); row.saved = s1;
    ok(s1.on && s1.circle && s1.kind === 'success' && s1.text === (lang === 'zh' ? '已保存到电脑' : 'Saved on the computer'), `${name}: the ✓ circle`);
    ok(!s1.bar && !(await ev(p, `!!document.getElementById('set-status-bar')`)), `${name}: no old bar`);
    await sleep(1900);

    // #27 a: hold the gear in Settings → tray + clipboard + the success notice (with its picture)
    await clearClip(p);
    await holdGear(p, touch);
    await waitFor(p, `document.getElementById('settings').hidden && document.querySelectorAll('#tray .chip').length === 1`, 8000);
    await sleep(400);
    writeFileSync(`${out}shot-ok-${name}.png`, await shoot(p));
    const s2 = await toastState(p), c2 = await clipPng(p); row.shotOk = { ...s2, clip: c2 };
    ok(c2 && c2.size > 1000 && c2.w === w, `${name}: the clipboard holds the page PNG (${JSON.stringify(c2)})`);
    ok(s2.on && s2.kind === 'success' && s2.media && /剪贴板|clipboard/.test(s2.text), `${name}: success notice with the picture (${s2.text})`);
    ok(s2.right <= w, `${name}: notice inside the screen`);
    ok(await ev(p, `document.getElementById('settings').hidden`), `${name}: the hold did not also open Settings`);
    await sleep(3200);

    // #27 b: the clipboard refuses → warn + 「复制图片」, which then works on its own tap
    await ev(p, `(() => { const c = navigator.clipboard; window.__realWrite = c.write.bind(c); c.write = () => Promise.reject(new DOMException('test', 'NotAllowedError')); })()`);
    await clearClip(p);
    await ev(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'C',code:'KeyC',altKey:true,shiftKey:true,bubbles:true}))`);
    await waitFor(p, `document.querySelectorAll('#tray .chip').length === 2`, 8000).catch(async (e) => { console.error('DEBUG', JSON.stringify(await toastState(p)), await ev(p, `document.body.dataset.view + ' chips=' + document.querySelectorAll('#tray .chip').length`), p.problems); throw e; }); await sleep(400);
    writeFileSync(`${out}shot-fallback-${name}.png`, await shoot(p));
    const s3 = await toastState(p); row.shotFail = s3;
    ok(s3.on && s3.kind === 'warn' && s3.media && s3.btns.includes(lang === 'zh' ? '复制图片' : 'Copy image'), `${name}: warn + 复制图片 (${JSON.stringify(s3.btns)})`);
    ok(!s3.btns.includes(lang === 'zh' ? '分享' : 'Share'), `${name}: no 分享 where navigator.share is missing (headless Linux)`);
    ok(s3.h <= h / 3, `${name}: notice height ${s3.h} stays small`);
    const tall = await ev(p, `[...document.querySelectorAll('#toast .nt-acts button')].every(b => b.getBoundingClientRect().height >= 44)`); ok(tall, `${name}: buttons ≥ 44 px`);
    if (!BEFORE) {
      await ev(p, `navigator.clipboard.write = window.__realWrite`);
      await tapSel(p, '#toast .nt-acts button', touch);
      await waitFor(p, `document.getElementById('toast').dataset.kind === 'success'`, 4000);
      const c3 = await clipPng(p); ok(c3 && c3.size > 1000, `${name}: 复制图片 copied (${JSON.stringify(c3)})`);
      await sleep(300); writeFileSync(`${out}shot-copied-${name}.png`, await shoot(p));
    }

    // #27 c: no ClipboardItem but a share sheet (an old WebView / Firefox-like) → 「分享」 alone, and it shares the PNG file
    if (!BEFORE) {
      await ev(p, `window.__shared = []; window.ClipboardItem = undefined; navigator.canShare = (d) => !!(d && d.files && d.files.length); navigator.share = async (d) => { window.__shared.push(d.files.map(f => [f.name, f.type, f.size])); };`);
      await ev(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'C',code:'KeyC',altKey:true,shiftKey:true,bubbles:true}))`);
      await waitFor(p, `document.querySelectorAll('#tray .chip').length === 3`, 8000); await sleep(400);
      const s4 = await toastState(p); row.shotShare = s4;
      ok(s4.kind === 'warn' && JSON.stringify(s4.btns) === JSON.stringify([lang === 'zh' ? '分享' : 'Share']), `${name}: only 分享 (${JSON.stringify(s4.btns)})`);
      ok(/不让网页|does not let pages/.test(s4.text), `${name}: says the browser cannot`);
      writeFileSync(`${out}shot-share-${name}.png`, await shoot(p));
      await tapSel(p, '#toast .nt-acts button', touch); await sleep(200);
      const shared = await ev(p, `window.__shared`);
      ok(shared.length === 1 && shared[0][0][1] === 'image/png' && shared[0][0][2] > 1000, `${name}: shared the PNG (${JSON.stringify(shared)})`);
    }
    ok((await ev(p, `document.getElementById('input').value`)) === '', `${name}: nothing typed`);
    eq(fake.log.filter((m) => m.t === 'say').length, 0, `${name}: never auto-sent`);
    eq(p.problems, [], `${name}: no page errors`);
    eq(p.offsite, [], `${name}: no off-site requests`);
    results.push(row);
    await p.dispose();
  }
  writeFileSync(out + 'results.json', JSON.stringify({ tag: TAG, scope: 'headless Chromium + CDP input, local fake host; not Safari / a real phone', checks, cases: results }, null, 2) + '\n');
  console.log(`P340-A ${TAG} PASS ${checks} checks, ${results.length} combos; shots → ${out}`);
} finally { await Promise.allSettled([B?.close(), fake?.stop(), web?.stop()]); }
