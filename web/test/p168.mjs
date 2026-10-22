// P168: the disconnected card + the human fallback mailbox feedback@agentj.app. Real page + local fake host (real Noise),
// own headless Chromium (never :9222). Per language × theme at 390×844 (touch):
//   a) the Agent J host goes away → for the quiet window nothing; then the grey page is blurred behind the P150 square:
//      「Agent J 主机可能关机或睡着了」 · 已断开不到 1 分钟 · 转发服务器能连上… · the two tips (`agentj doctor --reconnect`); focus stays
//      in the field; every target ≥ 44 px; centred
//   b) 「立即重试」 opens a new connection (session.reconnectNow / resume), the card stays while still offline
//   c) 「稍后」 hides it; past the 30-minute threshold (page clock moved, no sleeping) it comes back with 「长时间连不上？写信到
//      feedback@agentj.app」: mailto with subject + body template and none of the pairing data; one-tap copy of the address
//   d) ESC hides it again and it stays hidden (the long section was shown once)
//   e) the Agent J host is back → ready → the card closes by itself and the stored outage start is cleared
//   f) (zh light) a reload during the outage keeps its start (host record downAt)
//   g) (zh light, en dark) a reconnect ladder shows 「正在重新连接…」 with the real attempt count; reduced motion stops the pulse
//   h) (zh light, en dark) Settings → 帮助和反馈, the error page and the removed page name feedback@agentj.app (+ `agentj support`)
// and per language × theme at 1440×900: the short and the long card. Shots → reports/qa/p168/web/<lang>-<theme>-<size>-<state>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
import { LONG } from './density_shots.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.P168_SHOTS || join(ROOT, 'reports/qa/p168/web');
const $ = (id) => `document.getElementById('${id}')`;
const NAME = 'AgentJ-Father';
const MIN31 = 31 * 60 * 1000;
const S = {
  zh: { computer: 'Agent J 主机可能关机或睡着了', reconnecting: '正在重新连接…', lt1: '已断开不到 1 分钟', min31: /^已断开 31 分钟$/, wait: '转发服务器能连上，Agent J 主机一上线就会自动接上。',
    attempt: /^(自动重连：第 \d+ 次，\d+ 秒后再试|正在自动重连（第 \d+ 次）…)$/, long: '长时间连不上？写信到 feedback@agentj.app', subject: 'Agent J 连不上', copied: '已复制',
    tip1: '确认 Agent J 主机开着、连着网', help: /内置客服/, mailLine: /完全连不上时，写信到 feedback@agentj\.app/ },
  en: { computer: 'the Agent J host may be off or asleep', reconnecting: 'Reconnecting…', lt1: 'Disconnected for less than a minute', min31: /^Disconnected for 31 min$/,
    wait: 'The relay server is reachable. We reconnect by ourselves as soon as the Agent J host is back online.',
    attempt: /^(Automatic reconnect: attempt \d+, next try in \d+ s|Reconnecting automatically \(attempt \d+\)…)$/, long: "Still can't connect? Email feedback@agentj.app",
    subject: "Agent J can't connect", copied: 'Copied', tip1: 'Check that the Agent J host is on and online', help: /built-in support/, mailLine: /email feedback@agentj\.app/ },
};
export const CASES = { a: 'offline-card-blur', b: 'offline-card-retry', c: 'offline-card-long-mail', d: 'offline-card-dismiss', e: 'offline-card-reconnect-close',
  f: 'offline-card-reload-persist', g: 'offline-card-reconnecting-reduced-motion', h: 'feedback-help-lines', i: 'offline-card-desktop' };
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);
const esc = (p) => p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
async function tapAt(p, x, y) {
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
async function tapEl(p, sel) { const r = await box(p, sel); await tapAt(p, r.l + r.w / 2, r.t + r.h / 2); }
const card = (p) => evaluate(p, `import('./js/offline-card.js')`).then(() => 0);
const skew = (p, ms) => evaluate(p, `import('./js/offline-card.js').then(m=>{m._skew(${ms});return true})`);
const hostRec = (p) => evaluate(p, `new Promise((res)=>{const r=indexedDB.open('agentjarvis');r.onsuccess=()=>{const g=r.result.transaction('kv').objectStore('kv').get('host');g.onsuccess=()=>{const h=g.result;r.result.close();
  res(h?{channel:h.channel,relay:h.relay,hostPub:btoa(String.fromCharCode(...new Uint8Array(h.hostPub))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,''),downAt:h.downAt??null}:null)}}})`);
const small = (p) => evaluate(p, `[...document.querySelectorAll('#offDlg button,#offDlg a')].filter(e=>e.getClientRects().length&&!e.closest('[hidden]')).map(e=>{const r=e.getBoundingClientRect();return {id:e.id||e.textContent.trim().slice(0,12),w:r.width,h:r.height}}).filter(x=>x.w<44||x.h<44)`);

async function pairReady(p, fake, url, lang) {
  await navigate(p, fake.newPairing(url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
  await fake.addTurn({ k: 'phone', dev: 'other', text: lang === 'zh' ? '帮我对一下三家供应商的报价' : 'Compare the three suppliers' }, LONG[lang === 'zh' ? 0 : 1]);
  await waitFor(p, `${$('agent')}.textContent===${JSON.stringify(NAME)} && ${$('words')}.textContent.length>40`);
}

export async function runP168() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p168.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const seen = new Map();
  const step = async (c, f) => { try { await f(); if (seen.get(c) !== false) seen.set(c, true); } catch (e) { seen.set(c, false); throw e; } };
  try {
    for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
      const s = S[lang], tag = `${lang}/${theme}`, extra = (lang === 'zh' && theme === 'light') || (lang === 'en' && theme === 'dark');
      fake.reset(); fake.setUp(true);
      const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-390-${name}.png`), await shoot(p, ''));
      try {
        await pairReady(p, fake, web.url, lang);
        await card(p);

        if (extra) await step('h', async () => {   // Settings → 帮助和反馈
          await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
          check(s.mailLine.test(await evaluate(p, `${$('set-help-mail')}.textContent`)), 'settings: the fallback mailbox line ' + tag);
          check(await evaluate(p, `[...document.querySelectorAll('#settings a[href^="mailto:"]')].every(a=>a.getAttribute('href')==='mailto:feedback@agentj.app') && !!document.querySelector('#settings a[href="mailto:feedback@agentj.app"]')`), 'settings: feedback@ link ' + tag);
          await evaluate(p, `${$('set-help-mail')}.scrollIntoView({block:'center'})`); await save('settings-help');
          await esc(p); await waitFor(p, `${$('settings')}.hidden`);
        });

        await step('a', async () => {
          await evaluate(p, `${$('input')}.focus(); ${$('input')}.value='draft canary'`);
          const t0 = Date.now();
          fake.setUp(false);
          await waitState(p, 'waiting-host');
          await sleep(1500);
          check(await evaluate(p, `${$('offDlg')}.hidden`), 'nothing blurs during the quiet window ' + tag);
          await waitFor(p, `!${$('offDlg')}.hidden`, 10000);
          check(Date.now() - t0 >= 4900, `shown after the quiet window (${Date.now() - t0} ms) ${tag}`);   // P393: 5 s rule (was 6 s)
          check(await evaluate(p, `document.body.dataset.offlineCard`) === 'short', 'short card ' + tag);
          check(/blur\(8px\)/.test(await evaluate(p, `getComputedStyle(${$('offDlg')}).backdropFilter`)), 'the page behind is blurred ' + tag);
          check(await evaluate(p, `${$('offDlg')}.classList.contains('gdlg') && getComputedStyle(${$('offDlg')}.querySelector('.sq')).borderTopLeftRadius`) === '24px', 'the P375 composer-aligned card ' + tag);
          check(await evaluate(p, `document.activeElement.id`) === 'input', 'focus stays in the field ' + tag);
          check(await evaluate(p, `${$('offTitle')}.textContent`) === s.computer, 'title ' + tag);
          check(await evaluate(p, `${$('offSince')}.textContent`) === s.lt1, 'duration ' + tag);
          check(await evaluate(p, `${$('offProg')}.textContent`) === s.wait, 'progress: waiting for the Agent J host ' + tag);
          check(await evaluate(p, `document.querySelector('#offDlg .off-tips li').textContent`) === s.tip1, 'tip 1 ' + tag);
          check(await evaluate(p, `${$('offTip2')}.textContent`) === (lang === 'zh' ? '检查遥控器网络，或点「立即重试」。' : 'Check this remote’s network, or tap Try now.'), 'tip 2 = network and retry guidance ' + tag);
          check(await evaluate(p, `${$('offLong')}.hidden`), 'no mailbox before 30 minutes ' + tag);
          const sq = await box(p, '#offDlg .sq');
          check(Math.abs(sq.l + sq.w / 2 - 195) < 2 && sq.t >= 0 && sq.b <= 844, `centred, inside the screen (${sq.l},${sq.t},${sq.b}) ${tag}`);
          const tiny = await small(p); check(!tiny.length, 'targets ≥ 44 px ' + JSON.stringify(tiny));
          check(await evaluate(p, `document.documentElement.scrollWidth <= innerWidth`), 'no horizontal overflow ' + tag);
          await save('short');
        });

        await step('b', async () => {
          await evaluate(p, `(()=>{if(window.__ws!==undefined)return;const W=WebSocket;window.__ws=0;window.WebSocket=class extends W{constructor(...a){super(...a);window.__ws++}}})()`);
          const n = await evaluate(p, 'window.__ws');
          await tapEl(p, '#offRetry');
          await waitFor(p, `window.__ws > ${n}`, 4000); check(true, 'retry now opens a new connection ' + tag);
          await waitState(p, 'waiting-host');
          await sleep(300);
          check(!(await evaluate(p, `${$('offDlg')}.hidden`)), 'still offline → the card stays ' + tag);
        });

        await step('d', async () => {
          await tapEl(p, '#offLater'); await waitFor(p, `${$('offDlg')}.hidden`);
          await sleep(1500);
          check(await evaluate(p, `${$('offDlg')}.hidden`), '稍后 hides it ' + tag);
          check(await evaluate(p, `${$('input')}.value`) === 'draft canary', 'the field is untouched ' + tag);
        });

        await step('c', async () => {
          await skew(p, MIN31);
          await waitFor(p, `!${$('offDlg')}.hidden && document.body.dataset.offlineCard==='long'`, 3000);
          check(true, 'past 30 minutes the card comes back once ' + tag);
          await sleep(350);                                   // the square's .18 s entry (scale .94 → 1) is over before measuring
          check(s.min31.test(await evaluate(p, `${$('offSince')}.textContent`)), 'duration 31 min ' + tag);
          check(await evaluate(p, `document.querySelector('#offLong .off-long-h').textContent`) === s.long, 'the mailbox line ' + tag);
          const href = await evaluate(p, `${$('offMail')}.getAttribute('href')`);
          const u = new URL(href);
          check(u.protocol === 'mailto:' && u.pathname === 'feedback@agentj.app', 'mailto feedback@ ' + href.slice(0, 40));
          check(u.searchParams.get('subject') === s.subject, 'subject ' + tag);
          const body = u.searchParams.get('body') || '';
          check(/Account ID|账号 ID/.test(body) && /What I saw|看到了什么/.test(body) && /When it started|从什么时候开始/.test(body) && /Screenshot|截图/.test(body), 'body template ' + tag);
          const h = await hostRec(p);
          for (const secret of [h.channel, h.hostPub, h.relay, 'ws://', 'wss://', '#p=']) check(!href.includes(secret) && !decodeURIComponent(href).includes(secret), 'no pairing data in the mailto ' + tag);
          const tiny = await small(p); check(!tiny.length, 'targets ≥ 44 px (long) ' + JSON.stringify(tiny));
          await save('long');
          await tapEl(p, '#offCopy');
          await waitFor(p, `${$('offCopy')}.dataset.done==='1'`, 3000);
          check(await evaluate(p, `${$('offCopy')}.dataset.done`) === '1', 'copied, visible shared button state ' + tag);
          check(await evaluate(p, `navigator.clipboard.readText()`) === 'feedback@agentj.app', 'the address is on the clipboard ' + tag);
          await save('long-copied');
        });

        await step('d', async () => {
          await esc(p); await waitFor(p, `${$('offDlg')}.hidden`);
          await sleep(1500);
          check(await evaluate(p, `${$('offDlg')}.hidden`), 'ESC hides the long card and it stays hidden ' + tag);
          await skew(p, 0);
        });

        if (lang === 'zh' && theme === 'light') await step('f', async () => {
          const before = await hostRec(p);
          const since = await evaluate(p, `import('./js/offline-card.js').then(m=>m._debug().since)`);
          check(Number.isFinite(before.downAt) && before.downAt === since, `outage start stored in the host record (${before.downAt} / ${since})`);
          await navigate(p, web.url + '?lang=' + lang);
          await waitState(p, 'waiting-host');
          await waitFor(p, `!${$('offDlg')}.hidden`, 10000);
          check(await evaluate(p, `import('./js/offline-card.js').then(m=>m._debug().since)`) === before.downAt, 'after a reload the outage keeps its start');
          await evaluate(p, `(()=>{const W=WebSocket;window.__ws=0;window.WebSocket=class extends W{constructor(...a){super(...a);window.__ws++}}})()`);
        });

        await step('e', async () => {
          fake.setUp(true);
          await waitState(p, 'ready');
          await waitFor(p, `${$('offDlg')}.hidden && !document.body.dataset.offlineCard`, 3000);
          check(true, 'reconnected → the card closes by itself ' + tag);
          await waitFor(p, `new Promise((res)=>{const r=indexedDB.open('agentjarvis');r.onsuccess=()=>{const g=r.result.transaction('kv').objectStore('kv').get('host');g.onsuccess=()=>{r.result.close();res(g.result&&g.result.approved&&g.result.downAt===undefined)}}})`, 4000);
          check(true, 'stored outage start cleared ' + tag);
        });

        if (extra) await step('g', async () => {
          fake.refuseNextResumes(9); fake.kick(1001);
          await waitFor(p, `!${$('offDlg')}.hidden`, 12000);
          check(await evaluate(p, `${$('offDlg')}.dataset.state`) === 'reconnecting', 'reconnecting state ' + tag);
          check(await evaluate(p, `${$('offTitle')}.textContent`) === s.reconnecting, 'title reconnecting ' + tag);
          const prog = await evaluate(p, `${$('offProg')}.textContent`);
          check(s.attempt.test(prog), `the real attempt count (${prog}) ${tag}`);
          check(await evaluate(p, `getComputedStyle(document.querySelector('#offDlg .off-icon svg')).animationName`) === 'off-pulse', 'the icon pulses ' + tag);
          await save('reconnecting');
          await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }, { name: 'prefers-color-scheme', value: theme }] });
          check(await evaluate(p, `getComputedStyle(document.querySelector('#offDlg .off-icon svg')).animationName`) === 'none', 'reduced motion: no pulse ' + tag);
          check(await evaluate(p, `getComputedStyle(document.querySelector('#offDlg .sq')).animationName`) === 'none', 'reduced motion: no entry animation ' + tag);
          await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }, { name: 'prefers-color-scheme', value: theme }] });
          fake.st.refuseResumes = 0;
          await tapEl(p, '#offRetry');
          await waitState(p, 'ready', 15000);
          await waitFor(p, `${$('offDlg')}.hidden`, 3000); check(true, 'retry → ready → closed ' + tag);
        });

        if (extra) await step('h', async () => {   // the error page, then the removed page
          fake.sendRaw(Uint8Array.of(4, 1, 2, 3));
          await waitState(p, 'error');
          await sleep(300);
          check(await evaluate(p, `${$('offDlg')}.hidden`), 'the error page owns the screen (no card over it) ' + tag);
          check(s.help.test(await evaluate(p, `${$('error-help-support')}.textContent`)) && await evaluate(p, `${$('error-help-support')}.querySelector('code').textContent`) === 'agentj support', 'error page: agentj support line ' + tag);
          check(s.mailLine.test(await evaluate(p, `${$('error-help-mail')}.textContent`)), 'error page: mailbox line ' + tag);
          check(await evaluate(p, `!!document.querySelector('#error-view a[href="mailto:feedback@agentj.app"]')`), 'error page: feedback@ link ' + tag);
          await save('error');
          await evaluate(p, `${$('retry')}.click()`); await waitState(p, 'ready');
          await fake.revokeAll();
          await waitState(p, 'revoked');
          check(await evaluate(p, `${$('offDlg')}.hidden && !${$('repair')}.hidden`), 'removed: no card, 重新配对 reachable ' + tag);
          check(await evaluate(p, `!!document.querySelector('#revoked-view a[href="mailto:feedback@agentj.app"]')`) && s.mailLine.test(await evaluate(p, `document.querySelector('#revoked-view [data-i18n="help.mail"]').textContent`)), 'removed page: mailbox line ' + tag);
          await save('revoked');
        });
        check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + JSON.stringify([p.problems.slice(0, 2), p.offsite.slice(0, 2)]));
      } finally { await p.dispose(); }
    }

    // desktop: the short and the long card
    for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) await step('i', async () => {
      const s = S[lang], tag = `${lang}/${theme}/desktop`;
      fake.reset(); fake.setUp(true);
      const p = await newPage(B, 1440, 900, theme, { allow: [web.url, fake.relay + '/'] });
      try {
        await pairReady(p, fake, web.url, lang);
        fake.setUp(false);
        await waitState(p, 'waiting-host');
        await waitFor(p, `!${$('offDlg')}.hidden`, 10000);
        check(await evaluate(p, `${$('offTitle')}.textContent`) === s.computer, 'title ' + tag);
        const sq = await box(p, '#offDlg .sq');
        check(Math.abs(sq.l + sq.w / 2 - 720) < 2, 'centred ' + tag);
        writeFileSync(join(OUT, `${lang}-${theme}-desktop-short.png`), await shoot(p, ''));
        await skew(p, MIN31);
        await waitFor(p, `document.body.dataset.offlineCard==='long' && !${$('offLong')}.hidden`, 3000);
        writeFileSync(join(OUT, `${lang}-${theme}-desktop-long.png`), await shoot(p, ''));
        await skew(p, 0);
        fake.setUp(true);
        await waitState(p, 'ready');
        await waitFor(p, `${$('offDlg')}.hidden`, 3000);
        check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + tag);
      } finally { await p.dispose(); }
    });
    for (const [c, id] of Object.entries(CASES)) rec.record(id, seen.get(c) === true);
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host (real Noise); not a physical phone', checks, cases: Object.fromEntries(Object.entries(CASES).map(([c, id]) => [id, seen.get(c) === true])), complete: true }, null, 2) + '\n');
    console.log(`p168: ${checks} checks passed`);
  } catch (e) {
    for (const [c, id] of Object.entries(CASES)) rec.record(id, seen.get(c) === true);
    throw e;
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP168();
