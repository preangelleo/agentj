#!/usr/bin/env node
// F21 (0.15.2, PROTOCOL §13) · media out in the real page against the fake host, in an independent headless Chromium
// (never :9222), with the Worker's real CSP (serve.mjs). One finished page carries: an inline picture (`![](out/chart.png)` in
// the words → <img> from a Blob URL; tap → the viewer; Esc closes), an audio, a video (playsinline), a PDF card (open /
// download), an HTML preview whose <script> must NOT run (sandbox="" ; checked from an isolated world inside the frame, and
// the page title is unchanged; its remote <img> never leaves the phone), a file card with a download link, a media_skip
// line, and a picture whose bytes do not match its SHA-256 (→ the error, never the picture).
//   node web/test/p57_media.mjs      (screenshots → /var/tmp/p57-logs/media-shots/)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync, mkdtempSync, rmSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { deflateSync, crc32 } from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, sleep, cdp } from './browser.mjs';

const OUT = process.env.AJ_MEDIA_SHOTS || '/var/tmp/p57-logs/media-shots/';
mkdirSync(OUT, { recursive: true });
const sha = (b) => createHash('sha256').update(b).digest('hex');
const CHUNK = 45056, WINDOW = 8;

// ---------------------------------------------------------------- fixtures (made here, nothing downloaded)
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
function video() {
  const d = mkdtempSync(join(tmpdir(), 'aj-p57-')), out = join(d, 'v.mp4');
  const r = spawnSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=15:duration=1',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out], { encoding: 'utf8' });
  const b = r.status === 0 && existsSync(out) ? readFileSync(out) : null;
  rmSync(d, { recursive: true, force: true });
  return b;
}
const CHART = png(480, 270, (x, y) => [40 + (x * 200 / 480) | 0, 90 + (y * 120 / 270) | 0, 200 - (x * 150 / 480) | 0]);
const BAD = png(64, 64, () => [200, 40, 40]);
const AUDIO = wav();
const VIDEO = video();
const PDF = Buffer.from('%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj ' +
  '3 0 obj<</Type/Page/MediaBox[0 0 200 200]/Parent 2 0 R>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n');
const HTML = Buffer.from('<!doctype html><html><head><title>report</title><style>h1{color:#c00}</style></head><body>' +
  '<h1 id="h">季度报告</h1><p>Generated chart below.</p><img src="https://example.com/track.png" alt="remote">' +
  '<script>document.body.dataset.ran = "1"; try { parent.document.title = "pwned"; } catch (e) {} window.name = "ran";</script></body></html>');
const CSV = Buffer.from('城市,数量\n上海,12\n北京,9\n');

const ITEMS = [];
const BYTES = new Map();
function item(mid, name, mime, kind, bytes, ref = '', wrongSha = false) {
  BYTES.set(mid, bytes);
  const it = { mid, name, mime, kind, bytes: bytes.length, sha256: wrongSha ? sha(Buffer.concat([bytes, Buffer.from('x')])) : sha(bytes), ref };
  ITEMS.push(it);
  return it;
}
const id = (c) => c.repeat(22);
item(id('A'), 'chart.png', 'image/png', 'image', CHART, 'out/chart.png');
item(id('B'), 'voice.wav', 'audio/wav', 'audio', AUDIO);
if (VIDEO) item(id('C'), 'clip.mp4', 'video/mp4', 'video', VIDEO);
item(id('D'), 'report.pdf', 'application/pdf', 'pdf', PDF);
item(id('E'), 'report.html', 'text/html', 'html', HTML);
item(id('F'), 'data.csv', 'text/csv', 'file', CSV);
item(id('G'), 'bad.png', 'image/png', 'image', BAD, '', true);
const SKIP = [{ name: 'huge.mp4', why: 'too_big', bytes: 60 * 1024 * 1024 }, { name: '.env', why: 'secret' }];
const REPLY = '做好了，图表如下：\n\n![季度图表](out/chart.png)\n\n音频、视频、PDF、网页预览和数据都附在下面。';

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const rec = parityRecorder('web/test/p57_media.mjs');
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
// the only problems allowed: the HTML preview's remote picture refused by the CSP (that IS the check)
const realProblems = (p) => p.problems.filter((x) => !(/Content Security Policy/i.test(x) && /example\.com\/track\.png/.test(x)));

try {
  await C('media-page: inline picture, strip, skip notes, sha mismatch', async () => {
    const p = await paired(390, 844);
    try {
      const turn = await fake.addTurn({ k: 'phone', dev: null, text: '做个季度报告' }, REPLY, 'done');
      await fake.updateTurn(turn.id, { media: ITEMS, media_skip: SKIP });
      await waitFor(p, `!!document.querySelector('#words .mslot .mcard[data-state=ready] img')`, 15000);
      const inl = await ev(p, `(() => { const s = document.querySelector('#words .mslot'); const i = s.querySelector('img');
        return { ref: s.dataset.ref, src: i.src.slice(0, 5), w: i.naturalWidth, inStrip: !!document.querySelector('#mstrip [data-mid="${id('A')}"]') }; })()`);
      assert.deepEqual(inl, { ref: 'out/chart.png', src: 'blob:', w: 480, inStrip: false }, 'inline in the words, not again in the strip');
      const strip = await ev(p, `[...document.querySelectorAll('#mstrip .mcard')].map((c) => c.dataset.kind + ':' + c.querySelector('.mname').textContent)`);
      assert.deepEqual(strip, ['audio:voice.wav', ...(VIDEO ? ['video:clip.mp4'] : []), 'pdf:report.pdf', 'html:report.html', 'file:data.csv', 'image:bad.png']);
      const skips = await ev(p, `[...document.querySelectorAll('#mstrip .mskip')].map((x) => x.textContent)`);
      assert.deepEqual(skips, ['huge.mp4 太大（60.0 MB），没有发到手机。', '.env 可能含密钥，没有发。']);
      await waitFor(p, `document.querySelector('[data-mid="${id('G')}"]').dataset.state === 'error'`, 10000);
      assert.equal(await ev(p, `document.querySelector('[data-mid="${id('G')}"] .mtap').textContent`), '文件对不上，没有显示');
      assert.equal(await ev(p, `!!document.querySelector('[data-mid="${id('G')}"] img')`), false, 'a wrong SHA-256 never shows the picture');
      assert.ok(!gets.some((g) => g.mid === id('B') || g.mid === id('D')), 'audio / pdf wait for a tap');
      writeFileSync(join(OUT, 'media-page-phone.png'), await shoot(p));
      await ev(p, `document.getElementById('mstrip').scrollIntoView()`);
      writeFileSync(join(OUT, 'media-strip-phone.png'), await shoot(p));

      // tap the picture → the viewer; Esc closes it (and does not reach the composer's Esc Esc)
      await ev(p, `document.querySelector('#words .mslot img').click()`);
      await waitFor(p, `!document.querySelector('.mview').hidden && !!document.querySelector('.mview img.mzoom')`);
      writeFileSync(join(OUT, 'media-viewer-phone.png'), await shoot(p));
      await ev(p, `document.getElementById('input').value = 'keep me'`);
      await key(p, 'Escape', { code: 'Escape' }); await key(p, 'Escape', { code: 'Escape' });
      assert.equal(await ev(p, `document.querySelector('.mview').hidden`), true);
      assert.equal(await ev(p, `document.getElementById('input').value`), 'keep me', 'Esc closed the viewer only');

      // audio: tap → <audio controls> from a Blob URL, metadata loads under the page's CSP
      await ev(p, `document.querySelector('[data-mid="${id('B')}"] .mtap').click()`);
      await waitFor(p, `(() => { const a = document.querySelector('[data-mid="${id('B')}"] audio'); return a && a.src.startsWith('blob:') && a.readyState >= 1 && a.duration > 0.9; })()`, 10000);
      if (VIDEO) {
        await ev(p, `document.querySelector('[data-mid="${id('C')}"] .mtap').click()`);
        await waitFor(p, `(() => { const v = document.querySelector('[data-mid="${id('C')}"] video'); return v && v.hasAttribute('playsinline') && v.controls && v.src.startsWith('blob:'); })()`, 10000);
        const vw = await ev(p, `new Promise((r) => { const v = document.querySelector('[data-mid="${id('C')}"] video'); const f = () => r({ w: v.videoWidth, err: v.error && v.error.code }); if (v.readyState >= 1) f(); else { v.onloadedmetadata = f; v.onerror = f; setTimeout(f, 5000); } })`);
        if (vw.err) console.log(`    (note: this Chromium cannot decode H.264: error ${vw.err} — the card and Blob URL are still checked)`);
        else assert.equal(vw.w, 320);
      }
      // pdf: tap → open (new tab, the phone's PDF viewer) + download, both Blob URLs; nothing opened here
      await ev(p, `document.querySelector('[data-mid="${id('D')}"] .mtap').click()`);
      await waitFor(p, `document.querySelectorAll('[data-mid="${id('D')}"] .macts a').length >= 2`);
      const pdf = await ev(p, `[...document.querySelectorAll('[data-mid="${id('D')}"] .macts a')].map((a) => [a.textContent, a.href.slice(0, 5), a.target || a.getAttribute('download'), a.rel])`);
      assert.deepEqual(pdf, [['打开', 'blob:', '_blank', 'noopener noreferrer'], ['下载', 'blob:', 'report.pdf', 'noopener']]);
      // file: tap → a download link with the file's name
      await ev(p, `document.querySelector('[data-mid="${id('F')}"] .mtap').click()`);
      await waitFor(p, `!!document.querySelector('[data-mid="${id('F')}"] .macts a[download]')`);
      assert.equal(await ev(p, `document.querySelector('[data-mid="${id('F')}"] .macts a[download]').getAttribute('download')`), 'data.csv');
      await ev(p, `document.getElementById('mstrip').scrollIntoView()`);
      writeFileSync(join(OUT, 'media-cards-loaded-phone.png'), await shoot(p));
      assert.deepEqual(realProblems(p), []);
      assert.deepEqual(p.offsite, [], 'nothing left the phone but the session');
    } finally { await p.dispose(); }
  });

  await C('media-html: sandbox="" preview, the script never runs, nothing remote loads', async () => {
    const p = await paired(390, 844);
    try {
      const turn = await fake.addTurn({ k: 'phone', dev: null, text: '网页' }, '报告网页：[report.html](out/report.html)', 'done');
      await fake.updateTurn(turn.id, { media: ITEMS.filter((x) => x.kind === 'html') });
      // P59 (ADR-A164): the reply links the file, so its card sits at the link in the words (no longer in the strip)
      await waitFor(p, `!!document.querySelector('#words .mslot[data-link] .mcard[data-kind=html] .mtap') && document.getElementById('mstrip').hidden`);
      const title0 = await ev(p, 'document.title');
      await ev(p, `document.querySelector('#words .mcard[data-kind=html] .mtap').click()`);
      await waitFor(p, `!!document.querySelector('.mview iframe.mframe')`, 10000);
      const fr = await ev(p, `(() => { const f = document.querySelector('.mview iframe'); return { sandbox: f.getAttribute('sandbox'), csp: f.getAttribute('csp'),
        ref: f.getAttribute('referrerpolicy'), src: f.getAttribute('src'), srcdoc: f.srcdoc.includes('季度报告'), doc: f.contentDocument === null, note: document.querySelector('.mview .mnote').textContent }; })()`);
      assert.equal(fr.sandbox, '', 'an EMPTY sandbox');
      assert.equal(fr.ref, 'no-referrer');
      assert.match(fr.csp, /^default-src 'none'/);
      assert.equal(fr.src, null);
      assert.ok(fr.srcdoc && fr.doc, 'opaque origin: the page cannot reach into it either');
      assert.ok(fr.note.includes('不运行脚本'));
      await sleep(800);
      assert.equal(await ev(p, 'document.title'), title0, 'the script did not change the page title');
      // look inside the frame from an isolated world (DevTools may, scripts may not): rendered, and the script never ran
      // a sandboxed (opaque-origin) frame may run in its own process: then it is a target of its own, not in the frame tree
      const { frameTree } = await p.send('Page.getFrameTree');
      let probe = p, child = (frameTree.childFrames || []).map((f) => f.frame).find((f) => /srcdoc/.test(f.url));
      if (!child) {
        const { targetInfos } = await B.browser.send('Target.getTargets');
        const tf = targetInfos.find((x) => x.type === 'iframe' && /srcdoc/.test(x.url));
        assert.ok(tf, 'the preview frame exists: ' + JSON.stringify(targetInfos.map((x) => [x.type, x.url])));
        probe = cdp(`ws://127.0.0.1:${B.port}/devtools/page/${tf.targetId}`); await probe.ready;
        child = { id: tf.targetId };
      }
      const { executionContextId } = await probe.send('Page.createIsolatedWorld', { frameId: child.id, worldName: 'p57probe' });
      const inside = (await probe.send('Runtime.evaluate', { contextId: executionContextId, returnByValue: true,
        expression: `({ h: document.getElementById('h') && document.getElementById('h').textContent, ran: document.body.dataset.ran || null,
          img: document.querySelector('img').complete && document.querySelector('img').naturalWidth })` })).result.value;
      assert.deepEqual(inside, { h: '季度报告', ran: null, img: 0 }, 'content rendered, script did not run, remote picture did not load');
      if (probe !== p) probe.close();
      writeFileSync(join(OUT, 'media-html-preview-phone.png'), await shoot(p));
      await ev(p, `document.querySelector('.mview .mview-x').click()`);
      assert.equal(await ev(p, `document.querySelector('.mview').hidden`), true);
      assert.deepEqual(p.offsite, [], 'the remote picture in the HTML never left the phone');
      assert.deepEqual(realProblems(p), []);
    } finally { await p.dispose(); }
  });

  await C('media-desktop + dark + expired', async () => {
    const p = await paired(1280, 860, 'dark');
    try {
      const turn = await fake.addTurn({ k: 'phone', dev: null, text: '图' }, REPLY, 'done');
      const gone = { ...ITEMS[0], mid: id('X'), name: 'old.png', ref: '' };
      await fake.updateTurn(turn.id, { media: [ITEMS[0], ITEMS[1], ITEMS.find((x) => x.kind === 'pdf'), gone], media_skip: SKIP.slice(0, 1) });
      await waitFor(p, `!!document.querySelector('#words .mslot img') && document.querySelector('[data-mid="${id('X')}"]').dataset.state === 'error'`, 15000);
      assert.equal(await ev(p, `document.querySelector('[data-mid="${id('X')}"] .mtap').textContent`), '已过期');
      writeFileSync(join(OUT, 'media-page-desktop-dark.png'), await shoot(p));
      // another page → the Blob URLs of this one are revoked
      const urls = await ev(p, `[...document.querySelectorAll('#words img, #mstrip img')].map((i) => i.src)`);
      await fake.addTurn({ k: 'phone', dev: null, text: '下一条' }, '没有附件的回复', 'done');
      await waitFor(p, `document.getElementById('mstrip').hidden`);
      const alive = await ev(p, `Promise.all(${JSON.stringify(urls)}.map((u) => new Promise((r) => { const i = new Image(); i.onload = () => r(true); i.onerror = () => r(false); i.src = u; })))`);
      assert.deepEqual(alive, urls.map(() => false), 'object URLs revoked when the page left the screen');
      assert.deepEqual(realProblems(p).filter((x) => !/blob:/.test(x)), []);
    } finally { await p.dispose(); }
  });
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
console.log('parity results → ' + rec.write());
console.log(failed ? `p57_media: ${failed} failed` : 'p57_media: all passed');
process.exit(failed ? 1 : 0);
