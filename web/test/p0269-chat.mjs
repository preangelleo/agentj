// 0.26.9 selfheal lane w-chat (CONTRACT §C, #461 #464) — real phone page, local fake host, real Noise; no customer machine,
// no real Claude / Codex, no credentials (the authorisation link and the codes are made up).
//   · a reply carrying the host's `login` gets 「重新登录」 and its `/login` becomes the same button; plain prose saying
//     "login" / "/login" without the field gets neither (negative control)
//   · the button opens the login sheet over the chat: signed auth_rescue {action:"login", agent} → link (+ device code) →
//     「粘贴授权码」 → signed auth_rescue {action:"code", code} → fail with the host's plain reason (sheet stays) → again → done
//     ok (sheet closes); Esc closes it; a forged signature is refused by the fake host exactly like the real one
//   · the queued notice shows 「上一件还在做，已等 N 分钟」 from waited_s; 「停下上一件」 (0.26.9 #471 merged notice, lane w2-web)
//     only when stuck or ≥ 120 s → signed rescue_do end_turn
//   · Settings → 紧急修复: 「接管卡住的会话」 is the first card while a turn is stuck → signed rescue_do takeover, reset_step / reset_done
//   · the self-heal line 「刚才卡住了，已自动恢复。」 is an ordinary reply
// Screenshots (390 × 844 light + dark, 1440 × 900 light): reports/qa/p0269-chat/ (P0269_CHAT_OUT overrides). AJ_P0269_BEFORE=1 + AJ_P0269_PUBLIC=<base
// public/> draws the same scenes on the base build for the before shots (no new-UI assertions).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const before = process.env.AJ_P0269_BEFORE === '1';
const tag = before ? 'before' : 'after';
const out = process.env.P0269_CHAT_OUT ? pathToFileURL(process.env.P0269_CHAT_OUT.replace(/\/?$/, '/')) : new URL('../../../reports/qa/p0269-chat/', import.meta.url); mkdirSync(out, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.AJ_P0269_PUBLIC ? { publicDir: process.env.AJ_P0269_PUBLIC } : {}) });
const fake = await startFakeHost(), B = await launch();
const results = {};
const LINK = 'https://claude.ai/oauth/authorize?client_id=example-client&state=example-state';
const W = { zh: { codexWait: 'Agent J 主机上的 Codex 正在回答，等它说完我就把您的消息递过去。', healed: '刚才卡住了，已自动恢复。' },
  en: { codexWait: "The desktop Codex is mid-answer; I'll hand over your message when it finishes.", healed: 'It got stuck just now — recovered automatically.' } };
const shots = [];
const snap = async (p, name) => { const f = `${name}-${tag}.png`; writeFileSync(new URL(f, out), await shoot(p, '')); shots.push(f); };
const txt = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)})?.textContent ?? null`);
const box = (p, sel) => evaluate(p, `(() => { const n = document.querySelector(${JSON.stringify(sel)}); if (!n) return null; const r = n.getBoundingClientRect(); return {w: r.width, h: r.height, x: r.x, right: r.right}; })()`);
const goLast = (p) => evaluate(p, `(() => { document.getElementById('pgLatest')?.click(); return true; })()`);

async function pair(p, lang) {
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();return true})()`);
  await sleep(600);
}

try {
  const runs = before ? [['zh', 390, 'light'], ['zh', 390, 'dark']] : [['zh', 390, 'light'], ['zh', 390, 'dark'], ['en', 390, 'light'], ['zh', 1440, 'light']];
  for (const [lang, w, scheme] of runs) {
    fake.reset();
    const id = `${lang}-${w}-${scheme}`;
    const msgs = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'auth_rescue') {
        const target = m.action === 'login' ? { action: 'login', agent: m.agent } : m.action === 'code' ? { action: 'code', code: m.code } : { action: m.action };
        const ok = await fake.verifyControl(c, 'auth_rescue', m, target);
        msgs.push({ t: m.t, action: m.action, agent: m.agent, codeLen: typeof m.code === 'string' ? m.code.length : 0, ok });
        if (!ok) { await send({ t: 'auth_rescue', r: m.r, view: { step: 'done', ok: false, why: 'bad_signature' } }); return true; }
        if (m.action === 'login') {
          await send({ t: 'auth_rescue', r: m.r, view: { step: 'starting', agent: m.agent } });
          await sleep(120);
          await send({ t: 'auth_rescue', r: m.r, view: m.agent === 'codex' ? { step: 'url', agent: 'codex', url: 'https://auth.openai.com/codex/device', code: 'WXYZ-1234' } : { step: 'url', agent: 'claude', url: LINK } });
        }
        if (m.action === 'code') {
          await send({ t: 'auth_rescue', r: m.r, view: { step: 'verifying' } });
          await sleep(150);
          const good = m.code === 'good-code#state';
          await send({ t: 'auth_rescue', r: m.r, view: good ? { step: 'done', ok: true } : { step: 'done', ok: false, why: 'main_session', text: lang === 'zh' ? '登录成功了，但你正在用的对话还没回消息。' : 'Signed in, but the conversation you are using has not answered yet.' } });
        }
        return true;
      }
      if (m.t === 'rescue_do' && ['end_turn', 'takeover'].includes(m.action)) {
        const ok = await fake.verifyControl(c, 'rescue_do', m, { action: m.action });
        msgs.push({ t: m.t, action: m.action, ok });
        if (!ok) { await send({ t: 'reset_done', r: m.r, flow: m.action, ok: false, why: 'bad_signature' }); return true; }
        await send({ t: 'reset_step', r: m.r, flow: m.action, step: 1, state: 'run', text: '' });
        await sleep(m.action === 'takeover' ? 900 : 300);
        if (m.action === 'takeover') { await send({ t: 'reset_step', r: m.r, flow: 'takeover', step: 2, state: 'run', text: '' }); await sleep(600); }
        await send({ t: 'reset_done', r: m.r, flow: m.action, ok: true, step: m.action === 'takeover' ? 4 : 1, text: '' });
        return true;
      }
      return false;
    };
    const p = await newPage(B, w, w > 1000 ? 900 : 844, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 1000 });
    try {
      await pair(p, lang);
      // ---- 1. the signed-out reply (#464 screenshot: 「Not logged in · Please run /login」)
      await fake.addTurn({ k: 'phone', text: lang === 'zh' ? '回来啦' : "I'm back" }, 'Not logged in · Please run /login', 'done', null);
      const plain = fake.st.turns.at(-1).id;
      await sleep(500);
      // negative control: the same words without the host's field → no button, `/login` stays text
      assert.equal(await evaluate(p, `!!document.querySelector('#cmdx [data-login-btn]')`), false, 'no field → no 重新登录');
      assert.equal(await evaluate(p, `document.querySelectorAll('#words [data-login]').length`), 0, 'no field → /login is text');
      await fake.updateTurn(plain, { login: { agent: 'claude', why: 'not_logged_in' } });   // host history.py: top-level `login`
      await sleep(500);
      if (!before) {
        await waitFor(p, `!!document.querySelector('#cmdx [data-login-btn="claude"]')`);
        assert.equal(await txt(p, '#cmdx [data-login-btn]'), lang === 'zh' ? '重新登录' : 'Sign in again');
        assert.equal(await evaluate(p, `document.querySelectorAll('#words button[data-login]').length`), 1, '/login → the same button');
        assert.match(await txt(p, '#words'), /Not logged in · Please run \/login/);
        const g = await box(p, '#cmdx [data-login-btn]');
        assert.ok(g.h >= 44 && g.x >= 0 && g.right <= w, JSON.stringify(g));
      }
      await snap(p, `login-reply-${id}`);
      // a second reply mentioning /login in prose, no field: still nothing (negative control, after the positive one)
      await fake.addTurn({ k: 'phone', text: 'how?' }, 'You can run /login later, or login from Settings.', 'done', null);
      await sleep(400);
      assert.equal(await evaluate(p, `!!document.querySelector('#cmdx [data-login-btn]') || document.querySelectorAll('#words [data-login]').length > 0`), false, 'prose /login → nothing');

      if (!before) {
        // ---- 2. the login sheet: the inline /login of the first reply opens it (page back to that reply)
        await evaluate(p, `document.getElementById('pgPrev').click()`); await sleep(300);
        await waitFor(p, `!!document.querySelector('#words button[data-login]')`);
        await evaluate(p, `document.querySelector('#words button[data-login]').click()`);
        await waitFor(p, `!document.getElementById('lgs').hidden`);
        await waitFor(p, `!!document.getElementById('lgs-open')`, 6000);
        await sleep(400);   // the card's open animation (scale) has ended before anything is measured
        assert.equal(await evaluate(p, `document.getElementById('lgs-open').getAttribute('href')`), LINK);
        assert.equal(await evaluate(p, `document.getElementById('lgs-open').target + '|' + document.getElementById('lgs-open').rel`), '_blank|noopener noreferrer');
        assert.equal(await evaluate(p, `!!document.getElementById('lgs-code') && !document.getElementById('lgs-devcode')`), true);
        assert.equal(await txt(p, '#lgs-title'), lang === 'zh' ? '重新登录 Claude Code' : 'Sign in to Claude Code again');
        for (const sel of ['#lgs-open', '#lgs-submit', '#lgs-x', '#lgs-copy']) { const g = await box(p, sel); assert.ok(g && g.h >= 44 && g.w >= 44, sel + JSON.stringify(g)); }
        assert.deepEqual(msgs.map((x) => [x.t, x.action, x.agent, x.ok]), [['auth_rescue', 'login', 'claude', true]]);
        await snap(p, `login-sheet-url-${id}`);
        // empty submit → a hint, nothing sent
        await evaluate(p, `document.getElementById('lgs-submit').click()`); await sleep(150);
        assert.equal(msgs.length, 1);
        assert.ok((await txt(p, '#lgs-note')).length > 0);
        // a wrong code → the host's plain reason; the sheet stays open
        await evaluate(p, `(()=>{const f=document.getElementById('lgs-code');f.value='  wrong-code ';f.dispatchEvent(new Event('input'));document.getElementById('lgs-submit').click();return true})()`);
        await waitFor(p, `document.getElementById('lgs').dataset.state==='fail'`, 6000);
        assert.deepEqual(msgs.at(-1), { t: 'auth_rescue', action: 'code', agent: undefined, codeLen: 'wrong-code'.length, ok: true }, 'code sent trimmed, signed');
        assert.match(await txt(p, '#lgs-msg'), lang === 'zh' ? /你正在用的对话还没回消息/ : /has not answered yet/);
        assert.equal(await evaluate(p, `document.getElementById('lgs').hidden`), false, 'fail keeps the sheet');
        await snap(p, `login-sheet-fail-${id}`);
        // again → right code → done ok → the sheet closes by itself
        await evaluate(p, `document.getElementById('lgs-again').click()`);
        await waitFor(p, `!!document.getElementById('lgs-code')`, 6000);
        await evaluate(p, `(()=>{const f=document.getElementById('lgs-code');f.value='good-code#state';document.getElementById('lgs-submit').click();return true})()`);
        await waitFor(p, `document.getElementById('lgs').dataset.state==='ok'`, 6000);
        assert.equal(await evaluate(p, `document.getElementById('lgs-code')`), null, 'the code field is gone');
        await snap(p, `login-sheet-ok-${id}`);
        await waitFor(p, `document.getElementById('lgs').hidden`, 4000);
        assert.deepEqual(msgs.map((x) => x.action), ['login', 'code', 'login', 'code']);
        assert.ok(msgs.every((x) => x.ok), 'every auth_rescue signed');
        // Codex device code: shown big, no paste field needed; Esc closes the sheet
        await fake.addTurn({ k: 'phone', text: 'codex?' }, 'Codex: please sign in.', 'done', null, {});
        const cx = fake.st.turns.at(-1).id; await fake.updateTurn(cx, { card: { login: { agent: 'codex', why: 'device' } } });
        await goLast(p);
        await waitFor(p, `!!document.querySelector('#cmdx [data-login-btn="codex"]')`);
        await evaluate(p, `document.querySelector('#cmdx [data-login-btn]').click()`);
        await waitFor(p, `document.getElementById('lgs-devcode')?.textContent==='WXYZ-1234'`, 6000);
        assert.equal(await evaluate(p, `!!document.getElementById('lgs-code')`), false);
        await snap(p, `login-sheet-device-${id}`);
        await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
        await waitFor(p, `document.getElementById('lgs').hidden`, 3000);
      }

      // ---- 3. the queued notice (#461): waited_s → 「已等 N 分钟」; ≥ 120 s or stuck → 「强制结束这一轮」
      await fake.send({ t: 'status', s: 'working', agent: 'codex', name: 'Agent J' });
      await fake.addTurn({ k: 'sys', text: '' }, W[lang].codexWait, 'done', { queued: true, waited_s: 30 });
      const q = fake.st.turns.at(-1).id;
      await goLast(p); await sleep(500);
      if (!before) {
        await waitFor(p, `!!document.querySelector('#cmdx [data-waited]')`);
        assert.equal(await txt(p, '#cmdx .sh-waited'), lang === 'zh' ? '上一件还在做，已等不到 1 分钟' : 'Previous task still running · waiting under a minute');
        assert.equal(await evaluate(p, `!!document.querySelector('#cmdx [data-end-turn]')`), false, 'host notice < 120 s: no button yet');
        await fake.updateTurn(q, { card: { queued: true, waited_s: 300 } });
        await waitFor(p, `!!document.querySelector('#cmdx [data-end-turn]')`);
        assert.equal(await txt(p, '#cmdx .sh-waited'), lang === 'zh' ? '上一件还在做，已等 5 分钟' : 'Previous task still running · waiting 5 min');
        await fake.updateTurn(q, { card: { queued: true, waited_s: 40, stuck: true } });
        await waitFor(p, `document.querySelector('#cmdx .sh-waited')?.textContent===${JSON.stringify(lang === 'zh' ? '上一件还在做，已等不到 1 分钟' : 'Previous task still running · waiting under a minute')}`);
        assert.equal(await evaluate(p, `!!document.querySelector('#cmdx [data-end-turn]')`), true, 'stuck: button even under 120 s');
        const g = await box(p, '#cmdx [data-end-turn]'); assert.ok(g.h >= 44 && g.right <= w, JSON.stringify(g));
        assert.equal(await txt(p, '#cmdx [data-end-turn]'), lang === 'zh' ? '停下上一件' : 'Stop previous task', 'merged notice: 停下上一件');
      } else await fake.updateTurn(q, { card: { queued: true, waited_s: 300, stuck: true } });
      await sleep(300);
      await snap(p, `queued-stuck-${id}`);
      if (!before) {
        await evaluate(p, `document.querySelector('#cmdx [data-end-turn]').click()`);
        await waitFor(p, `!document.getElementById('confirm').hidden`, 3000);
        await evaluate(p, `document.getElementById('confirm-no').click()`); await sleep(200);
        assert.equal(msgs.filter((x) => x.action === 'end_turn').length, 0, 'cancel sends nothing');
        await evaluate(p, `document.querySelector('#cmdx [data-end-turn]').click()`);
        await waitFor(p, `!document.getElementById('confirm').hidden`, 3000);
        await evaluate(p, `document.getElementById('confirm-yes').click()`);
        await waitFor(p, `document.getElementById('sh-end-msg')?.classList.contains('rsok')`, 6000);
        assert.deepEqual(msgs.filter((x) => x.action === 'end_turn'), [{ t: 'rescue_do', action: 'end_turn', ok: true }]);
        assert.equal(await txt(p, '#sh-end-msg'), lang === 'zh' ? '这一轮已经结束，可以接着说了。' : 'That turn has ended. You can carry on.');
        await snap(p, `queued-ended-${id}`);
      }

      // ---- 4. Settings → 紧急修复: 「接管卡住的会话」 first while stuck
      await evaluate(p, `import('./js/settings.js').then(s=>s.openRescue())`); await sleep(500);
      if (!before) {
        await waitFor(p, `!!document.getElementById('rs-takeover')`);
        assert.equal(await evaluate(p, `document.querySelector('#set-s-rescue .rscards').firstElementChild.id`), 'rs-takeover', 'stuck → takeover first');
        assert.equal(await evaluate(p, `!!document.getElementById('rs-takeover-tag')`), true);
        await evaluate(p, `document.getElementById('rs-takeover').scrollIntoView({block:'start'})`); await sleep(200);
        await snap(p, `rescue-takeover-${id}`);
        await evaluate(p, `document.getElementById('rs-takeover-go').click()`);
        await waitFor(p, `!document.getElementById('confirm').hidden`, 3000);
        await evaluate(p, `document.getElementById('confirm-yes').click()`);
        await waitFor(p, `document.getElementById('rs-takeover').dataset.state==='run'`, 4000);
        await waitFor(p, `!!document.getElementById('rs-takeover-bar')`, 4000);
        await snap(p, `rescue-takeover-run-${id}`);
        await waitFor(p, `document.getElementById('rs-takeover').dataset.state==='ok'`, 6000);
        assert.deepEqual(msgs.filter((x) => x.action === 'takeover'), [{ t: 'rescue_do', action: 'takeover', ok: true }]);
        await snap(p, `rescue-takeover-ok-${id}`);
      } else await snap(p, `rescue-takeover-${id}`);
      await evaluate(p, `import('./js/settings.js').then(s=>s.close?.())`);
      await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`); await sleep(300);

      // ---- 5. the self-heal line is an ordinary agent reply; the stuck mark is gone → takeover is no longer first
      await fake.updateTurn(q, { card: { queued: false } });
      await fake.addTurn({ k: 'agent', text: '' }, W[lang].healed, 'done', null);
      await goLast(p);
      await waitFor(p, `document.getElementById('words').textContent.includes(${JSON.stringify(W[lang].healed)})`);
      if (!before) {
        await evaluate(p, `import('./js/settings.js').then(s=>s.openRescue())`); await sleep(400);
        assert.equal(await evaluate(p, `document.getElementById('rs-reset').previousElementSibling?.id ?? null`), null, 'not stuck → ① first again');
        await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`); await sleep(300);
      }
      await snap(p, `selfheal-line-${id}`);
      assert.equal(p.problems.length, 0, p.problems.join('\n'));
      results[id] = 'PASS';
    } finally { await p.close?.(); }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(new URL(`browser-${tag}.json`, out), JSON.stringify({ results, shots }, null, 2) + '\n');
console.log(JSON.stringify(results));
