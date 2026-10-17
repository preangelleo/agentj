#!/usr/bin/env node
// P210 「连上你的Agent J 主机」 — the unpaired page as numbered, mutually exclusive method cards (任选一种).
// Real page against fakehost.mjs: phone 390×844 + desktop 1440×900 × light / dark × 中文 / English, full-page shots
// → reports/qa/p210/screens/<tag>-*.png (AJ_P210_TAG=before|after, default after) and checks on the "after" layout:
// method order + numbering, each card = title + ≤ 3 steps + one primary button, the copy icon copies the command,
// the Face ID 「以前配过？」 section sits apart at the bottom, iPhone Safari wording, 44 px taps, no overflow.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitState, shoot, sleep } from './browser.mjs';
import { dictionaries } from '../build.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const TAG = process.env.AJ_P210_TAG || 'after';
const OUT = fileURLToPath(new URL(`../../reports/qa/p210/screens/`, import.meta.url));
mkdirSync(OUT, { recursive: true });
const D = dictionaries();
const plain = (s) => s.replace(/`/g, '');
const failures = [];
const rec = parityRecorder('web/test/p210.mjs');
// case names (parity features.json p210-pair-methods): methods · layout · copy · account · past · iphone · renumber
const check = (ok, msg, name = 'layout') => { rec.record(name, ok); if (!ok) failures.push(msg); console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); return ok; };
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const ALLOW = [web.url, fake.relay + '/'];
const B = await launch();
const url = (lang) => `${web.url}?relay=${encodeURIComponent(fake.relay)}${lang === 'en' ? '&lang=en' : ''}`;

// the pairing page scrolls inside its own container: grow the viewport to the content so one shot shows the whole page
async function tall(p, w, h) {
  const extra = await evaluate(p, `Math.max(0, ...[...document.querySelectorAll('*')].filter((e) => e.scrollHeight > e.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(e).overflowY)).map((e) => e.scrollHeight - e.clientHeight))`);
  await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h + extra, deviceScaleFactor: 1, mobile: w < 500 });
  await sleep(250);
}
async function shotAll(p, w, h, f) { await tall(p, w, h); writeFileSync(f, await shoot(p, f)); console.log(`      shot ${f}`); await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 }); }

async function open(w, h, scheme, lang, opts = {}) {
  const p = await newPage(B, w, h, scheme, { allow: ALLOW, ...opts });
  await navigate(p, url(lang));
  await waitState(p, 'idle');
  await sleep(600);                      // the reverse-pairing code and the language switch settle
  return p;
}

try {
  for (const lang of ['zh', 'en']) for (const scheme of ['dark', 'light']) for (const [sz, w, h] of [['phone', 390, 844], ['desktop', 1440, 900]]) {
    const tag = `${lang}-${scheme}-${sz}`;
    const p = await open(w, h, scheme, lang);
    const f = join(OUT, `${TAG}-${tag}.png`);
    await shotAll(p, w, h, f);
    if (TAG === 'after') {
      const T = D[lang];
      const r = await evaluate(p, `(() => {
        const vis = (e) => !!e.getClientRects().length && !e.closest('[hidden]');
        const cards = [...document.querySelectorAll('#pair-view .pm')].filter(vis);
        return {
          ids: cards.map((c) => c.id),
          nos: cards.map((c) => c.querySelector('.pm__no').textContent),
          titles: cards.map((c) => c.querySelector('h2').textContent),
          steps: cards.map((c) => c.querySelectorAll('ol.steps > li').length),
          primaries: cards.map((c) => [...c.querySelectorAll('.aj-btn--primary')].filter(vis).length),
          ph: document.getElementById('pair-link').placeholder,
          cmd: document.getElementById('pm-cmd').textContent,
          cmdLink: document.getElementById('pm-cmd-link').textContent,
          past: (() => { const s = document.getElementById('pm-past'); return s && vis(s) ? s.getBoundingClientRect().top : -1; })(),
          lastCard: cards.length ? cards[cards.length - 1].getBoundingClientRect().bottom : 0,
          wide: [...document.querySelectorAll('#pair-view *')].filter(vis).filter((e) => e.getBoundingClientRect().right > innerWidth + 1).map((e) => e.id || e.className),
          small: [...document.querySelectorAll('#pair-view button, #pair-view a[href], #pair-view textarea')].filter(vis)
            .map((e) => ({ id: e.id || e.className || e.tagName, r: e.getBoundingClientRect() })).filter(({ r }) => r.height < 44 || r.width < 44).map(({ id, r }) => id + ' ' + Math.round(r.width) + 'x' + Math.round(r.height)),
        };
      })()`);
      check(JSON.stringify(r.ids) === JSON.stringify(['rv-card', 'pm-term', 'pm-acct']), `${tag}: three methods in order rv → terminal → account (${r.ids})`, 'methods');
      check(JSON.stringify(r.nos) === JSON.stringify([T['pair.no1'], T['pair.no2'], T['pair.no3']]), `${tag}: numbered ${r.nos.join(' / ')}`, 'methods');
      check(r.titles[0] === plain(T['pair.m1.title']) && r.titles[1] === plain(T['pair.m2.title']) && r.titles[2] === plain(T['pair.m3.title']), `${tag}: card titles from the dictionary`, 'methods');
      check(r.steps.every((n) => n >= 2 && n <= 3), `${tag}: every card has 2–3 steps (${r.steps})`, 'methods');
      check(JSON.stringify(r.primaries) === '[0,1,1]', `${tag}: one primary per card; the reverse card's action is its code (${r.primaries})`, 'methods');
      check(r.ph === plain(T['pair.placeholder']) && /https:\/\//.test(r.ph) && /agentj pair --link/.test(r.ph), `${tag}: placeholder says the https:// link from agentj pair --link`, 'methods');
      check(r.cmd === 'agentj pair' && r.cmdLink === 'agentj pair --link', `${tag}: both commands as code blocks`, 'copy');
      check(r.past === -1, `${tag}: no Face ID here → no 「以前配过？」 section`, 'past');
      check(r.wide.length === 0, `${tag}: nothing wider than the screen (${r.wide})`);
      check(r.small.length === 0, `${tag}: taps ≥ 44 px (${r.small})`);
      // the fake relay has no rendezvous host route: the reverse code's own socket 404s here (not this page's doing)
      const probs = p.problems.filter((x) => !/\/v1\/host\/.*404/.test(x));
      check(probs.length === 0, `${tag}: no console errors (${probs.slice(0, 2)})`);
      if (lang === 'zh' && scheme === 'dark' && sz === 'phone') {
        // the copy icon copies exactly the command
        await evaluate(p, `document.getElementById('pm-copy').click()`);
        await sleep(300);
        const clip = await evaluate(p, `navigator.clipboard.readText()`);
        check(clip === 'agentj pair', `copy icon puts 「agentj pair」 on the clipboard (${clip})`, 'copy');
        await evaluate(p, `document.getElementById('pm-copy-link').click()`);
        await sleep(300);
        check((await evaluate(p, `navigator.clipboard.readText()`)) === 'agentj pair --link', 'second copy icon puts 「agentj pair --link」 on the clipboard', 'copy');
        // the account method's link opens https://agentj.app/account in a new tab
        check(await evaluate(p, `(() => { const a = document.querySelector('#pm-acct a[href]'); return a && a.href === 'https://agentj.app/account/' && a.target === '_blank'; })()`), 'account method links https://agentj.app/account in a new tab', 'account');
        // both scan buttons start the same in-page scanner
        check(await evaluate(p, `document.querySelectorAll('#pair-view [data-pair-scan]').length === 2`), 'terminal + account cards each have a 「扫二维码」 primary', 'account');
        // the 「以前配过？」 section: shown only with the Face ID button, after the last method
        await evaluate(p, `(() => { document.getElementById('pk-restore').hidden = false; })()`);
        await sleep(100);
        const past = await evaluate(p, `(() => { const s = document.getElementById('pm-past'), c = [...document.querySelectorAll('#pair-view .pm')].pop(); return !s.hidden && s.getBoundingClientRect().top > c.getBoundingClientRect().bottom; })()`);
        check(past, '「以前配过？」 + Face ID sits below the three methods, apart from them', 'past');
        const f2 = join(OUT, `${TAG}-${tag}-faceid.png`);
        await shotAll(p, w, h, f2);
      }
    }
    await p.dispose();
  }
  if (TAG === 'after') {
    // iPhone Safari (not from the Home Screen): the terminal method says --link + paste, and the Home Screen note shows
    for (const lang of ['zh', 'en']) {
      const p = await open(390, 844, 'dark', lang, { ua: IPHONE, touch: true });
      const T = D[lang];
      const s = await evaluate(p, `({ s1: document.getElementById('pair-step1').textContent, ip: !document.getElementById('pair-iphone').hidden })`);
      check(s.s1 === plain(T['pair.m2.s1ios']) && s.ip, `iPhone Safari ${lang}: terminal step 1 uses --link, Home Screen note shown`, 'iphone');
      const f = join(OUT, `${TAG}-${lang}-dark-iphone.png`);
      await shotAll(p, 390, 844, f);
      await p.dispose();
    }
    // no reverse-pairing code possible (no WebCrypto X25519) → the other two renumber as 方法一 / 方法二
    const p = await open(390, 844, 'dark', 'zh');
    await evaluate(p, `(() => { document.getElementById('rv-card').hidden = true; })()`);
    await sleep(100);
    const nos = await evaluate(p, `[...document.querySelectorAll('#pair-view .pm:not([hidden]) .pm__no')].map((e) => e.textContent)`);
    check(JSON.stringify(nos) === JSON.stringify([D.zh['pair.no1'], D.zh['pair.no2']]), `without the reverse code the rest renumber (${nos})`, 'renumber');
    await p.dispose();
  }
} finally {
  await B.close();
  await fake.stop();
  await web.stop();
}
writeFileSync(join(OUT, `${TAG}-results.json`), JSON.stringify({ failures }, null, 1));
if (TAG === 'after') rec.write();
console.log(failures.length ? `\n${failures.length} FAIL` : '\nALL PASS');
process.exit(failures.length ? 1 : 0);
