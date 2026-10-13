// P205 item 6 — the file card's 下载 in Safari's engine (Playwright WebKit, iPhone 390×844 with an iPhone user agent), on the
// paired page against the fake host: in a Safari tab the icon saves 思念.mp3 under its own name with the exact bytes; as
// a home-screen app (navigator.standalone) — where WebKit opens nothing for a blob link — the icon opens the share sheet with
// the file (its "Save to Files" is the download). Not physical iPhone acceptance (the report says what Leo should try).
// Not in the parity run (needs the owner's Playwright + PLAYWRIGHT_BROWSERS_PATH).
// Usage: node web/test/p205_dl_webkit.mjs
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const sha = (b) => createHash('sha256').update(b).digest('hex');
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1';
const MP3 = Buffer.concat([Buffer.from('ID3\x04\x00\x00\x00\x00\x00\x00', 'latin1'), randomBytes(1_060_000)]);
const MD = Buffer.from('# 文案\n\n思念如歌。\n');
const ITEMS = [
  { mid: 'A'.repeat(22), name: '思念.mp3', mime: 'audio/mpeg', kind: 'audio', bytes: MP3.length, sha256: sha(MP3), ref: '~/work/audio/memorial/思念.mp3' },
  { mid: 'M'.repeat(22), name: '文案.md', mime: 'text/markdown', kind: 'file', bytes: MD.length, sha256: sha(MD), ref: '~/work/audio/memorial/文案.md' },
];
const BYTES = new Map([[ITEMS[0].mid, MP3], [ITEMS[1].mid, MD]]);
const REPLY = '董事长，音频生成好了：\n\n- [思念.mp3](~/work/audio/memorial/思念.mp3)\n- [文案.md](~/work/audio/memorial/文案.md)\n';
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost();
const B = await pw.webkit.launch();
const results = {};
let failed = 0;
function serve(st) {
  st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const b = BYTES.get(m.mid);
    for (let i = 0; i < 8; i++) {
      const o = m.o + i * 45056;
      if (o >= b.length) break;
      const d = b.subarray(o, Math.min(b.length, o + 45056));
      await send({ t: 'media_chunk', mid: m.mid, o, d: Buffer.from(d).toString('base64url'), last: o + d.length >= b.length });
    }
    return true;
  };
}
async function paired(standalone) {
  fake.reset(); serve(fake.st);
  const ctx = await B.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, userAgent: UA, acceptDownloads: true });
  const p = await ctx.newPage();
  p.problems = []; p.on('pageerror', (e) => p.problems.push(String(e)));
  // WebKit on Linux has no media stack (an <audio> with a source can take the page down); the player is not what is tested here
  await p.addInitScript(() => { Object.defineProperty(HTMLMediaElement.prototype, 'src', { configurable: true, get() { return this.getAttribute('data-src') || ''; }, set(v) { this.setAttribute('data-src', v); } }); });
  if (standalone) await p.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true });
    window.__shared = [];
    navigator.canShare = () => true;
    navigator.share = async (d) => { window.__shared.push({ names: d.files.map((f) => f.name), sizes: d.files.map((f) => f.size) }); };
  });
  await p.goto(fake.newPairing(web.url + '?lang=zh'));
  await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 20000 });
  await fake.approve();
  await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 20000 });
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  const turn = await fake.addTurn({ k: 'phone', dev: null, text: '把音频发我' }, REPLY, 'done');
  await fake.updateTurn(turn.id, { media: ITEMS, media_skip: [] });
  await p.waitForFunction(() => document.querySelectorAll('.mcard').length === 2, null, { timeout: 15000 });
  return { ctx, p };
}
const dlBtn = (p, mid) => p.locator(`.mcard[data-mid="${mid}"] .mact.dl`);
async function C(name, fn) {
  try { await fn(); results[name] = 'PASS'; console.log('  ✓ ' + name); } catch (e) { failed++; results[name] = 'FAIL'; console.log('  ✗ ' + name + ': ' + (e.stack || e)); }
}
try {
  await C('webkit-dl-tab: Safari tab — 下载 saves the file under its own name, exact bytes (中文 name; audio and Markdown)', async () => {
    const { ctx, p } = await paired(false);
    try {
      for (const [m, bytes] of [[ITEMS[0], MP3], [ITEMS[1], MD]]) {
        const [d] = await Promise.all([p.waitForEvent('download', { timeout: 20000 }), dlBtn(p, m.mid).tap()]);
        assert.equal(d.suggestedFilename(), m.name);
        assert.equal(sha(readFileSync(await d.path())), sha(bytes), m.name + ': exact bytes');
      }
      assert.match(await p.locator('#toast').textContent(), /文案\.md/);
      assert.deepEqual(p.problems, []);
    } finally { await ctx.close(); }
  });
  await C('webkit-dl-app: home-screen app — 下载 opens the share sheet with the file (Save to Files), no blob link', async () => {
    const { ctx, p } = await paired(true);
    let dl = 0; p.on('download', () => dl++);
    try {
      await dlBtn(p, ITEMS[0].mid).tap();
      await p.waitForFunction(() => window.__shared.length === 1, null, { timeout: 20000 });
      assert.deepEqual(await p.evaluate(() => window.__shared[0]), { names: [ITEMS[0].name], sizes: [MP3.length] });
      await p.waitForTimeout(500);
      assert.equal(dl, 0, 'no blob download in the home-screen app');
      assert.deepEqual(p.problems, []);
    } finally { await ctx.close(); }
  });
} finally { await B.close(); await fake.stop(); await web.stop(); }
console.log(JSON.stringify(results));
console.log(failed ? `${failed} FAILED` : 'ALL PASS');
process.exit(failed ? 1 : 0);
