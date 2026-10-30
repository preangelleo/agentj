#!/usr/bin/env node
// P565 追加 C (#348, 董事长 10-28 14:27 iPhone: 「长按批准已经没有动态效果了，按下去看不到那个状态栏从左到右」).
// The approval card's 长按批准 / 批量批准 fill must move left → right from the press, on Safari's engine (Playwright WebKit,
// iPhone UA) and Chromium (Android-sized touch + a desktop mouse), at 390 and 1440 wide, light and dark — WHILE the host keeps
// talking. Root cause: every snapshot re-renders the card, renderCard hides #apprBtns for a moment and measureCmd (P154) reads
// layout in between; that flush cancelled the CSS width transition and the fill jumped to full. 追加 C 补充 (董事长 10-28 15:1x
// 「以前就很好……从来没让你改这个特效」): the look is the earliest one exactly (7e45af4f1: width 0 → 100 %, var(--state) at .55,
// no colour change under the finger, an early release snaps back); relay.js holdFill runs that width as a Web Animation from
// the press. Checked from the fill's painted width, sampled every frame in the page — not from a class name:
//   1. held: the share follows the clock (|share − t/1200| < 0.2 from 20 % to 80 %), never decreases, never full before 85 %,
//      while ≥ 3 card re-renders land during the hold (host `status` messages every 120 ms); the button keeps its resting
//      colours mid-hold and the fill is var(--state) at .55
//   2. let go early (~600 ms): straight back to 0 (right after the release and every frame after)
//   3. held to the end: the signed answer goes out (fake host), the fill kept up with the clock to its last frame
//   4. 批量批准 (the second hold button) moves the same way
// Input: Chromium 390 = CDP touch events (real touch → pointer events), 1440 = the real mouse. WebKit-on-Linux: any pointer
// input reaches speaker.unlock() (Web Audio) and kills the web process here, so audio is absent in the page and the press is a
// dispatched PointerEvent on the button (the page's own handlers run; the iPhone's real touch stays with Leo's device).
//   node web/test/p565_hold.mjs                                         → after (asserts) + frames
//   P565_PHASE=before P565_PUBLIC=<old tree>/web/public node …           → before: frames + curves only
// Frames: $P565_OUT (default reports/qa/p565/hold) — <engine>-<w>-<scheme>-<phase>-<ms>ms.png (+ -release), clip = the
// buttons only (no address bar, no pairing link, no token). Curves: p565-hold-<phase>.json.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const pw = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const phase = process.env.P565_PHASE === 'before' ? 'before' : 'after';
const check = phase === 'after' || process.env.P565_ASSERT === '1';   // P565_ASSERT=1 with the old tree: the checks must fail there
const OUT = resolve(process.env.P565_OUT || 'reports/qa/p565/hold');
mkdirSync(OUT, { recursive: true });
const ENGINES = (process.env.P565_ENGINES || 'chromium,webkit').split(',');
const HOLD = 1200;
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1';
const ANDROID = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36';
const hex = (n) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.P565_PUBLIC ? { publicDir: resolve(process.env.P565_PUBLIC) + '/' } : {}) });
const fake = await startFakeHost();
const browsers = {};
let failed = 0, passed = 0;
const curves = [];
const ok = async (name, fn) => { try { await fn(); passed++; console.log('  ✓ ' + name); } catch (e) { failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); } };

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
    async up(id) {
      if (engine === 'webkit') return p.evaluate((i) => document.getElementById(i).dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, isPrimary: true, pointerType: 'touch', pointerId: 7 })), id);
      if (w < 600) return cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      return p.mouse.up();
    },
  };
}
// the host keeps talking during the hold (what a live host does: status / usage / activity) — each one re-renders the card
async function chatter(ms) { const end = Date.now() + ms; let i = 0; while (Date.now() < end) { await fake.send({ t: 'status', s: 'waiting', kind: 'permission', agent: 'claude', name: 'Agent J' + (i++ % 2 ? '' : ' ') }); await sleep(120); } }
const PAINT = (i) => { const b = document.getElementById(i), f = b.querySelector('.fill'), cb = getComputedStyle(b), cf = getComputedStyle(f);
  const tok = (v) => { const q = document.createElement('i'); q.style.color = `var(${v})`; b.append(q); const c = getComputedStyle(q).color; q.remove(); return c; };
  return { bg: cb.backgroundColor, fg: cb.color, fill: cf.backgroundColor, op: cf.opacity, state: tok('--state'), strongBg: tok('--btn-strong-bg'), strongInk: tok('--btn-strong-ink') }; };
async function hold(p, io, id, releaseAt) {
  await p.evaluate((i) => window.__p565.watch(i), id);
  const rest = await p.evaluate(PAINT, id);
  await io.down(id);
  const total = releaseAt ?? HOLD + 150;
  await chatter(300);
  let held = await p.evaluate(PAINT, id);                       // the colours under the finger, mid-hold
  if (io.forceActive) held = { ...held, ...(await io.forceActive(id)) };
  await chatter(total - 300);
  if (releaseAt != null) { await io.up(id); await sleep(450); await toastGone(p); }
  const r = await p.evaluate(() => { const R = window.__p565; R.stop = true; return { rec: R.rec, renders: R.renders, up: R.up, atUp: R.atUp, afterUp: R.afterUp }; });
  return { ...r, rest, held };
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
// an early release says 「按住不放才会批准」 in a centred notice over the buttons: the next press waits until it has gone
async function toastGone(p) { await sleep(300); await p.waitForFunction(() => { const t = document.getElementById('toast'); return !t || !t.classList.contains('on'); }, null, { timeout: 6000 }); await sleep(250); }
const at = (rec, t) => { let best = null; for (const s of rec) if (s[0] <= t) best = s; return best ? best[1] : 0; };

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
        // frames: one press per frame, the host talking, let go after the frame (never reaches the approval)
        for (const ms of [0, 300, 600, 900]) {
          const n = fake.st.answers.length;
          await p.evaluate(() => window.__p565.watch('apprAllow'));
          await io.down('apprAllow');
          const chat = chatter(ms + 60);
          await sleep(ms); await shot(p, `${OUT}/${tag}-${phase}-${String(ms).padStart(4, '0')}ms.png`);
          await chat; await io.up('apprAllow');
          await sleep(60); if (ms === 900) await shot(p, `${OUT}/${tag}-${phase}-0900ms-release.png`);
          // a slow screenshot can outlast the hold on a loaded machine: then the card was approved — bring a new one
          await sleep(250);
          const gone = await p.evaluate(() => document.body.dataset.sheet !== '1' || document.getElementById('apprBtns').hidden);
          if (gone || fake.st.answers.length !== n) await openCard(p); else await toastGone(p);
        }
        // 1 + 2: hold ~600 ms with re-renders, then let go
        const early = await hold(p, io, 'apprAllow', 600);
        curves.push({ tag, phase, case: 'early-release', ...early });
        // 4: the batch button, held ~800 ms
        const batch = await hold(p, io, 'apprBatch', 800);
        curves.push({ tag, phase, case: 'batch', ...batch });
        // 3: to the end
        const n0 = fake.st.answers.length;
        const full = await hold(p, io, 'apprAllow', null);
        full.sent = fake.st.answers.length - n0;
        curves.push({ tag, phase, case: 'full', ...full });
        if (check) {
          for (const [name, c, upAt] of [['allow', early, early.up], ['batch', batch, batch.up]]) {
            assert.ok(c.renders >= 3, `${name}: the card re-rendered during the hold (${c.renders})`);
            const held = c.rec.filter(([t]) => t <= upAt - 20);
            assert.ok(held.length >= 4, `${name}: frames sampled while held (${held.length}; headless WebKit paints slowly under load)`);
            for (let i = 1; i < held.length; i++) assert.ok(held[i][1] >= held[i - 1][1] - 0.01, `${name}: never goes back while held ${JSON.stringify(held.slice(Math.max(0, i - 2), i + 1))}`);
            for (const [t, s] of held) {
              if (t >= 0.2 * HOLD && t <= Math.min(upAt - 20, 0.8 * HOLD)) assert.ok(Math.abs(s - t / HOLD) < 0.2, `${name}: at ${t} ms the fill is ${s}, the clock says ${(t / HOLD).toFixed(2)} ${JSON.stringify(held.slice(0, 12))}`);
              if (t < 0.85 * HOLD) assert.ok(s < 0.95, `${name}: full already at ${t} ms (${s}) — the old jump`);
            }
            const sUp = c.atUp, after = c.rec.filter(([t]) => t > upAt);
            assert.ok(sUp > 0.3 && Math.abs(sUp - upAt / HOLD) < 0.2, `${name}: had moved by the release (${sUp} at ${upAt} ms)`);
            // as at first (7e45af4f1): letting go puts it straight back to 0 — right after the release, and every frame after
            assert.ok(c.afterUp.length && c.afterUp.every((s) => s < 0.01), `${name}: back to 0 at once (${sUp} → ${JSON.stringify(c.afterUp)})`);
            assert.ok(after.filter(([t]) => t > upAt + 40).every(([, s]) => s < 0.01), `${name}: stays at 0 ${JSON.stringify(after.slice(0, 4))}`);
            // the earliest colours: the button keeps its resting colours under the finger; the fill is the state colour at .55
            assert.deepEqual([c.held.bg, c.held.fg], [c.rest.bg, c.rest.fg], `${name}: no colour change while held`);
            // 长按批准 wears exactly its 7e45af4f1 colours, at rest (it has the focus once the card opens) and under the finger
            if (name === 'allow') for (const v of [c.rest, c.held]) assert.deepEqual([v.bg, v.fg], [v.strongBg, v.strongInk], `allow: --btn-strong-bg / --btn-strong-ink ${JSON.stringify(v)}`);
            assert.equal(c.held.fill, c.held.state, `${name}: the fill is var(--state)`);
            assert.equal(c.held.op, '0.55', `${name}: at .55`);
          }
          assert.equal(full.sent, 1, 'held to the end: one answer sent');
          assert.equal(fake.st.answers.at(-1).ok, true, 'the answer is an approval');
          assert.ok(full.renders >= 3, 'the card re-rendered during the full hold');
          const before = full.rec.filter(([t]) => t < HOLD - 30);
          const last = before.at(-1);
          assert.ok(last && last[0] >= 0.6 * HOLD && Math.abs(last[1] - last[0] / HOLD) < 0.2, `the fill kept up to the end (${JSON.stringify(last)})`);
          assert.ok(before.filter(([t]) => t < 0.85 * HOLD).every(([, s]) => s < 0.95), 'never full early');
          assert.deepEqual(p.problems, [], 'no page errors / crash');
        }
      } finally { await ctx.close(); }
    });
  }
} finally {
  writeFileSync(`${OUT}/p565-hold-${phase}.json`, JSON.stringify(curves.map((c) => ({ ...c, rec: c.rec.map(([t, s]) => [Math.round(t), s]) })), null, 0));
  for (const B of Object.values(browsers)) await B.close().catch(() => {});
  await fake.stop(); await web.stop();
}
console.log(`p565_hold (${phase}): ${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
