// P205 item 5 — the recording card in WebKit (iPhone 390×844, 3×): WebKit on Linux has no audio stack, so no take can run;
// this renders the card's two looks (held, locked) on the paired page and measures them: a square, round circle
// in the middle, the icons under it (P346 removed the dimmed look; its blur is p346_rec_blur_webkit.mjs). The behaviour itself is p205_rec.mjs (Chromium, fake microphone).
// Shots → reports/qa/p205/rec-webkit-*.png. Not in the parity run (needs the owner's Playwright + PLAYWRIGHT_BROWSERS_PATH).
// Usage: node web/test/p205_rec_webkit.mjs
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const OUT = fileURLToPath(new URL('../../../reports/qa/p205/', import.meta.url)); mkdirSync(OUT, { recursive: true });
const web = await startWebServer(), fake = await startFakeHost();
const B = await pw.webkit.launch();
const results = {};
try {
  const ctx = await B.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 });
  const p = await ctx.newPage();
  const problems = []; p.on('pageerror', (e) => problems.push(String(e)));
  await p.addInitScript(() => { window.AudioContext = undefined; window.webkitAudioContext = undefined;
    window.Audio = function () { return { preload: '', volume: 1, currentTime: 0, play: () => Promise.resolve(), pause() {} }; }; });
  await p.goto(fake.newPairing(web.url + '?lang=zh'));
  await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 20000 });
  await fake.approve();
  await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 20000 });
  const look = (cls, hint, act, note) => p.evaluate(([cls, hint, act, note]) => {
    const b = document.getElementById('ptt'); b.hidden = false; b.className = 'ptt ' + cls;
    document.getElementById('pttTime').textContent = '0:12'; document.getElementById('pttTime').dataset.lock = '已锁定';
    document.getElementById('pttHint').textContent = hint;
    const s = document.getElementById('pttSeg'); s.hidden = !act; s.textContent = '第 1/10 段 · 最长 200 分钟';
    document.getElementById('pttAct').hidden = !act;
    const n = document.getElementById('pttNote'); n.hidden = !note; n.textContent = note;
    document.querySelector('#ptt .pttc').style.setProperty('--lv', '.6'); document.querySelector('#ptt .pttc').dataset.live = '1';
    const r = document.querySelector('#ptt .pttc').getBoundingClientRect(), cs = getComputedStyle(document.querySelector('#ptt .pttc'));
    const a = document.getElementById('pttAct').getBoundingClientRect();
    return { w: r.width, h: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2, vw: innerWidth, vh: innerHeight, radius: cs.borderRadius,
      actTop: a.top, circleBottom: r.bottom, bg: getComputedStyle(b).backgroundColor };
  }, [cls, hint, act, note]);
  for (const [name, cls, hint, act, note] of [
    ['hold', '', '松开转成文字 · 上滑取消', false, ''],
    ['lock', 'lock', '再点一下结束 · 上滑取消', true, '请别按电源键锁屏，录音会继续'],
  ]) {
    const g = await look(cls, hint, act, note);
    await p.waitForTimeout(400);
    assert.ok(Math.abs(g.w - g.h) < 1 && g.w > 200, `${name}: square ${g.w}×${g.h}`);
    assert.ok(parseFloat(g.radius) >= g.w / 2 - 1 || /50%/.test(g.radius), `${name}: round ${g.radius}`);
    assert.ok(Math.abs(g.cx - g.vw / 2) < 2, `${name}: centred`);
    if (act) assert.ok(g.actTop > g.circleBottom, `${name}: icons under the circle`);
    await p.screenshot({ path: OUT + `rec-webkit-${name}-390.png` });
    results['webkit-' + name] = 'PASS';
  }
  assert.deepEqual(problems, []);
} finally { await B.close(); await fake.stop(); await web.stop(); }
console.log(JSON.stringify(results));
if (Object.keys(results).length !== 2) { console.log('FAILED'); process.exit(1); }
console.log('ALL PASS');
