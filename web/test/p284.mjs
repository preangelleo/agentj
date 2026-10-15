#!/usr/bin/env node
// P284 (owner, 2026-10-14; rework 22:5x 「有点过度设计了」): only two Markdown marks change colour — the quote's left bar
// and the list dots / numbers: Peachy Feeling #d97757, on the orange waiting page Emerald Oasis #629887. Everything else
// is the base build's. Real page + local fake host, own headless Chromium (never :9222). Five state cards (idle / working /
// waiting / question / offline) × light / dark + the Noir reader: the marks' computed colour is asserted, the contrast
// against what is really behind them (translucent layers composited down) is measured and recorded, and the other
// marks (rule, code, link, table) are asserted equal to the base build. Shots → reports/qa/p284/ (<theme>-<state>.png,
// reader.png, grid.png). `--before` serves the base build's app.css (merge-base with main; evidence only).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p284');
const BASE = execFileSync('git', ['merge-base', 'HEAD', 'main'], { cwd: ROOT }).toString().trim();
const BEFORE = process.argv.includes('--before');
const PEACHY = [217, 119, 87], EMERALD = [98, 152, 135];
const REPLY = [
  '## 本周进度',
  '> 引用：这条竖线是 Peachy Feeling。',
  '- 圆点列表第一项，带 `inline code`',
  '- 第二项，一个[链接](https://example.com)',
  '',
  '1. 有序第一步',
  '2. 有序第二步',
  '',
  '---',
  '',
  '| 项目 | 状态 |',
  '|---|---|',
  '| P284 | 完成 |',
  '',
  '```',
  'agentj doctor --reconnect',
  '```',
].join('\n');
const STATES = [['idle', 'idle', ''], ['working', 'working', ''], ['waiting', 'waiting', ''], ['question', 'waiting', 'question'], ['offline', 'unknown', '']];

// In the page: every mark's colour and the colour really behind it, as sRGB 0–255 triples.
const MEASURE = `(() => {
  const parse = (s) => { s = String(s).trim();
    let m = s.match(/^rgba?\\(([^)]+)\\)$/);
    if (m) { const v = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1]; }
    m = s.match(/^color\\(srgb ([^)]+)\\)$/);
    if (m) { const v = m[1].split(/[ \\/]+/).filter(Boolean).map(Number); return [v[0] * 255, v[1] * 255, v[2] * 255, v.length > 3 ? v[3] : 1]; }
    throw new Error('colour ' + s); };
  const over = (top, bot) => { const a = top[3]; return [0, 1, 2].map((i) => top[i] * a + bot[i] * (1 - a)).concat(1); };
  // the opaque colour behind node n (n's own background included unless own=false)
  const behind = (n, own = true) => { const layers = []; let e = own ? n : n.parentElement;
    for (; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c[3] > 0) layers.push(c); if (c[3] >= 1) break; }
    let c = layers.length && layers[layers.length - 1][3] >= 1 ? layers.pop() : [0, 0, 0, 1];
    while (layers.length) c = over(layers.pop(), c); return c; };
  const ink = (c, bg) => over(c, bg);
  const root = document.getElementById('rd').hidden ? document.getElementById('words') : document.getElementById('rdWords');
  const q = (s) => root.querySelector(s);
  const bq = q('blockquote'), ul = q('ul>li'), ol = q('ol>li'), hr = q('hr'), code = q('p code, li code'), a = q('a'), th = q('th'), td = q('td'), pre = q('.codeblock');
  const r = {};
  r.quoteBar = [ink(parse(getComputedStyle(bq).borderLeftColor), behind(bq, false)), behind(bq, false)];
  r.bullet = [ink(parse(getComputedStyle(ul, '::marker').color), behind(ul)), behind(ul)];
  r.number = [ink(parse(getComputedStyle(ol, '::marker').color), behind(ol)), behind(ol)];
  r.raw = { quoteBar: parse(getComputedStyle(bq).borderLeftColor), bullet: parse(getComputedStyle(ul, '::marker').color),
    number: parse(getComputedStyle(ol, '::marker').color) };
  const cs = (n, ...ks) => ks.map((k) => getComputedStyle(n)[k]).join(' | ');
  r.others = { hr: cs(hr, 'borderTopColor', 'borderTopStyle'), code: cs(code, 'color', 'backgroundColor'), a: cs(a, 'color', 'textDecorationColor'),
    th: cs(th, 'backgroundColor', 'borderBottomColor'), td: cs(td, 'borderTopColor'), pre: cs(pre, 'backgroundColor', 'borderTopWidth'),
    bq: cs(bq, 'backgroundColor', 'color', 'paddingLeft'), words: cs(root.querySelector('p') || root, 'color') };
  return r; })()`;
const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => m - n); return (y + 0.05) / (x + 0.05); };
const hex = (c) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');

export async function runP284() {
  mkdirSync(OUT, { recursive: true });
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  const shots = []; const table = {};
  try {
    const p = await newPage(B, 390, 1000, 'light', { allow: [web.url, fake.relay + '/'] });
    await p.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 1000, deviceScaleFactor: 2, mobile: true });
    if (BEFORE) {
      const css = execFileSync('git', ['show', `${BASE}:web/public/app.css`], { cwd: ROOT });
      await p.send('Fetch.enable', { patterns: [{ urlPattern: '*app.css*' }] });
      p.on((m) => { if (m.method === 'Fetch.requestPaused') p.send('Fetch.fulfillRequest', { requestId: m.params.requestId, responseCode: 200,
        responseHeaders: [{ name: 'Content-Type', value: 'text/css' }], body: css.toString('base64') }); });
    }
    await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ-Father' });
    await fake.addTurn({ k: 'phone', dev: null, text: '本周进度怎么样' }, REPLY, 'done');
    await waitFor(p, `!!document.querySelector('#words blockquote') && !!document.querySelector('#words table')`, 10000);
    for (const theme of ['light', 'dark']) {
      await evaluate(p, `document.documentElement.setAttribute('data-theme', '${theme}')`);
      for (const [name, s, kind] of STATES) {
        await evaluate(p, `(() => { const b = document.body; b.dataset.status = '${s}'; b.dataset.conn = 'on';
          if ('${kind}') b.dataset.kind = '${kind}'; else delete b.dataset.kind; })()`);
        await sleep(350);
        const cell = `${theme}-${name}`;
        table[cell] = await evaluate(p, MEASURE);
        const r = await evaluate(p, `(() => { const r = document.querySelector('.rm').getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
        const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip: { x: r.x, y: r.y, width: r.w, height: r.h, scale: 2 } });
        writeFileSync(join(OUT, `${BEFORE ? 'before-' : ''}${cell}.png`), Buffer.from(data, 'base64'));
        shots.push([cell, data]);
      }
    }
    await evaluate(p, `document.getElementById('readBtn').click()`);
    await waitFor(p, `!document.getElementById('rd').hidden && !!document.querySelector('#rdWords blockquote')`, 8000);
    await sleep(500);
    table.reader = await evaluate(p, MEASURE);
    const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip: { x: 0, y: 0, width: 390, height: 1000, scale: 2 } });
    writeFileSync(join(OUT, `${BEFORE ? 'before-' : ''}reader.png`), Buffer.from(data, 'base64'));
    shots.push(['noir-reader', data]);
    assert.deepEqual(p.problems, [], 'no page errors');

    // the grid: two rows of five state cards (light, dark) + the reader
    const g = await newPage(B, 2400, 1400, 'light');
    const cells = shots.map(([n, d]) => `<figure><img src="data:image/png;base64,${d}"><figcaption>${n}</figcaption></figure>`).join('');
    const html = `<!doctype html><meta charset=utf-8><style>body{margin:0;padding:24px;background:#faf9f5;font:600 20px system-ui;color:#141414}
      h1{font-size:26px;margin:0 0 16px}.g{display:grid;grid-template-columns:repeat(6,1fr);gap:16px;align-items:start}
      figure{margin:0}img{width:100%;display:block;border-radius:12px}figcaption{margin-top:6px;text-align:center}
      figure:nth-child(11){grid-column:6;grid-row:1/3}</style>
      <h1>P284 ${BEFORE ? '改前' : '改后'} · 引用竖线 + 列表符号（上：浅色 · 下：深色 · 右：全屏阅读）</h1><div class=g>${cells}</div>`;
    const { frameTree } = await g.send('Page.getFrameTree');
    await g.send('Page.setDocumentContent', { frameId: frameTree.frame.id, html });
    await sleep(800);
    const h = await evaluate(g, `document.documentElement.scrollHeight`);
    const grid = await g.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: 2400, height: h, scale: 1 } });
    writeFileSync(join(OUT, BEFORE ? 'grid-before.png' : 'grid.png'), Buffer.from(grid.data, 'base64'));
    if (BEFORE) { writeFileSync(join(OUT, 'before.json'), JSON.stringify(table, null, 2) + '\n'); console.log('p284 --before: shots written'); return; }

    // the measurements
    let before = null; try { before = JSON.parse(readFileSync(join(OUT, 'before.json'), 'utf8')); } catch {}
    const rows = []; let worst = { v: 99 };
    for (const [cell, m] of Object.entries(table)) {
      const want = /-waiting$/.test(cell) ? EMERALD : PEACHY;
      const out = {};
      for (const k of ['quoteBar', 'bullet', 'number']) {
        assert.deepEqual(m.raw[k].slice(0, 3).map(Math.round), want, `${cell} ${k}: ${hex(m.raw[k])}`);
        const [fg, bg] = m[k]; const v = ratio(fg, bg); out[k] = { fg: hex(fg), bg: hex(bg), ratio: +v.toFixed(2) };
        if (v < worst.v) worst = { v: +v.toFixed(2), cell, k };
      }
      if (before) assert.deepEqual(m.others, before[cell].others, `${cell}: rule / code / link / table / quote fill / words unchanged`);
      rows.push([cell, out]);
    }
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host; not a physical phone',
      othersComparedToBase: !!before, worstMark: worst, cells: Object.fromEntries(rows) }, null, 2) + '\n');
    console.log(`p284: ${rows.length} surfaces pass (others vs base: ${!!before}); lowest mark contrast ${worst.v}:1 (${worst.cell} ${worst.k})`);
  } finally { await B.close(); await fake.stop(); await web.stop(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP284();
