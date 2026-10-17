#!/usr/bin/env node
// P367 · Settings → 账号和帮助: 「打开账号后台 · 邀请与积分 · 点数」 — the new 「点数」 link opens https://agentj.app/account/#credits in a
// new tab (the account page's credits block). Real page + local fake host, independent headless Chromium (never :9222).
// Checks: the link's href / target / rel, the three links on one row (wrapping only when narrow), every link ≥ 44 px tall,
// links ≥ 8 px apart, no horizontal overflow, no page errors. Shots (zh/en × light/dark × 390/1440) of the 账号和帮助 card
// → agentjarvis/reports/qa/p367/phone-settings-<w>-<scheme>-<lang>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../reports/qa/p367/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);

async function paired({ w, h, scheme, lang }) {
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: true, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'shared' } };
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  return p;
}

const AUDIT = `(() => {
  const card = document.getElementById('set-s-help');
  const row = card.querySelector('.setlinks');
  const links = [...row.querySelectorAll('a')].map((a) => { const r = a.getBoundingClientRect(); return { key: a.dataset.i18n, href: a.getAttribute('href'), target: a.target, rel: a.rel, text: a.textContent, h: r.height, top: r.top, left: r.left, right: r.right }; });
  const gaps = [];
  for (let i = 1; i < links.length; i++) if (Math.abs(links[i].top - links[i - 1].top) < 4) gaps.push(links[i].left - links[i - 1].right);
  return { links, gaps, overflowX: document.documentElement.scrollWidth > innerWidth || card.scrollWidth > card.clientWidth + 1 };
})()`;

try {
  for (const [w, h] of [[390, 844], [1440, 900]]) for (const scheme of ['light', 'dark']) for (const lang of ['zh', 'en']) {
    const tag = `${w}-${scheme}-${lang}`;
    const p = await paired({ w, h, scheme, lang });
    try {
      await ev(p, `document.querySelector('#setBtn').click()`);
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await ev(p, `document.getElementById('set-s-help').scrollIntoView({ block: 'center', behavior: 'instant' })`);
      await sleep(300);
      const a = await ev(p, AUDIT);
      const cr = a.links.find((l) => l.key === 'menu.credits');
      assert.ok(cr, 'the 点数 link is there');
      assert.equal(cr.href, 'https://agentj.app/account/#credits');
      assert.equal(cr.target, '_blank');
      assert.equal(cr.rel, 'noopener');
      assert.equal(cr.text, lang === 'en' ? 'Credits' : '点数');
      assert.deepEqual(a.links.map((l) => l.key), ['set.accountOpen', 'menu.invite', 'menu.credits']);
      for (const l of a.links) assert.ok(l.h >= 43.5, `${l.key} ${Math.round(l.h)} px tall`);
      for (const g of a.gaps) assert.ok(g >= 7.5, `links ${Math.round(g)} px apart`);
      assert.equal(a.overflowX, false, 'no horizontal overflow');
      assert.deepEqual(p.problems, [], p.problems.join('\n'));
      const r = await ev(p, `(() => { const r = document.getElementById('set-s-help').getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
      const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, r.x - 8), y: Math.max(0, r.y - 8), width: Math.min(w, r.w + 16), height: Math.min(h, r.h + 16), scale: 1 } });
      writeFileSync(`${OUT}phone-settings-${tag}.png`, Buffer.from(data, 'base64'));
      console.log(`  ✓ ${tag}`);
    } catch (e) { failed++; console.log(`  ✗ ${tag}: ${e && e.stack || e}`); } finally { await p.dispose(); }
  }
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `P367 phone: ${failed} failed` : 'P367 phone: all passed');
process.exit(failed ? 1 : 0);
