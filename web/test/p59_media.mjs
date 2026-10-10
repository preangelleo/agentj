#!/usr/bin/env node
// P59 (ADR-A164) · F21 finishing in the real page against the fake host, in an independent headless Chromium (never :9222),
// with the Worker's real CSP (serve.mjs). One finished page whose reply holds: a local picture `![](out/chart.png)`, a local
// link `[季度报告](out/report.pdf)`, a refused local picture `![](out/huge.png)` (too_big), a remote picture
// `![](https://example.com/r.png)`, and an item the reply only names in passing (voice.wav → the strip).
// Checks: the PDF card sits at the link (not again in the strip), the skip note sits where the picture was written (not again
// in the strip), the remote picture stays a link and nothing leaves the phone; the full-screen reader shows the same picture
// from the same Blob (no second media_get), the skip note and the PDF card in place of the Markdown; tap → the viewer, Esc
// closes the viewer only; the reader's object URLs go when it closes; dark mode on a phone and a desktop.
//   flock /tmp/p59-browser.lock node web/test/p59_media.mjs   (screenshots → /var/tmp/p59-logs/media-shots/)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { deflateSync, crc32 } from 'node:zlib';
import { join } from 'node:path';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, sleep } from './browser.mjs';

const OUT = process.env.AJ_MEDIA_SHOTS || '/var/tmp/p59-logs/media-shots/';
mkdirSync(OUT, { recursive: true });
const sha = (b) => createHash('sha256').update(b).digest('hex');
const CHUNK = 45056, WINDOW = 8;

function png(w, h, f) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const [r, g, b] = f(x, y); raw.set([r, g, b], y * (w * 3 + 1) + 1 + x * 3); }
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
function wav(secs = 1, rate = 8000) {
  const n = secs * rate, data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / rate)), i * 2);
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVE', 8); h.write('fmt ', 12); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write('data', 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}
const CHART = png(480, 270, (x, y) => [40 + (x * 200 / 480) | 0, 90 + (y * 120 / 270) | 0, 200 - (x * 150 / 480) | 0]);
const PDF = Buffer.from('%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj ' +
  '3 0 obj<</Type/Page/MediaBox[0 0 200 200]/Parent 2 0 R>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n');
const AUDIO = wav();
const id = (c) => c.repeat(22);
const BYTES = new Map();
const item = (mid, name, mime, kind, bytes, ref) => { BYTES.set(mid, bytes); return { mid, name, mime, kind, bytes: bytes.length, sha256: sha(bytes), ref }; };
const ITEMS = [
  item(id('A'), 'chart.png', 'image/png', 'image', CHART, 'out/chart.png'),
  item(id('D'), 'report.pdf', 'application/pdf', 'pdf', PDF, 'out/report.pdf'),
  item(id('B'), 'voice.wav', 'audio/wav', 'audio', AUDIO, 'out/voice.wav'),
];
const SKIP = [{ name: 'huge.png', why: 'too_big', bytes: 12 * 1024 * 1024 }, { name: '.env', why: 'secret' }];
const REPLY = [
  '季度报告做好了。', '', '![季度图表](out/chart.png)', '', '完整版见 [季度报告](out/report.pdf)，录音在 out/voice.wav。', '',
  '![原图](out/huge.png)', '', '参考图：![远程](https://example.com/r.png)', '',
  '这是一段足够长的正文，'.repeat(40),
].join('\n');

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const rec = parityRecorder('web/test/p59_media.mjs');
const ev = (p, s) => evaluate(p, s);
const gets = [];

function serveMedia(st) {
  st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    gets.push(m);
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
  serveMedia(fake.st);
  gets.length = 0;
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  return p;
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
async function page(p) {
  const turn = await fake.addTurn({ k: 'phone', dev: null, text: '做个季度报告' }, REPLY, 'done');
  await fake.updateTurn(turn.id, { media: ITEMS, media_skip: SKIP });
  await waitFor(p, `!!document.querySelector('#words .mslot .mcard[data-state=ready] img')`, 15000);
}
async function openReader(p) {
  await ev(p, `document.getElementById('readBtn').click()`);
  await waitFor(p, `!document.getElementById('rd').hidden && !!document.querySelector('#rdWords .mslot .mcard[data-state=ready] img')`, 10000);
  await sleep(450);                                         // past the reader's click guard (350 ms)
}
/** WCAG contrast of an element's text against what is really behind it (translucent layers composited up to the first
 *  opaque one; `color(srgb …)` and rgb()/rgba() both parsed). */
const contrastJs = (fg, bg) => `(() => {
  const parse = (c) => { const srgb = /^color\\(srgb/.test(c), m = (c.match(/[\\d.]+/g) || []).map(Number);
    const k = srgb ? 255 : 1; return [m[0] * k, m[1] * k, m[2] * k, m.length > 3 ? m[3] : 1]; };
  const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  const layers = [];
  for (let n = ${bg}; n; n = n.parentElement) { const c = parse(getComputedStyle(n).backgroundColor); if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
  let base = [0, 0, 0];
  for (const [r, g, b, a] of layers.reverse()) base = [r * a + base[0] * (1 - a), g * a + base[1] * (1 - a), b * a + base[2] * (1 - a)];
  const x = lum(parse(getComputedStyle(${fg}).color)), y = lum(base); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); })()`;

try {
  await C('p59-page: [x](local.pdf) inline card, skip note in place, no duplicate in the strip, remote picture not fetched', async () => {
    const p = await paired(390, 844);
    try {
      await page(p);
      const words = await ev(p, `(() => {
        const ws = document.getElementById('words');
        const link = ws.querySelector('.mslot[data-link="1"]');
        return {
          link: link && { ref: link.dataset.ref, label: link.querySelector('.mlabel')?.textContent, kind: link.querySelector('.mcard')?.dataset.kind, mid: link.dataset.mid },
          skip: [...ws.querySelectorAll('.mslot[data-skip] .mskip')].map((x) => x.textContent),
          raw: /!\\[|\\]\\(out\\//.test(ws.textContent),
          remote: [...ws.querySelectorAll('a')].filter((a) => !a.closest('.mcard')).map((a) => a.getAttribute('href')),
          imgs: [...ws.querySelectorAll('img')].map((i) => i.src.slice(0, 5)),
        }; })()`);
      assert.deepEqual(words.link, { ref: 'out/report.pdf', label: '季度报告', kind: 'pdf', mid: id('D') }, 'the PDF card sits at the link, its words kept');
      assert.deepEqual(words.skip, ['huge.png 太大（12.0 MB），没有发到手机。'], 'the refused picture: its note where it was written');
      assert.equal(words.raw, false, 'no raw Markdown of a local file left in the words');
      assert.deepEqual(words.remote, ['https://example.com/r.png'], 'the remote picture stays a link');
      assert.deepEqual(words.imgs, ['blob:'], 'one picture, from a Blob URL');
      const strip = await ev(p, `({ cards: [...document.querySelectorAll('#mstrip .mcard')].map((c) => c.dataset.kind + ':' + c.querySelector('.mname').textContent),
        skips: [...document.querySelectorAll('#mstrip .mskip')].map((x) => x.textContent) })`);
      assert.deepEqual(strip, { cards: ['audio:voice.wav'], skips: ['.env 可能含密钥，没有发。'] }, 'the strip: only what the reply did not place');
      assert.equal(await ev(p, `document.querySelectorAll('[data-mid="${id('D')}"]').length`), 2, 'the PDF once (slot + its card), not again in the strip');
      // the inline PDF card works like the strip's: tap → open / download
      await ev(p, `document.querySelector('#words .mslot[data-link] .mtap').click()`);
      await waitFor(p, `document.querySelectorAll('#words .mslot[data-link] .macts a').length >= 2`, 10000);
      const acts = await ev(p, `[...document.querySelectorAll('#words .mslot[data-link] .macts a')].map((a) => [a.textContent, a.href.slice(0, 5), a.target || a.getAttribute('download')])`);
      assert.deepEqual(acts, [['打开', 'blob:', '_blank'], ['下载', 'blob:', 'report.pdf']]);
      await ev(p, `document.querySelector('#words .mslot[data-link]').scrollIntoView({ block: 'center' })`);
      writeFileSync(join(OUT, 'p59-page-phone.png'), await shoot(p));
      assert.ok(!gets.some((g) => g.mid === id('B')), 'audio still waits for a tap');
      assert.deepEqual(p.offsite, [], 'nothing left the phone but the session (the remote picture was not fetched)');
      assert.deepEqual(p.problems, []);
    } finally { await p.dispose(); }
  });

  await C('p59-reader: inline picture from the same Blob, skip note, PDF card; viewer; URLs revoked on close', async () => {
    const p = await paired(390, 844);
    try {
      await page(p);
      const before = gets.filter((g) => g.mid === id('A')).length;
      assert.equal(before, 1, 'the picture was fetched once for the page');
      await openReader(p);
      const rd = await ev(p, `(() => { const ws = document.getElementById('rdWords');
        return { raw: /!\\[|\\]\\(out\\//.test(ws.textContent), img: ws.querySelector('.mslot img').naturalWidth, src: ws.querySelector('.mslot img').src.slice(0, 5),
          skip: [...ws.querySelectorAll('.mslot[data-skip] .mskip')].map((x) => x.textContent),
          pdf: ws.querySelector('.mslot[data-link] .mcard')?.dataset.kind, label: ws.querySelector('.mslot[data-link] .mlabel')?.textContent,
          remote: [...ws.querySelectorAll('a')].filter((a) => !a.closest('.mcard')).map((a) => a.getAttribute('href')), strip: !!document.querySelector('#rd #mstrip') }; })()`);
      assert.deepEqual(rd, { raw: false, img: 480, src: 'blob:', skip: ['huge.png 太大（12.0 MB），没有发到手机。'], pdf: 'pdf', label: '季度报告',
        remote: ['https://example.com/r.png'], strip: false });
      assert.equal(gets.filter((g) => g.mid === id('A')).length, before, 'the reader used the cached Blob: no second media_get');
      writeFileSync(join(OUT, 'p59-reader-phone.png'), await shoot(p));
      // the inline PDF card in the reader works too (the bytes come from the LRU after the first fetch)
      await ev(p, `document.querySelector('#rdWords .mslot[data-link] .mtap').click()`);
      await waitFor(p, `document.querySelectorAll('#rdWords .mslot[data-link] .macts a').length >= 2`, 10000);
      // tap the picture → the viewer above the reader; Esc closes the viewer only
      await ev(p, `document.querySelector('#rdWords .mslot img').click()`);
      await waitFor(p, `!document.querySelector('.mview').hidden && !!document.querySelector('.mview img.mzoom')`);
      writeFileSync(join(OUT, 'p59-reader-viewer-phone.png'), await shoot(p));
      await key(p, 'Escape', { code: 'Escape' });
      assert.equal(await ev(p, `document.querySelector('.mview').hidden`), true, 'the viewer closed');
      assert.equal(await ev(p, `document.activeElement === document.querySelector('#rdWords .mslot img')`), true, 'viewer Escape restores the image trigger');
      assert.equal(await ev(p, `document.getElementById('rd').hidden`), false, 'the reader stayed');
      // close the reader: its object URLs go, the page's picture keeps working
      const rdUrl = await ev(p, `document.querySelector('#rdWords .mslot img').src`);
      const pageUrl = await ev(p, `document.querySelector('#words .mslot img').src`);
      assert.notEqual(rdUrl, pageUrl, 'each view owns its own object URL');
      await key(p, 'Escape', { code: 'Escape' });
      await waitFor(p, `document.getElementById('rd').hidden`);
      const alive = await ev(p, `Promise.all(${JSON.stringify([rdUrl, pageUrl])}.map((u) => new Promise((r) => { const i = new Image(); i.onload = () => r(true); i.onerror = () => r(false); i.src = u; })))`);
      assert.deepEqual(alive, [false, true], 'the reader URL revoked, the page URL alive');
      // open again: still no new fetch
      await sleep(450);                                     // past the close's click guard
      await openReader(p);
      assert.equal(gets.filter((g) => g.mid === id('A')).length, before, 'reopening uses the cache again');
      assert.deepEqual(p.offsite, []);
      assert.deepEqual(p.problems.filter((x) => !/blob:/.test(x)), []);
    } finally { await p.dispose(); }
  });

  await C('p59-dark: phone and desktop, page and reader, readable cards and notes', async () => {
    for (const [w, h, tag] of [[390, 844, 'phone'], [1280, 860, 'desktop']]) {
      const p = await paired(w, h, 'dark');
      try {
        await page(p);
        const dark = await ev(p, `matchMedia('(prefers-color-scheme: dark)').matches`);
        assert.equal(dark, true);
        if (process.env.P59_DEBUG) console.log(await ev(p, `(() => { const c = document.querySelector('#words .mslot[data-link] .mcard'), n = c.querySelector('.mname'); return [getComputedStyle(n).color, getComputedStyle(c).backgroundColor, getComputedStyle(document.body).backgroundColor, getComputedStyle(document.getElementById('words')).color]; })()`));
        const cPage = await ev(p, contrastJs(`document.querySelector('#words .mslot[data-link] .mname')`, `document.querySelector('#words .mslot[data-link] .mcard')`));
        assert.ok(cPage >= 4.5, `card name contrast on the page ${cPage.toFixed(2)}`);
        await ev(p, `document.querySelector('#words .mslot[data-link]').scrollIntoView({ block: 'center' })`);
        const shotP = await shoot(p);
        writeFileSync(join(OUT, `p59-page-${tag}-dark.png`), shotP);
        await openReader(p);
        const cRd = await ev(p, contrastJs(`document.querySelector('#rdWords .mslot[data-skip] .mskip')`, `document.getElementById('rd')`));
        assert.ok(cRd >= 4.5, `skip note contrast in the reader ${cRd.toFixed(2)}`);
        const cRdCard = await ev(p, contrastJs(`document.querySelector('#rdWords .mslot[data-link] .mname')`, `document.querySelector('#rdWords .mslot[data-link] .mcard')`));
        assert.ok(cRdCard >= 4.5, `card name contrast in the reader ${cRdCard.toFixed(2)}`);
        await ev(p, `document.querySelector('#rdWords .mslot[data-link]').scrollIntoView({ block: 'center' })`);
        const shotR = await shoot(p);
        writeFileSync(join(OUT, `p59-reader-${tag}-dark.png`), shotR);
        assert.ok(shotP.length > 8000 && shotR.length > 8000, 'non-trivial screenshots');
        const fit = await ev(p, `(() => { const c = document.querySelector('#rdWords .mslot[data-link] .mcard').getBoundingClientRect(), s = document.getElementById('rdScroll').getBoundingClientRect(); return c.left >= s.left - 1 && c.right <= s.right + 1; })()`);
        assert.ok(fit, 'the card fits the reader column');
        assert.deepEqual(p.offsite, []);
        assert.deepEqual(p.problems, []);
      } finally { await p.dispose(); }
    }
  });
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
console.log('parity results → ' + rec.write());
console.log(failed ? `p59_media: ${failed} failed` : 'p59_media: all passed');
process.exit(failed ? 1 : 0);
