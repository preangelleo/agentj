// P400 item 3: 「在线客服」 → https://support.agentj.app/ on the phone page, real page + local fake host (real Noise), own
// headless Chromium (never :9222). Per language × theme at 390×844 (touch) and 1440×900:
//   a) Settings → 帮助和反馈: 手机怎么用 · 写信给我们 · 在线客服 (new tab, full address), ≥ 44 px
//   b) Settings → 紧急修复: 在线客服 as the block's last line (under the restart card and the help page link)
//   c) the Agent J host goes away → the disconnected card (no site link of its own, backlog #84) → 「打开紧急恢复」 → 紧急修复 ends
//      with 在线客服 (the three levels: card → menu → web)
//   d) the disconnect page (#error-view): the online support line + support.agentj.app between the agent and the mail lines
// Shots → agentjarvis/reports/qa/p400/web-<lang>-<theme>-<width>-<state>.png (no address bar, no pairing data on screen).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.P400_SHOTS || join(ROOT, 'agentjarvis/reports/qa/p400');
const URL_ = 'https://support.agentj.app/';
const $ = (id) => `document.getElementById('${id}')`;
const S = {
  zh: { link: '在线客服', help: 'Agent J 不回话时，也能在网页上问在线客服，不用配对。', rescue: '也可以在网页上问在线客服，不用配对。' },
  en: { link: 'Online support', help: "If Agent J can't answer, ask online support on the web. No pairing needed.",
    rescue: 'You can also ask online support on the web. No pairing needed.' },
};
export const CASES = { a: 'support-link-settings-help', b: 'support-link-rescue', c: 'support-link-offline-card', d: 'support-link-error-page' };
const esc = (p) => p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
const linkInfo = (p, id) => evaluate(p, `(()=>{const a=${$(id)};const r=a.getBoundingClientRect();return {href:a.getAttribute('href'),target:a.target,rel:a.rel,text:a.textContent.trim(),w:r.width,h:r.height,shown:!!a.getClientRects().length&&!a.closest('[hidden]')}})()`);

async function pairReady(p, fake, url, lang) {
  await navigate(p, fake.newPairing(url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ-Father' });
}

export async function runP400() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p400.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const seen = new Map();
  const step = async (c, f) => { try { await f(); if (seen.get(c) !== false) seen.set(c, true); } catch (e) { seen.set(c, false); throw e; } };
  const isLink = (l, text, tag) => {
    check(l.shown, 'visible ' + tag);
    check(l.href === URL_ && l.target === '_blank' && l.rel === 'noopener', 'full address, new tab, noopener ' + JSON.stringify(l) + ' ' + tag);
    check(l.text === text, `text ${JSON.stringify(l.text)} ${tag}`);
    check(l.w >= 44 && l.h >= 44, `target ≥ 44 px (${l.w}×${l.h}) ${tag}`);
  };
  try {
    for (const [w, h] of [[390, 844], [1440, 900]]) for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
      const s = S[lang], tag = `${lang}/${theme}/${w}`;
      fake.reset(); fake.setUp(true);
      const p = await newPage(B, w, h, theme, { touch: w < 600, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `web-${lang}-${theme}-${w}-${name}.png`), await shoot(p, ''));
      try {
        await pairReady(p, fake, web.url, lang);

        await step('a', async () => {
          await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
          await evaluate(p, `${$('set-help-support')}.scrollIntoView({block:'center'})`); await sleep(250);
          isLink(await linkInfo(p, 'set-help-support'), s.link, 'settings help ' + tag);
          check(await evaluate(p, `[...${$('set-help-support')}.parentElement.querySelectorAll('a')].map(a=>a.getAttribute('href')).join()`) ===
            'https://agentj.app/docs/phone/,mailto:feedback@agentj.app,' + URL_, 'next to 手机怎么用 / 写信给我们 ' + tag);
          await save('settings-help');
        });

        await step('b', async () => {
          await evaluate(p, `document.querySelector('[data-set-jump="set-s-rescue"]')?.click()`); await sleep(200);
          await evaluate(p, `${$('rs-support')}.scrollIntoView({block:'center'})`); await sleep(250);
          check(await evaluate(p, `${$('rs-support-note')}.textContent`) === s.rescue, 'rescue line ' + tag);
          check(await evaluate(p, `${$('set-s-rescue')}.lastElementChild.id`) === 'rs-support-box', 'the last line of 紧急修复 (under the cards and the help page link) ' + tag);
          isLink(await linkInfo(p, 'rs-support'), s.link, 'rescue ' + tag);
          if (w === 390) await save('rescue');
          await esc(p); await waitFor(p, `${$('settings')}.hidden`);
        });

        await step('c', async () => {
          fake.setUp(false);
          await waitState(p, 'waiting-host');
          await waitFor(p, `!${$('offDlg')}.hidden`, 10000); await sleep(350);
          check(!(await evaluate(p, `!!${$('offDlg')}.querySelector('a[href^="https:"]')`)), 'the card has no site link of its own (#84) ' + tag);
          await save('offline-card');
          await evaluate(p, `${$('offRescue')}.click()`);
          await waitFor(p, `!${$('settings')}.hidden && ${$('offDlg')}.hidden`, 4000);
          await evaluate(p, `${$('rs-support')}.scrollIntoView({block:'center'})`); await sleep(250);
          isLink(await linkInfo(p, 'rs-support'), s.link, 'card → 紧急修复 ' + tag);
          await save('offline-rescue');
          await esc(p); await waitFor(p, `${$('settings')}.hidden`);
          fake.setUp(true);
          await waitState(p, 'ready');
          await waitFor(p, `${$('offDlg')}.hidden`, 4000);
        });

        if (w === 390) await step('d', async () => {
          fake.sendRaw(Uint8Array.of(4, 1, 2, 3));
          await waitState(p, 'error'); await sleep(300);
          check(await evaluate(p, `${$('error-help-online')}.textContent`) === s.help, 'error page line ' + tag);
          isLink(await linkInfo(p, 'error-support'), 'support.agentj.app', 'error page ' + tag);
          await save('error');
        });
        check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + JSON.stringify([p.problems.slice(0, 2), p.offsite.slice(0, 2)]));
      } finally { await p.dispose(); }
    }
    for (const [c, id] of Object.entries(CASES)) rec.record(id, seen.get(c) === true);
    console.log(`p400: ${checks} checks passed`);
  } catch (e) {
    for (const [c, id] of Object.entries(CASES)) rec.record(id, seen.get(c) === true);
    throw e;
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP400();
