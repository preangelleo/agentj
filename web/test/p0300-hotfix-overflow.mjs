// 0.30.0 hotfix (董事长 11-09): on a phone, Settings and every Settings sub-page scrolls vertically only —
// no horizontal scroll and no element wider than the viewport. Real Noise-paired fakehost, touch-emulated 390×844
// (iPhone) and 412×915 (Android), light + dark. HF_PUBLIC_DIR runs the same sweep against an older web build.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import * as H from './browser.mjs';

const phase = process.env.HF_PHASE || 'after';
const out = resolve(process.env.HF_OUT || 'reports/qa/0300-hotfix-settings');
const shots = process.env.HF_SHOTS !== '0';
mkdirSync(out, { recursive: true });
const web = await startWebServer(process.env.HF_PUBLIC_DIR ? { publicDir: resolve(process.env.HF_PUBLIC_DIR) } : {});
const fake = await startFakeHost(), B = await H.launch();
const rows = []; let failures = 0;

// Measures the page the owner sees: document / Settings scrollers must not scroll sideways, and no visible element may
// stick out of the viewport (elements inside a deliberate horizontal scroller — overflow-x auto/scroll — are exempt).
export const SWEEP = `(() => {
  const W = innerWidth, bad = [], scrollers = [];
  const de = document.documentElement, body = document.body;
  for (const n of [de, body, document.getElementById('settings'), document.getElementById('set-main'), ...document.querySelectorAll('.setsub:not([hidden]), .setsub__in')]) {
    if (!n || !n.getClientRects().length) continue;
    const cs = getComputedStyle(n);
    const canScrollX = n === de || n === body ? true : !/^(hidden|clip)$/.test(cs.overflowX);
    if (canScrollX && n.scrollWidth > n.clientWidth + 1) scrollers.push({ id: n.id || n.className || n.tagName, sw: n.scrollWidth, cw: n.clientWidth, ox: cs.overflowX });
  }
  const inScroller = (n) => { for (let p = n.parentElement; p && p !== body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
  const root = document.getElementById('settings');
  for (const n of root ? root.querySelectorAll('*') : []) {
    const r = n.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(n);
    if (cs.visibility === 'hidden' || cs.position === 'fixed') continue;
    if ((r.right > W + 1 || r.left < -1) && !inScroller(n)) bad.push({ tag: n.tagName, id: n.id, cls: String(n.className).slice(0, 60), left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width) });
  }
  return { W, scrollX: scrollX, rootScrollLeft: de.scrollLeft, bodyScrollLeft: body.scrollLeft, scrollers, bad: bad.slice(0, 12), badCount: bad.length };
})()`;

const PAGES = ['main', 'set-a2hs', 'set-remotes-open', 'set-mail-nav', 'set-auth-nav', 'set-accts-nav', 'set-local-files-nav', 'set-imsg-nav', 'set-cards-nav',
  'set-notify-nav', 'set-hooks-nav', 'set-contacts-nav', 'set-owner-nav'];
const DEVICES = [{ name: 'iphone', w: 390, h: 844 }, { name: 'android', w: 412, h: 915 }];

async function capture(p, name) { const { data } = await p.send('Page.captureScreenshot', { format: 'png' }); writeFileSync(`${out}/${name}.png`, Buffer.from(data, 'base64')); }

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
      for (const pg of PAGES) {
        if (pg !== 'main') {
          const ok = await H.evaluate(p, `(()=>{const b=document.getElementById('${pg}');if(!b)return false;if(!b.getClientRects().length){for(let n=b;n&&n.id!=='settings';n=n.parentElement)if(n.hidden)n.hidden=false;}if(!b.getClientRects().length)return false;b.click();return true})()`);
          if (!ok) { rows.push({ device: d.name, theme, page: pg, skipped: 'nav not visible' }); continue; }
          await H.sleep(500);
        }
        // scroll the visible page top→bottom like a thumb would, checking at each stop
        const stops = [0, 0.5, 1];
        let worst = null;
        for (const s of stops) {
          await H.evaluate(p, `(()=>{const sc=[...document.querySelectorAll('#settings .setsub:not([hidden]), #set-main')].find(n=>n.getClientRects().length&&n.scrollHeight>n.clientHeight)||document.getElementById('settings');if(sc)sc.scrollTop=(sc.scrollHeight-sc.clientHeight)*${s}})()`);
          await H.sleep(120);
          const m = await H.evaluate(p, SWEEP);
          if (!worst || m.badCount + m.scrollers.length > worst.badCount + worst.scrollers.length) worst = { ...m, stop: s };
          if (shots && s === 0.5 && (d.name === 'iphone')) {
            await capture(p, `${phase}-${d.name}-${theme}-${pg}`);
            // what an iOS thumb pan reaches on an over-wide page: capture the whole layout width (beyond the viewport)
            const wide = await H.evaluate(p, `Math.max(document.documentElement.scrollWidth, document.body.scrollWidth, innerWidth)`);
            const { data } = await p.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: wide, height: d.h, scale: 1 } });
            writeFileSync(`${out}/${phase}-${d.name}-${theme}-${pg}-fullwidth.png`, Buffer.from(data, 'base64'));
            rows.push({ device: d.name, theme, page: pg, layoutWidth: wide, info: 'full layout width capture' });
          }
        }
        rows.push({ device: d.name, theme, page: pg, ...worst });
        try {
          assert.equal(worst.scrollers.length, 0, 'horizontal scroller: ' + JSON.stringify(worst.scrollers));
          assert.equal(worst.badCount, 0, 'wider than viewport: ' + JSON.stringify(worst.bad));
        } catch (e) { failures++; console.error(`${d.name} ${theme} ${pg}: ${e.message}`); }
        if (pg !== 'main') await H.evaluate(p, `(()=>{const b=[...document.querySelectorAll('#settings .setsub:not([hidden]) .back, #settings [data-sub-back], #set-back')].find(n=>n.getClientRects().length);b?.click()})()`), await H.sleep(300);
      }
    } finally { await p.dispose(); }
  }
} finally {
  writeFileSync(`${out}/${phase}-overflow.json`, JSON.stringify({ phase, scope: 'Chromium, touch-emulated phone viewports, real Noise paired fakehost; not physical iPhone Safari', rows, failures }, null, 2));
  await B.close(); await fake.stop(); await web.stop();
}
console.log(JSON.stringify({ phase, failures, out }));
process.exitCode = failures ? 1 : 0;
