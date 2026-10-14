// P258: Settings → AI 登录救援 on the real page against the local fake host — the red dot from a host push, the per-harness
// rows (login expired / balance), Claude's authorize-link flow, Codex's device code, the 「代按确认」 press, zh/en × light/dark.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const $ = id => `document.getElementById('${id}')`;
const URL_ = 'https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=s1';
const row = (kind, extra = {}) => ({kind, label: {claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode'}[kind], installed: true,
  current: kind === 'claude', state: 'ok', routes: [], ...extra});
const L = {
  zh: {login: '登录已过期', balance: '余额 / 额度不足', a: '重新登录（推荐）', press: '代按确认', blocked: /重启后停在/, recovered: '已恢复'},
  en: {login: 'Login expired', balance: 'Out of balance / quota', a: 'Sign in again (recommended)', press: 'Press it for me', blocked: /stopped at/, recovered: 'Recovered'},
};
export const CASES = {
  dot: 'p258 a) a login alert lights the red dot; balance does not',
  rows: 'p258 b) per-harness rows: state chip, Claude routes, Codex device code button',
  claude: 'p258 c) Claude: passkey-gated start, authorize link shown, never a code in the page',
  before: 'p266 a) Codex pre-start reminder, official settings link and preserved failure hint',
  codex: 'p258 d) Codex: device link + one-time code + countdown',
  press: 'p258 e) a blocked restart screen offers 「代按确认」 and sends auth_press',
};
export async function runP258({B, web, fake, out = process.env.P258_OUT || 'reports/qa/p258', rec = parityRecorder('web/test/p258.mjs')}) {
  mkdirSync(out, {recursive: true});
  const ok = new Set();
  const step = async (c, f) => { try { await f(); } catch (e) { rec.record(CASES[c], false); throw e; } ok.add(c); };
  for (const [lang, theme] of [['zh', 'light'], ['zh', 'dark'], ['en', 'light'], ['en', 'dark']]) {
    const s = L[lang]; fake.reset();
    const seen = [];
    let view = {dot: true, flow: null, harnesses: [row('claude', {state: 'login', token: {minted: '2025-11-01', expires: '2026-11-01', days_left: 18}, warn: true}),
                                                   row('codex'), row('opencode', {installed: false, current: false})]};
    fake.st.onApp = async (c, m, send) => {
      if (!String(m.t).startsWith('auth_')) return false;
      seen.push({t: m.t, kind: m.kind, route: m.route, id: m.id});
      if (m.t === 'auth_start' && m.kind === 'claude') view = {...view, flow: {kind: 'claude', route: m.route, stage: 'url', url: URL_, expires_at: Math.floor(Date.now() / 1000) + 600}};
      await send({t: 'auth_res', r: m.r, ok: true, view});
      return true;
    };
    const p = await newPage(B, 390, 844, theme, {touch: true, allow: [web.url, fake.relay + '/']});
    const save = async name => writeFileSync(resolve(out, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
      await step('dot', async () => {
        await fake.send({t: 'auth_rescue', view});
        await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
        await waitFor(p, `!${$('ar-dot')}.hidden`);
      });
      await step('rows', async () => {
        await evaluate(p, `${$('set-auth-nav')}.click()`); await waitFor(p, `!${$('set-auth')}.hidden`);
        await waitFor(p, `document.querySelector('#ar-list [data-kind=claude] .archip')?.textContent===${JSON.stringify(s.login)}`);
        const labels = await evaluate(p, `[...document.querySelectorAll('#ar-list [data-kind=claude] button')].map(b=>b.textContent)`);
        assert.ok(labels.includes(s.a), 'Claude rescue button');
        assert.ok(await evaluate(p, `!!document.querySelector('#ar-list [data-kind=codex] button.aj-btn--primary')`), 'Codex device button');
        assert.ok(!(await evaluate(p, `!!document.querySelector('#ar-list [data-kind=opencode]')`)), 'not installed and not current → hidden');
        assert.match(await evaluate(p, `document.querySelector('#ar-list [data-kind=claude] .ardanger')?.textContent||''`), /2026-11-01/);
        assert.equal(seen[0].t, 'auth_get');
        await sleep(150); await save('rows');
      });
      await step('before', async () => {
        assert.equal(seen.filter(m => m.t === 'auth_start').length, 0, 'reminder appears before any start');
        const before = await evaluate(p, `(()=>{const row=document.querySelector('#ar-list [data-kind=codex]');const note=row.querySelector('.arbefore'),a=note.querySelector('a'),b=row.querySelector('button');return {text:note.textContent,href:a.href,target:a.target,rel:a.rel,before:!!(note.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING),visible:note.getBoundingClientRect().height>0}})()`);
        assert.ok(before.before && before.visible, 'visible before the login button');
        assert.match(before.text, lang === 'zh' ? /第一次用？先在任何浏览器登录 ChatGPT.*已开过可跳过/ : /First time.*any browser.*skip if already enabled/);
        assert.equal(before.href, 'https://chatgpt.com/#settings/Security');
        assert.equal(before.target, '_blank'); assert.match(before.rel, /noopener/); assert.match(before.rel, /noreferrer/);
        assert.equal(await evaluate(p, `document.querySelectorAll('#ar-list [data-kind=claude] .arbefore').length`), 0);
        await evaluate(p, `document.querySelector('#ar-list [data-kind=codex]').scrollIntoView({block:'center'})`);
        await save('codex-before');
        view = {...view, harnesses: [row('codex', {state:'login',last:{result:'failed',why:'device_disabled'}})]};
        await fake.send({t:'auth_rescue',view});
        await waitFor(p, `!!document.querySelector('#ar-list [data-kind=codex] .ardanger')`);
        assert.match(await evaluate(p, `document.querySelector('#ar-list [data-kind=codex] .ardanger').textContent`), lang === 'zh' ? /设备码登录没开.*再试一次/ : /device code login is off.*retry/);
        assert.ok(await evaluate(p, `!!document.querySelector('#ar-list [data-kind=codex] .arbefore')`));
        await save('codex-disabled');
        view = {...view, harnesses:[row('claude',{state:'login'}),row('codex')]};
        await fake.send({t:'auth_rescue',view});
        await waitFor(p, `!!document.querySelector('#ar-list [data-kind=claude] button')`);
      });
      await step('claude', async () => {
        await evaluate(p, `[...document.querySelectorAll('#ar-list [data-kind=claude] button')].find(b=>b.textContent===${JSON.stringify(s.a)}).click()`);
        await waitFor(p, `!${$('ar-flow')}.hidden && !!document.querySelector('#ar-flow a.arlink')`);
        assert.deepEqual(seen.at(-1), {t: 'auth_start', kind: 'claude', route: 'setup_token', id: undefined});
        const a = await evaluate(p, `(()=>{const a=document.querySelector('#ar-flow a.arlink');return {href:a.href,target:a.target,rel:a.rel}})()`);
        assert.equal(a.href, URL_); assert.equal(a.target, '_blank'); assert.match(a.rel, /noopener/);
        assert.equal(await evaluate(p, `document.querySelectorAll('#set-auth input,#set-auth textarea').length`), 0, 'no code field on this page: the secret card takes it');
        await sleep(150); await save('claude-url');
      });
      await step('codex', async () => {
        view = {...view, flow: {kind: 'codex', route: 'device', stage: 'device', url: 'https://auth.openai.com/codex/device', code: 'AB12-CD34E',
                                expires_at: Math.floor(Date.now() / 1000) + 899}};
        await fake.send({t: 'auth_rescue', view});
        await waitFor(p, `document.querySelector('#ar-flow .arcode')?.textContent==='AB12-CD34E'`);
        assert.match(await evaluate(p, `${$('ar-flow')}.textContent`), /14:5\d/);
        await sleep(150); await save('codex-device');
      });
      await step('press', async () => {
        view = {...view, dot: true, flow: {kind: 'claude', route: 'paste', stage: 'failed', why: 'x'},
                harnesses: [row('claude', {state: 'ok', last: {result: 'blocked', screen: {id: 'mcp', action: 'confirm', label: 'MCP'}}}), row('codex', {state: 'balance'})]};
        await fake.send({t: 'auth_rescue', view});
        await waitFor(p, `[...document.querySelectorAll('#ar-list button')].some(b=>b.textContent===${JSON.stringify(s.press)})`);
        assert.match(await evaluate(p, `document.querySelector('#ar-list [data-kind=claude]').textContent`), s.blocked);
        assert.equal(await evaluate(p, `document.querySelector('#ar-list [data-kind=codex] .archip').textContent`), s.balance);
        const colors = await evaluate(p, `(()=>{const e=document.createElement('span');e.style.color='var(--text)';document.querySelector('#set-auth').append(e);const want=getComputedStyle(e).color;e.remove();return {got:getComputedStyle(document.querySelector('#ar-list [data-kind=codex] .archip')).color,want}})()`);
        assert.equal(colors.got, colors.want, 'balance status uses the existing theme text token, without a new off-palette fallback');
        await evaluate(p, `[...document.querySelectorAll('#ar-list button')].find(b=>b.textContent===${JSON.stringify(s.press)}).click()`);
        await waitFor(p, `true`); await sleep(300);
        assert.deepEqual(seen.at(-1), {t: 'auth_press', kind: 'claude', route: undefined, id: 'mcp'});
        await save('blocked');
        await fake.send({t: 'auth_rescue', view: {...view, dot: false, flow: null}});
        await waitFor(p, `${$('ar-dot')}.hidden`);
      });
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      console.log('PASS P258 ' + lang + '-' + theme);
    } finally { fake.st.onApp = null; await p.dispose(); }
  }
  for (const c of Object.keys(CASES)) if (ok.has(c)) rec.record(CASES[c], true);
  console.log('parity results → ' + rec.write());
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
  try { await runP258({B, web, fake}); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
