#!/usr/bin/env node
// P150 追加 9 → P273 (2026-10-14 19:10, 「在任何页面都不出现这种下拉状态条」): the long Bash command in an approval —
// the case P150 painted in the state colours — now draws NO scrollbar at all in any state, and still scrolls (the case
// id stays: same place, the rule replaced). Classic bars are switched on (launch scrollbars:true), so a drawn bar would
// take layout width (offsetWidth − clientWidth) and show in the pixels at the scroller's right edge. Real page + local fake host, independent headless Chromium (never :9222).
// An open approval holds the page on waiting; idle / working set body[data-status] directly to paint the same sheet green /
// blue (the CSS keys on that attribute only). Shots → reports/qa/p150/scrollbar/<state>.png (+ a crop of the bar).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';
import { inflateSync } from 'node:zlib';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p150/scrollbar');
const CMD = Array.from({ length: 40 }, (_, i) => `echo "step ${i}: rebuild the index and copy it to exports/2026-10-12/part-${i}.json"`).join('\n');
// The scroller that holds the command: the innermost element in the approval whose content overflows vertically.
const FIND = `(() => { for (const n of [...document.querySelectorAll('body *')].reverse()) { const cs = getComputedStyle(n);
  if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 20 && n.clientHeight > 40 && n.offsetParent && n.textContent.includes('step 39')) {
    n.dataset.p150sb = '1'; const r = n.getBoundingClientRect();
    return { color: cs.scrollbarColor, width: cs.scrollbarWidth, bar: n.offsetWidth - n.clientWidth - (parseFloat(cs.borderLeftWidth) || 0) - (parseFloat(cs.borderRightWidth) || 0), top: n.scrollTop, x: r.left, y: r.top, w: r.width, h: r.height,
      thumb: [n.scrollTop / n.scrollHeight * r.height, n.clientHeight / n.scrollHeight * r.height] }; } } return null; })()`;
// One device pixel of the page → [r, g, b] (1×1 PNG: one filter byte + RGBA after the IDAT inflate).
async function px(p, x, y) {
  const b = Buffer.from((await p.send('Page.captureScreenshot', { format: 'png', clip: { x, y, width: 1, height: 1, scale: 1 } })).data, 'base64');
  const idat = []; for (let o = 8; o < b.length;) { const n = b.readUInt32BE(o), t = b.toString('ascii', o + 4, o + 8); if (t === 'IDAT') idat.push(b.subarray(o + 8, o + 8 + n)); o += 12 + n; }
  const raw = inflateSync(Buffer.concat(idat)); return [raw[1], raw[2], raw[3]];
}

export async function runScrollbar() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p150_scrollbar.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch({ scrollbars: true });
  let ok = false;
  try {
    const p = await newPage(B, 390, 844, 'light', { allow: [web.url, fake.relay + '/'] });
    await p.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: false });  // desktop draws classic bars (mobile overlays hide them) — Leo's case is the desktop window
    await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
    await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'AgentJ-Father' });
    await fake.ask({ id: randomBytes(16).toString('hex'), tool: 'Bash', summary: CMD, ttl: 120, cat: ['delete'], why: 'rm -rf' });
    await waitFor(p, `document.body.textContent.includes('step 39')`, 8000);
    // P154: a long command now folds to 2 lines; unfold it (展开全部) — the opened command is what scrolls.
    await evaluate(p, `document.getElementById('cmdMore').click()`); await sleep(200);
    const seen = {};
    for (const s of ['idle', 'working', 'waiting']) {
      // An open approval keeps the page orange; the green / blue shots paint the same sheet on those states' colours.
      await evaluate(p, `document.body.dataset.status = '${s}'`); await sleep(500);
      const r = await evaluate(p, FIND);
      assert.ok(r, `${s}: the long command scrolls inside the approval`);
      // P251 supersedes the palette: no rendered bar, while the exact same command still scrolls.
      assert.equal(r.width,'none');
      const result=await evaluate(p,`(() => {const n=document.querySelector('[data-p150sb]');n.scrollTop=100;return {top:n.scrollTop,display:getComputedStyle(n,'::-webkit-scrollbar').display};})()`);
      assert.ok(result.top>0);assert.equal(result.display,'none');
      const q=await evaluate(p,FIND);seen[s]=result;
      const full = await p.send('Page.captureScreenshot', { format: 'png' });
      writeFileSync(join(OUT, `${s}.png`), Buffer.from(full.data, 'base64'));
      const crop = await p.send('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, q.x + q.w - 120), y: q.y, width: 120, height: Math.min(q.h, 300), scale: 3 } });
      writeFileSync(join(OUT, `${s}-bar.png`), Buffer.from(crop.data, 'base64'));
    }
    assert.equal(Object.keys(seen).length,3,'all states hide bars and remain scrollable');
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host; not a physical phone', colors: seen, complete: true }, null, 2) + '\n');
    console.log('p150-scrollbar: 3 states pass', JSON.stringify(seen));
    ok = true;
  } finally { rec.record('scrollbar-palette', ok); await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runScrollbar();
