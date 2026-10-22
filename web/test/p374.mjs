// P374 「Daily AI News」 on the real page against the local fake host (real Noise; contract §5 news_get / news_set → news_res):
// opening Settings sends ONE news_get · the switch (default off) sends news_set {on} · ok → on + 「发到 {email}」 (only when on) ·
// the sample link (host's https sample_url, new tab, rel=noopener; a non-https URL is never used) · ok:false → back where it
// was + the P445 buttonless transient notice · unlinked → 「先把这台电脑连上 Agent J 账号」 + the 「打开账号后台」 link, switch
// disabled · no news_res (a computer before P374) → disabled + hint · a set without an answer → reverted + 「没有及时回应」 ·
// settings search finds the row · taps ≥ 44 px, no overflow · zh / en × light / dark × 390 (+ zh 1440).
// Shots → reports/qa/p374/web/ (P374_OUT). P374_PUBLIC=<old public/> only shoots the 账号和帮助 card into before/ (修前).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, key, sleep } from './browser.mjs';

const $ = (id) => `document.getElementById('${id}')`;
const BEFORE = !!process.env.P374_PUBLIC;
const OUT = process.env.P374_OUT || 'reports/qa/p374/web' + (BEFORE ? '/before' : '');
const EMAIL = 'owner@example.com';                        // never a real account name in a shot
const SAMPLE = 'https://agentj.app/account/news/sample';
const L = {
  zh: { label: 'Daily AI News', help: /^每天早上把 Agent J 的 Daily Agent 整理的《Musk \+ AI 24h》/, sample: '看一封样例', to: '发到 ' + EMAIL,
    unlinked: '先把这台 Agent J 主机连上 Agent J 账号。', acct: '打开账号后台', rate: '改得太频繁了，请过一会儿再试。', title: '这个开关没有改成',
    old: 'Agent J 主机还不支持这一项，升级后就能打开。', noAnswer: 'Agent J 主机没有及时回应，开关已经恢复原样。稍后再试。', find: '每日',
    queued: '名额 500 个已满，你在候补第 3 位。有人退订会自动补上，并发邮件告诉你。' },
  en: { label: 'Daily AI News', help: /^Every morning, the “Musk \+ AI 24h” digest/, sample: 'See a sample', to: 'Sent to ' + EMAIL,
    unlinked: 'Link this Agent J host to your Agent J account first.', acct: 'Open the account dashboard', rate: 'Too many changes. Try again in a little while.',
    title: 'This switch did not change', old: "Your Agent J host doesn't support this yet. Update it to turn this on.",
    noAnswer: "The Agent J host didn't answer in time, so the switch is back where it was. Try again later.", find: 'newsletter',
    queued: "All 500 seats are taken. You're number 3 on the waiting list: when someone unsubscribes you move up automatically, and we email you." },
};

export async function runP374({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  // host side: every news_* message recorded; answers come from the queue (null = stay silent like a host before P374)
  const calls = [], answers = [];
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'news_get' && m.t !== 'news_set') return false;
    calls.push({ t: m.t, on: m.on, keys: Object.keys(m).filter((k) => k !== 'r').sort().join() });
    const a = answers.length ? answers.shift() : { ok: true, on: false, email: EMAIL, sample_url: SAMPLE };
    if (a) await send({ t: 'news_res', r: m.r, ...a });
    return true;
  };
  const combos = BEFORE ? [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 390], ['zh', 'dark', 1440]]
    : [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 390], ['zh', 'light', 1440], ['zh', 'dark', 1440], ['en', 'light', 1440], ['en', 'dark', 1440]];
  for (const [lang, theme, w] of combos) {
    const s = L[lang], tag = `${lang}-${theme}-${w}`, deep = tag === 'zh-light-390';   // the two 10 s waits run once
    fake.reset(); fake.setUp(true); calls.length = 0; answers.length = 0;
    const p = await newPage(B, w, w === 390 ? 844 : 900, theme, { touch: w === 390, allow: [web.url, fake.relay + '/'] });
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${name}.png`), await shoot(p, ''));
    const view = (id) => evaluate(p, `${$(id)}.scrollIntoView({block:'center'}); true`).then(() => sleep(250));
    const openSet = async () => { await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`); };
    const closeSet = () => evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`);
    const checked = () => evaluate(p, `${$('set-news')}.getAttribute('aria-checked')`);
    const waitNotice = async (text, ms = 4000) => {
      await waitFor(p, `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent===${JSON.stringify(text)}`, ms);
      check(await evaluate(p, `document.getElementById('confirm').hidden && !!document.querySelector('#toast.circle.glance') && !document.querySelector('#toast button')`), 'P445: buttonless notice without a confirm card ' + tag);
    };
    const noticeGone = () => waitFor(p, `!document.getElementById('toast').classList.contains('on')`, 4000);
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      // P380 QA: the persisted theme overrides emulated media; explicitly select and verify each visual variant.
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      assert.equal(await evaluate(p, `document.documentElement.getAttribute('data-theme')`), theme);
      const expectedBg = theme === 'light' ? '#faf9f5' : '#141414';
      assert.equal(await evaluate(p, `getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()`), expectedBg);
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      if (BEFORE) {
        await openSet(); await view('set-s-help'); await save('help-card');
        check(true, 'before ' + tag); console.log('BEFORE P374 ' + tag);
        continue;
      }

      // ---- open → exactly one news_get, nothing else in it; answer off + sample
      await openSet();
      await waitFor(p, `${$('set-news')}.disabled===false`);
      check(calls.length === 1 && calls[0].t === 'news_get' && calls[0].keys === 't', 'one bare news_get on open ' + tag + ' ' + JSON.stringify(calls));
      check(await evaluate(p, `!!${$('set-news-row')}.closest('#set-s-help')`), 'row lives in 账号和帮助 ' + tag);
      check(await evaluate(p, `${$('set-news-row')}.querySelector('.setlab').textContent`) === s.label, 'label ' + tag);
      check(s.help.test(await evaluate(p, `${$('set-news-row')}.querySelector('.setnote').textContent`)), 'one-sentence help ' + tag);
      check(await checked() === 'false', 'default off ' + tag);
      check(await evaluate(p, `${$('set-news-to')}.hidden && !${$('set-news-to')}.textContent`), 'no 「发到」 line while off ' + tag);
      const link = await evaluate(p, `(()=>{const a=${$('set-news-sample')};return {vis:a.getClientRects().length>0,href:a.href,target:a.target,rel:a.rel,text:a.textContent}})()`);
      check(link.vis && link.href === SAMPLE && link.target === '_blank' && /\bnoopener\b/.test(link.rel) && link.text === s.sample, 'sample link ' + tag + ' ' + JSON.stringify(link));
      check(await evaluate(p, `${$('set-news-acct')}.hidden && ${$('set-news-msg')}.hidden`), 'no problem line ' + tag);
      const audit = async (label) => {
        const a = await evaluate(p, `(()=>{const r=document.getElementById('set-news-row');const small=[...r.querySelectorAll('button,a')].filter(e=>e.getClientRects().length).map(e=>{const b=e.getBoundingClientRect();return {id:e.id||e.tagName,w:Math.round(b.width),h:Math.round(b.height)}}).filter(x=>x.h<44||x.w<44);const m=document.getElementById('set-main');return {small,over:r.scrollWidth>r.clientWidth+1||m.scrollWidth>m.clientWidth+1}})()`);
        check(!a.small.length && !a.over, `${label}: taps ≥ 44 px, no overflow ${tag} ${JSON.stringify(a)}`);
      };
      await audit('off');
      await view('set-news-row'); await save('1-off');

      // ---- toggle on → news_set {on:true} → 「发到 owner@example.com」
      answers.push({ ok: true, on: true, email: EMAIL, sample_url: SAMPLE });
      await evaluate(p, `${$('set-news')}.click()`);
      await waitFor(p, `${$('set-news')}.getAttribute('aria-checked')==='true' && !${$('set-news')}.disabled`);
      check(calls.length === 2 && calls[1].t === 'news_set' && calls[1].on === true && calls[1].keys === 'on,t', 'news_set {on:true} ' + tag + ' ' + JSON.stringify(calls[1]));
      check(await evaluate(p, `${$('set-news-to')}.textContent`) === s.to && await evaluate(p, `!${$('set-news-to')}.hidden`), '「发到 {email}」 when on ' + tag);
      await audit('on');
      await view('set-news-row'); await save('2-on');

      // ---- toggle off refused → switch back on + the transient notice; it fades without a tap
      answers.push({ ok: false, problem: 'rate_limited' });
      await evaluate(p, `${$('set-news')}.click()`);
      await waitNotice(s.rate);
      check(calls[2].t === 'news_set' && calls[2].on === false, 'news_set {on:false} ' + tag);
      check(await checked() === 'true', 'refused → reverted to on ' + tag);
      check(await evaluate(p, `${$('toast')}.textContent`) === s.rate, 'rate_limited notice ' + tag);
      check(await evaluate(p, `!${$('set-news-to')}.hidden`), 'still sent to the account ' + tag);
      if (w === 390) await save('3-refused');
      await noticeGone();
      check(await evaluate(p, `${$('confirm')}.hidden && !${$('settings')}.hidden`), 'notice fades without a tap, Settings stays ' + tag);

      // ---- the settings search finds the row (data-keywords + label)
      await evaluate(p, `import('./js/settings.js').then(m=>{m.toggleSearch();return true})`);
      await evaluate(p, `(()=>{const i=${$('set-find-input')};i.value=${JSON.stringify(s.find)};i.dispatchEvent(new Event('input'));return true})()`);
      check(await evaluate(p, `import('./js/settings.js').then(m=>m.searchItems(${JSON.stringify(s.find)}).some(n=>n.id==='set-news-row'))`), 'search finds it: ' + s.find + ' ' + tag);
      check(await evaluate(p, `import('./js/settings.js').then(m=>m.searchItems('Daily AI').some(n=>n.id==='set-news-row'))`), 'search finds it: Daily AI ' + tag);
      await evaluate(p, `import('./js/settings.js').then(m=>{m.closeSearch();return true})`);

      // ---- reopen → a fresh news_get; the computer is not linked → disabled, plain sentence + 「打开账号后台」
      await closeSet();
      answers.push({ ok: false, problem: 'unlinked' });
      const n0 = calls.length;
      await openSet();
      await waitFor(p, `!${$('set-news-msg')}.hidden`);
      check(calls.length === n0 + 1 && calls[n0].t === 'news_get', 'reopen = one more news_get ' + tag);
      check(await evaluate(p, `${$('set-news-msg')}.textContent`) === s.unlinked, 'unlinked sentence ' + tag);
      const acct = await evaluate(p, `(()=>{const a=${$('set-news-acct')};return {vis:a.getClientRects().length>0,href:a.href,text:a.textContent,target:a.target,rel:a.rel}})()`);
      check(acct.vis && acct.href === 'https://agentj.app/account/' && acct.text === s.acct && acct.target === '_blank' && /noopener/.test(acct.rel), 'account link ' + tag + ' ' + JSON.stringify(acct));
      check(await evaluate(p, `${$('set-news')}.disabled && ${$('set-news')}.getAttribute('aria-checked')==='false' && ${$('set-news-to')}.hidden`), 'unlinked: disabled, off, no e-mail line ' + tag);
      await audit('unlinked');
      await view('set-news-row'); await save('4-unlinked');

      // P380: an authenticated-but-rejected host must remain disabled with a precise binding hint.
      await closeSet(); answers.push({ ok: false, problem: 'not_bound' }); await openSet();
      await waitFor(p, `!${$('set-news-msg')}.hidden`);
      check(await evaluate(p, `${$('set-news-msg')}.textContent`) === (lang === 'zh' ? '这台 Agent J 主机还没连在你的 Agent J 账号下，请在账号后台重新连接。' : "This Agent J host isn't linked to your Agent J account. Link it again in your account dashboard."), 'not_bound hint ' + tag);
      check(await evaluate(p, `${$('set-news')}.disabled && !${$('set-news-acct')}.hidden`), 'not_bound remains disabled with dashboard link ' + tag);
      await view('set-news-row'); await save('7-not-bound');

      // ---- P374 追加: all seats taken → on + 「名额 500 个已满，你在候补第 3 位…」 instead of 「发到」
      await closeSet();
      answers.push({ ok: true, on: true, queued: 3, seats: 500, email: EMAIL, sample_url: SAMPLE });
      await openSet(); await waitFor(p, `${$('set-news')}.disabled===false`);
      check(await checked() === 'true', 'queued = on ' + tag);
      check(await evaluate(p, `${$('set-news-to')}.textContent`) === s.queued && await evaluate(p, `!${$('set-news-to')}.hidden`), 'queue line ' + tag);
      await audit('queued');
      await view('set-news-row'); await save('6-queued');

      if (deep) {
        // a host answering a script URL: the link is not used; then unlinked on a write → reverted + card + row sentence
        await closeSet();
        answers.push({ ok: true, on: false, email: EMAIL, sample_url: 'javascript:alert(1)' });
        await openSet(); await waitFor(p, `${$('set-news')}.disabled===false`);
        check(await evaluate(p, `${$('set-news-sample')}.href`) === SAMPLE, 'a non-https sample_url is never used (keeps the last good one) ' + tag);
        answers.push({ ok: false, problem: 'unlinked' });
        await evaluate(p, `${$('set-news')}.click()`);
        await waitNotice(s.unlinked);
        check(await checked() === 'false' && await evaluate(p, `${$('toast')}.textContent`) === s.unlinked, 'unlinked on a write: reverted + notice ' + tag);
        await noticeGone();
        check(await evaluate(p, `!${$('set-news-acct')}.hidden && ${$('set-news')}.disabled`), 'unlinked on a write: link + disabled ' + tag);
        // a write without an answer (10 s) → reverted + 「没有及时回应」
        await closeSet();
        answers.push({ ok: true, on: false, email: EMAIL, sample_url: SAMPLE }, null);
        await openSet(); await waitFor(p, `${$('set-news')}.disabled===false`);
        await evaluate(p, `${$('set-news')}.click()`);
        check(await checked() === 'true', 'the switch moves at once while the write is out ' + tag);
        await waitNotice(s.noAnswer, 14000);
        check(await checked() === 'false' && await evaluate(p, `${$('toast')}.textContent`) === s.noAnswer, 'no answer → reverted + notice ' + tag);
        await noticeGone();
        // a computer before P374 (never answers news_get) → disabled + hint, no sample link from nowhere
        await closeSet();
        answers.push(null);
        await openSet();
        await waitFor(p, `!${$('set-news-msg')}.hidden`, 14000);
        check(await evaluate(p, `${$('set-news-msg')}.textContent`) === s.old && await evaluate(p, `${$('set-news')}.disabled`), 'older computer: disabled + hint ' + tag);
        await view('set-news-row'); await save('5-old-host');
      }
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS P374 ' + tag);
    } finally { await p.dispose(); }
  }
  fake.st.onApp = null;
  console.log(`P374: ${checks} checks; shots → ${out}`);
  return checks;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer(BEFORE ? { port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: process.env.P374_PUBLIC } : { port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost(), B = await launch();
  try { await runP374({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
