#!/usr/bin/env node
// P196 · the full-screen preview is its own layer: ← / → (desktop) and a sideways swipe (touch) step through the files of
// the SAME reply that can be previewed, in reading order (picture, PDF, Markdown, CSV, audio, video; a pptx is skipped),
// no wrap-around (an edge note), a player paused when it goes; the chat's own shortcuts and the deck's card swipe never
// see a key or a swipe while it is open; a zoomed picture pans instead of stepping. Real page against the fake host, own
// headless Chromium (never :9222), the Worker's real CSP.
//   node web/test/p196.mjs        (screenshots + a screencast → reports/qa/p196/)
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { deflateSync, crc32 } from 'node:zlib';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, swipe, tap, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P196_SHOTS || join(ROOT, 'reports/qa/p196');
mkdirSync(OUT, { recursive: true });
const FIX = (n) => readFileSync(new URL(`./fixtures/${n}`, import.meta.url));
const sha = (b) => createHash('sha256').update(b).digest('hex');
const CHUNK = 45056, WINDOW = 8;

function png(w, h, f) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const [r, g, b] = f(x, y); raw.set([r, g, b], y * (w * 3 + 1) + 1 + x * 3); }
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
function wav(secs = 4, rate = 8000) {
  const n = secs * rate, data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / rate)), i * 2);
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVE', 8); h.write('fmt ', 12); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write('data', 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}
const id = (c) => c.repeat(22);
const BYTES = new Map();
const item = (mid, name, mime, kind, bytes) => { BYTES.set(mid, bytes); return { mid, name, mime, kind, bytes: bytes.length, sha256: sha(bytes), ref: 'out/' + name }; };
const grad = (a, b) => (x, y) => [a[0] + (x * (b[0] - a[0]) / 480) | 0, a[1] + (y * (b[1] - a[1]) / 270) | 0, a[2]];
// one reply, eight files; the reply places the two pictures in its words, the rest go to the strip under it (reading order:
// pic1, pic2, then the strip in the page's order). deck.pptx cannot be previewed → skipped by ← / →.
const FILES = [
  item(id('A'), 'chart.png', 'image/png', 'image', png(480, 270, grad([40, 90, 200], [240, 210, 60]))),
  item(id('B'), 'photo.png', 'image/png', 'image', png(480, 270, grad([200, 60, 60], [60, 200, 120]))),
  item(id('P'), 'report.pdf', 'application/pdf', 'pdf', FIX('p183/report.pdf')),
  item(id('S'), 'deck.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'file', FIX('p183/deck.pptx')),
  item(id('M'), 'notes.md', 'text/markdown', 'file', Buffer.from('# 会议纪要\n\n- 全屏预览里左右键切换文件\n- 左右滑也一样\n')),
  item(id('C'), 'wide.csv', 'text/csv', 'file', Buffer.from(['a,' + Array.from({ length: 40 }, (_, i) => 'column_' + i).join(','), '1,' + Array.from({ length: 40 }, (_, i) => 'value_' + i).join(',')].join('\n'))),
  item(id('W'), 'voice.wav', 'audio/wav', 'audio', wav()),
  item(id('V'), 'clip.webm', 'video/webm', 'video', FIX('p196/clip.webm')),
];
const NAV = ['chart.png', 'photo.png', 'report.pdf', 'notes.md', 'wide.csv', 'voice.wav', 'clip.webm'];
const REPLY = ['董事长，两张图在这里：', '', '![图一](out/chart.png)', '', '![图二](out/photo.png)', '', '其余文件见下方卡片。'].join('\n');
const OTHER = [item(id('Z'), 'older.png', 'image/png', 'image', png(480, 270, grad([0, 0, 0], [255, 255, 255])))];

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const rec = parityRecorder('web/test/p196.mjs');
const ev = (p, s) => evaluate(p, s);

function serve(st) {
  st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const b = BYTES.get(m.mid);
    if (!b) { await send({ t: 'media_err', mid: m.mid, why: 'gone' }); return true; }
    for (let i = 0; i < WINDOW; i++) {
      const o = m.o + i * CHUNK;
      if (o >= b.length) break;
      const d = b.subarray(o, Math.min(b.length, o + CHUNK));
      await send({ t: 'media_chunk', mid: m.mid, o, d: Buffer.from(d).toString('base64url'), last: o + d.length >= b.length });
    }
    return true;
  };
}
async function paired(w, h, scheme = 'light') {
  fake.reset();
  serve(fake.st);
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  // an older reply with its own picture first, then the reply under test: ← on the chat screen would go back to it
  const t0 = await fake.addTurn({ k: 'phone', dev: null, text: '旧的一条' }, '![旧图](out/older.png)', 'done');
  await fake.updateTurn(t0.id, { media: OTHER, media_skip: [] });
  const t1 = await fake.addTurn({ k: 'phone', dev: null, text: '发几个测试文件' }, REPLY, 'done');
  await fake.updateTurn(t1.id, { media: FILES, media_skip: [] });
  await waitFor(p, `document.querySelectorAll('.mcard').length === ${FILES.length}`, 15000);
  // The deck snapshot must follow thumbnail loading: loading labels are not reply navigation.
  await ev(p, `document.querySelectorAll('#words .mcard[data-kind="image"]').forEach(c => c.querySelector('.mtap')?.click())`);
  await waitFor(p, `(() => { const c = [...document.querySelectorAll('#words .mcard[data-kind="image"]')]; return c.length === 2 && c.every(n => n.dataset.state === 'ready'); })()`, 15000);
  return p;
}
const ONLY = process.env.P196_ONLY ? new RegExp(process.env.P196_ONLY) : null;
async function C(name, fn) {
  if (ONLY && !ONLY.test(name)) return;
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
const card = (mid) => `document.querySelector('#words .mcard[data-mid="${mid}"], #mstrip .mcard[data-mid="${mid}"]')`;
// P573 (#386): Markdown / text / code open in the reply's full-screen reader (#rd, file mode) — the same ← / → / swipe order,
// no viewer bar (so no 「n / N」 count there): state() / at() read the reader for those
const state = (p) => ev(p, `(() => { const rd = document.getElementById('rd'); if (!rd.hidden && rd.dataset.file === '1') return { open: true, mode: 'reader', title: document.getElementById('rdFileName').textContent, count: 'reader',
  page: document.getElementById('pg').textContent, words: document.getElementById('words').textContent.slice(0, 40) };
  const v = document.querySelector('.mview'); return { open: !!v && !v.hidden, mode: v?.dataset.mode, title: v ? document.querySelector('.mvtitle').textContent : '',
  count: !v || document.querySelector('.mvcount').hidden ? '' : document.querySelector('.mvcount').textContent, page: document.getElementById('pg').textContent, words: document.getElementById('words').textContent.slice(0, 40) }; })()`);
const at = (p, name) => waitFor(p, `(document.querySelector('.mview:not([hidden]) .mvtitle')?.textContent === ${JSON.stringify(name)} && !document.querySelector('.mview .mvloading'))
  || (!document.getElementById('rd').hidden && document.getElementById('rd').dataset.file === '1' && document.getElementById('rdFileName').textContent === ${JSON.stringify(name)})`, 15000);
const center = async (p) => ev(p, `(() => { const r = document.querySelector('.mstage').getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width }; })()`);
async function openImage(p, mid) {
  await ev(p, `${card(mid)}.scrollIntoView({ block: 'center' })`);
  await waitFor(p, `${card(mid)}.dataset.state === 'ready'`, 15000);
  await ev(p, `${card(mid)}.querySelector('img').click()`);
  await waitFor(p, `!document.querySelector('.mview').hidden && document.querySelector('.mview').dataset.mode === 'image'`);
}

/** A CDP screencast → mp4 (ffmpeg) for the review set; frames held for their real duration. */
async function screencast(p, file, fn) {
  const dir = join(OUT, '.frames-' + Date.now());
  mkdirSync(dir, { recursive: true });
  const frames = [];
  p.on((m) => {
    if (m.method !== 'Page.screencastFrame') return;
    frames.push({ data: m.params.data, ts: m.params.metadata.timestamp });
    p.send('Page.screencastFrameAck', { sessionId: m.params.sessionId }).catch(() => {});
  });
  await p.send('Page.startScreencast', { format: 'jpeg', quality: 70, everyNthFrame: 1 });
  try { await fn(); await sleep(600); } finally { await p.send('Page.stopScreencast').catch(() => {}); }
  if (frames.length < 2) return 0;
  let list = '';
  frames.forEach((f, i) => {
    const n = join(dir, `f${String(i).padStart(4, '0')}.jpg`);
    writeFileSync(n, Buffer.from(f.data, 'base64'));
    const dur = i + 1 < frames.length ? Math.max(0.02, frames[i + 1].ts - f.ts) : 0.5;
    list += `file '${n}'\nduration ${dur.toFixed(3)}\n`;
  });
  list += `file '${join(dir, `f${String(frames.length - 1).padStart(4, '0')}.jpg`)}'\n`;
  writeFileSync(join(dir, 'list.txt'), list);
  const r = spawnSync('ffmpeg', ['-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', join(dir, 'list.txt'), '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2,fps=30', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', join(OUT, file)]);
  rmSync(dir, { recursive: true, force: true });
  if (r.status !== 0) throw new Error('ffmpeg: ' + r.stderr);
  return frames.length;
}

try {
  await C('p196-keys: ← / → step through the same reply\'s previewable files in reading order; no wrap; the chat behind never moves', async () => {
    const p = await paired(1280, 860, 'dark');
    try {
      const before = await state(p);
      await openImage(p, id('A'));
      let s = await state(p);
      assert.equal(s.title, 'chart.png'); assert.equal(s.count, `1 / ${NAV.length}`);
      writeFileSync(join(OUT, 'p196-desktop-image-1.png'), await shoot(p));
      // ← on the first: an edge note, nothing moves (on the chat screen ← would have gone back to the older reply)
      await key(p, 'ArrowLeft', { code: 'ArrowLeft' });
      await waitFor(p, `document.querySelector('.mvhint').classList.contains('on')`);
      assert.equal(await ev(p, `document.querySelector('.mvhint').textContent`), '已经是第一个了');
      writeFileSync(join(OUT, 'p196-desktop-edge-first.png'), await shoot(p));
      s = await state(p);
      assert.equal(s.title, 'chart.png'); assert.equal(s.page, before.page, 'the reply card behind did not turn');
      // → through every file; the pptx is skipped
      const seen = ['chart.png'];
      for (let i = 1; i < NAV.length; i++) {
        await key(p, 'ArrowRight', { code: 'ArrowRight' });
        await at(p, NAV[i]);
        s = await state(p);
        if (s.count !== 'reader') assert.equal(s.count, `${i + 1} / ${NAV.length}`, NAV[i]);
        seen.push(s.title);
        if (NAV[i] === 'report.pdf') { await waitFor(p, `!!document.querySelector('.mdoc .mpage[data-drawn=ok] canvas')`, 20000); writeFileSync(join(OUT, 'p196-desktop-pdf-3.png'), await shoot(p)); }
        if (NAV[i] === 'voice.wav') {
          await waitFor(p, `(() => { const a = document.querySelector('.mview audio.mplayer'); return a && !a.paused; })()`, 8000);
          await ev(p, `window.__a = document.querySelector('.mview audio.mplayer'), true`);
        }
      }
      assert.deepEqual(seen, NAV);
      assert.equal(await ev(p, `window.__a.paused && !window.__a.isConnected`), true, 'the audio stopped when the video came');
      assert.equal(await ev(p, `document.querySelector('.mview').dataset.mode`), 'player');
      assert.equal(await ev(p, `document.querySelector('.mvnext').disabled && !document.querySelector('.mvprev').disabled`), true);
      writeFileSync(join(OUT, 'p196-desktop-video-7.png'), await shoot(p));
      await key(p, 'ArrowRight', { code: 'ArrowRight' });
      await waitFor(p, `document.querySelector('.mvhint').textContent === '已经是最后一个了' && document.querySelector('.mvhint').classList.contains('on')`);
      assert.equal((await state(p)).title, 'clip.webm', 'no wrap-around');
      // ← back two (video paused when it goes), then the bar's ‹ too
      await ev(p, `window.__v = document.querySelector('.mview video.mplayer'), true`);
      await key(p, 'ArrowLeft', { code: 'ArrowLeft' });
      await at(p, 'voice.wav');
      assert.equal(await ev(p, `window.__v.paused`), true, 'the video paused');
      await ev(p, `document.querySelector('.mvprev').click()`);
      await at(p, 'wide.csv');
      // the chat screen's own keys are swallowed: ? (shortcuts), s (settings), / and i (into the field), j / k (cards), m (talk)
      for (const k of ['?', 's', '/', 'i', 'j', 'k', 'g', 'o', 'f']) await key(p, k);
      await key(p, ',', { ctrl: true, code: 'Comma' });
      const leak = await ev(p, `({ keys: !document.getElementById('keys').hidden, settings: !document.getElementById('settings').hidden,
        field: document.activeElement === document.getElementById('input') || document.getElementById('input').value !== '',
        reader: document.body.dataset.reader === '1', open: !document.querySelector('.mview').hidden })`);
      assert.deepEqual(leak, { keys: false, settings: false, field: false, reader: false, open: true });
      assert.equal((await state(p)).page, before.page);
      // Esc closes; the focus goes back to the file last shown
      await key(p, 'Escape', { code: 'Escape' });
      await waitFor(p, `document.querySelector('.mview').hidden`);
      assert.equal(await ev(p, `document.activeElement === ${card(id('C'))}`), true, 'focus on the last file shown');
      // and the chat's ← works again once the preview is gone
      await ev(p, `document.activeElement.blur()`);
      await key(p, 'ArrowLeft', { code: 'ArrowLeft' });
      await waitFor(p, `document.getElementById('pg').textContent !== ${JSON.stringify(before.page)}`, 4000);
      assert.deepEqual(p.problems.filter((x) => !/webm|media/i.test(x)), []);
      assert.deepEqual(p.offsite, []);
    } finally { await p.dispose(); }
  });

  await C('p196-space: Space still closes (Quick Look); a focused card\'s Space opens, ← / → then move, Space closes', async () => {
    const p = await paired(1280, 860);
    try {
      await ev(p, `${card(id('M'))}.scrollIntoView({ block: 'center' })`);
      await waitFor(p, `${card(id('M'))}.dataset.state === 'ready'`, 15000);
      await ev(p, `${card(id('M'))}.focus()`);
      await key(p, ' ', { code: 'Space' });
      await at(p, 'notes.md');
      await key(p, 'ArrowRight', { code: 'ArrowRight' });
      await at(p, 'wide.csv');
      await key(p, ' ', { code: 'Space' });
      await waitFor(p, `document.querySelector('.mview').hidden`);
      assert.equal(await ev(p, `document.activeElement === ${card(id('C'))}`), true);
    } finally { await p.dispose(); }
  });

  await C('p196-touch: a sideways swipe steps (picture, document, player); the deck behind never turns; a wide table scrolls first', async () => {
    const p = await paired(390, 844);
    try {
      const before = await state(p);
      await openImage(p, id('A'));
      const c = await center(p);
      writeFileSync(join(OUT, 'p196-phone-image-1.png'), await shoot(p));
      await swipe(p, c.x + 100, c.y, c.x - 120, c.y + 10);
      await at(p, 'photo.png');
      assert.equal((await state(p)).count, `2 / ${NAV.length}`);
      writeFileSync(join(OUT, 'p196-phone-image-2.png'), await shoot(p));
      await swipe(p, c.x - 100, c.y, c.x + 120, c.y);
      await at(p, 'chart.png');
      await swipe(p, c.x - 100, c.y, c.x + 120, c.y);                  // the first: an edge note
      await waitFor(p, `document.querySelector('.mvhint').classList.contains('on')`);
      writeFileSync(join(OUT, 'p196-phone-edge-first.png'), await shoot(p));
      assert.equal((await state(p)).title, 'chart.png');
      // a mostly vertical drag is not a step
      await swipe(p, c.x, c.y - 120, c.x + 30, c.y + 120);
      await sleep(300);
      assert.equal((await state(p)).title, 'chart.png');
      // a short drag springs back
      await swipe(p, c.x, c.y, c.x - 25, c.y, 8, 400);
      await sleep(300);
      assert.equal((await state(p)).title, 'chart.png');
      // forward into the documents: pdf → (pptx skipped) → markdown
      await swipe(p, c.x + 100, c.y, c.x - 120, c.y);
      await at(p, 'photo.png');
      await swipe(p, c.x + 100, c.y, c.x - 120, c.y);
      await at(p, 'report.pdf');
      await waitFor(p, `!!document.querySelector('.mdoc .mpage[data-drawn=ok] canvas')`, 20000);
      writeFileSync(join(OUT, 'p196-phone-pdf-3.png'), await shoot(p));
      await swipe(p, c.x + 100, c.y, c.x - 120, c.y);
      await at(p, 'notes.md');
      writeFileSync(join(OUT, 'p196-phone-md-4.png'), await shoot(p));
      // a rightward swipe on a document goes back a file — never the browser's own overscroll "back" (the page stays)
      await ev(p, `window.__stay = 1`);
      await swipe(p, c.x - 110, c.y + 150, c.x + 130, c.y + 150);
      await at(p, 'report.pdf');
      assert.equal(await ev(p, `window.__stay`), 1, 'the page did not navigate');
      await swipe(p, c.x + 100, c.y, c.x - 120, c.y);
      await at(p, 'notes.md');
      await swipe(p, c.x + 100, c.y, c.x - 120, c.y);
      await at(p, 'wide.csv');
      await waitFor(p, `!!document.querySelector('.mdoc table') && getComputedStyle(document.querySelector('.mstage')).transform === 'none'`);
      // the wide table: a swipe on it scrolls the table (it can still scroll that way), not the file
      const tb = await ev(p, `(() => { const r = document.querySelector('.mdoc .tablewrap td').getBoundingClientRect(); return { x: r.x + 20, y: r.y + r.height / 2 }; })()`);
      await swipe(p, c.x + 100, tb.y, c.x - 120, tb.y);
      await sleep(400);
      assert.equal((await state(p)).title, 'wide.csv', 'the table scrolled instead');
      assert.ok(await ev(p, `document.querySelector('.mdoc .tablewrap').scrollLeft > 0`), 'the table moved sideways');
      writeFileSync(join(OUT, 'p196-phone-table-5.png'), await shoot(p));
      // below the table (no sideways scroll there): steps
      await swipe(p, c.x + 100, c.y + 200, c.x - 120, c.y + 200);
      await at(p, 'voice.wav');
      await waitFor(p, `(() => { const a = document.querySelector('.mview audio.mplayer'); return a && !a.paused; })()`, 8000);
      await ev(p, `window.__a = document.querySelector('.mview audio.mplayer'), true`);
      writeFileSync(join(OUT, 'p196-phone-audio-6.png'), await shoot(p));
      await swipe(p, c.x + 100, c.y + 200, c.x - 120, c.y + 200);
      await at(p, 'clip.webm');
      assert.equal(await ev(p, `window.__a.paused`), true, 'the audio paused when it went');
      await waitFor(p, `(() => { const v = document.querySelector('.mview video.mplayer'); return v && v.readyState >= 2; })()`, 8000);
      writeFileSync(join(OUT, 'p196-phone-video-7.png'), await shoot(p));
      assert.equal((await state(p)).page, before.page, 'the reply card behind never turned');
      // × closes; the deck still turns afterwards
      await ev(p, `document.querySelector('.mview-x').click()`);
      await waitFor(p, `document.querySelector('.mview').hidden`);
      assert.deepEqual(p.problems.filter((x) => !/webm|media/i.test(x)), []);
    } finally { await p.dispose(); }
  });

  await C('p196-zoom: zoomed (double tap or pinch) one finger pans the picture; back to 1× the swipe steps again', async () => {
    const p = await paired(390, 844);
    try {
      await openImage(p, id('B'));
      const c = await center(p);
      await tap(p, c.x, c.y); await sleep(80); await tap(p, c.x, c.y);
      await waitFor(p, `document.querySelector('.mzoom').style.transform.includes('scale(2.5)')`);
      await swipe(p, c.x + 60, c.y, c.x - 60, c.y);
      await sleep(300);
      const z = await ev(p, `document.querySelector('.mzoom').style.transform`);
      assert.equal((await state(p)).title, 'photo.png', 'zoomed: no step');
      assert.match(z, /translate\(-1\d\dpx, 0px\) scale\(2\.5\)/, 'zoomed: the picture panned with the finger: ' + z);
      writeFileSync(join(OUT, 'p196-phone-zoom-pan.png'), await shoot(p));
      await sleep(400);
      await tap(p, c.x, c.y); await sleep(80); await tap(p, c.x, c.y);
      await waitFor(p, `document.querySelector('.mzoom').style.transform.includes('scale(1)')`);
      await sleep(400);
      await swipe(p, c.x + 100, c.y, c.x - 120, c.y);
      await at(p, 'report.pdf');
      // a pinch out, then a one-finger sideways drag in the same picture: pans, never steps
      await swipe(p, c.x - 100, c.y, c.x + 120, c.y);
      await at(p, 'photo.png');
      await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: c.x - 20, y: c.y, id: 1 }, { x: c.x + 20, y: c.y, id: 2 }] });
      for (let i = 1; i <= 6; i++) { await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: c.x - 20 - i * 15, y: c.y, id: 1 }, { x: c.x + 20 + i * 15, y: c.y, id: 2 }] }); await sleep(20); }
      await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      const zs = await ev(p, `Number(/scale\\(([\\d.]+)\\)/.exec(document.querySelector('.mzoom').style.transform)[1])`);
      assert.ok(zs > 1.5, 'pinched: ' + zs);
      await swipe(p, c.x + 80, c.y, c.x - 80, c.y);
      await sleep(300);
      assert.equal((await state(p)).title, 'photo.png', 'pinched: one finger pans, no step');
    } finally { await p.dispose(); }
  });

  await C('p196-reader: from the full-screen reader the same files step the same way, and Esc closes only the preview', async () => {
    const p = await paired(1280, 860);
    try {
      await key(p, 'f');
      await waitFor(p, `document.body.dataset.reader === '1'`);
      const rc = `document.querySelector('#rdWords .mcard[data-mid="${id('A')}"]')`;
      await waitFor(p, `!!${rc} && ${rc}.dataset.state === 'ready'`, 15000);
      await sleep(400);                                             // the reader's own ghost-click guard (350 ms after it opens)
      await ev(p, `${rc}.querySelector('img').click()`);
      await at(p, 'chart.png');
      assert.equal((await state(p)).count, `1 / ${NAV.length}`, 'the reader\'s two pictures + the strip of the same reply');
      await key(p, 'ArrowRight', { code: 'ArrowRight' });
      await at(p, 'photo.png');
      await key(p, 'ArrowRight', { code: 'ArrowRight' });
      await at(p, 'report.pdf');
      await key(p, 'Escape', { code: 'Escape' });
      await waitFor(p, `document.querySelector('.mview').hidden`);
      assert.equal(await ev(p, `document.body.dataset.reader`), '1', 'the reader stays open');
    } finally { await p.dispose(); }
  });

  await C('p196-video: a phone screencast of the swipe flow (review set)', async () => {
    const p = await paired(390, 844);
    try {
      await openImage(p, id('A'));
      const c = await center(p);
      const n = await screencast(p, 'p196-phone-swipe.mp4', async () => {
        await sleep(500);
        for (const want of ['photo.png', 'report.pdf', 'notes.md']) { await swipe(p, c.x + 110, c.y + 150, c.x - 130, c.y + 150, 14, 320); await at(p, want); await sleep(700); }
        await swipe(p, c.x - 110, c.y + 150, c.x + 130, c.y + 150, 14, 320); await at(p, 'report.pdf'); await sleep(500);
        await swipe(p, c.x - 110, c.y + 150, c.x + 130, c.y + 150, 14, 320); await at(p, 'photo.png'); await sleep(500);
        await tap(p, c.x, c.y); await sleep(80); await tap(p, c.x, c.y); await sleep(500);
        await swipe(p, c.x + 60, c.y, c.x - 60, c.y, 14, 400); await sleep(600);
        await tap(p, c.x, c.y); await sleep(80); await tap(p, c.x, c.y); await sleep(500);
        await swipe(p, c.x - 110, c.y, c.x + 130, c.y, 14, 320); await at(p, 'chart.png'); await sleep(300);
        await swipe(p, c.x - 110, c.y, c.x + 130, c.y, 14, 320); await sleep(1000);
      });
      assert.ok(n > 10, 'frames: ' + n);
    } finally { await p.dispose(); }
  });

  await C('p196-video-desktop: a desktop screencast of ← / → (review set)', async () => {
    const p = await paired(1280, 860, 'dark');
    try {
      await openImage(p, id('A'));
      const n = await screencast(p, 'p196-desktop-keys.mp4', async () => {
        await sleep(500);
        for (const want of NAV.slice(1)) { await key(p, 'ArrowRight', { code: 'ArrowRight' }); await at(p, want); await sleep(want === 'report.pdf' ? 1200 : 700); }
        await key(p, 'ArrowRight', { code: 'ArrowRight' }); await sleep(900);
        for (const want of NAV.slice(0, -1).reverse().slice(0, 3)) { await key(p, 'ArrowLeft', { code: 'ArrowLeft' }); await at(p, want); await sleep(500); }
        await key(p, 'Escape', { code: 'Escape' }); await sleep(800);
      });
      assert.ok(n > 10, 'frames: ' + n);
    } finally { await p.dispose(); }
  });
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
console.log('parity results → ' + rec.write());
console.log(failed ? `p196: ${failed} failed` : 'p196: all passed');
process.exit(failed ? 1 : 0);
