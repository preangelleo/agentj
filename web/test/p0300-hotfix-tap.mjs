// 0.30.0 hotfix (董事长 11-09): Settings scrolls with a stutter and taps sometimes do nothing on a phone.
// Touch-emulated iPhone/Android viewports against the real Noise-paired fakehost:
//  · hit test — every visible button / link / summary / switch in each Settings page: the element at its centre must be
//    the control itself (or inside it); nothing laid over it may swallow the tap;
//  · horizontal pan — a vertical thumb swipe must not move the page sideways (scrollX / scrollLeft stay 0);
//  · motion — no infinite animation may keep running behind Settings (the water is hidden there);
//  · long tasks — counted during the swipes (reported; the gate is the three rules above).
// HF_PUBLIC_DIR runs the same probe against an older web build.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import * as H from './browser.mjs';

const phase = process.env.HF_PHASE || 'after';
const out = resolve(process.env.HF_OUT || 'reports/qa/0300-hotfix-settings');
mkdirSync(out, { recursive: true });
const web = await startWebServer(process.env.HF_PUBLIC_DIR ? { publicDir: resolve(process.env.HF_PUBLIC_DIR) } : {});
const fake = await startFakeHost(), B = await H.launch();
const rows = []; let failures = 0;

const PAGES = ['main', 'set-a2hs', 'set-remotes-open', 'set-mail-nav', 'set-auth-nav', 'set-accts-nav', 'set-local-files-nav',
  'set-imsg-nav', 'set-cards-nav', 'set-notify-nav', 'set-hooks-nav', 'set-contacts-nav', 'set-owner-nav'];
const DEVICES = [{ name: 'iphone', w: 390, h: 844 }, { name: 'android', w: 412, h: 915 }];

const PROBE = `(() => {
  const W = innerWidth, Hh = innerHeight, miss = [];
  const page = [...document.querySelectorAll('#settings .setsub:not([hidden])')].find(n => n.getClientRects().length) || document.getElementById('set-main');
  let n = 0;
  for (const c of page.querySelectorAll('button, a[href], summary, [role=switch], input[type=checkbox], label.sw, .aj-action')) {
    const r = c.getBoundingClientRect();
    if (!r.width || !r.height || c.disabled) continue;
    const cs = getComputedStyle(c); if (cs.visibility === 'hidden' || cs.pointerEvents === 'none') continue;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || x > W || y < 70 || y > Hh - 10) continue;   // only what is on screen below the header
    n++;
    const hit = document.elementFromPoint(x, y);
    if (c.closest('dialog:not([open]), [hidden]')) continue;
    const fold = c.closest('details'); if (fold && !fold.open && !c.closest('summary')) continue;   // folded away, not on screen
    if (!hit || !(hit === c || c.contains(hit) || hit.contains(c) || (c.tagName === 'INPUT' && c.labels && [...c.labels].some(l => l.contains(hit)))))
      miss.push({ ctl: (c.id || c.className || c.tagName).toString().slice(0, 50), hit: hit ? (hit.id || String(hit.className) || hit.tagName).slice(0, 60) : null,
                  path: hit ? [hit, hit.parentElement, hit.parentElement?.parentElement].filter(Boolean).map(e => e.tagName + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : '')).join(' < ') : null });
  }
  // only what runs BEHIND Settings (covered by it): the visible header's own orb is part of Settings
  const behind = (el) => !!el && !el.closest('#settings, .top, header');
  const anims = document.getAnimations().filter(a => a.playState === 'running' && a.effect?.getTiming?.().iterations === Infinity && behind(a.effect.target))
    .map(a => { const t = a.effect.target; return (t?.id || String(t?.className?.baseVal ?? t?.className ?? t?.tagName)).slice(0, 40) + ':' + (a.animationName || ''); });
  const water = document.getElementById('water'), wcs = water && getComputedStyle(water);
  return { checked: n, miss, anims: [...new Set(anims)], water: wcs ? wcs.display : null,
           scrollX, rootLeft: document.documentElement.scrollLeft, bodyLeft: document.body.scrollLeft };
})()`;

try {
  for (const d of DEVICES) for (const theme of ['light', 'dark']) {
    fake.reset();
    fake.st.prefs = { appearance: { language: 'zh', theme }, voice: { speak_replies: false }, agent: { session_mode: 'shared' } };
    const p = await H.newPage(B, d.w, d.h, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
    try {
      await H.navigate(p, fake.newPairing(web.url + '?lang=zh'));
      await H.waitState(p, 'awaiting-approval'); await fake.approve(); await H.waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J', ...(process.env.HF_CTX ? { usage: { context_pct: Number(process.env.HF_CTX) } } : {}) });   // HF_CTX: a known context level (the water shows, as on a real phone)
      await H.evaluate(p, `(async()=>{document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();document.getElementById('setBtn').click();await document.fonts.ready})()`);
      await H.waitFor(p, `document.getElementById('set-main')?.getClientRects().length>0`);
      await H.sleep(400);
      await H.evaluate(p, `(()=>{window.__lt=[];try{new PerformanceObserver(l=>{for(const e of l.getEntries())window.__lt.push(Math.round(e.duration))}).observe({type:'longtask',buffered:false})}catch{}})()`);
      for (const pg of PAGES) {
        if (pg !== 'main') {
          const ok = await H.evaluate(p, `(()=>{const b=document.getElementById('${pg}');if(!b)return false;if(!b.getClientRects().length){for(let n=b;n&&n.id!=='settings';n=n.parentElement)if(n.hidden)n.hidden=false;}if(!b.getClientRects().length)return false;b.click();return true})()`);
          if (!ok) { rows.push({ device: d.name, theme, page: pg, skipped: 'nav missing' }); continue; }
          await H.sleep(450);
        }
        await H.evaluate(p, `window.__lt.length=0`);
        const misses = [], pans = []; let first = null;
        for (let i = 0; i < 3; i++) {
          const m = await H.evaluate(p, PROBE);
          if (!first) first = m;
          misses.push(...m.miss);
          if (m.scrollX || m.rootLeft || m.bodyLeft) pans.push({ i, scrollX: m.scrollX, rootLeft: m.rootLeft, bodyLeft: m.bodyLeft });
          // a slightly diagonal thumb swipe upward (real thumbs are never perfectly vertical)
          await H.swipe(p, d.w * 0.55, d.h * 0.78, d.w * 0.40, d.h * 0.30, 10, 160);
          await H.sleep(250);
        }
        const end = await H.evaluate(p, PROBE);
        if (end.scrollX || end.rootLeft || end.bodyLeft) pans.push({ i: 'end', scrollX: end.scrollX, rootLeft: end.rootLeft, bodyLeft: end.bodyLeft });
        misses.push(...end.miss);
        const lt = await H.evaluate(p, `window.__lt.slice()`);
        const row = { device: d.name, theme, page: pg, checked: first.checked + end.checked, miss: misses.slice(0, 8), missCount: misses.length,
                      pans, anims: first.anims, water: first.water, longTasks: lt.length, longTaskMs: lt.reduce((a, b) => a + b, 0) };
        rows.push(row);
        try {
          assert.equal(row.missCount, 0, 'tap swallowed: ' + JSON.stringify(row.miss));
          assert.equal(pans.length, 0, 'page moved sideways: ' + JSON.stringify(pans));
          assert.equal(row.anims.length, 0, 'infinite animation behind Settings: ' + JSON.stringify(row.anims));
        } catch (e) { failures++; console.error(`${d.name} ${theme} ${pg}: ${e.message}`); }
        if (pg !== 'main') await H.evaluate(p, `(()=>{const b=[...document.querySelectorAll('#settings .setsub:not([hidden]) .back, #settings [data-sub-back], #set-back')].find(n=>n.getClientRects().length);b?.click()})()`), await H.sleep(300);
      }
    } finally { await p.dispose(); }
  }
} finally {
  writeFileSync(`${out}/${phase}-tap.json`, JSON.stringify({ phase, scope: 'Chromium, touch-emulated phone viewports (CDP touch swipes), real Noise paired fakehost; not physical iPhone Safari', rows, failures }, null, 2));
  await B.close(); await fake.stop(); await web.stop();
}
console.log(JSON.stringify({ phase, failures, out }));
process.exitCode = failures ? 1 : 0;
