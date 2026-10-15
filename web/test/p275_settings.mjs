#!/usr/bin/env node
// P275 · Settings → 「下载 Agent J Brand Kit」 in a real headless Chromium against the fake host (never :9222).
//   node web/test/p275_settings.mjs → reports/qa/p275/settings-{zh,en}-{390,1440}-{light,dark}.png
// Checks: the row is the last item of the panel, visible after scrolling, ≥ 44 px tall, a line icon, opens agentj.app
// /docs/robots/#brand-kit in a new tab (the page itself does not navigate), no console problems, no horizontal overflow.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate as ev, navigate, waitState, shoot, sleep } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p275/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let n = 0;
try {
  for (const lang of ['zh', 'en']) for (const w of [390, 1440]) for (const scheme of ['light', 'dark']) {
    fake.reset();
    fake.st.prefs = { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: true, session_mode: 'shared' } };
    const h = w === 390 ? 844 : 900;
    const p = await newPage(B, w, h, scheme, { touch: w === 390, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await ev(p, `document.getElementById('setBtn').click()`);
      await sleep(300);
      await ev(p, `document.getElementById('set-brandkit').scrollIntoView({block:'center',behavior:'instant'})`);
      await sleep(200);
      const a = await ev(p, `(() => { const a = document.getElementById('set-brandkit'), r = a.getBoundingClientRect(), main = document.getElementById('set-main');
        const items = [...main.children].filter((x) => x.offsetParent);
        return { href: a.href, target: a.target, rel: a.rel, label: a.querySelector('b').textContent, note: a.querySelector('small').textContent,
          h: Math.round(r.height), visible: r.top >= 0 && r.bottom <= innerHeight, last: items[items.length - 1] === a,
          icon: !!a.querySelector('svg[aria-hidden="true"] path') && getComputedStyle(a.querySelector('svg')).fill === 'none',
          color: getComputedStyle(a).color, deco: getComputedStyle(a.querySelector('b')).textDecorationLine,
          overflow: document.documentElement.scrollWidth > innerWidth || main.scrollWidth > main.clientWidth }; })()`);
      assert.equal(a.href, 'https://agentj.app/docs/robots/#brand-kit');
      assert.equal(a.target, '_blank'); assert.equal(a.rel, 'noopener');
      assert.equal(a.label, lang === 'en' ? 'Download Agent J Brand Kit' : '下载 Agent J Brand Kit');
      assert.match(a.note, /bot/);
      assert.ok(a.h >= 44, `tap ${a.h}`); assert.ok(a.visible); assert.ok(a.last, 'last item of the panel'); assert.ok(a.icon, 'line icon');
      assert.equal(a.deco, 'none'); assert.equal(a.overflow, false);
      // the link opens elsewhere: the app page itself stays where it is
      const before = await ev(p, 'location.href');
      await ev(p, `document.getElementById('set-brandkit').addEventListener('click', (e) => { window.__opened = e.currentTarget.target; e.preventDefault(); }, { once: true }); document.getElementById('set-brandkit').click()`);
      assert.equal(await ev(p, 'window.__opened'), '_blank'); assert.equal(await ev(p, 'location.href'), before);
      assert.deepEqual(p.problems.filter((x) => !/favicon/.test(x)), []);
      writeFileSync(OUT + `settings-${lang}-${w}-${scheme}.png`, await shoot(p, 'unused'));
      n++;
    } finally { await p.dispose(); }
  }
  console.log(`p275 settings: ${n} combinations OK → reports/qa/p275/`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
process.exit(0);
