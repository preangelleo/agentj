// P288: AI 登录到期前主动续期 on the real page against the local fake host — the chat's 「现在续期」 banner (view.renew, ✕
// per reminder mark, tap → Settings → AI 登录救援 + auth_start claude/setup_token), 「Claude 登录还有 N 天」 per row with how
// logins without a fixed expiry are watched (daily check), and the authorize round's countdown + automatic reopen note.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const $ = id => `document.getElementById('${id}')`;
const URL_ = 'https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=s2';
const row = (kind, extra = {}) => ({kind, label: {claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode'}[kind], installed: true,
  current: kind === 'claude', state: 'ok', routes: [], ...extra});
const L = {
  zh: {banner: /Claude 登录还有 4 天到期/, today: /Claude 登录今天到期/, days: /Claude 登录还有 4 天到期（2026-10-18）/, renew: '现在续期',
       auto: /ChatGPT 登录会自己续期/, none: /服务商密钥一般不会过期/, unknown: /看不出哪天到期/, ok: /上次体检 .*正常/, reopen: /已自动换了一个新链接/, round: /第 2\/3 轮/},
  en: {banner: /Claude login expires in 4 days/, today: /Claude login expires today/, days: /Claude login expires in 4 days \(2026-10-18\)/, renew: 'Renew now',
       auto: /ChatGPT login renews itself/, none: /Provider keys usually do not expire/, unknown: /expiry is unknown/, ok: /Last check .*OK/, reopen: /new one was opened automatically/, round: /round 2\/3/},
};
export const CASES = {
  banner: 'p288 a) the renew banner shows N days, ✕ hides it until the next mark, a tap starts route A',
  days: 'p288 b) rows: 「Claude 登录还有 N 天」 + 现在续期; auto / none / unknown expiry and the daily check line',
  round: 'p288 c) the authorize round: countdown, round number, automatic reopen note',
};
export async function runP288({B, web, fake, out = process.env.P288_OUT || 'reports/qa/p288', rec = parityRecorder('web/test/p288.mjs')}) {
  mkdirSync(out, {recursive: true});
  const ok = new Set();
  const step = async (c, f) => { try { await f(); } catch (e) { rec.record(CASES[c], false); throw e; } ok.add(c); };
  for (const [lang, theme] of [['zh', 'light'], ['zh', 'dark'], ['en', 'light'], ['en', 'dark']]) {
    const s = L[lang]; fake.reset();
    const seen = [];
    const now = Math.floor(Date.now() / 1000);
    const token = {minted: '2025-10-18', expires: '2026-10-18', days_left: 4};
    let view = {dot: true, flow: null, renew: {kind: 'claude', days_left: 4, expires: '2026-10-18', mark: 7},
                harnesses: [row('claude', {expiry: 'known', token, warn: true, health: {at: now - 3600, ok: true}}),
                            row('codex', {expiry: 'auto', health: {at: now - 3600, ok: true}}), row('opencode', {expiry: 'none', current: false})]};
    fake.st.onApp = async (c, m, send) => {
      if (!String(m.t).startsWith('auth_')) return false;
      seen.push({t: m.t, kind: m.kind, route: m.route});
      if (m.t === 'auth_start') view = {...view, flow: {kind: 'claude', route: m.route, stage: 'url', url: URL_, expires_at: Math.floor(Date.now() / 1000) + 600, round: 1, rounds: 3}};
      await send({t: 'auth_res', r: m.r, ok: true, view});
      return true;
    };
    const p = await newPage(B, 390, 844, theme, {touch: true, allow: [web.url, fake.relay + '/']});
    const save = async name => writeFileSync(resolve(out, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
      await evaluate(p, `localStorage.removeItem('aj.arRenew')`);
      await step('banner', async () => {
        await fake.send({t: 'auth_rescue', view});
        await waitFor(p, `!${$('ar-renew')}.hidden`);
        assert.match(await evaluate(p, `${$('ar-renew-open')}.textContent`), s.banner);
        await sleep(150); await save('banner');
        await evaluate(p, `${$('ar-renew-x')}.click()`);
        await waitFor(p, `${$('ar-renew')}.hidden`);
        await fake.send({t: 'auth_rescue', view});              // same mark again: stays hidden
        await sleep(300);
        assert.ok(await evaluate(p, `${$('ar-renew')}.hidden`), 'dismissed for this mark');
        view = {...view, renew: {kind: 'claude', days_left: 0, expires: '2026-10-18', mark: 1}};
        await fake.send({t: 'auth_rescue', view});              // the next mark: back
        await waitFor(p, `!${$('ar-renew')}.hidden`);
        assert.match(await evaluate(p, `${$('ar-renew-open')}.textContent`), s.today);
        await evaluate(p, `${$('ar-renew-open')}.click()`);
        await waitFor(p, `!${$('settings')}.hidden && !${$('set-auth')}.hidden`);
        await waitFor(p, `!!document.querySelector('#ar-flow a.arlink')`);
        assert.ok(seen.some(m => m.t === 'auth_start' && m.kind === 'claude' && m.route === 'setup_token'), 'route A started');
        view = {...view, flow: null, renew: {kind: 'claude', days_left: 4, expires: '2026-10-18', mark: 7}};
        await fake.send({t: 'auth_rescue', view});
      });
      await step('days', async () => {
        await waitFor(p, `!!document.querySelector('#ar-list [data-kind=claude] .artoken')`);
        assert.match(await evaluate(p, `document.querySelector('#ar-list [data-kind=claude] .artoken').textContent`), s.days);
        const labels = await evaluate(p, `[...document.querySelectorAll('#ar-list [data-kind=claude] button')].map(b=>b.textContent)`);
        assert.ok(labels.includes(s.renew), 'renew button inside the last 7 days');
        assert.match(await evaluate(p, `document.querySelector('#ar-list [data-kind=codex] .arexp').textContent`), s.auto);
        assert.match(await evaluate(p, `document.querySelector('#ar-list [data-kind=codex] .arhealth').textContent`), s.ok);
        assert.match(await evaluate(p, `document.querySelector('#ar-list [data-kind=opencode] .arexp').textContent`), s.none);
        await sleep(150); await save('days');
        view = {...view, harnesses: [row('claude', {expiry: 'unknown'}), row('codex', {expiry: 'auto'})]};
        await fake.send({t: 'auth_rescue', view});
        await waitFor(p, `!!document.querySelector('#ar-list [data-kind=claude] .arexp')`);
        assert.match(await evaluate(p, `document.querySelector('#ar-list [data-kind=claude] .arexp').textContent`), s.unknown);
        assert.ok(await evaluate(p, `[...document.querySelectorAll('#ar-list [data-kind=claude] button')].some(b=>b.textContent===${JSON.stringify(s.renew)})`));
      });
      await step('round', async () => {
        view = {...view, flow: {kind: 'claude', route: 'setup_token', stage: 'url', url: URL_, expires_at: Math.floor(Date.now() / 1000) + 599, round: 2, rounds: 3, reopened: 'expired'}};
        await fake.send({t: 'auth_rescue', view});
        await waitFor(p, `!!document.querySelector('#ar-flow .arleft')`);
        const txt = await evaluate(p, `${$('ar-flow')}.textContent`);
        assert.match(txt, s.reopen); assert.match(txt, s.round); assert.match(txt, /9:5\d/);
        assert.equal(await evaluate(p, `document.querySelectorAll('#set-auth input,#set-auth textarea').length`), 0, 'never a code field here');
        await sleep(150); await save('round');
      });
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      console.log('PASS P288 ' + lang + '-' + theme);
    } finally { fake.st.onApp = null; await p.dispose(); }
  }
  for (const c of Object.keys(CASES)) if (ok.has(c)) rec.record(CASES[c], true);
  console.log('parity results → ' + rec.write());
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
  try { await runP288({B, web, fake}); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
