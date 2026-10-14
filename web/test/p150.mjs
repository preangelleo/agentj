// P150 (PROTOCOL §22): 「在办事项」 dots at the right of the reply card's header + the global centred dialog (busy list,
// 「服务器已升级」). Real page + local fake host, independent headless Chromium (never :9222).
//   a) no worker → no dots, the header is taller and the name lower; 1 / 3 workers → 1 / 3 dots, fixed distinct colours,
//      blinking Heavy ⇄ Light, right of the name, never over it, inside the right 2/3
//   b) a tap on the dots opens the centred square: 「有 N 个 Agent 在后台干活」 + 「<Agent 名> 正在替你监控」 + who · what;
//      ESC, a tap outside and 知道了 close it; the list going empty closes it; one fewer worker = one fewer dot
//   c) the version notice is the same centred square (title 服务器已升级, button 刷新); it never takes focus, ESC = later
//   d) the Agent name explains itself (title); the flashing page capsule's ring is a light Lotion veil
// Shots → reports/qa/p150/screens/<lang>-<theme>-<state>-<n>.png; `--anim` also writes the blink loop (mp4 + gif).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
import { LONG } from './density_shots.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p150/screens');
const $ = (id) => `document.getElementById('${id}')`;
const NAME = 'AgentJ-Father';
const W = { zh: [['掌枢', '发布收尾', 0], [null, '转录视频', 1], ['玄枢', '巡检代理', 2]],
  en: [['Relay', 'Wrap up', 0], [null, 'Transcribe', 1], ['Xuanshu', 'Proxy scan', 2]] };
const busyMsg = (lang, n) => ({ t: 'busy', by: NAME, tasks: W[lang].slice(0, n).map(([who, label, c], i) =>
  ({ id: 'k' + i, who, kind: who ? 'ceo' : 'temp', label, since: 1760000000 + i, c })) });
const S = { zh: { title3: '有 3 个 Agent 在后台干活', sub: `${NAME} 正在替你监控`, temp: '临时助理', up: '服务器已升级', refresh: '刷新', tip: /Agent 的名字/ },
  en: { title3: '3 agents at work in the background', sub: `${NAME} is watching them for you`, temp: 'Helper', up: 'Server upgraded', refresh: 'Refresh', tip: /Agent's name/ } };
export const CASES = { a: 'busy-dots', b: 'busy-dialog', c: 'global-dialog-upgrade', d: 'name-tip-and-capsule-ring' };
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);
const esc = (p) => p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
async function tapAt(p, x, y) {
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
async function tapEl(p, sel) { const r = await box(p, sel); await tapAt(p, r.l + r.w / 2, r.t + r.h / 2); }

export async function runP150({ anim = false } = {}) {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p150.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const step = async (c, f) => { try { await f(); rec.record(CASES[c], true); } catch (e) { rec.record(CASES[c], false); throw e; } };
  try {
    for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
      const s = S[lang];
      fake.reset();
      const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
      const freeze = (ms) => evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=${ms}})`);
      const play = () => evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
        await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 30, max: 100 }, h5: { pct: 34, reset: 0 }, week: { pct: 58, reset: 0 }, at: 0 });
        await fake.addTurn({ k: 'phone', dev: 'other', text: lang === 'zh' ? '帮我对一下三家供应商的报价' : 'Compare the three suppliers' }, LONG[lang === 'zh' ? 0 : 1]);
        await waitFor(p, `${$('agent')}.textContent===${JSON.stringify(NAME)} && ${$('words')}.textContent.length>40`);

        await step('a', async () => {
          check(await evaluate(p, `${$('busyDots')}.hidden`), 'no worker, no dots ' + lang);
          const hd = await box(p, '.rmtop>.rmhd');
          check(hd.h >= 51, `header taller (${hd.h}) ` + lang);
          const nm = await box(p, '#agent');
          check(nm.t - hd.t >= 10, `name lower (${nm.t - hd.t}) ` + lang);
          for (const [st, color] of [['idle', 'green'], ['working', 'blue'], ['waiting', 'orange']]) {
            if (st === 'waiting' && theme === 'dark') continue;
            await fake.send({ t: 'status', s: st, agent: 'claude', name: NAME }); await waitFor(p, `document.body.dataset.status===${JSON.stringify(st)}`);
            if (st === 'idle') { await fake.send({ t: 'busy', by: NAME, tasks: [] }); await sleep(150); await save(`${color}-0`); }
            for (const n of [1, 3]) {
              await fake.send(busyMsg(lang, n));
              await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===${n} && !${$('busyDots')}.hidden`);
              const dots = await box(p, '#busyDots'), name = await box(p, '#agent'), card = await box(p, '.rmtop>.rmhd');
              check(dots.l >= name.r, `dots right of the name ${lang}/${st}/${n}`);
              check(dots.r <= card.r + 1 && dots.w <= card.w * 0.67, `dots inside the right 2/3 ${lang}/${st}/${n}`);
              const cs = await evaluate(p, `[...${$('busyDots')}.querySelectorAll('i.rb:not(.bye)')].map(i=>getComputedStyle(i).getPropertyValue('--h').trim())`);
              check(new Set(cs).size === n, `distinct colours ${cs}`);
              await waitFor(p, `!${$('busyDots')}.querySelector('i.rb.in')`, 2000);   // a new robot pops in first, then dances
              check(await evaluate(p, `getComputedStyle(${$('busyDots')}.querySelector('i.rb')).animationName`) === 'rb-dance-18', 'dances (P154 追加 10: robots)');
              check(/[0-9]/.test(await evaluate(p, `${$('busyDots')}.getAttribute('aria-label')`)), 'dots are labelled');
              await freeze(n === 1 ? 0 : 600); await save(`${color}-${n}`); await play();
            }
          }
          await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME }); await waitFor(p, `document.body.dataset.status==='working'`);
          await fake.send({ ...busyMsg(lang, 3), tasks: busyMsg(lang, 3).tasks.slice(1) });
          await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===2`, 4000);
          check(true, 'one finished → one fewer dot');
          if (anim && lang === 'zh' && theme === 'light') await blinkLoop(p, B);
        });

        await step('b', async () => {
          await fake.send(busyMsg(lang, 3)); await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===3`);
          await evaluate(p, `${$('input')}.value='draft canary'`);
          await tapEl(p, '#busyDots');
          await waitFor(p, `!${$('busyDlg')}.hidden`);
          check(await evaluate(p, `${$('busyTitle')}.textContent`) === s.title3, 'title ' + lang);
          check(await evaluate(p, `${$('busySub')}.textContent`) === s.sub, 'watching ' + lang);
          const items = await evaluate(p, `[...document.querySelectorAll('#busyList li')].map(li=>li.querySelector('b').textContent+'·'+li.querySelector('span').textContent)`);
          check(items.length === 3 && items[1].startsWith(s.temp + '·') && items[0] === W[lang][0][0] + '·' + W[lang][0][1], 'who · what ' + items);
          const sq = await box(p, '#busyDlg .sq');
          check(Math.abs(sq.l + sq.w / 2 - 195) < 2 && Math.abs(sq.t + sq.h / 2 - 422) < 3, `centred (${sq.l},${sq.t})`);
          check(sq.w >= 330 && sq.h <= 796, `enlarged card fits viewport (${sq.w}x${sq.h})`);
          check(await evaluate(p, `document.getElementById('busyTasksTab').getAttribute('aria-selected')==='true' && !!document.getElementById('busyWorkflowTab')`), 'P244 two tabs, work list default');
          check(await evaluate(p, `document.activeElement.id`) === 'busyClose', 'focus to its button');
          await freeze(600); await save('dialog-tasks'); await play();
          await esc(p); await waitFor(p, `${$('busyDlg')}.hidden`);
          check(await evaluate(p, `${$('input')}.value`) === 'draft canary', 'draft kept');
          await tapEl(p, '#busyDots'); await waitFor(p, `!${$('busyDlg')}.hidden`);
          await tapAt(p, 20, 820); await waitFor(p, `${$('busyDlg')}.hidden`); check(true, 'outside closes');
          await tapEl(p, '#busyDots'); await waitFor(p, `!${$('busyDlg')}.hidden`);
          await tapEl(p, '#busyClose'); await waitFor(p, `${$('busyDlg')}.hidden`); check(true, 'button closes');
          await tapEl(p, '#busyDots'); await waitFor(p, `!${$('busyDlg')}.hidden`);
          await fake.send({ t: 'busy', by: NAME, tasks: [] });
          await waitFor(p, `${$('busyDlg')}.hidden && ${$('busyDots')}.hidden`, 4000); check(true, 'empty list closes and hides');
        });

        await step('c', async () => {
          const ver = await evaluate(p, `import('./version.js').then(m=>m.default.version)`);
          const next = ver.replace(/^(\d+)\.(\d+)\.(\d+)/, (m, a, b, c) => `${a}.${b}.${+c + 1}`);
          await evaluate(p, `${$('input')}.focus()`);
          // P240: the dialog only offers a page the server really serves — stand in for version.json at the host's version.
          const serve = (v) => evaluate(p, `(()=>{const f=window.__realFetch||(window.__realFetch=window.fetch);window.fetch=(u,o)=>String(u).endsWith('/version.json')?Promise.resolve(new Response(JSON.stringify({v:1,version:${JSON.stringify(v)}}))):f(u,o);})()`);
          await serve(next);
          await evaluate(p, `import('./js/version-notice.js').then(m=>m.setHostVersion(${JSON.stringify(ver)}))`);
          await evaluate(p, `document.dispatchEvent(new Event('visibilitychange'))`);
          await waitFor(p, `!${$('version-notice')}.hidden`);
          check(await evaluate(p, `${$('version-notice-title')}.textContent`) === s.up, 'title ' + lang);
          check(await evaluate(p, `${$('version-refresh')}.textContent`) === s.refresh, 'button ' + lang);
          check((await evaluate(p, `${$('version-notice-text')}.textContent`)).includes(next), 'version in the text');
          check(await evaluate(p, `document.activeElement.id`) === 'input', 'never takes focus');
          const sq = await box(p, '#version-notice .sq');
          check(Math.abs(sq.l + sq.w / 2 - 195) < 2 && Math.abs(sq.t + sq.h / 2 - 422) < 3, 'centred');
          await save('dialog-update');
          await esc(p); await waitFor(p, `${$('version-notice')}.hidden`); check(true, 'ESC = later');
          await serve(next.replace(/(\d+)$/, (d) => +d + 1));
          await evaluate(p, `import('./js/version-notice.js').then(m=>m.setHostVersion(${JSON.stringify(ver)}))`);
          await evaluate(p, `document.dispatchEvent(new Event('visibilitychange'))`);
          await waitFor(p, `!${$('version-notice')}.hidden`);
          await tapAt(p, 20, 820); await waitFor(p, `${$('version-notice')}.hidden`); check(true, 'outside = later');
        });

        await step('d', async () => {
          check(s.tip.test(await evaluate(p, `${$('agent')}.title`)), 'name explains itself ' + lang);
          check(await evaluate(p, `getComputedStyle(document.querySelector('.pg-corner .pg')).borderTopColor`) !== 'rgb(0, 0, 0)', 'capsule ring not black');
        });
        check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + JSON.stringify(p.problems.slice(0, 2)));
      } finally { await p.dispose(); }
    }
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host; not a physical phone', checks, complete: true }, null, 2) + '\n');
    console.log(`p150: ${checks} checks passed`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

/** The blink as a short loop (frames from paused animations → mp4 + gif), reports/qa/p150/anim/. */
async function blinkLoop(p) {
  const dir = join(ROOT, 'reports/qa/p150/anim'), tmp = join(dir, '.frames');
  rmSync(tmp, { recursive: true, force: true }); mkdirSync(tmp, { recursive: true });
  const fps = 12, n = 58;
  for (let i = 0; i < n; i++) {
    await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=${(i * 1000 / fps).toFixed(1)}})`);
    const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip: { x: 0, y: 0, width: 390, height: 844, scale: 1 } });
    writeFileSync(join(tmp, String(i).padStart(4, '0') + '.png'), Buffer.from(data, 'base64'));
  }
  const ff = (args) => execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args]);
  ff(['-framerate', String(fps), '-i', join(tmp, '%04d.png'), '-vf', 'scale=390:-2:flags=lanczos,format=yuv420p', '-c:v', 'libx264', '-crf', '20', '-movflags', '+faststart', join(dir, 'blink-working-zh.mp4')]);
  ff(['-framerate', String(fps), '-i', join(tmp, '%04d.png'), '-vf', 'scale=390:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer', '-loop', '0', join(dir, 'blink-working-zh.gif')]);
  rmSync(tmp, { recursive: true, force: true });
  await evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP150({ anim: process.argv.includes('--anim') });
