// P169 (PROTOCOL §23): the phone's theme, end to end on the page. The messages come from the real host code
// (agentj.theme: a custom theme applied with apply_dir, then phone_message), sent by the local fake host into the real
// page in an independent headless Chromium (never :9222). Without a reload:
//   a) factory robots (default): nothing overridden, the robots dance from assets/robots-18.png
//   b) a custom theme repaints at once: state colours (the whole page + the status bar), the owner's card, the top-left
//      logo (blob:), the Send glyph mask, the theme's own 3 robots (their sheets, sizes and names)
//   c) dots: the factory dots preset (P150 look: blinking round dots, no dance) and a custom dots theme (its 4 colours)
//   d) back to factory: every override removed, blob: URLs revoked, the official logo back; the page never reloaded
//   e) a malformed message (wrong PNG sizes, bad colours) only falls back — factory robots, official logo
//   f) locked: the pairing / security screens keep the official logo whatever the theme says
// Shots → reports/qa/p169/screens/<lang>-<scheme>-<theme>.png.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
import { LONG } from './density_shots.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p169/screens');
const HOST = join(ROOT, 'agentjarvis/host');
const $ = (id) => `document.getElementById('${id}')`;
const NAME = 'AgentJ-Father';
export const CASES = { a: 'theme-factory-robots', b: 'theme-custom-hot', c: 'theme-dots', d: 'theme-reset', e: 'theme-malformed', f: 'theme-locked' };

/** The real host code makes the messages (python3 + the package source; agentj.theme needs no third-party module). */
export function hostMessages() {
  const code = `
import json, sys, tempfile, pathlib
sys.path[:0] = [${JSON.stringify(HOST)}, ${JSON.stringify(join(HOST, 'tests'))}]
from agentj import theme
import theme_fixtures as fx
base = pathlib.Path(tempfile.mkdtemp(prefix="p169-"))
out = {}
fx.make_theme(base / "owl"); theme.apply_dir(base / "st", base / "owl"); out["custom"] = theme.phone_message(base / "st")
fx.make_theme(base / "bare", disc_bg=False, id="bare-owl"); theme.apply_dir(base / "st", base / "bare"); out["bare"] = theme.phone_message(base / "st")
fx.make_theme(base / "dt", style="dots", id="owl-dots"); theme.apply_dir(base / "st", base / "dt"); out["customDots"] = theme.phone_message(base / "st")
theme.apply_preset(base / "st", "dots"); out["dots"] = theme.phone_message(base / "st")
theme.apply_preset(base / "st", "robots"); out["robots"] = theme.phone_message(base / "st")
print(json.dumps(out))`;
  const py = process.env.AJ_HOST_PY || 'python3';
  return JSON.parse(execFileSync(py, ['-I', '-c', code], { encoding: 'utf8', maxBuffer: 16 << 20 }));
}
/** rgb(…) / color(srgb …) → #rrggbb (Chromium serialises color-mix results as color(srgb …)). */
const hex = (c) => {
  const m = /^rgba?\(([^)]+)\)$/.exec(c) || /^color\(srgb ([^)]+)\)$/.exec(c);
  if (!m) return '';
  const k = c.startsWith('color(') ? 255 : 1;
  return '#' + m[1].split(/[\s,/]+/).filter(Boolean).slice(0, 3).map((x) => Math.round(+x * k).toString(16).padStart(2, '0')).join('');
};
const rgb = (h) => `rgb(${[1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)).join(', ')})`;

export async function runP169() {
  mkdirSync(OUT, { recursive: true });
  const M = hostMessages();
  const rec = parityRecorder('web/test/p169.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const step = async (c, f) => { try { await f(); rec.record(CASES[c], true); } catch (e) { rec.record(CASES[c], false); throw e; } };
  const busy = (n) => ({ t: 'busy', by: NAME, tasks: Array.from({ length: n }, (_, i) => ({ id: 'k' + i, who: ['掌枢', null, '玄枢'][i % 3], kind: 'ceo', label: 'job ' + i, since: 1760000000 + i, c: i })) });
  try {
    for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) {
      fake.reset();
      const p = await newPage(B, 390, 844, scheme, { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => { await evaluate(p, `document.activeElement?.blur()`); await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=600})`); writeFileSync(join(OUT, `${lang}-${scheme}-${name}.png`), await shoot(p, '')); await evaluate(p, `document.getAnimations().forEach(a=>a.play())`); };
      const cssVar = (k) => evaluate(p, `getComputedStyle(document.documentElement).getPropertyValue(${JSON.stringify(k)}).trim()`);
      const robots = () => evaluate(p, `[...${$('busyDots')}.querySelectorAll('i.rb:not(.bye)')].map(i=>({img:i.style.backgroundImage,size:i.style.backgroundSize,title:i.title,anim:getComputedStyle(i).animationName}))`);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await evaluate(p, `window.__p169 = 'same page'`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
        await fake.addTurn({ k: 'phone', dev: 'other', text: lang === 'zh' ? '帮我把主题换成夜猫子' : 'Switch my theme to Night owl' }, LONG[lang === 'zh' ? 0 : 1]);
        await waitFor(p, `${$('agent')}.textContent===${JSON.stringify(NAME)} && ${$('words')}.textContent.length>40`);
        await fake.send(busy(3));
        await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===3`);
        const factoryIdle = await evaluate(p, `getComputedStyle(document.body).backgroundColor`);

        await step('a', async () => {
          await fake.send(M.robots);
          await waitFor(p, `document.documentElement.dataset.look==='robots'`);
          check(!(await evaluate(p, `document.documentElement.getAttribute('style')||''`)).includes('--c-idle'), 'factory: no override');
          const r = await robots();
          check(r.length === 3 && r.every((x) => x.img.includes('assets/robots-18.png') && x.size === '234px 288px'), 'factory robots ' + JSON.stringify(r[0]));
          check(await evaluate(p, `${$('orb')}.getAttribute('src')`) === 'brand/img/logo-mark.png', 'official logo');
          if (scheme === 'light') await save('robots');
        });

        await step('b', async () => {
          await fake.send(M.custom);
          await waitFor(p, `document.documentElement.dataset.look==='night-owl'`);
          const c = M.custom.colors;
          check(await cssVar('--c-idle') === c.state.idle, 'idle variable');
          check(await evaluate(p, `getComputedStyle(document.body).backgroundColor`) !== factoryIdle, 'the page took the new idle colour');
          if (scheme === 'light') { const bg = await evaluate(p, `getComputedStyle(document.body).backgroundColor`); check(hex(bg) === c.state.idle, 'light: exactly the theme colour ' + bg); }
          await sleep(120);
          const tc = await evaluate(p, `document.querySelector('meta[name="theme-color"]').content`);
          check(tc.length === 7 && tc !== '#629887', 'the status bar follows ' + tc);
          const src = await evaluate(p, `document.querySelector('.om').dataset.src||''`);
          const card = { '': 'owner', agent: 'agent', host: 'computer', dev: 'developer', sys: 'system', task: 'system', cmd: 'system' }[src];
          check(card && hex(await evaluate(p, `getComputedStyle(document.querySelector('.om')).backgroundColor`)) === c.card[card], `card fill (${src || 'owner'} → ${card})`);
          await waitFor(p, `${$('orb')}.src.startsWith('blob:')`, 2000); check(true, 'logo from the theme');
          await waitFor(p, `${$('orb')}.complete && ${$('orb')}.naturalWidth===128`, 2000); check(true, 'the logo decodes (128 px)');
          check(!(await evaluate(p, `${$('orb')}.classList.contains('bare')`)), 'disc kept');
          check((await evaluate(p, `getComputedStyle(document.querySelector('.send .rocket')).maskImage || getComputedStyle(document.querySelector('.send .rocket')).webkitMaskImage`)).includes('blob:'), 'Send glyph from the theme');
          await waitFor(p, `!${$('busyDots')}.querySelector('i.rb.in')`, 2000);   // the new robots pop in first, then dance
          const r = await robots();
          check(r.length === 3 && r.every((x) => x.img.includes('blob:') && x.size === '234px 54px' && x.anim === 'rb-dance-18'), 'own robots dance ' + JSON.stringify(r[0]));
          check(r.map((x) => x.title).join() === (lang === 'zh' ? '一号,二号,三号' : 'One,Two,Three'), 'robot names from the theme ' + r.map((x) => x.title));
          await evaluate(p, `${$('busyDots')}.click()`);
          await waitFor(p, `!${$('busyDlg')}.hidden`);
          const big = await evaluate(p, `[...document.querySelectorAll('#busyBig i.rb')].map(i=>i.style.backgroundSize)`);
          check(big.length === 3 && big.every((s) => s === '312px 72px'), 'dialog: 24 px robots from the 12 px sheet ' + big);
          await evaluate(p, `${$('busyClose')}.click()`);
          await fake.send({ t: 'status', s: 'waiting', kind: 'question', agent: 'claude', name: NAME });
          await waitFor(p, `document.body.dataset.kind==='question'`).catch(() => {});
          await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
          await waitFor(p, `document.body.dataset.status==='working'`);
          if (scheme === 'light') check(hex(await evaluate(p, `getComputedStyle(document.body).backgroundColor`)) === c.state.working, 'working colour');
          check((await evaluate(p, `${$('orb')}.src`)).startsWith('blob:'), 'the theme logo stays in every state');
          await save('custom-working');
          await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
          await waitFor(p, `document.body.dataset.status==='idle'`);
          await save('custom');
          await fake.send(M.bare);
          await waitFor(p, `document.documentElement.dataset.look==='bare-owl'`);
          await waitFor(p, `${$('orb')}.classList.contains('bare') && getComputedStyle(${$('orb')}).backgroundColor==='rgba(0, 0, 0, 0)'`, 3000);   // after the .8 s fade
          check(true, 'disc:false = no light disc');
        });

        await step('c', async () => {
          await fake.send(M.dots);
          await waitFor(p, `document.documentElement.dataset.lookStyle==='dots' && ${$('busyDots')}.querySelectorAll('i.bd').length===3`);
          check(await evaluate(p, `${$('busyDots')}.querySelectorAll('i.rb').length`) === 0, 'no robots in dots mode');
          const d = await evaluate(p, `[...${$('busyDots')}.querySelectorAll('i.bd')].map(i=>({c:i.dataset.c,anim:getComputedStyle(i).animationName,w:i.getBoundingClientRect().width,r:getComputedStyle(i).borderRadius}))`);
          check(d.every((x) => x.anim === 'busy-blink' && x.w === 12 && x.r === '50%') && new Set(d.map((x) => x.c)).size === 3, 'blinking round dots ' + JSON.stringify(d));
          check(await cssVar('--busy-h0') === '#71bbde', 'factory dot colours');
          await waitFor(p, `${$('orb')}.getAttribute('src')==='brand/img/logo-mark.png'`, 2000); check(true, 'factory dots: official logo again');
          if (scheme === 'light') await save('dots');
          await fake.send({ ...busy(6) });
          await fake.send(M.customDots);
          await waitFor(p, `document.documentElement.dataset.look==='owl-dots' && ${$('busyDots')}.querySelectorAll('i.bd').length===6`);
          const cd = await evaluate(p, `[...${$('busyDots')}.querySelectorAll('i.bd')].map(i=>[i.dataset.c,getComputedStyle(i).getPropertyValue('--h').trim()])`);
          check(cd.every(([k]) => +k < 4) && cd[0][1] === '#2d4f83' && cd[4][1] === '#2d4f83', '4 theme pairs repeat ' + JSON.stringify(cd));
          await evaluate(p, `${$('busyDots')}.click()`);
          await waitFor(p, `!${$('busyDlg')}.hidden`);
          check(await evaluate(p, `document.querySelectorAll('#busyList i.bd').length`) === 6, 'dialog rows: dots');
          await evaluate(p, `${$('busyClose')}.click()`);
          await fake.send(busy(3));
        });

        await step('d', async () => {
          await fake.send(M.custom); await waitFor(p, `document.documentElement.dataset.look==='night-owl'`);
          const blob = await evaluate(p, `${$('orb')}.src`);
          await fake.send(M.robots); await waitFor(p, `document.documentElement.dataset.look==='robots' && ${$('busyDots')}.querySelectorAll('i.rb').length===3`);
          check(!(await evaluate(p, `document.documentElement.getAttribute('style')||''`)).match(/--c-|--src-|--send-mask|--busy-/), 'overrides removed');
          check(await evaluate(p, `getComputedStyle(document.body).backgroundColor`) === factoryIdle, 'factory idle colour back');
          await waitFor(p, `${$('orb')}.getAttribute('src')==='brand/img/logo-mark.png'`, 2000); check(true, 'official logo back');
          check(await evaluate(p, `import('./js/theme.js').then(m=>m.liveUrls().length)`) === 0 && blob.startsWith('blob:'), 'old blob: URLs revoked, none held');
          check(!(await evaluate(p, `document.documentElement.dataset.thSend`)), 'rocket back');
          check(await evaluate(p, `window.__p169`) === 'same page', 'never reloaded');
        });

        await step('e', async () => {
          const bad = JSON.parse(JSON.stringify(M.custom));
          bad.id = 'broken'; bad.colors.state.idle = 'red; background:url(x)'; bad.logo.png = M.custom.busy.s12; bad.busy.n = 4;
          bad.busy.names.push({ zh: '四号', en: 'Four' });
          await fake.send(bad);
          await waitFor(p, `document.documentElement.dataset.look==='broken'`);
          check(!(await evaluate(p, `document.documentElement.getAttribute('style')||''`)).includes('--c-idle'), 'a bad colour is dropped');
          await sleep(100); check(await evaluate(p, `${$('orb')}.getAttribute('src')`) === 'brand/img/logo-mark.png', 'a wrong-size logo is dropped');
          check((await robots()).every((x) => x.img.includes('assets/robots-18.png')), 'sheets that do not match n → factory robots');
          await fake.send(M.robots); await waitFor(p, `document.documentElement.dataset.look==='robots'`);
        });

        await step('f', async () => {
          await fake.send(M.custom); await waitFor(p, `document.documentElement.dataset.look==='night-owl'`);
          const locked = await evaluate(p, `[...document.querySelectorAll('img')].filter(i=>i.id!=='orb').map(i=>i.getAttribute('src')).filter(s=>s&&!s.startsWith('brand/'))`);
          check(locked.length === 0, 'every other logo stays official ' + locked);
          check(await evaluate(p, `document.querySelector('link[rel=manifest]')?.getAttribute('href')||'manifest.webmanifest'`) === 'manifest.webmanifest', 'home-screen icon untouched');
          await fake.send(M.robots); await waitFor(p, `document.documentElement.dataset.look==='robots'`);
        });
        check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + JSON.stringify(p.problems.slice(0, 2)));
      } finally { await p.dispose(); }
    }
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host + real host theme code; not a physical phone', checks, complete: true }, null, 2) + '\n');
    console.log(`p169: ${checks} checks passed`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP169();
