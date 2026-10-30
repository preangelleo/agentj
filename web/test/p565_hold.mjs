#!/usr/bin/env node
// P565 追加 C (#348) measured the 1.2 s 长按批准 fill here. P573 (chairman 10-29, item 3: 「新增『点一下』按钮档，不长按」)
// retired the hold: 批准 / 批量批准 answer on ONE tap — press and release on the same button — on Safari's engine (Playwright
// WebKit, iPhone UA) and Chromium (Android-sized touch + a desktop mouse), at 390 and 1440 wide, light and dark, WHILE the host
// keeps talking (every snapshot re-renders the card; renderCard hides #apprBtns while measureCmd reads layout — P154).
//   1. a tap on 批准: exactly one signed approval leaves at the release (fake host), with ≥ 1 card re-render during the press
//   2. press, slide off the button, let go: nothing is sent (a finger that changes its mind answers nothing)
//   3. a tap on 批量批准: the batch answer leaves
//   4. the button keeps its resting colours under the finger (P565 追加 C 补充: no hover / active colour; --btn-strong-*)
//   5. no hold hint, no fill animation: the fill never moves
// Input: Chromium 390 = CDP touch events (real touch → pointer events), 1440 = the real mouse. WebKit-on-Linux: any pointer
// input reaches speaker.unlock() (Web Audio) and kills the web process here, so audio is absent in the page and the press is a
// dispatched PointerEvent on the button (the page's own handlers run; the iPhone's real touch stays with Leo's device).
//   node web/test/p565_hold.mjs              (P565_ENGINES=chromium to skip WebKit)
// Frames: $P565_OUT (default reports/qa/p565/hold) — <engine>-<w>-<scheme>-tap.png, clip = the buttons only (no address
// bar, no pairing link, no token).
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const pw = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const OUT = resolve(process.env.P565_OUT || 'reports/qa/p565/hold');
mkdirSync(OUT, { recursive: true });
const ENGINES = (process.env.P565_ENGINES || 'chromium,webkit').split(',');
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1';
const ANDROID = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36';
const hex = (n) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.P565_PUBLIC ? { publicDir: resolve(process.env.P565_PUBLIC) + '/' } : {}) });
const fake = await startFakeHost();
const browsers = {};
const parity = parityRecorder('web/test/p565_hold.mjs');
let failed = 0, passed = 0;
const ok = async (name, fn) => { try { await fn(); parity.record(name, true); passed++; console.log('  ✓ ' + name); } catch (e) { parity.record(name, false); failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); } };

// In the page: the fill's painted share every frame (computed transform → its box; the old CSS drew it with width)
const RECORDER = () => {
  window.__p565 = { rec: [], renders: 0, t0: null, up: null };
  const R = window.__p565;
  // the painted share: the fill's box after its transform (new) or its width (old CSS), over the button's box
  const scaleOf = (b) => {
    const f = b.querySelector('.fill'); if (!f || !f.isConnected) return 0;
    const w = b.getBoundingClientRect().width; return w ? Math.min(1, f.getBoundingClientRect().width / w) : 0;
  };
  R.scaleOf = scaleOf;
  new MutationObserver(() => { if (R.t0 !== null) R.renders++; }).observe(document.getElementById('sheetTitle'), { childList: true, characterData: true, subtree: true });
  R.watch = (id) => {
    const b = document.getElementById(id);
    R.rec = []; R.renders = 0; R.t0 = null; R.up = null;
    b.addEventListener('pointerdown', () => { if (R.t0 === null) R.t0 = performance.now(); }, { capture: true, once: true });
    R.atUp = null; R.afterUp = [];
    // at the release: the share just before the page's own handler, then right after it (a task later) and 40 ms on —
    // headless WebKit can paint 150 ms apart, so the frame sampler alone could miss the moment of the release
    document.addEventListener('pointerup', () => {
      if (R.up !== null || R.t0 === null) return;
      R.up = performance.now() - R.t0; R.atUp = scaleOf(b);
      setTimeout(() => { R.afterUp.push(scaleOf(b)); setTimeout(() => R.afterUp.push(scaleOf(b)), 40); }, 0);
    }, { capture: true, once: true });
    R.btnState = () => ({ cls: b.className, anims: b.querySelector('.fill')?.getAnimations?.().length ?? null });
    const gen = R.gen = (R.gen || 0) + 1;                       // one sampling loop at a time
    const tick = () => { if (R.gen !== gen) return; if (R.t0 !== null) R.rec.push([+(performance.now() - R.t0).toFixed(1), +scaleOf(b).toFixed(4)]); if (!R.stop) requestAnimationFrame(tick); };
    R.stop = false; requestAnimationFrame(tick);
  };
};

async function pagePair(engine, w, scheme) {
  const phone = w < 600, B = browsers[engine];
  const ctx = await B.newContext({ viewport: { width: w, height: phone ? 844 : 900 }, deviceScaleFactor: phone ? 2 : 1, colorScheme: scheme, serviceWorkers: 'block',
    ...(phone ? { isMobile: engine === 'webkit', hasTouch: true, userAgent: engine === 'webkit' ? IPHONE : ANDROID } : {}) });
  // WebKit-on-Linux: Web Audio from a pointer gesture crashes the web process here (see the header)
  if (engine === 'webkit') await ctx.addInitScript(() => { window.Audio = undefined; window.AudioContext = undefined; window.webkitAudioContext = undefined; });
  const p = await ctx.newPage();
  p.problems = [];
  p.on('crash', () => p.problems.push('web process crashed'));
  p.on('pageerror', (e) => p.problems.push(String(e)));
  fake.reset();
  fake.st.prefs = { appearance: { language: 'zh', theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
  await p.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost') + '?lang=zh'));
  await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 20000 });
  await fake.approve();
  await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 20000 });
  await p.evaluate(async () => { for (const id of ['phone-env-dismiss', 'a2hs-ok']) document.getElementById(id)?.click(); await document.fonts.ready; });
  await fake.send({ t: 'status', s: 'waiting', kind: 'permission', agent: 'claude', name: 'Agent J' });
  await p.evaluate(RECORDER);
  return { p, ctx };
}
let cardId = null;
async function openCard(p) {
  fake.st.holdAnswers = true;
  if (cardId) { await fake.askDone(cardId, 'allow'); await p.waitForFunction(() => document.body.dataset.sheet === '0', null, { timeout: 8000 }); await sleep(300); }
  cardId = hex(16);
  await fake.ask({ id: cardId, tool: 'Bash', summary: 'agentj wechat setup --phone', ttl: 120, batch: 'Bash: agentj', batch_max: 20, batch_secs: 600 });
  await p.waitForFunction(() => document.body.dataset.sheet === '1' && !document.getElementById('apprBtns').hidden && !document.getElementById('apprBatch').hidden, null, { timeout: 8000 });
  await sleep(350);
}
async function center(p, id) { return p.evaluate((i) => { const r = document.getElementById(i).getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }, id); }
// press / release with the engine's input (see the header)
function input(engine, p, w) {
  let cdp = null;
  return {
    async down(id) {
      await p.waitForFunction((i) => { const b = document.getElementById(i), r = b.getBoundingClientRect(); const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!h && b.contains(h); }, id, { timeout: 6000 })
        .catch(async (e) => { throw new Error(e.message + ' — on top: ' + await p.evaluate((i) => { const b = document.getElementById(i), r = b.getBoundingClientRect(); const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return h ? h.outerHTML.slice(0, 200) + ' / ' + (h.closest('[id]')?.id) : 'nothing'; }, id)); });
      const c = await center(p, id);
      if (engine === 'webkit') return p.evaluate((i) => document.getElementById(i).dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, isPrimary: true, pointerType: 'touch', pointerId: 7 })), id);
      if (w < 600) { cdp ??= await p.context().newCDPSession(p); return cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: c.x, y: c.y, id: 1 }] }); }
      await p.mouse.move(c.x, c.y); return p.mouse.down();
    },
    // Chromium: also read the colours with :active + :hover forced on (what an iPhone finger gets; CDP, as p458 does)
    forceActive: engine === 'webkit' ? null : async (id) => {
      cdp ??= await p.context().newCDPSession(p);
      await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
      const { root } = await cdp.send('DOM.getDocument');
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#' + id });
      await cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: ['active', 'hover', 'focus'] });
      const v = await p.evaluate(PAINT, id);
      await cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: [] });
      return { bg: v.bg, fg: v.fg };
    },
    async up(id, { off = false } = {}) {
      const c = await center(p, id), y = off ? c.y + 140 : c.y;   // off = the finger slid 140 px down, off the button
      if (engine === 'webkit') return p.evaluate(([i, x, yy]) => document.getElementById(i).dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, isPrimary: true, pointerType: 'touch', pointerId: 7, clientX: x, clientY: yy })), [id, c.x, y]);
      if (w < 600) {
        if (off) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: c.x, y, id: 1 }] });
        return cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      }
      if (off) await p.mouse.move(c.x, y, { steps: 4 });
      return p.mouse.up();
    },
  };
}
// the host keeps talking during the hold (what a live host does: status / usage / activity) — each one re-renders the card
async function chatter(ms) { const end = Date.now() + ms; let i = 0; while (Date.now() < end) { await fake.send({ t: 'status', s: 'waiting', kind: 'permission', agent: 'claude', name: 'Agent J' + (i++ % 2 ? '' : ' ') }); await sleep(120); } }
const PAINT = (i) => { const b = document.getElementById(i), f = b.querySelector('.fill'), cb = getComputedStyle(b), cf = getComputedStyle(f);
  const tok = (v) => { const q = document.createElement('i'); q.style.color = `var(${v})`; b.append(q); const c = getComputedStyle(q).color; q.remove(); return c; };
  return { bg: cb.backgroundColor, fg: cb.color, fill: cf.backgroundColor, op: cf.opacity, state: tok('--state'), strongBg: tok('--btn-strong-bg'), strongInk: tok('--btn-strong-ink') }; };
// one tap: press, ~120 ms with the host talking (≥ 1 re-render), release; the colours read under the finger
async function tap(p, io, id, { off = false } = {}) {
  await p.evaluate((i) => window.__p565.watch(i), id);
  const rest = await p.evaluate(PAINT, id);
  const n = fake.st.answers.length;
  await io.down(id);
  await chatter(260);
  let held = await p.evaluate(PAINT, id);
  if (io.forceActive) held = { ...held, ...(await io.forceActive(id)) };
  await io.up(id, { off });
  const t0 = Date.now();
  while (fake.st.answers.length === n && Date.now() - t0 < 1500) await sleep(25);
  const r = await p.evaluate(() => { const R = window.__p565; R.stop = true; return { rec: R.rec, renders: R.renders, toast: document.getElementById('toast')?.textContent || '' }; });
  return { ...r, rest, held, sent: fake.st.answers.length - n, ms: Date.now() - t0 };
}
async function shot(p, file) {
  const clip = await p.evaluate(() => {
    const els = ['apprBtns', 'apprBatch'].map((i) => document.getElementById(i)).filter((e) => e && !e.hidden && e.getClientRects().length);
    if (!els.length) return null;
    const rs = els.map((e) => e.getBoundingClientRect()), pad = 10;
    const x = Math.max(0, Math.min(...rs.map((r) => r.left)) - pad), y = Math.max(0, Math.min(...rs.map((r) => r.top)) - pad);
    return { x, y, width: Math.min(innerWidth, Math.max(...rs.map((r) => r.right)) + pad) - x, height: Math.min(innerHeight, Math.max(...rs.map((r) => r.bottom)) + pad) - y };
  });
  if (clip) await p.screenshot({ path: file, clip });
}
try {
  if (ENGINES.includes('chromium')) browsers.chromium = await pw.chromium.launch({ executablePath: process.env.CHROMIUM || '/usr/bin/chromium', args: ['--no-sandbox', '--disable-gpu', '--force-color-profile=srgb', '--hide-scrollbars'] });
  if (ENGINES.includes('webkit')) browsers.webkit = await pw.webkit.launch();
  for (const engine of ENGINES) for (const w of [390, 1440]) for (const scheme of ['light', 'dark']) {
    const tag = `${engine}-${w}-${scheme}`;
    await ok(tag, async () => {
      cardId = null;
      const { p, ctx } = await pagePair(engine, w, scheme);
      try {
        await openCard(p);
        const io = input(engine, p, w);
        // 2 first (the card stays): slide off and let go → nothing sent
        const off = await tap(p, io, 'apprAllow', { off: true });
        assert.equal(off.sent, 0, `slid off: nothing sent (${off.sent})`);
        assert.ok(await p.evaluate(() => document.body.dataset.sheet === '1' && !document.getElementById('apprBtns').hidden), 'slid off: the card is still up');
        await shot(p, `${OUT}/${tag}-tap.png`);
        // 1: one tap on 批准, the host talking
        const one = await tap(p, io, 'apprAllow');
        assert.equal(one.sent, 1, `one tap: one answer (${one.sent})`);
        assert.equal(fake.st.answers.at(-1).ok, true, 'the answer is an approval');
        assert.ok(one.ms < 1000, `sent at the release, not after a hold (${one.ms} ms)`);
        assert.ok(one.renders >= 1, `the card re-rendered during the press (${one.renders})`);
        assert.notEqual(one.toast.trim(), '按住不放才会批准', 'no hold hint');
        // 4 + 5: resting colours under the finger, the fill never moved
        for (const c of [off, one]) {
          assert.deepEqual([c.held.bg, c.held.fg], [c.rest.bg, c.rest.fg], 'no colour change under the finger');
          for (const v of [c.rest, c.held]) assert.deepEqual([v.bg, v.fg], [v.strongBg, v.strongInk], `--btn-strong-bg / --btn-strong-ink ${JSON.stringify(v)}`);
          assert.ok(c.rec.every(([, x]) => x < 0.01), `the fill never moves ${JSON.stringify(c.rec.slice(0, 6))}`);
        }
        // 3: a tap on 批量批准 (a fresh card)
        await openCard(p);
        const batch = await tap(p, io, 'apprBatch');
        assert.equal(batch.sent, 1, `batch: one answer (${batch.sent})`);
        assert.equal(fake.st.answers.at(-1).batch, true, 'the answer is the batch approval');
        assert.deepEqual(p.problems, [], 'no page errors / crash');
      } finally { await ctx.close(); }
    });
  }
} finally {
  for (const B of Object.values(browsers)) await B.close().catch(() => {});
  await fake.stop(); await web.stop();
}
console.log(`p565_hold (P573 one tap): ${passed} passed, ${failed} failed`);
parity.write();
process.exit(failed ? 1 : 0);
