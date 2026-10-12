#!/usr/bin/env node
// P163: the phone web app (m.agentj.app) and `agentj admin` draw <select> the way agentj.app does (P145 block, now in
// brand/base.css): no native arrow, a token-coloured chevron 16 px from the right border, text 44 px clear, light + dark.
//   node web/test/p163-select.mjs   → reports/qa/p163/select-{web,admin}-{light,dark}.png
// Real Chromium on the real pages (the web app served locally; the admin page from a real `agentj admin` with a throw-away
// state dir). A select is added to each page (the shipped screens open them only deep in flows); the negative control turns
// brand/base.css off and must see the native look again.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { launch, newPage, navigate, evaluate, shoot } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p163/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const HOST = fileURLToPath(new URL('../../host/', import.meta.url));

async function startAdmin() {
  const dir = mkdtempSync(join(tmpdir(), 'aj-p163-'));
  const env = { ...process.env, AGENTJ_STATE_DIR: join(dir, 's'), XDG_CONFIG_HOME: join(dir, 'c') };
  const py = ['run', '--offline', 'python', '-P', '-c',
    'from agentj.state import State; State().init(relay="ws://127.0.0.1:1")'];
  await new Promise((ok, no) => spawn('uv', py, { cwd: HOST, env, stdio: 'ignore' }).on('exit', (c) => (c ? no(new Error('init ' + c)) : ok())));
  const p = spawn('uv', ['run', '--offline', 'python', '-P', '-m', 'agentj.cli', 'admin', '--events', 'jsonl', '--no-stdin'],
    { cwd: HOST, env, stdio: ['ignore', 'pipe', 'inherit'] });
  const rl = createInterface({ input: p.stdout });
  const line = await new Promise((ok) => rl.once('line', ok));     // keep reading afterwards: its "stopped" line needs a reader
  const port = JSON.parse(line).port;     // the page itself is static: no login needed to load it (the link is not used)
  return { url: `http://127.0.0.1:${port}/`, stop: async () => {
    const gone = new Promise((ok) => p.once('exit', ok)); p.kill('SIGTERM'); await gone; rmSync(dir, { recursive: true, force: true }); } };
}

const MEASURE = `(() => {
  const s = document.createElement('select'); s.className = 'aj-select'; s.id = 'p163sel';
  for (const t of ['一个很长很长的选项，看看文字会不会压到箭头上', 'B']) { const o = document.createElement('option'); o.textContent = t; s.append(o); }
  s.style.cssText = 'position:fixed;left:16px;top:120px;width:300px;z-index:99999';
  document.getElementById('p163sel')?.remove(); document.body.append(s);
  const cs = getComputedStyle(s), r = s.getBoundingClientRect();
  const pos = cs.backgroundPositionX.split(',').map((x) => x.trim());
  return { appearance: cs.appearance, image: cs.backgroundImage, pad: parseFloat(cs.paddingInlineEnd), width: r.width, pos,
           arrowColor: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() };
})()`;

const web = await startWebServer();
const admin = await startAdmin();
const B = await launch();
let failed = 0;
try {
  for (const [name, url] of [['web', web.url], ['admin', admin.url]]) {
    for (const scheme of ['light', 'dark']) {
      const p = await newPage(B, 390, 844, scheme, { allow: [url] });
      try {
        await navigate(p, url);
        const m = await evaluate(p, MEASURE);
        assert.equal(m.appearance, 'none', `${name}/${scheme}: native arrow replaced`);
        assert.match(m.image, /linear-gradient.*linear-gradient/, `${name}/${scheme}: chevron drawn with two gradients`);
        assert.equal(m.pad, 44, `${name}/${scheme}: text keeps 44 px clear of the arrow`);
        assert.equal(m.pos.length, 2, JSON.stringify(m.pos));
        assert.match(m.pos[1], /100% - 16px/, `${name}/${scheme}: arrow 16 px from the right border (${m.pos[1]})`);
        assert.ok(m.arrowColor, `${name}/${scheme}: --text-muted defined (arrow follows the theme)`);
        writeFileSync(OUT + `select-${name}-${scheme}.png`, await shoot(p, 'unused'));
        // negative control: without brand/base.css the same select is native again
        const off = await evaluate(p, `(() => { for (const l of document.querySelectorAll('link[href*="base.css"]')) l.disabled = true;
          return getComputedStyle(document.getElementById('p163sel')).appearance; })()`);
        assert.notEqual(off, 'none', `${name}/${scheme}: the control can tell the difference`);
        console.log(`  ✓ ${name}-${scheme}`);
      } catch (e) {
        failed++; console.log(`  ✗ ${name}-${scheme}: ${e.message}`);
      } finally { await p.dispose(); }
    }
  }
} finally {
  await B.close(); await web.stop(); await admin.stop();
}
console.log(failed ? `✗ ${failed} failed` : '✓ P163 select style: web + admin, light + dark');
process.exit(failed ? 1 : 0);
