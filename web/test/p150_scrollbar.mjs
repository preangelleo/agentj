#!/usr/bin/env node
// P150 追加 9: every scrollbar the page still draws (here: a long Bash command in an approval) is thin, round and in the
// colours of the current state — the thumb a light shade of green / blue / orange, the track a faint veil of it; never
// the browser's grey. Real page + local fake host, independent headless Chromium (never :9222).
// An open approval holds the page on waiting; idle / working set body[data-status] directly to paint the same sheet green /
// blue (the CSS keys on that attribute only). Shots → reports/qa/p150/scrollbar/<state>.png (+ a crop of the bar).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';
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
    return { color: cs.scrollbarColor, width: cs.scrollbarWidth, x: r.left, y: r.top, w: r.width, h: r.height }; } } return null; })()`;

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
    const seen = {};
    for (const s of ['idle', 'working', 'waiting']) {
      // An open approval keeps the page orange; the green / blue shots paint the same sheet on those states' colours.
      await evaluate(p, `document.body.dataset.status = '${s}'`); await sleep(500);
      const r = await evaluate(p, FIND);
      assert.ok(r, `${s}: the long command scrolls inside the approval`);
      assert.equal(r.width, 'thin', `${s}: thin bar`);
      const cols = r.color.match(/(?:rgba?|color|oklab)\([^)]*\)/g) || [];
      assert.equal(cols.length, 2, `${s}: scrollbar-color sets thumb + track (${r.color})`);
      assert.ok(!/^auto$/.test(r.color), `${s}: not the browser default`);
      seen[s] = r.color;
      await evaluate(p, `(() => { const n = document.querySelector('[data-p150sb]'); n.scrollTop = n.scrollHeight / 3; })()`);
      await sleep(150);
      const full = await p.send('Page.captureScreenshot', { format: 'png' });
      writeFileSync(join(OUT, `${s}.png`), Buffer.from(full.data, 'base64'));
      const crop = await p.send('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, r.x + r.w - 120), y: r.y, width: 120, height: Math.min(r.h, 300), scale: 3 } });
      writeFileSync(join(OUT, `${s}-bar.png`), Buffer.from(crop.data, 'base64'));
    }
    assert.ok(new Set(Object.values(seen)).size === 3, 'the bar follows the state colour: ' + JSON.stringify(seen));
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host; not a physical phone', colors: seen, complete: true }, null, 2) + '\n');
    console.log('p150-scrollbar: 3 states pass', JSON.stringify(seen));
    ok = true;
  } finally { rec.record('scrollbar-palette', ok); await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runScrollbar();
