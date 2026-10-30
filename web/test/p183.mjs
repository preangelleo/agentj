#!/usr/bin/env node
// P183 · the reply's file cards in the real page against the fake host, in an independent headless Chromium (never :9222),
// with the Worker's real CSP (serve.mjs): dashed outline on no fill, the reply card's icon system (预览 · 下载 · 分享 ·
// 转发到邮箱 · 转发到 Telegram), auto-load (small files on screen; big ones on a tap with their size; never on data saver),
// the full-screen preview of every kind (picture, PDF via pdf.js, Markdown, CSV, xlsx, docx, JSON code, text, HTML, audio,
// pptx = not supported → download), Space / Esc on desktop, the Telegram forward of one file (host cap `tgfile`) and the
// P184 mail door (mailfwd.js). Nothing but the session and our own files ever loads.
//   node web/test/p183.mjs        (screenshots → reports/qa/p183/)
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { deflateSync, crc32 } from 'node:zlib';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P183_SHOTS || join(ROOT, 'reports/qa/p183');
mkdirSync(OUT, { recursive: true });
const FIX = (n) => readFileSync(new URL(`./fixtures/p183/${n}`, import.meta.url));
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
const id = (c) => c.repeat(22);
const BYTES = new Map();
const item = (mid, name, mime, kind, bytes) => { BYTES.set(mid, bytes); return { mid, name, mime, kind, bytes: bytes.length, sha256: sha(bytes), ref: 'out/' + name }; };
const BIG = Buffer.concat([Buffer.from('%PDF-1.4\n'), Buffer.alloc(6 * 1024 * 1024, 32)]);
const PAGE1 = [
  item(id('A'), 'chart.png', 'image/png', 'image', png(480, 270, (x, y) => [40 + (x * 200 / 480) | 0, 90 + (y * 120 / 270) | 0, 200 - (x * 150 / 480) | 0])),
  item(id('P'), 'report.pdf', 'application/pdf', 'pdf', FIX('report.pdf')),
  item(id('M'), 'notes.md', 'text/markdown', 'file', Buffer.from('# 会议纪要\n\n- 卡片改成**虚线框**\n- 按钮改成图标\n\n```js\nconst x = 1;\n```\n')),
  item(id('C'), 'data.csv', 'text/csv', 'file', Buffer.from('产品,销量,备注\n卡片,120,"含逗号, 和引号""好"""\n图标,80,\n')),
  item(id('X'), 'sales.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'file', FIX('sales.xlsx')),
  item(id('D'), 'plan.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'file', FIX('plan.docx')),
  item(id('J'), 'config.json', 'application/json', 'file', Buffer.from('{\n  "name": "agentj",\n  "cards": [1, 2, 3],\n  "dashed": true\n}\n')),
  item(id('B'), 'big.pdf', 'application/pdf', 'pdf', BIG),
];
const PAGE2 = [
  item(id('T'), 'run.log', 'text/plain', 'file', Buffer.from('12:00 start\n12:01 load cards\n12:02 done\n')),
  item(id('H'), 'page.html', 'text/html', 'html', Buffer.from('<!doctype html><h1 style="color:#2d4f83">预览页</h1><script>parent.x=1</script><img src="https://example.com/x.png">')),
  item(id('W'), 'voice.wav', 'audio/wav', 'audio', wav()),
  item(id('S'), 'deck.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'file', FIX('deck.pptx')),
];
const REPLY1 = ['董事长，四种常见格式的测试文件都在下面：', '', '![测试图片](out/chart.png)', '', '其余文件见下方卡片。'].join('\n');
const REPLY2 = '日志、网页、录音和幻灯片：';

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const rec = parityRecorder('web/test/p183.mjs');
const ev = (p, s) => evaluate(p, s);
const gets = [], fwds = [];
let fwdAnswer = { ok: true };

function serve(st) {
  st.onApp = async (c, m, send) => {
    if (m.t === 'tg_fwd') { fwds.push(m); await send({ t: 'tg_fwd_res', r: m.r, ...fwdAnswer }); return true; }
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
async function paired(w, h, scheme = 'light', { caps = [], tgfwd = false, saveData = false } = {}) {
  fake.reset();
  fake.st.extraCaps = caps; fake.st.tgfwd = tgfwd;
  serve(fake.st);
  gets.length = 0; fwds.length = 0; fwdAnswer = { ok: true };
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  if (saveData) await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `Object.defineProperty(Navigator.prototype, 'connection', { get: () => ({ saveData: true, effectiveType: '4g' }) });` });
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
async function page(p, items = PAGE1, reply = REPLY1) {
  const turn = await fake.addTurn({ k: 'phone', dev: null, text: '发几个测试文件' }, reply, 'done');
  await fake.updateTurn(turn.id, { media: items, media_skip: [] });
  await waitFor(p, `document.querySelectorAll('.mcard').length === ${items.length}`, 15000);
  return turn;
}
const card = (mid) => `document.querySelector('#words .mcard[data-mid="${mid}"], #mstrip .mcard[data-mid="${mid}"]')`;
/** Auto-load is for what is on screen: bring each card into view and wait for it. */
async function loadAll(p, items) {
  for (const m of items) { await ev(p, `${card(m.mid)}.scrollIntoView({ block: 'center' })`); await waitFor(p, `${card(m.mid)}.dataset.state === 'ready'`, 15000); }
}
async function preview(p, mid, mode = 'doc') {
  await ev(p, `${card(mid)}.querySelector('.mact.pv').click()`);
  await waitFor(p, `!!document.querySelector('.mview') && !document.querySelector('.mview').hidden && document.querySelector('.mview').dataset.mode === '${mode}'`, 15000);
}
// P573 (#386): Markdown / text / code (JSON) open in the reply's own full-screen reader (#rd, file mode), not the viewer
async function readIn(p, mid) {
  await ev(p, `${card(mid)}.querySelector('.mact.pv').click()`);
  await waitFor(p, `!document.getElementById('rd').hidden && document.getElementById('rd').dataset.file === '1'`, 15000);
}
async function closeRd(p) {
  await key(p, 'Escape', { code: 'Escape' });
  await waitFor(p, `document.getElementById('rd').hidden`, 3000);
  await sleep(400);   // the reader's click guard after it closes
}
async function closeEsc(p) {
  await key(p, 'Escape', { code: 'Escape' });
  await waitFor(p, `document.querySelector('.mview').hidden`, 3000);
}

try {
  await C('p183-card: dashed outline, no fill, the reply card\'s icon system; small files auto-load, a big one waits with its size', async () => {
    const p = await paired(390, 844);
    try {
      await page(p);
      assert.equal(await ev(p, `${card(id('J'))}.dataset.state`), 'idle', 'a small card below the fold waits until it is on screen');
      await loadAll(p, PAGE1.filter((x) => x.mid !== id('B')));
      const look = await ev(p, `(() => { const c = ${card(id('P'))}, cs = getComputedStyle(c), b = c.querySelector('.mact.pv'), bs = getComputedStyle(b),
        r = document.getElementById('readBtn'), rs = getComputedStyle(r), svg = b.querySelector('svg.g0'), rsvg = r.querySelector('svg');
        const tool = getComputedStyle(document.querySelector('.tools .tool'));
        return { bg: cs.backgroundColor, style: cs.borderTopStyle, color: cs.borderTopColor, tool: tool.borderTopColor, toolStyle: tool.borderTopStyle,
          btn: [b.getBoundingClientRect().width, b.getBoundingClientRect().height], read: [r.getBoundingClientRect().width, r.getBoundingClientRect().height],
          svg: [svg.getBoundingClientRect().width, svg.getAttribute('stroke-width')], rsvg: [rsvg.getBoundingClientRect().width, rsvg.getAttribute('stroke-width')],
          btnBg: bs.backgroundColor, readBg: rs.backgroundColor, ink: bs.color, readInk: rs.color,
          labels: [...c.querySelectorAll('.mact')].filter((x) => !x.hidden).map((x) => x.getAttribute('aria-label') + '|' + x.title),
          text: [...c.querySelectorAll('.mact')].map((x) => x.textContent.replace('!', '').trim()).join('') }; })()`);
      assert.equal(look.bg, 'rgba(0, 0, 0, 0)', 'no fill');
      assert.equal(look.style, 'dashed'); assert.equal(look.toolStyle, 'dashed');
      assert.equal(look.color, look.tool, 'the same outline colour as the composer\'s tool buttons');
      assert.equal(look.btn[1], look.read[1], 'full-screen action retains the icon target height'); assert.ok(look.btn[0] >= look.read[0], 'visible label widens its target');
      // P251: reply slots are elastic (24px floor, 46px desktop pitch); file targets stay fixed.
      assert.ok(look.read[0] >= 24 && look.read[0] <= 46, 'reply target follows the P251 elastic range');
      assert.deepEqual(look.svg, look.rsvg, 'same glyph size and stroke');
      assert.equal(look.btnBg, look.readBg); assert.equal(look.ink, look.readInk);
      assert.deepEqual(look.labels, ['全屏查看|全屏查看', '下载|下载'], 'icons with labels; no share sheet in headless, no mail / Telegram without P184 / tgfile');
      assert.equal(look.text, '全屏查看', 'P260 visible full-screen entry; other actions stay icons');
      // the big PDF: not fetched, its size on the button
      assert.equal(await ev(p, `${card(id('B'))}.dataset.state`), 'idle');
      assert.equal(await ev(p, `${card(id('B'))}.querySelector('.mtap').textContent`), '点一下加载 · 6.0 MB');
      assert.ok(!gets.some((g) => g.mid === id('B')), 'the 6 MB PDF waits for a tap');
      assert.equal(await ev(p, `${card(id('D'))}.querySelector('.mbody').childElementCount`), 0, 'a loaded document card has no body — name, size and icons');
      await ev(p, `${card(id('P'))}.scrollIntoView({ block: 'start' })`);
      writeFileSync(join(OUT, 'p183-cards-phone-light.png'), await shoot(p));
      await ev(p, `document.getElementById('words').scrollIntoView({ block: 'start' })`);
      writeFileSync(join(OUT, 'p183-cards-phone-light-top.png'), await shoot(p));
      // tap the big one: it loads
      await ev(p, `${card(id('B'))}.querySelector('.mtap').click()`);
      await waitFor(p, `${card(id('B'))}.dataset.state === 'ready'`, 30000);
      assert.deepEqual(p.offsite, []); assert.deepEqual(p.problems, []);
    } finally { await p.dispose(); }
  });

  await C('p183-savedata: on data saver nothing loads by itself; a tap still does', async () => {
    const p = await paired(390, 844, 'light', { saveData: true });
    try {
      await page(p);
      await sleep(1500);
      assert.deepEqual(gets, [], 'no media_get without a tap');
      assert.equal(await ev(p, `${card(id('M'))}.querySelector('.mtap').textContent`), '点一下加载 · 89 B');
      await ev(p, `${card(id('M'))}.querySelector('.mact.pv').click()`);
      await waitFor(p, `!document.getElementById('rd').hidden && !!document.querySelector('#rdWords h1')`, 10000);   // P573: the reader
      assert.deepEqual(gets.map((g) => g.mid), [id('M')]);
      assert.deepEqual(p.problems, []);
    } finally { await p.dispose(); }
  });

  await C('p183-preview: pdf (pdf.js), markdown, csv, xlsx, docx, json, text, html, audio, picture, pptx → download; Esc closes', async () => {
    for (const [w, h, tag, scheme] of [[390, 844, 'phone', 'light'], [1280, 860, 'desktop', 'dark']]) {
      const p = await paired(w, h, scheme);
      try {
        await page(p);
        await preview(p, id('P'));
        await waitFor(p, `document.querySelectorAll('.mdoc .mpage[data-drawn=ok] canvas').length >= 1`, 20000);
        const pdf = await ev(p, `({ pages: document.querySelectorAll('.mdoc .mpage').length, title: document.querySelector('.mvtitle').textContent,
          ink: (() => { const c = document.querySelector('.mpage canvas'), x = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < x.length; i += 4) if (x[i] < 100) n++; return n; })() })`);
        assert.equal(pdf.pages, 2); assert.equal(pdf.title, 'report.pdf'); assert.ok(pdf.ink > 200, 'the text was drawn: ' + pdf.ink);
        writeFileSync(join(OUT, `p183-preview-pdf-${tag}-${scheme}.png`), await shoot(p));
        await closeEsc(p);

        await readIn(p, id('M'));
        await waitFor(p, `!!document.querySelector('#rdWords h1') && !!document.querySelector('#rdWords pre[data-rx=ok]')`, 10000);
        assert.equal(await ev(p, `document.querySelector('#rdWords strong').textContent`), '虚线框');
        await sleep(700);   // the reader ignores taps for 600 ms after it opens (the opening double tap's tail)
        await ev(p, `document.querySelector('#rdWords .codeblock .copy').click()`);
        await waitFor(p, `document.querySelector('#rdWords .codeblock .copy').dataset.done === '1'`, 3000);
        writeFileSync(join(OUT, `p183-preview-md-${tag}-${scheme}.png`), await shoot(p));
        await closeRd(p);

        await preview(p, id('C'));
        await waitFor(p, `!!document.querySelector('.mdoc table')`);
        assert.deepEqual(await ev(p, `[...document.querySelectorAll('.mdoc tr')].map((r) => [...r.children].map((c) => c.tagName[1] + ':' + c.textContent))`),
          [['H:产品', 'H:销量', 'H:备注'], ['D:卡片', 'D:120', 'D:含逗号, 和引号"好"'], ['D:图标', 'D:80', 'D:']]);
        await closeEsc(p);

        await preview(p, id('X'));
        await waitFor(p, `!!document.querySelector('.mdoc table')`, 15000);
        assert.deepEqual(await ev(p, `[...document.querySelectorAll('.mpvtab')].map((b) => b.textContent + ':' + b.getAttribute('aria-selected'))`), ['一季度:true', '二季度:false']);
        assert.equal(await ev(p, `document.querySelectorAll('.mdoc tbody tr').length`), 2);
        writeFileSync(join(OUT, `p183-preview-xlsx-${tag}-${scheme}.png`), await shoot(p));
        await ev(p, `document.querySelectorAll('.mpvtab')[1].click()`);
        assert.deepEqual(await ev(p, `[...document.querySelectorAll('.mdoc tbody td')].map((c) => c.textContent)`), ['预览', '200']);
        await closeEsc(p);

        await preview(p, id('D'));
        await waitFor(p, `!!document.querySelector('.mdoc iframe.mframe')`, 15000);
        const docx = await ev(p, `(() => { const f = document.querySelector('.mdoc iframe.mframe'); return { sandbox: f.getAttribute('sandbox'), csp: f.getAttribute('csp'),
          has: f.srcdoc.includes('季度计划') && f.srcdoc.includes('<strong>季度计划</strong>'), script: /<script/i.test(f.srcdoc) }; })()`);
        assert.deepEqual(docx, { sandbox: '', csp: "default-src 'none'; img-src data:; style-src 'unsafe-inline'", has: true, script: false });
        await sleep(300);
        writeFileSync(join(OUT, `p183-preview-docx-${tag}-${scheme}.png`), await shoot(p));
        await closeEsc(p);

        await readIn(p, id('J'));
        await waitFor(p, `!!document.querySelector('#rdWords pre[data-rx=ok] code.hljs span')`, 10000);
        writeFileSync(join(OUT, `p183-preview-json-${tag}-${scheme}.png`), await shoot(p));
        await closeRd(p);

        await preview(p, id('A'), 'image');
        assert.equal(await ev(p, `document.querySelector('.mview img.mzoom').naturalWidth`), 480);
        await closeEsc(p);

        // page two: text, html, audio, pptx
        await page(p, PAGE2, REPLY2);
        await loadAll(p, PAGE2);
        await readIn(p, id('T'));
        assert.equal(await ev(p, `document.getElementById('rdWords').innerText.trim()`), '12:00 start\n12:01 load cards\n12:02 done');
        await closeRd(p);
        await preview(p, id('H'));
        assert.deepEqual(await ev(p, `(() => { const f = document.querySelector('.mview iframe.mframe'); return [f.getAttribute('sandbox'), document.querySelector('.mnote').hidden]; })()`), ['', false]);
        await closeEsc(p);
        await preview(p, id('W'), 'player');
        assert.equal(await ev(p, `document.querySelector('.mview audio.mplayer').src.slice(0, 5)`), 'blob:');
        await closeEsc(p);
        await preview(p, id('S'));
        assert.deepEqual(await ev(p, `[document.querySelector('.mpvnone .mpvname').textContent, document.querySelector('.mpvnone .mpvnote').textContent, document.querySelector('.mpvnone button.mtap').textContent]`),
          ['deck.pptx', '这种文件暂时不能预览，可以下载后用手机上的应用打开。', '下载']);
        writeFileSync(join(OUT, `p183-preview-pptx-${tag}-${scheme}.png`), await shoot(p));
        await closeEsc(p);
        assert.deepEqual(p.offsite, [], 'only our own files and the session — no remote picture from the HTML, no CDN');
        assert.deepEqual(p.problems.filter((x) => !/example\.com|Content Security Policy.*img-src|Blocked script execution in 'about:srcdoc'/.test(x)), []);
      } finally { await p.dispose(); }
    }
  });

  await C('p183-keys: Space on a focused card previews, Space again closes (Quick Look); Enter too; the page does not scroll', async () => {
    const p = await paired(1280, 860);
    try {
      await page(p);
      await loadAll(p, [PAGE1[3]]);
      await ev(p, `${card(id('C'))}.focus()`);
      const top = await ev(p, `document.querySelector('main').scrollTop`);
      await key(p, ' ', { code: 'Space' });
      await waitFor(p, `!document.querySelector('.mview').hidden && !!document.querySelector('.mdoc table')`);
      await key(p, ' ', { code: 'Space' });
      await waitFor(p, `document.querySelector('.mview').hidden`);
      assert.equal(await ev(p, `document.activeElement === ${card(id('C'))}`), true, 'focus back on the card');
      assert.equal(await ev(p, `document.querySelector('main').scrollTop`), top, 'Space did not scroll the cards');
      await key(p, 'Enter', { code: 'Enter' });
      await waitFor(p, `!document.querySelector('.mview').hidden`);
      await closeEsc(p);
      assert.equal(await ev(p, `document.activeElement === ${card(id('C'))}`), true);
      assert.deepEqual(p.problems, []);
    } finally { await p.dispose(); }
  });

  await C('p183-telegram: with `tgfile` a card sends its file by id; not linked → where to link it; the reply bar shows the Telegram mark', async () => {
    let p = await paired(390, 844, 'light', { caps: ['tgfile'], tgfwd: true });
    try {
      await page(p);
      const tg = `${card(id('P'))}.querySelector('.mact.tg')`;
      assert.equal(await ev(p, `${tg}.hidden`), false);
      assert.equal(await ev(p, `${tg}.getAttribute('aria-label')`), '转发到 Telegram');
      assert.equal(await ev(p, `getComputedStyle(${tg}.querySelector('svg.g0')).fill === getComputedStyle(${tg}).color`), true, 'one colour = the ink');
      await ev(p, `${tg}.click()`);
      await waitFor(p, `${tg}.dataset.state === 'sent'`);
      assert.deepEqual(fwds.map((m) => [m.mid, m.id]), [[id('P'), undefined]], 'by media id only — no bytes go up');
      assert.equal(await ev(p, `${card(id('P'))}.querySelector('.mtg-status').textContent`), '已发到 Telegram');
      fwdAnswer = { ok: false, why: 'too_big' };
      await ev(p, `${card(id('B'))}.querySelector('.mact.tg').click()`);
      await waitFor(p, `${card(id('B'))}.querySelector('.mact.tg').dataset.state === 'failed'`);
      assert.equal(await ev(p, `${card(id('B'))}.querySelector('.mtg-status').textContent`), '文件超过 Telegram 的 50 MB 上限，没有发');
      // the reply card's forward button: the Telegram mark instead of the arrow — once the owner picked Telegram as the
      // forward destination (P536: explicit 「转发去向」, default system share)
      assert.equal(await ev(p, `import('./js/forward-choice.js').then((m) => (m.select('tg'), m.destination()))`), 'tg');
      const bar = await ev(p, `(() => { const b = document.getElementById('fwdBtn'); return [b.dataset.tg, getComputedStyle(b.querySelector('.gtg')).display, getComputedStyle(b.querySelector('.g0')).display]; })()`);
      assert.deepEqual(bar, ['1', 'block', 'none']);
      // P201: both places draw the same paper plane, same box and the same colour as the glyphs next to them
      assert.deepEqual(await ev(p, `(() => { const b = document.getElementById('fwdBtn').querySelector('.gtg'), f = ${tg}.querySelector('svg');
        const near = document.getElementById('copyReply').querySelector('svg'), fn = ${card(id('P'))}.querySelector('.mact.dl svg') || f;
        const box = (s) => getComputedStyle(s).width + 'x' + getComputedStyle(s).height;   // computed: a sent card shows its tick
        return [b.querySelector('path').getAttribute('d') === f.querySelector('path').getAttribute('d'), b.querySelector('path').getAttribute('d').startsWith('M20.69 3.91'),
          box(b) === box(near), box(f) === box(fn), getComputedStyle(b).fill === getComputedStyle(near).stroke, getComputedStyle(f).fill === getComputedStyle(fn).stroke]; })()`),
        [true, true, true, true, true, true], 'paper plane: one path, sibling size, sibling colour');
      await ev(p, `document.getElementById('rmbar').scrollIntoView({ block: 'end' })`);
      writeFileSync(join(OUT, 'p183-telegram-phone-light.png'), await shoot(p));
      assert.deepEqual(p.problems, []);
    } finally { await p.dispose(); }
    p = await paired(1440, 900, 'dark', { caps: ['tgfile'], tgfwd: true });   // P201: the paper plane on a computer, dark
    try {
      await page(p);
      await ev(p, `import('./js/forward-choice.js').then((m) => m.select('tg'))`);   // P536: the owner's explicit choice
      await ev(p, `document.getElementById('rmbar').scrollIntoView({ block: 'end' })`);
      writeFileSync(join(OUT, 'p183-telegram-desktop-dark.png'), await shoot(p));
      assert.deepEqual(p.problems, []);
    } finally { await p.dispose(); }
    p = await paired(390, 844, 'light', { caps: ['tgfile'], tgfwd: false });
    try {
      await page(p);
      await ev(p, `${card(id('P'))}.querySelector('.mact.tg').click()`);
      await waitFor(p, `${card(id('P'))}.querySelector('.mtg-status').textContent.includes('设置')`);
      assert.equal(await ev(p, `${card(id('P'))}.querySelector('.mtg-status').textContent`), '还没绑定 Telegram：到设置里绑定后，就能一键转发到你自己的 Telegram 私聊。');
      assert.deepEqual(fwds, [], 'nothing sent while Telegram is not linked');
      assert.deepEqual(await ev(p, `(() => { const b = document.getElementById('fwdBtn'); return [b.dataset.tg, getComputedStyle(b.querySelector('.gtg')).display, getComputedStyle(b.querySelector('.g0')).display]; })()`),
        ['', 'none', 'block'], 'no Telegram: the reply bar keeps the share arrow');
    } finally { await p.dispose(); }
    p = await paired(390, 844);
    try {
      await page(p);
      assert.equal(await ev(p, `${card(id('P'))}.querySelector('.mact.tg').hidden`), true, 'an older host (no tgfile): no Telegram icon');
    } finally { await p.dispose(); }
  });

  await C('p183-mail: the 转发到邮箱 icon appears once P184 registers its dialog, and hands it the file', async () => {
    const p = await paired(390, 844);
    try {
      await page(p);
      assert.equal(await ev(p, `${card(id('P'))}.querySelector('.mact.mail').hidden`), true, 'hidden while nothing is registered');
      await ev(p, `import('./js/mailfwd.js').then((m) => { m.registerMailForward((r) => { window.__mail = { kind: r.kind, mid: r.mid, name: r.name, mime: r.mime, bytes: r.bytes, page: typeof r.page };
        r.blob().then((b) => { window.__mailBlob = b.size; }); return true; }); return true; })`);
      assert.equal(await ev(p, `${card(id('P'))}.querySelector('.mact.mail').hidden`), false);
      assert.equal(await ev(p, `${card(id('P'))}.querySelector('.mact.mail').getAttribute('aria-label')`), '转发到邮箱');
      await ev(p, `${card(id('B'))}.querySelector('.mact.mail').click()`);
      await waitFor(p, `window.__mailBlob === ${BIG.length}`, 30000);
      assert.deepEqual(await ev(p, `window.__mail`), { kind: 'file', mid: id('B'), name: 'big.pdf', mime: 'application/pdf', bytes: BIG.length, page: 'number' });
      writeFileSync(join(OUT, 'p183-mail-icon-phone-light.png'), await shoot(p));
    } finally { await p.dispose(); }
  });

  await C('p183-shots: cards on phone / desktop × light / dark (review set)', async () => {
    for (const [w, h, tag] of [[390, 844, 'phone'], [1280, 860, 'desktop']]) {
      for (const scheme of ['light', 'dark']) {
        const p = await paired(w, h, scheme, { caps: ['tgfile'], tgfwd: true });
        try {
          await page(p);
          await loadAll(p, PAGE1.filter((x) => x.mid !== id('B')));
          await ev(p, `document.getElementById('words').scrollIntoView({ block: 'start' })`);
          writeFileSync(join(OUT, `p183-cards-${tag}-${scheme}.png`), await shoot(p));
          await ev(p, `${card(id('X'))}.scrollIntoView({ block: 'center' })`);
          writeFileSync(join(OUT, `p183-cards-${tag}-${scheme}-2.png`), await shoot(p));
        } finally { await p.dispose(); }
      }
    }
  });
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
console.log('parity results → ' + rec.write());
console.log(failed ? `p183: ${failed} failed` : 'p183: all passed');
process.exit(failed ? 1 : 0);
