#!/usr/bin/env node
// P393 #128 「Daily AI News」 belongs to the person of THIS host's seat — the phone side, on the real page against the local
// fake host (real Noise; contract reports/qa/p393/news-128-contract.md § Phone):
//   news_get → {ok:true, on:false, need_email:true} → the row explains it, the switch cannot be used, e-mail field + 「发验证码」
//   a bad-looking address never leaves the phone · news_email {email, lang} → the code step (「验证码已发到 x」, 6-digit field,
//   「重新发送」 after 60 s, 「换一个邮箱」) · news_verify {email, code} wrong → plain sentence in the row, field marked ·
//   「重新发送」 sends the same address again · right code → the normal switch with 「发到 x」, then news_set {on:true} → on ·
//   account_email → plain words + the account link · not_bound stays the genuinely-unbound sentence · need_email on a write →
//   the row asks (no card) · settings search still finds the row · taps ≥ 44 px, no overflow (also at the large font size).
// zh / en × light / dark × 390 / 1440 → reports/qa/p393/web-news-<lang>-<theme>-<w>-<step>.png + web-news-result.json.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, key, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.P393_NEWS_OUT || ROOT + 'reports/qa/p393/';
const $ = (id) => `document.getElementById('${id}')`;
const SEAT = 'seat.person@example.com';                       // never a real account name in a shot
const SAMPLE = 'https://agentj.app/account/news/sample';
const NEED = { ok: true, on: false, need_email: true, queued: null, seats: 500, sample_url: SAMPLE };
const L = {
  zh: { need: '这台 Agent J 的新闻发到你自己的邮箱。填一个邮箱，我们发验证码确认。', send: '发验证码', verify: '确认',
    sent: `验证码已发到 ${SEAT}，10 分钟内有效。`, resendIn: /^\d+ 秒后可以重新发送$/, resend: '重新发送', change: '换一个邮箱',
    bad: '这个邮箱地址好像不对，请检查一下。', wrong: '验证码不对，或者已经过期、输错次数太多。核对邮件里的 6 位数字，或点「重新发送」拿一个新的。',
    to: '发到 ' + SEAT, verified: '邮箱已确认。打开开关，每天早上就发到这个邮箱。',
    account: '这台 Agent J 主机用的是你账号的邮箱。要换邮箱，请到账号后台修改。', acct: '打开账号后台',
    notBound: '这台 Agent J 主机还没连在你的 Agent J 账号下，请在账号后台重新连接。', find: '邮箱', emailPh: '你常用的邮箱' },
  en: { need: "This Agent J host sends the news to your own e-mail. Enter an address and we'll send a code to confirm it.", send: 'Send code',
    verify: 'Confirm', sent: `We sent a code to ${SEAT}. It works for 10 minutes.`, resendIn: /^Send again in \d+ s$/, resend: 'Send again',
    change: 'Use another e-mail', bad: "That e-mail address doesn't look right. Please check it.",
    wrong: 'That code is wrong, has expired, or was tried too many times. Check the 6 digits in the e-mail, or tap “Send again” for a new one.',
    to: 'Sent to ' + SEAT, verified: 'E-mail confirmed. Turn the switch on and the news arrives there every morning.',
    account: "This Agent J host uses your account's own e-mail. To change it, go to the account dashboard.", acct: 'Open the account dashboard',
    notBound: "This Agent J host isn't linked to your Agent J account. Link it again in your account dashboard.", find: 'email', emailPh: 'Your usual e-mail' },
};
const COMBOS = [];
for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) for (const w of [390, 1440]) COMBOS.push([lang, theme, w]);

export async function runP393News({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const results = [], shots = [];
  const check = (ok, label) => { assert.ok(ok, label); checks++; results.push(label); };
  const calls = [], answers = [];
  fake.st.onApp = async (c, m, send) => {
    if (!['news_get', 'news_set', 'news_email', 'news_verify'].includes(m.t)) return false;
    calls.push({ t: m.t, on: m.on, email: m.email, code: m.code, lang: m.lang, keys: Object.keys(m).filter((k) => k !== 'r').sort().join() });
    const a = answers.length ? answers.shift() : { ok: false, problem: 'fail' };
    if (a) await send({ t: 'news_res', r: m.r, ...a });
    return true;
  };
  for (const [lang, theme, w] of COMBOS) {
    const s = L[lang], tag = `${lang}-${theme}-${w}`, deep = tag === 'zh-light-390';
    fake.reset(); fake.setUp(true); calls.length = 0; answers.length = 0;
    const p = await newPage(B, w, w === 390 ? 844 : 900, theme, { touch: w === 390, allow: [web.url, fake.relay + '/'] });
    const save = async (name) => { const f = `web-news-${tag}-${name}.png`; writeFileSync(out + f, await shoot(p, '')); shots.push('reports/qa/p393/' + f); };
    const view = (id) => evaluate(p, `${$(id)}.scrollIntoView({block:'center'}); true`).then(() => sleep(250));
    const openSet = async () => { await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`); };
    const closeSet = () => evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`);
    const text = (id) => evaluate(p, `${$(id)}.textContent`);
    const shown = (id) => evaluate(p, `${$(id)}.getClientRects().length>0`);
    const type = (id, v) => evaluate(p, `(()=>{const i=${$(id)};i.focus();i.value=${JSON.stringify(v)};i.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
    const audit = async (label) => {
      const a = await evaluate(p, `(()=>{const r=${$('set-news-row')};const small=[...r.querySelectorAll('button,a,input')].filter(e=>e.getClientRects().length).map(e=>{const b=e.getBoundingClientRect();return {id:e.id||e.tagName,w:Math.round(b.width),h:Math.round(b.height)}}).filter(x=>x.h<44||x.w<44);const m=${$('set-main')};return {small,over:r.scrollWidth>r.clientWidth+1||m.scrollWidth>m.clientWidth+1}})()`);
      check(!a.small.length && !a.over, `${label}: taps ≥ 44 px, no overflow ${tag} ${JSON.stringify(a)}`);
    };
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      check(await evaluate(p, `document.documentElement.getAttribute('data-theme')`) === theme, 'theme ' + tag);
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);

      // ---- 1. no address for this host's person → explanation + e-mail field + 「发验证码」, the switch cannot be used
      answers.push(NEED);
      await openSet();
      await waitFor(p, `!${$('set-news-mail')}.hidden`);
      check(calls.length === 1 && calls[0].t === 'news_get' && calls[0].keys === 't', 'one bare news_get ' + tag);
      check(await evaluate(p, `${$('set-news')}.disabled && ${$('set-news')}.getAttribute('aria-checked')==='false'`), 'need_email: switch off and unusable ' + tag);
      check(await evaluate(p, `${$('set-news-mail')}.querySelector('.setnote').textContent`) === s.need, 'need_email explanation ' + tag);
      check(await shown('set-news-email') && await shown('set-news-send') && await text('set-news-send') === s.send, 'e-mail field + send button ' + tag);
      check(await evaluate(p, `${$('set-news-email')}.placeholder`) === s.emailPh && await evaluate(p, `${$('set-news-email')}.type`) === 'email', 'e-mail field placeholder/type ' + tag);
      check(!(await shown('set-news-code')) && !(await shown('set-news-resend')), 'no code step yet ' + tag);
      check(await evaluate(p, `${$('set-news-msg')}.hidden && ${$('set-news-to')}.hidden && ${$('set-news-acct')}.hidden`), 'need_email is not a problem line, no 「发到」, no account link ' + tag);
      check(await evaluate(p, `getComputedStyle(${$('set-news-email')}).fontSize`).then((f) => parseFloat(f) >= 16), 'field text ≥ 16 px (no iOS zoom) ' + tag);
      await audit('need');
      await view('set-news-row'); await save('1-need');

      // ---- 2. a bad-looking address never leaves the phone
      await type('set-news-email', 'not-an-address');
      await evaluate(p, `${$('set-news-send')}.click()`);
      await waitFor(p, `!${$('set-news-msg')}.hidden`);
      check(await text('set-news-msg') === s.bad && await evaluate(p, `${$('set-news-email')}.getAttribute('aria-invalid')`) === 'true', 'bad address: plain sentence, field marked ' + tag);
      check(calls.length === 1, 'bad address: nothing sent ' + tag);

      // ---- 3. send code → the code step (Enter in the field submits, like the button)
      await type('set-news-email', SEAT);
      check(await evaluate(p, `${$('set-news-msg')}.hidden`), 'typing clears the bad-address line ' + tag);
      answers.push({ ok: true, expires_in: 600 });
      await evaluate(p, `${$('set-news-mail')}.requestSubmit(); true`);
      await waitFor(p, `!${$('set-news-step2')}.hidden`);
      check(calls.length === 2 && calls[1].t === 'news_email' && calls[1].email === SEAT && calls[1].lang === lang && calls[1].keys === 'email,lang,t', 'news_email {email, lang} ' + tag + ' ' + JSON.stringify(calls[1]));
      check(await text('set-news-sent') === s.sent, 'code sent line ' + tag);
      check(!(await shown('set-news-email')) && await shown('set-news-code') && await text('set-news-verify') === s.verify, 'code field + confirm ' + tag);
      check(await evaluate(p, `${$('set-news-verify')}.disabled`), 'confirm waits for 6 digits ' + tag);
      check(await evaluate(p, `${$('set-news-resend')}.disabled`) && s.resendIn.test(await text('set-news-resend')), 'resend counts down 60 s ' + tag + ' ' + await text('set-news-resend'));
      check(await text('set-news-change') === s.change, 'change address ' + tag);
      check(await evaluate(p, `document.activeElement===${$('set-news-code')}`), 'focus moves to the code field ' + tag);
      check(await evaluate(p, `${$('set-news')}.disabled`), 'switch still unusable until confirmed ' + tag);
      await audit('code');
      await view('set-news-row'); await save('2-code');

      // ---- 4. wrong code → plain sentence in the row, field marked, form stays
      await type('set-news-code', '12a34-56789');
      check(await evaluate(p, `${$('set-news-code')}.value`) === '123456', 'only 6 digits are kept ' + tag);
      await type('set-news-code', '111111');
      answers.push({ ok: false, problem: 'invalid_code' });
      await evaluate(p, `${$('set-news-verify')}.click()`);
      await waitFor(p, `!${$('set-news-msg')}.hidden`);
      check(calls[2].t === 'news_verify' && calls[2].email === SEAT && calls[2].code === '111111' && calls[2].keys === 'code,email,t', 'news_verify {email, code} ' + tag + ' ' + JSON.stringify(calls[2]));
      check(await text('set-news-msg') === s.wrong, 'wrong code sentence ' + tag);
      check(await evaluate(p, `${$('set-news-code')}.getAttribute('aria-invalid')`) === 'true' && await shown('set-news-code'), 'code field marked, form stays ' + tag);
      check(await evaluate(p, `${$('confirm')}.hidden`), 'no centred card while typing ' + tag);
      await audit('wrong');
      await view('set-news-row'); await save('3-wrong');

      // ---- 5. after 60 s 「重新发送」 sends the same address again
      await evaluate(p, `import('./js/news.js').then(m=>{m._rewind(61000);return true})`);
      check(!(await evaluate(p, `${$('set-news-resend')}.disabled`)) && await text('set-news-resend') === s.resend, 'resend usable after 60 s ' + tag);
      answers.push({ ok: true, expires_in: 600 });
      await evaluate(p, `${$('set-news-resend')}.click()`);
      await waitFor(p, `${$('set-news-resend')}.disabled`);
      check(calls[3].t === 'news_email' && calls[3].email === SEAT, 'resend = news_email again ' + tag);
      check(await evaluate(p, `${$('set-news-msg')}.hidden && ${$('set-news-code')}.value===''`), 'resend clears the wrong-code line ' + tag);

      // ---- 6. right code → the normal row: 「发到 x」, switch usable; then on
      await type('set-news-code', '123456');
      answers.push({ ok: true, on: false, email: SEAT, queued: null, seats: 500, sample_url: SAMPLE });
      await evaluate(p, `${$('set-news-mail')}.requestSubmit(); true`);
      await waitFor(p, `${$('set-news-mail')}.hidden`);
      check(calls[4].t === 'news_verify' && calls[4].code === '123456', 'verify with the right code ' + tag);
      check(!(await evaluate(p, `${$('set-news')}.disabled`)) && await evaluate(p, `${$('set-news')}.getAttribute('aria-checked')`) === 'false', 'confirmed: switch usable, still off ' + tag);
      check(await text('set-news-to') === s.to && await shown('set-news-to'), '「发到 x」 after confirming ' + tag);
      check(await text('set-news-msg') === s.verified, 'confirmed sentence ' + tag);
      await audit('verified');
      await view('set-news-row'); await save('4-verified');
      answers.push({ ok: true, on: true, email: SEAT, queued: null, seats: 500, sample_url: SAMPLE });
      await evaluate(p, `${$('set-news')}.click()`);
      await waitFor(p, `${$('set-news')}.getAttribute('aria-checked')==='true' && !${$('set-news')}.disabled`);
      check(calls[5].t === 'news_set' && calls[5].on === true && calls[5].keys === 'on,t', 'news_set {on:true} after confirming ' + tag);
      check(await text('set-news-to') === s.to && await evaluate(p, `${$('set-news-msg')}.hidden`), 'on: 「发到 x」, the confirmed line gone ' + tag);
      await audit('on');
      await view('set-news-row'); await save('5-on');

      // ---- 7. account_email → plain words + the account link
      await closeSet();
      answers.push(NEED);
      await openSet(); await waitFor(p, `!${$('set-news-mail')}.hidden`);
      check(await evaluate(p, `!${$('set-news-email')}.hidden`), 'a fresh opening starts at the e-mail step ' + tag);
      await type('set-news-email', SEAT);
      answers.push({ ok: false, problem: 'account_email' });
      await evaluate(p, `${$('set-news-send')}.click()`);
      await waitFor(p, `!${$('set-news-msg')}.hidden`);
      check(await text('set-news-msg') === s.account, 'account_email sentence ' + tag);
      const acct = await evaluate(p, `(()=>{const a=${$('set-news-acct')};return {vis:a.getClientRects().length>0,href:a.href,text:a.textContent,target:a.target,rel:a.rel}})()`);
      check(acct.vis && acct.href === 'https://agentj.app/account/' && acct.text === s.acct && acct.target === '_blank' && /noopener/.test(acct.rel), 'account_email: the account link ' + tag);
      check(await evaluate(p, `${$('set-news-mail')}.hidden && ${$('set-news')}.disabled`), 'account_email: form gone, switch unusable ' + tag);
      await audit('account');
      await view('set-news-row'); await save('6-account');

      // ---- 8. not_bound stays the genuinely-unbound sentence (and never shows the e-mail step)
      await closeSet();
      answers.push({ ok: false, problem: 'not_bound' });
      await openSet(); await waitFor(p, `!${$('set-news-msg')}.hidden`);
      check(await text('set-news-msg') === s.notBound && await evaluate(p, `${$('set-news-mail')}.hidden && ${$('set-news')}.disabled && !${$('set-news-acct')}.hidden`), 'not_bound: own sentence, link, no e-mail step ' + tag);
      await view('set-news-row'); await save('7-not-bound');

      // ---- 9. the settings search still finds the row
      await evaluate(p, `import('./js/settings.js').then(m=>{m.toggleSearch();return true})`);
      for (const word of [s.find, 'Daily AI']) {
        check(await evaluate(p, `import('./js/settings.js').then(m=>m.searchItems(${JSON.stringify(word)}).some(n=>n.id==='set-news-row'))`), `search finds the row: ${word} ${tag}`);
      }
      await evaluate(p, `import('./js/settings.js').then(m=>{m.closeSearch();return true})`);

      if (deep) {
        // a switch-on that the account answers need_email → the row asks (no card), the switch stays off
        await closeSet();
        answers.push({ ok: true, on: false, email: '', queued: null, seats: 500, sample_url: SAMPLE });
        await openSet(); await waitFor(p, `${$('set-news')}.disabled===false`);
        answers.push({ ok: false, problem: 'need_email' });
        await evaluate(p, `${$('set-news')}.click()`);
        await waitFor(p, `!${$('set-news-mail')}.hidden`);
        check(await evaluate(p, `${$('set-news')}.getAttribute('aria-checked')==='false' && ${$('set-news')}.disabled && ${$('confirm')}.hidden`), 'need_email on a write: the row asks, no card ' + tag);
        check(await evaluate(p, `document.activeElement===${$('set-news-email')}`), 'need_email on a write: focus in the e-mail field ' + tag);
        // 「换一个邮箱」 goes back; mail_failed / rate_limited / no answer are plain sentences in the row
        await type('set-news-email', SEAT);
        answers.push({ ok: false, problem: 'mail_failed' });
        await evaluate(p, `${$('set-news-send')}.click()`); await waitFor(p, `!${$('set-news-msg')}.hidden`);
        check(await text('set-news-msg') === '验证码邮件这次没发出去，请稍后再试。', 'mail_failed sentence ' + tag);
        answers.push({ ok: false, problem: 'rate_limited' });
        await evaluate(p, `${$('set-news-send')}.click()`); await waitFor(p, `${$('set-news-msg')}.textContent==='验证码发得太频繁了，请过一会儿再试。'`);
        check(true, 'rate_limited sentence ' + tag);
        answers.push({ ok: true, expires_in: 600 });
        await evaluate(p, `${$('set-news-send')}.click()`); await waitFor(p, `!${$('set-news-step2')}.hidden`);
        await evaluate(p, `${$('set-news-change')}.click()`);
        check(await evaluate(p, `!${$('set-news-step1')}.hidden && ${$('set-news-step2')}.hidden && document.activeElement===${$('set-news-email')}`), '「换一个邮箱」 → back to the e-mail step ' + tag);
        // the large font size: still no overflow, still ≥ 44 px
        await evaluate(p, `document.documentElement.dataset.fz='l'; true`);
        await sleep(200);
        await audit('font-l');
        await view('set-news-row'); await save('8-font-large');
        await evaluate(p, `delete document.documentElement.dataset.fz; true`);
        // Esc leaves Settings even with the cursor in the field
        await evaluate(p, `${$('set-news-email')}.focus(); true`);
        await key(p, 'Escape');
        await waitFor(p, `${$('settings')}.hidden`);
        check(true, 'Esc closes Settings from the e-mail field ' + tag);
      }
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS P393 news ' + tag);
    } finally { await p.dispose(); }
  }
  fake.st.onApp = null;
  writeFileSync(out + 'web-news-result.json', JSON.stringify({ ok: true, checks, combos: COMBOS.map((c) => c.join('-')), shots, results,
    note: '390 px = Chromium touch emulation, not a physical device; WebKit not run (L2)' }, null, 2));
  console.log(`P393 news: ${checks} checks; shots → ${out}`);
  return checks;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost(), B = await launch();
  try { await runP393News({ B, web, fake }); } catch (e) { console.error(e); process.exitCode = 1; }
  finally { await Promise.allSettled([B.close(), fake.stop(), web.stop()]); }
  process.exit(process.exitCode || 0);
}
