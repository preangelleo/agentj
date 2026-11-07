// 0.26.9 lane w4-web · the real page against the local encrypted fake host (synthetic data only):
//   #433/#447 Agent J /commands in a system page and a reply are internal links (Banana Republic); [label](reply:/cmd) shows the
//             label; a look-only command goes at once as typed (say "/recover-background", no reply_to), /clear only fills the
//             composer; a path, a fenced block and an unknown /name stay words
//   #446      the host's plain-words /recover-background list: 「读报告」 sends the full id, no 64-hex id or JSON on screen
//   #451      「主机已升级，新版网页稍后上线」 = the shared notice with 关闭, gone by itself after 10 s, once per host version
//   #452/#454/#456 Settings → AI 账号: the login in use listed at once, 「添加另一个账号」, name 「备用 1」 ready; the AuthRescue card
//             is answered above Settings (Settings stays open), titled 「添加另一个 Claude Code 账号」 with one sentence (no JSON,
//             no request hash); the finished flow = one 10-second notice, no resident box — also after a reload
//   #453      a file card's 全屏查看 is icon-only (label in aria-label) and its actions stay inside the card at 320 / 390 px
//   #444      a background task sent to Olares says 「在 Olares 上跑」, the rest 「在本机跑」
// 390 × light / dark × zh / en (+ 1440 for the chat links) → $W4_OUT (default reports/qa/feedback573-lost-batch/), names after-*.png.
// W4_BEFORE=<a public/ dir of the base> → the same scenes on the old page, no assertions, names before-*.png.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep } from './browser.mjs';

const BEFORE = process.env.W4_BEFORE || '';
const out = process.env.W4_OUT || 'reports/qa/feedback573-lost-batch';
mkdirSync(out, { recursive: true });
const tag = BEFORE ? 'before' : 'after';
const $ = (id) => `document.getElementById('${id}')`;
const HEX = 'c'.repeat(64);
const NOW = Math.floor(Date.now() / 1000);
const T = {
  zh: {
    sys: '2 件后台活已结束（千夜邮件），原操作没有重做；其中 1 件留了报告，点 /recover-background 查看。',
    reply: '后台的邮件任务都结束了。\n\n- [查看待核对的后台结果](reply:/recover-background)\n- 上下文快满时可以点 /compact 压缩一次\n- 想从头开始就发 /clear\n- 文件在 /home/demo/status 下，网址 https://example.com/help\n- `/usage`\n\n```\n/clear\n```\n\n/nosuchcmd 不是命令。',
    list: '2 条后台结果。其中 1 条有报告可看，点「读报告」；其余都不用你处理。\n1. 千夜邮件 · 11-03 22:23 · 有报告 [读报告](reply:/recover-background ' + HEX + ')\n\n不用你处理（1 条）：\n- 一件后台活 · 11-03 22:36 · 当时被拦下的 Bash 没有重做，要做直接跟 Agent 说',
    later: 'Agent J 主机已升级，新版网页稍后上线', close: '关闭', inUse: '现在登录的账号', addAnother: '添加另一个账号', spare: '备用 1',
    addTitle: '添加另一个 Claude Code 账号', added: '已添加 备用 1', where: '在 Olares 上跑', local: '在本机跑',
    card: 'AuthRescue\n{}\nAgent 附注:\n{\n  "summary": "添加另一个 Claude Code 账号「备用 1」：请用另一个账号登录，现在这个不受影响。"\n}\nRequest SHA-256: a06626f19f2e570b33e',
  },
  en: {
    sys: '2 background job(s) finished (mail run); nothing was replayed. 1 left a report: tap /recover-background to read it.',
    reply: 'The mail jobs are done.\n\n- [See the background results](reply:/recover-background)\n- When the context is nearly full, tap /compact once\n- To start over send /clear\n- Files are under /home/demo/status, site https://example.com/help\n- `/usage`\n\n```\n/clear\n```\n\n/nosuchcmd is not a command.',
    list: '2 background results. 1 have a report: tap Read; the rest need nothing from you.\n1. mail run · 11-03 22:23 · report kept [Read](reply:/recover-background ' + HEX + ')\n\nNothing to do (1):\n- a background job · 11-03 22:36 · the blocked Bash was not replayed; ask the Agent if you still want it',
    later: 'the Agent J host is upgraded', close: 'Close', inUse: 'The account signed in now', addAnother: 'Add another account', spare: 'Backup 1',
    addTitle: 'Add another Claude Code account', added: 'Added Backup 1', where: 'Runs on Olares', local: 'Runs on this computer',
    card: 'AuthRescue\n{}\nAgent note:\n{\n  "summary": "Add another Claude Code account “Backup 1”: sign in with a different account; the one in use is not affected."\n}\nRequest SHA-256: a06626f19f2e570b33e',
  },
};
const sha = (b) => createHash('sha256').update(b).digest('hex');
const FILE = Buffer.from('report body\n'.repeat(40));
const FMID = 'F'.repeat(22);
const FILES = [{ mid: FMID, name: 'weekly-mail-report.txt', mime: 'text/plain', kind: 'file', bytes: FILE.length, sha256: sha(FILE), ref: 'out/weekly-mail-report.txt' }];

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(BEFORE || process.env.W4_PUBLIC ? { publicDir: BEFORE || process.env.W4_PUBLIC } : {}) });   // W4_PUBLIC: another page WITH assertions (red-first)
const fake = await startFakeHost();
const B = await launch();
const results = [];
let checks = 0;
const check = (ok, label) => { if (BEFORE) return; assert.ok(ok, label); checks++; };
try {
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    if (width === 1440 && theme === 'light' && lang === 'en') continue;    // 1440: three of the four, enough for the wide layout
    const s = T[lang], name = `${lang}-${theme}-${width}`;
    if (process.env.W4_FILTER && !name.includes(process.env.W4_FILTER)) continue;
    fake.reset(); fake.setUp(true); fake.st.extraCaps = ['tgfile'];
    let acctView = { primary: { claude: {state:'in',use:'sub'}, codex: {state:'in',use:'sub'} },
      claude: { active: null, items: [] }, codex: { active: null, items: [] }, conflict: [] };
    let authView = { dot: false, flow: null, harnesses: [] };
    const askId = 'ab'.repeat(16);
    fake.st.onApp = async (c, m, send) => {
      if(m.t==='pref_set' && m.key==='ui.remote_servers'){fake.st.remoteNames=m.value;await send({t:'pref_res',r:m.r,ok:true});return true;}
      if (String(m.t).startsWith('acct_')) { await send({ t: 'acct_res', r: m.r, ok: true, view: acctView }); return true; }
      if (m.t === 'auth_get') { await send({ t: 'auth_res', r: m.r, ok: true, view: authView }); return true; }
      if (m.t === 'auth_start') {   // like the host: the AuthRescue card first, auth_res after the owner's answer
        fake.st.authStart = m;
        await fake.ask({ id: askId, tool: 'AuthRescue', summary: s.card, ttl: 600, host_tool: true });   // C-2: Agent J's own card
        fake.st.authRes = async () => { authView = { ...authView, flow: { kind: m.kind, route: m.route, stage: 'url', url: 'https://claude.com/cai/oauth/authorize?code=true&state=s1', expires_at: NOW + 600, account: m.account?.name } };
          await send({ t: 'auth_res', r: m.r, ok: true, view: authView }); };
        return true;
      }
      if (m.t === 'media_get') { await send({ t: 'media_chunk', mid: m.mid, o: 0, d: FILE.toString('base64url'), last: true }); return true; }
      return false;
    };
    const p = await newPage(B, width, width === 390 ? 844 : 1000, theme, { touch: width === 390, allow: [web.url, fake.relay + '/'] });
    const save = async (scene, keepToast = false) => {
      if (!keepToast) await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`).catch(() => {});
      const f = resolve(out, `${tag}-${scene}-${name}.png`); writeFileSync(f, await shoot(p, ''));
    };
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });

      // ---- #433: the system page with the recovery reminder
      await fake.addTurn({ k: 'sys', text: '' }, s.sys, 'done');
      await waitFor(p, `${$('words')}.textContent.includes('/recover-background')`);
      await sleep(300); await save('sys');
      check(await evaluate(p, `!!${$('words')}.querySelector('button.slash-command-link[data-cmd="/recover-background"]')`), 'sys page: /recover-background is a link ' + name);

      // ---- #433/#447: the Agent's reply
      await fake.addTurn({ k: 'phone', dev: null, text: lang === 'zh' ? '后台怎么样了' : 'how is the background?' }, s.reply, 'done');
      await waitFor(p, `${$('words')}.textContent.includes('/compact')`);
      await sleep(300); await save('reply');
      const links = await evaluate(p, `[...${$('words')}.querySelectorAll('button.slash-command-link')].map(b=>({t:b.textContent,c:b.dataset.cmd,col:getComputedStyle(b).webkitTextFillColor,h:b.getBoundingClientRect().height,tap:(()=>{const a=getComputedStyle(b,'::after');return parseFloat(a.height)||0})()}))`);
      if (!BEFORE) {
        const byCmd = Object.fromEntries(links.map((l) => [l.c, l]));
        check(byCmd['/recover-background']?.t === (lang === 'zh' ? '查看待核对的后台结果' : 'See the background results'), 'reply: label kept ' + JSON.stringify(links));
        check(!!byCmd['/compact'] && !!byCmd['/clear'] && !!byCmd['/usage'], 'reply: /compact /clear `/usage` linked');
        check(links.length === 4, 'path, URL, fenced block and /nosuchcmd stay words ' + links.map((l) => l.c));
        check(!(await evaluate(p, `${$('words')}.textContent.includes('](reply:')`)), 'no Markdown source left');
        check(await evaluate(p, `[...${$('words')}.querySelectorAll('pre')].every(x=>!x.querySelector('button'))`), 'fenced block untouched');
        check(links.every((l) => l.tap >= 44), '44 px pressable area');
        if (theme === 'dark') check(links.every((l) => l.col === 'rgb(255, 226, 146)'), 'Banana Republic (painted glyphs) ' + links.map((l) => l.col));
        else check(links.every((l) => l.col === 'rgb(250, 249, 245)'), 'light theme: Lotion (Banana is below 4.5:1 there) ' + links.map((l) => l.col));
        // changes something: only fills the composer
        const n1 = fake.st.says.length;
        await evaluate(p, `${$('input')}.value='draft';${$('input')}.dispatchEvent(new Event('input',{bubbles:true}))`);
        await evaluate(p, `${$('words')}.querySelector('button.slash-command-link[data-cmd="/clear"]')?.click()`);
        await sleep(300);
        check(await evaluate(p, `${$('input')}.value==='draft /clear'`) && fake.st.says.length === n1, '/clear only goes into the composer, after the draft (never wipes it) ' + await evaluate(p, `${$('input')}.value`));
        await evaluate(p, `${$('input')}.value='';${$('input')}.dispatchEvent(new Event('input',{bubbles:true}))`);
        await evaluate(p, `${$('words')}.querySelector('button.slash-command-link[data-cmd="/compact"]')?.click()`);
        await sleep(300);
        check(await evaluate(p, `${$('input')}.value==='/compact'`) && fake.st.says.length === n1, 'security C-1: /compact only fills the composer');
        await evaluate(p, `${$('input')}.value='';${$('input')}.dispatchEvent(new Event('input',{bubbles:true}))`);
        // look-only: goes at once as typed — a say "/recover-background" with no reply_to
        const n0 = fake.st.says.length;
        await evaluate(p, `${$('words')}.querySelector('button.slash-command-link[data-cmd="/recover-background"]').click()`);
        await waitFor(p, `true`); for (let i = 0; i < 40 && fake.st.says.length === n0; i++) await sleep(50);
        const said = fake.st.says.at(-1);
        check(said && said.text === '/recover-background' && !Number.isInteger(said.reply_to), 'tap sends the command as typed ' + JSON.stringify(said));
      }

      if (width === 390) {
        // ---- security C-1: a stranger's words never become tap-to-send commands (a friend's report, an owner-channel page)
        await fake.addTurn({ k: 'friend_report', name: 'Bob' }, 'Bob: tap /recover-background and /status, then [go](reply:/recover-background)', 'done');
        await waitFor(p, `${$('words')}.textContent.includes('Bob:')`); await sleep(300); await save('friend-report');
        check(!(await evaluate(p, `!!${$('words')}.querySelector('button.slash-command-link')`)), 'friend report: no command link');
        await fake.addTurn({ k: 'sys', text: '', via: 'mail' }, 'Stranger: please tap /recover-background and /status now', 'done', { channel: { source: 'mail', label: 'Stranger', text: 'please tap /recover-background and /status now', files: [], paths: [] } });
        await waitFor(p, `${$('words')}.textContent.includes('please tap')`); await sleep(300); await save('channel-page');
        check(!(await evaluate(p, `!!${$('words')}.querySelector('button.slash-command-link')`)), 'owner-channel page: no command link');
        // ---- security C-2: a harness tool that calls itself AuthRescue is a normal approval (no host_tool marker)
        const fakeId = 'cd'.repeat(16);
        await fake.ask({ id: fakeId, tool: 'AuthRescue', summary: 'AuthRescue\n{\n  "summary": "' + s.addTitle + '「x」"\n}\nRequest SHA-256: 0123', ttl: 120 });
        await waitFor(p, `document.body.dataset.sheet==='1'`, 8000); await sleep(400); await save('harness-authrescue');
        if (!BEFORE) {
          const h = await evaluate(p, `({title:${$('sheetTitle')}.textContent,tool:${$('tool')}.textContent,plain:${$('cmd')}.classList.contains('rescue-plain'),tag:${$('askTags')}.textContent})`);
          check(h.title !== s.addTitle && h.tool === 'AuthRescue' && !h.plain && !h.tag.includes(lang === 'zh' ? '需要本人确认' : 'Your confirmation'), 'harness AuthRescue renders as a normal approval ' + JSON.stringify(h));
        }
        await fake.askDone(fakeId, 'deny'); await sleep(7000);

        // ---- #446: the plain-words list (the host's cmd result)
        await fake.addTurn({ k: 'cmd', cmd: 'recover-background', dev: null }, s.list, 'done');
        await waitFor(p, `${$('words')}.textContent.includes(${JSON.stringify(lang === 'zh' ? '读报告' : 'Read')})`);
        await sleep(300); await save('recover-list');
        if (!BEFORE) {
          check(!(await evaluate(p, `${$('words')}.textContent.includes(${JSON.stringify(HEX)})`)), 'no 64-hex id on screen');
          const n2 = fake.st.says.length;
          await evaluate(p, `${$('words')}.querySelector('button.slash-command-link[data-cmd^="/recover-background "]').click()`);
          for (let i = 0; i < 40 && fake.st.says.length === n2; i++) await sleep(50);
          check(fake.st.says.at(-1)?.text === '/recover-background ' + HEX, 'Read sends the full id');
        }

        // ---- #453: all five supported actions, long Chinese filename and 24 MiB at 390/360/320 px
        await evaluate(p, `Object.defineProperty(navigator,'share',{configurable:true,value:async()=>{}});Object.defineProperty(navigator,'canShare',{configurable:true,value:()=>true});import('./js/mailfwd.js').then(m=>m.registerMailForward(()=>false))`);
        const t1 = await fake.addTurn({ k: 'phone', dev: null, text: lang === 'zh' ? '发报告' : 'send the report' }, (lang === 'zh' ? '报告：' : 'Report:') + '\n\n[weekly-mail-report.txt](out/weekly-mail-report.txt)', 'done');
        await fake.updateTurn(t1.id, { media: FILES.map(f=>({...f,name:'全屏预览窄屏回归测试报告与后台任务核对结果-20261106.md',bytes:24*1024*1024})), media_skip: [] });
        const card = `document.querySelector('#words .mcard[data-mid="${FMID}"]')`;
        await waitFor(p, `!!${card}`, 15000);
        await evaluate(p, `${card}.scrollIntoView({block:'center'})`);
        for (const w of [390, 360, 320]) {
          if (w !== 390) { await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: 720, deviceScaleFactor: 2, mobile: true }); await sleep(400); }
          await save('filecard-' + w);
          const g = await evaluate(p, `(()=>{const c=${card},r=c.getBoundingClientRect();const bs=[...c.querySelectorAll('.macts .mact')].filter(b=>!b.hidden&&b.getClientRects().length);return {count:bs.length,label:!!c.querySelector('.mfullscreen-label'),aria:c.querySelector('.mact.pv').getAttribute('aria-label'),out:bs.filter(b=>{const q=b.getBoundingClientRect();return q.right>r.right+1||q.left<r.left-1}).length,min:Math.min(...bs.map(b=>b.getBoundingClientRect().height))}})()`);
          if (BEFORE && process.env.W4_REQUIRE_ICON === '1') assert.equal(g.label, false, '#453 baseline red: fullscreen must have no visible label');
          check(!g.label && /全屏查看|Full screen|full screen/i.test(g.aria || ''), 'icon only, words in aria-label ' + JSON.stringify(g));
          check(g.count === 5 && g.out === 0 && g.min >= 44, `actions inside the card at ${w} px ` + JSON.stringify(g));
        }
        await p.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }); await sleep(300);

        // ---- #444: where a background task runs
        await fake.send({ t: 'busy', tasks: [
          { id: 'x1', who: null, kind: 'temp', label: lang === 'zh' ? 'Olares 上跑主机测试' : 'host tests on Olares', since: NOW - 300, c: 1, harness: 'claude', model: 'claude-opus-5-5', where: 'Olares' },
          { id: 'x2', who: null, kind: 'temp', label: lang === 'zh' ? '网页测试' : 'web tests', since: NOW - 120, c: 2, harness: 'claude', model: 'claude-opus-5-5' }], running: 2 });
        await sleep(300);
        await evaluate(p, `(document.getElementById('busyDots')||document.querySelector('.busy-dots,#busy'))?.click()`);
        await sleep(500);
        await save('busy');
        if (!BEFORE) {
          const txt = await evaluate(p, `import('./js/busy.js').then(m=>[m.identityText({where:'Olares',harness:'claude',model:'claude-opus-5-5'}),m.identityText({harness:'claude',model:'claude-opus-5-5'})])`);
          check(txt[0].includes(s.where) && txt[1].includes(s.local), 'where it runs ' + JSON.stringify(txt));
        }
        await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`); await sleep(200);

        // ---- #451: host ahead of every page a refresh can fetch
        await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
        const future = '9.9.9';
        await evaluate(p, `import('./js/version-notice.js').then(m=>m.setHostVersion(${JSON.stringify(future)}))`);
        await sleep(500); await save('host-ahead', true);
        if (!BEFORE) {
          const v = await evaluate(p, `(()=>{const t=${$('toast')};return {on:t.classList.contains('on'),text:t.textContent,btn:[...t.querySelectorAll('button')].map(b=>b.textContent),line:!${$('version-later')}.hidden}})()`);
          check(v.on && v.text.includes(s.later) && v.btn.includes(s.close) && !v.line, 'shared notice with Close, no resident line ' + JSON.stringify(v));
          if (theme === 'dark') {   // the clock: still on at 9 s, gone by 10.6 s; the same host version never again
            await sleep(8500); check(await evaluate(p, `${$('toast')}.classList.contains('on')`), 'still there at 9 s');
            await sleep(1700); check(!(await evaluate(p, `${$('toast')}.classList.contains('on')`)), 'gone by itself after 10 s');
          } else {
            await evaluate(p, `[...${$('toast')}.querySelectorAll('button')].find(b=>b.textContent===${JSON.stringify(s.close)}).click()`); await sleep(200);
            check(!(await evaluate(p, `${$('toast')}.classList.contains('on')`)), 'Close puts it away');
          }
          await evaluate(p, `import('./js/version-notice.js').then(m=>m.setHostVersion(${JSON.stringify(future)}))`); await sleep(300);
          check(!(await evaluate(p, `${$('toast')}.classList.contains('on')`)), 'once per host version');
        }

        // ---- #452/#454/#456: Settings → AI 账号
        await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
        if (!BEFORE) {
          await evaluate(p, `import('./js/settings.js').then(m=>m.setPrefs({ui:{remote_servers:[]}}, {settable:['ui.remote_servers']}))`);
          await evaluate(p, `(()=>{const d=document.getElementById('set-remote-names');d.open=true;d.scrollIntoView();d.querySelector('button').click();const inputs=d.querySelectorAll('input');inputs[0].value='olares';inputs[1].value='Home server';inputs[1].dispatchEvent(new Event('input',{bubbles:true}));d.querySelector('button.aj-action--primary').click()})()`);
          for(let i=0;i<40&&!fake.st.remoteNames;i++) await sleep(50);
          check(JSON.stringify(fake.st.remoteNames)===JSON.stringify([{host:'olares',name:'Home server'}]),'remote alias saved through ordinary pref_set');
          await save('remote-names');
        }
        await evaluate(p, `${$('set-accts-nav')}.click()`);
        await waitFor(p, `!${$('set-accts')}.hidden && !!document.querySelector('#acct-list [data-kind=claude]')`);
        await sleep(300); await evaluate(p, `${$('set-accts')}.scrollTop=0`); await save('accts');
        if (!BEFORE) {
          const rows = await evaluate(p, `[...document.querySelectorAll('#acct-list .acctgrp')].map(g=>({row:g.querySelector('.acctinuse .acctname')?.textContent||'',cur:!!g.querySelector('.acctinuse .acctcur'),add:g.querySelector('.acctadd')?.textContent}))`);
          check(rows.length === 2 && rows.every((r) => !r.row && r.add === s.addAnother), 'primary current login is separate from backups ' + JSON.stringify(rows));
          check(await evaluate(p, `document.querySelectorAll('#acct-primary .acctprimary').length===2 && !document.querySelector('#acct-primary').textContent.includes('acct.primaryUnknown')`), 'existing primary metadata kept, never duplicate saved account');
        }
        await evaluate(p, `document.querySelector('#acct-list [data-kind=claude] .acctadd').click()`);
        await waitFor(p, `!!document.querySelector('#acct-list [data-kind=claude] .acctform input')`);
        await sleep(200); await save('accts-name');
        if (!BEFORE) check(await evaluate(p, `document.querySelector('#acct-list [data-kind=claude] .acctform input').value===${JSON.stringify(s.spare)}`), 'name ready: ' + s.spare);
        await evaluate(p, `(()=>{const f=document.querySelector('#acct-list [data-kind=claude] .acctform');const i=f.querySelector('input');if(!i.value)i.value=${JSON.stringify(s.spare)};f.querySelector('.aj-action--primary').click()})()`);
        await waitFor(p, `document.body.dataset.sheet==='1'`, 8000);
        await sleep(500); await save('accts-card');
        if (!BEFORE) {
          const c = await evaluate(p, `({settings:!${$('settings')}.hidden && !${$('set-accts')}.hidden,view:document.body.dataset.view,title:${$('sheetTitle')}.textContent,cmd:${$('cmd')}.textContent,tool:${$('tool')}.textContent,front:(()=>{const s=${$('sheet')}.getBoundingClientRect();const e=document.elementFromPoint(s.left+s.width/2,s.top+Math.min(40,s.height/2));return !!e&&${$('sheet')}.contains(e)})()})`);
          check(c.settings && c.front, 'the card is answered above Settings, Settings stays ' + JSON.stringify(c));
          check(c.title === s.addTitle, 'title: ' + c.title);
          check(!c.cmd.includes('{') && !c.cmd.includes('SHA') && c.cmd.includes(s.spare) && c.tool === 'Agent J', 'one sentence, no JSON / hash ' + JSON.stringify(c));
        }
        // the owner agrees (the host answers auth_start), the sign-in runs, then it is done
        await fake.askDone(askId, 'allow');
        await fake.st.authRes?.();
        await waitFor(p, `!${$('acct-flow')}.hidden`, 8000).catch(() => {});
        acctView = { ...acctView, claude: { active: 'm1', items: [{ id: 'm1', name: lang === 'zh' ? '主号' : 'Main', method: 'login', added: NOW, quota: null }, { id: 'b1', name: s.spare, method: 'token', added: NOW, quota: null }] } };
        authView = { ...authView, flow: { kind: 'claude', route: 'setup_token', stage: 'done', account: s.spare } };
        await fake.send({ t: 'auth_rescue', view: authView });
        await sleep(600); await evaluate(p, `${$('set-accts')}.scrollTop=0`); await save('accts-done', true);
        if (!BEFORE) {
          const d = await evaluate(p, `({box:!${$('acct-flow')}.hidden,toast:${$('toast')}.classList.contains('on')?${$('toast')}.textContent:'',settings:!${$('set-accts')}.hidden})`);
          check(!d.box && d.toast.includes(s.added) && d.settings, 'done: one notice, no resident box, still on AI 账号 ' + JSON.stringify(d));
          await sleep(10500);
          check(!(await evaluate(p, `${$('toast')}.classList.contains('on')`)), 'the notice goes after 10 s');
        }
        // a reload: the host still remembers the ended flow — nothing shows
        await navigate(p, web.url + '?lang=' + lang); await waitState(p, 'ready');
        await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
        await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
        await evaluate(p, `${$('set-accts-nav')}.click()`); await waitFor(p, `!${$('set-accts')}.hidden`);
        await sleep(800); await evaluate(p, `${$('set-accts')}.scrollTop=0`); await save('accts-reload');
        if (!BEFORE) check(await evaluate(p, `${$('acct-flow')}.hidden && !(${$('acct-flow')}.textContent||'').trim()`), 'after a reload: no status box');
        const geom = await evaluate(p, `(()=>{const e=${$('set-accts')};return {overflow:e.scrollWidth>e.clientWidth+1}})()`);
        check(!geom.overflow, 'no sideways overflow');
      }
      if (!BEFORE) assert.deepEqual(p.problems.filter((x) => !/favicon/.test(x)), [], 'console ' + name);
      results.push({ name, ok: true });
    } finally { await p.dispose(); }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(resolve(out, `${tag}-results.json`), JSON.stringify({ scope: 'real Chromium + local Noise fake host; synthetic data; not a physical phone', checks, cases: results }, null, 2) + '\n');
console.log(`feedback573-lost-batch ${tag}: ${results.length} cases, ${checks} checks → ${out}`);
