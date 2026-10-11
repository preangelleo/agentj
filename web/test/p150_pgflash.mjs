#!/usr/bin/env node
// P150 追加 5: the flashing page capsule (new reply while reading an older page) — before (near-black ring) / after
// (Lotion veil) on the three state colours. → reports/qa/p150/pgflash/<state>-<before|after>.png (+ a zoomed crop)
// Independent headless Chromium (test/browser.mjs), never :9222.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
import { LONG } from './density_shots.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p150/pgflash/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const W = 390, H = 844;
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
try {
  const p = await newPage(B, W, H, 'light', { allow: [web.url, fake.relay + '/'], touch: true });
  await p.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 2, mobile: true });
  await navigate(p, fake.newPairing(web.url));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ-Father' });
  await fake.addTurn({ k: 'phone', dev: 'other', text: '帮我对一下三家供应商的报价' }, LONG[0]);
  await fake.addTurn({ k: 'phone', dev: 'other', text: '做成表格，顺便看看退货' }, LONG[2]);
  await waitFor(p, `document.getElementById('pg').textContent.trim().startsWith('2')`);
  await evaluate(p, `document.getElementById('pgPrev').click()`);
  await waitFor(p, `document.getElementById('pg').textContent.trim().startsWith('1')`);
  await fake.addTurn({ k: 'agent', text: '', via: 'loop' }, '巡检完了，一切正常。');
  await waitFor(p, `document.getElementById('pgCorner').dataset.unread === '1'`);
  for (const s of ['idle', 'working', 'waiting']) {
    await fake.send({ t: 'status', s, agent: 'claude', name: 'AgentJ-Father' });
    await waitFor(p, `document.body.dataset.status === '${s}'`); await sleep(500);
    for (const tag of ['before', 'after']) {
      await evaluate(p, `(() => { let st = document.getElementById('p150old'); if (!st) { st = document.createElement('style'); st.id = 'p150old'; document.head.append(st); }
        st.textContent = ${JSON.stringify(tag)} === 'before' ? '.pg-corner[data-unread="1"] .pg{border-color:var(--sooty)!important}' : '';
        for (const a of document.getAnimations()) { a.pause(); a.currentTime = 300; } })()`);
      await sleep(100);
      const shot = async (clip, f) => { const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip: { ...clip, scale: 1 } }); writeFileSync(join(OUT, f), Buffer.from(data, 'base64')); };
      await shot({ x: 0, y: 0, width: W, height: H }, `${s}-${tag}.png`);
      const r = await evaluate(p, `(() => { const b = document.getElementById('om').getBoundingClientRect(); return { x: b.left, y: b.top, width: b.width, height: Math.min(b.height, 120) }; })()`);
      await shot(r, `${s}-${tag}-zoom.png`);
      console.log('shot', s, tag);
    }
  }
  // One comparison board: rows = idle / working / waiting, columns = before / after (zoomed).
  const z = (s, t) => join(OUT, `${s}-${t}-zoom.png`);
  execFileSync('magick', ['(', z('idle', 'before'), z('idle', 'after'), '+append', ')', '(', z('working', 'before'), z('working', 'after'), '+append', ')',
    '(', z('waiting', 'before'), z('waiting', 'after'), '+append', ')', '-append', join(OUT, 'board-before-after.png')]);
  await p.dispose();
} finally { await B.close(); await fake.stop(); await web.stop(); }
process.exit(0);
