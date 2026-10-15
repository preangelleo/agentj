#!/usr/bin/env node
// P294 · evidence shots: Settings at 390 px, dark — the 回复字号 preview box with its 「预览」 label, and the 会话模式 section
// (shared: the note under 隔离运行; isolation off: 允许使用 Docker greyed). Fake host, independent headless Chromium.
//   node web/test/p294_shots.mjs   → reports/qa/p294/settings-{zh,en}-390-dark-{font,mode-shared,docker-off}.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p294/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
const at = (p, id) => ev(p, `(() => { const m = document.getElementById('set-main'), n = document.getElementById(${JSON.stringify(id)});
  m.scrollTop += n.getBoundingClientRect().top - m.getBoundingClientRect().top - 64; return true; })()`);
try {
  for (const lang of ['zh', 'en']) {
    fake.reset();
    fake.st.prefs = { appearance: { language: lang, theme: 'dark' }, voice: { speak_replies: false, wake_enabled: false },
      agent: { high_risk_warnings: false, session_mode: 'shared', isolation: true, allow_docker: false } };
    const p = await newPage(B, 390, 844, 'dark', { touch: true, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await ev(p, `document.getElementById('setBtn').click()`);
      await waitFor(p, `!document.getElementById('settings').hidden && !document.getElementById('set-iso-shared').hidden`);
      await at(p, 'set-h-read'); await sleep(300);
      assert.equal(await ev(p, `getComputedStyle(document.querySelector('.setsamplelab')).display !== 'none'`), true);
      writeFileSync(OUT + `settings-${lang}-390-dark-font.png`, await shoot(p, 'unused'));
      await at(p, 'set-mode'); await sleep(300);
      writeFileSync(OUT + `settings-${lang}-390-dark-mode-shared.png`, await shoot(p, 'unused'));
      fake.st.prefs = { ...fake.st.prefs, agent: { ...fake.st.prefs.agent, session_mode: 'independent', isolation: false } };
      await fake.send(fake.prefsMsg(fake.st.prefs));
      await waitFor(p, `document.getElementById('set-docker').disabled && document.getElementById('set-iso-shared').hidden`);
      await at(p, 'set-mode'); await sleep(300);
      writeFileSync(OUT + `settings-${lang}-390-dark-docker-off.png`, await shoot(p, 'unused'));
      console.log(`  ✓ ${lang}`);
    } finally { await p.dispose(); }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
process.exit(0);
