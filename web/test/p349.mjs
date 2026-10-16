// P349 (backlog #39, Leo 10-16: 「左边和右边都能够有一个等边距……两边撑满，不管它是多少秒」) — the audio player inside a file
// card fills the card's content box with equal left / right margins, whatever the clip's length; and the long-press page
// screenshot (pageshot.js), which cannot paint a native <audio>, draws a player stand-in instead of the empty block in
// Leo's 151313 screenshot. On the paired page against the fake host, zh + en × 390 and the fold's outer screen (344) and
// inner screen (673); one short (1 s) and one long (100 s) clip side by side.
//   node web/test/p349.mjs [chromium|webkit]      (default chromium; gates p349-phone / p349-webkit)
// WebKit on Linux has no media stack (an <audio> with a source can take the page down), so there the player keeps its
// layout but never gets bytes (as in p205_dl_webkit.mjs) — the geometry is what is tested; physical iPhone stays with Leo.
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const ENGINE = process.argv[2] || 'chromium';
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P349_SHOTS || join(ROOT, 'reports/qa/p349');
mkdirSync(OUT, { recursive: true });
const sha = (b) => createHash('sha256').update(b).digest('hex');
function wav(secs, rate = 4000) {
  const n = secs * rate, data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(6000 * Math.sin(2 * Math.PI * 330 * i / rate)), i * 2);
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVE', 8); h.write('fmt ', 12); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write('data', 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}
const SHORT = wav(1), LONG = wav(100);
const ITEMS = [
  { mid: 'S'.repeat(22), name: 'agentj-demo-voice.wav', mime: 'audio/wav', kind: 'audio', bytes: SHORT.length, sha256: sha(SHORT), ref: 'out/agentj-demo-voice.wav' },
  { mid: 'L'.repeat(22), name: 'long-briefing.wav', mime: 'audio/wav', kind: 'audio', bytes: LONG.length, sha256: sha(LONG), ref: 'out/long-briefing.wav' },
];
const BYTES = new Map([[ITEMS[0].mid, SHORT], [ITEMS[1].mid, LONG]]);
const REPLY = {
  zh: '董事长，这条多媒体演示可以直接给朋友看。\n\n🎙️ **语音**：用你本人的中文克隆声音配的一段介绍，点开就能播放。\n\n[贾维斯语音介绍](out/agentj-demo-voice.wav)\n\n[长版简报](out/long-briefing.wav)\n',
  en: 'Here is the multimedia demo you can show friends.\n\n🎙️ **Voice**: an intro in your cloned voice, tap to play.\n\n[Jarvis voice intro](out/agentj-demo-voice.wav)\n\n[Long briefing](out/long-briefing.wav)\n',
};
const SIZES = [[390, 844, '390'], [344, 882, 'fold-outer'], [673, 841, 'fold-inner']];

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost();
const B = await pw[ENGINE].launch(ENGINE === 'chromium' ? { executablePath: process.env.CHROMIUM || '/usr/bin/chromium' } : {});
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
async function paired(w, h, lang) {
  fake.reset(); serve(fake.st);
  const ctx = await B.newContext({ viewport: { width: w, height: h }, isMobile: ENGINE === 'chromium' ? true : undefined, hasTouch: true, deviceScaleFactor: 2 });
  const p = await ctx.newPage();
  p.problems = []; p.on('pageerror', (e) => p.problems.push(String(e)));
  if (ENGINE === 'webkit') await p.addInitScript(() => { Object.defineProperty(HTMLMediaElement.prototype, 'src', { configurable: true, get() { return this.getAttribute('data-src') || ''; }, set(v) { this.setAttribute('data-src', v); } }); });
  await p.goto(fake.newPairing(web.url + '?lang=' + lang));
  await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 20000 });
  await fake.approve();
  await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 20000 });
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  const turn = await fake.addTurn({ k: 'phone', dev: null, text: lang === 'zh' ? '发个语音演示' : 'send the voice demo' }, REPLY[lang], 'done');
  await fake.updateTurn(turn.id, { media: ITEMS, media_skip: [] });
  await p.waitForFunction(() => document.querySelectorAll('.mcard audio').length === 2, null, { timeout: 20000 });
  return { ctx, p };
}
// the card's content box (inside border + padding) and the player's box, both cards
const GEOM = () => [...document.querySelectorAll('.mcard[data-kind=audio]')].map((c) => {
  const a = c.querySelector('audio'), cr = c.getBoundingClientRect(), ar = a.getBoundingClientRect(), cs = getComputedStyle(c);
  const bl = parseFloat(cs.borderLeftWidth), br = parseFloat(cs.borderRightWidth);
  const foot = c.querySelector('.mfoot').getBoundingClientRect();
  return { left: ar.left - (cr.left + bl), right: (cr.right - br) - ar.right, aw: ar.width, ah: ar.height, cw: cr.width - bl - br,
    footL: foot.left - (cr.left + bl), footR: (cr.right - br) - foot.right, words: document.getElementById('words').getBoundingClientRect().width };
});
async function C(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); } catch (e) { failed++; console.log('  ✗ ' + name + ': ' + (e.stack || e)); }
}
try {
  for (const lang of ['zh', 'en']) for (const [w, h, tag] of SIZES) {
    await C(`${ENGINE} ${lang} ${tag}: the player fills the card with equal margins, short and long clip alike`, async () => {
      const { ctx, p } = await paired(w, h, lang);
      try {
        await p.evaluate(() => document.querySelector('.mcard[data-kind=audio]').scrollIntoView({ block: 'start' }));
        await p.waitForTimeout(400);
        const g = await p.evaluate(GEOM);
        console.log('    ' + JSON.stringify(g));
        for (const x of g) {
          assert.ok(Math.abs(x.left - x.right) <= 1, `equal margins: left ${x.left} right ${x.right}`);
          assert.ok(x.left >= 6 && x.left <= 16, `a card margin, not a centred gap: ${x.left}`);
          assert.ok(x.aw >= x.cw - 32, `fills the card: player ${x.aw} of ${x.cw}`);
          assert.ok(x.ah >= 30, `the player is drawn: height ${x.ah}`);
        }
        assert.ok(Math.abs(g[0].aw - g[1].aw) <= 0.5, `width does not depend on the length: ${g[0].aw} vs ${g[1].aw}`);
        writeFileSync(join(OUT, `p349-${ENGINE}-${lang}-${tag}.png`), await p.screenshot());
        // the long-press screenshot: a stand-in where the native player is (no empty block)
        const shot = await p.evaluate(async () => {
          const { snapshotSVG } = await import('./js/pageshot.js');
          const { svg } = await snapshotSVG();
          return { stand: (svg.match(/data-pageshot="audio"/g) || []).length, audioTag: /<audio/i.test(svg), long: svg.includes('0:00 / 1:40'), short: svg.includes('0:00 / 0:01') };
        });
        assert.equal(shot.audioTag, false, 'no live <audio> in the snapshot');
        assert.equal(shot.stand, 2, 'one stand-in per player');
        if (ENGINE === 'chromium') assert.ok(shot.long && shot.short, 'the stand-in shows each clip\'s length');
        if (ENGINE === 'chromium') {
          const png = await p.evaluate(async () => {
            const { capturePage } = await import('./js/pageshot.js');
            const f = await capturePage();
            const b = new Uint8Array(await f.arrayBuffer()); let s = ''; for (const x of b) s += String.fromCharCode(x); return btoa(s);
          });
          writeFileSync(join(OUT, `p349-${ENGINE}-${lang}-${tag}-pageshot.png`), Buffer.from(png, 'base64'));
        }
        assert.deepEqual(p.problems, []);
      } finally { await ctx.close(); }
    });
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
console.log(failed ? `p349 ${ENGINE}: ${failed} FAILED` : `p349 ${ENGINE}: ALL PASS`);
process.exit(failed ? 1 : 0);
