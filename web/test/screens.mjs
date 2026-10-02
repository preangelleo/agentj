#!/usr/bin/env node
// Web client: screenshots + layout audit + full client flow against a local fake relay/host, in an INDEPENDENT headless
// Chromium. Never touches the shared CDP on :9222 — own chromium on a random debugging port, temp profile, one isolated
// browser context per page; public/ is served by test/serve.mjs (Worker headers, connect-src ws://127.0.0.1:*).
//   1. idle + security panel, every size × scheme (zh)               — the A2 checks
//   2. full flow (pair → chat → host restart → reload → bad frame → revoke → re-pair → expired link)   — the A2 checks
//   3. gallery: every reachable screen at 360×800 and 1440×900, light + dark, 中文 + English, with an audit on every shot:
//      no horizontal overflow, every visible tap target ≥ 44×44 px, no console errors / CSP violations, no request
//      leaving the page's own origin (+ the local relay)
//   4. language: the switch sets <html lang>, survives a reload (localStorage "aj.lang"), ?lang=en wins and is saved
// Run: node web/test/screens.mjs      Output: /tmp/aj-web-shots/*.png (AJ_SHOTS to change)
//      AJ_QUICK=1 → gallery only for 360 light zh + 1440 dark en
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, existsSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { dictionaries } from '../build.mjs';

const SHOTS = process.env.AJ_SHOTS || '/tmp/aj-web-shots';
const CHROMIUM = process.env.CHROMIUM || '/usr/bin/chromium';
const SIZES = [['mobile', 360, 800], ['desktop', 1440, 900]];
const SCHEMES = ['light', 'dark'];
const D = dictionaries();
const ZH = D.zh, EN = D.en;
const plain = (s) => s.replace(/`/g, '');
const PAIR_FAIL = plain(ZH['st.pairFail']);
const UA = {
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  android: 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36',
};

mkdirSync(SHOTS, { recursive: true });
const failures = [];
const check = (ok, msg) => { if (!ok) failures.push(msg); console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const profile = mkdtempSync(join(tmpdir(), 'aj-web-chrome-'));
const chrome = spawn(CHROMIUM, ['--headless=new', `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--disable-background-networking',
  '--disable-component-update', '--disable-sync', '--disable-extensions', '--mute-audio', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
let chromeErr = '';
chrome.stderr.on('data', (d) => { chromeErr += d; });

async function devtoolsPort() {
  const f = join(profile, 'DevToolsActivePort');
  for (let i = 0; i < 300; i++) { if (existsSync(f)) { const [p] = readFileSync(f, 'utf8').split('\n'); if (p) return Number(p); } await sleep(100); }
  throw new Error('chromium did not start: ' + chromeErr.slice(-500));
}

function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0; const pending = new Map(); const listeners = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
    else if (m.method) listeners.forEach((l) => l(m));
  };
  const ready = new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  return {
    ready,
    send: (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); }),
    on: (fn) => listeners.push(fn),
    waitFor: (method, ms = 15000) => new Promise((res, rej) => {
      const t = setTimeout(() => rej(new Error(`timeout waiting for ${method}`)), ms);
      listeners.push((m) => { if (m.method === method) { clearTimeout(t); res(m.params); } });
    }),
    close: () => ws.close(),
  };
}

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const BASE = web.url;
const fake = await startFakeHost();
const RELAY = fake.relay;

try {
  const port = await devtoolsPort();
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const browser = cdp(version.webSocketDebuggerUrl); await browser.ready;

  async function newPage(w, h, scheme, { ua } = {}) {
    const { browserContextId } = await browser.send('Target.createBrowserContext', { disposeOnDetach: true });
    const { targetId } = await browser.send('Target.createTarget', { url: 'about:blank', browserContextId });
    const p = cdp(`ws://127.0.0.1:${port}/devtools/page/${targetId}`); await p.ready;
    p.problems = []; p.offsite = [];
    p.on((m) => {
      if (m.method === 'Log.entryAdded' && (m.params.entry.level === 'error' || /Content Security Policy/i.test(m.params.entry.text))) p.problems.push(`log: ${m.params.entry.text} ${m.params.entry.url || ''}`);
      if (m.method === 'Runtime.exceptionThrown') p.problems.push(`exception: ${m.params.exceptionDetails.text} ${m.params.exceptionDetails.exception?.description || ''}`);
      if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) p.problems.push(`console.${m.params.type}: ${m.params.args.map((a) => a.value ?? a.description).join(' ')}`);
      if (m.method === 'Network.requestWillBeSent') {
        const u = m.params.request.url;
        if (!(u.startsWith(BASE) || u.startsWith(RELAY + '/') || /^(data|blob|about):/.test(u))) p.offsite.push(u);
      }
      if (m.method === 'Network.webSocketCreated' && !m.params.url.startsWith(RELAY + '/')) p.offsite.push(m.params.url);
    });
    await p.send('Page.enable'); await p.send('Runtime.enable'); await p.send('Log.enable'); await p.send('Network.enable');
    await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
    await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
    if (ua) await p.send('Emulation.setUserAgentOverride', { userAgent: ua, platform: ua.includes('iPhone') ? 'iPhone' : 'Linux armv8l' });
    p.dispose = async () => { p.close(); await browser.send('Target.closeTarget', { targetId }).catch(() => {}); await browser.send('Target.disposeBrowserContext', { browserContextId }).catch(() => {}); };
    return p;
  }
  const evaluate = async (p, expr) => { const r = await p.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };
  async function navigate(p, url) { const load = p.waitFor('Page.loadEventFired'); await p.send('Page.navigate', { url }); await load; await sleep(250); }
  async function waitState(p, want, ms = 10000) {
    let s;
    for (let t = 0; t < ms; t += 100) { s = await evaluate(p, 'window.__ajState'); if (s === want) return true; await sleep(100); }
    throw new Error(`state ${s} != ${want} (status: ${await evaluate(p, `document.getElementById('status').textContent + ' / ' + document.getElementById('error-text').textContent`)}; problems: ${p.problems.join(' | ')})`);
  }
  async function waitFor(p, expr, ms = 8000) {
    for (let t = 0; t < ms; t += 50) { if (await evaluate(p, expr)) return true; await sleep(50); }
    throw new Error('timeout: ' + expr);
  }
  async function shoot(p, name, { full = false } = {}) {
    await sleep(120);
    const { cssContentSize: s, cssLayoutViewport: v } = await p.send('Page.getLayoutMetrics');
    const clip = full ? { x: 0, y: 0, width: s.width, height: s.height, scale: 1 } : { x: 0, y: v.pageY, width: v.clientWidth, height: v.clientHeight, scale: 1 };
    const { data } = await p.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: full, clip });
    const file = join(SHOTS, `${name}.png`);
    writeFileSync(file, Buffer.from(data, 'base64'));
    console.log(`      shot ${file}`);
  }
  // every visible interactive element (incl. fixed-position sheet / toast) must be ≥ 44×44; nothing wider than the screen
  async function layoutOk(p, label) {
    const r = await evaluate(p, `(() => {
      const vis = (e) => { if (!e.getClientRects().length) return false; const cs = getComputedStyle(e); return cs.visibility !== 'hidden' && cs.display !== 'none'; };
      const small = [...document.querySelectorAll('button, a[href], summary, input, select, textarea')].filter(vis)
        .map((e) => ({ e, r: e.getBoundingClientRect() })).filter(({ r }) => r.height < 44 || r.width < 44)
        .map(({ e, r }) => (e.id || e.className || e.tagName) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
      const wide = [...document.querySelectorAll('body *')].filter(vis).filter((e) => { const r = e.getBoundingClientRect(); return r.right > innerWidth + 1 && getComputedStyle(e).position !== 'fixed'; })
        .filter((e) => !e.closest('.ask-summary, .mem-text, .hash, pre')).slice(0, 5).map((e) => e.id || e.className || e.tagName);
      return { sw: document.documentElement.scrollWidth, iw: innerWidth, small, wide };
    })()`);
    check(r.sw <= r.iw && r.wide.length === 0, `${label}: no horizontal overflow (scrollWidth ${r.sw} <= ${r.iw}${r.wide.length ? '; sticks out: ' + r.wide.join(', ') : ''})`);
    check(r.small.length === 0, `${label}: every visible tap target ≥ 44×44 px${r.small.length ? ' — ' + r.small.join(', ') : ''}`);
  }
  const noProblems = (p, label) => {
    check(p.problems.length === 0, `${label}: zero console errors / CSP violations${p.problems.length ? ' — ' + p.problems.join(' | ') : ''}`);
    check(p.offsite.length === 0, `${label}: no request leaves the page's origin${p.offsite.length ? ' — ' + p.offsite.join(' | ') : ''}`);
  };
  async function typeAndEnter(p, text) {
    await evaluate(p, `document.getElementById('msg-input').focus()`);
    await p.send('Input.insertText', { text });
    await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' });
    await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  }
  async function waitIn(p, text, ms = 5000) {
    for (let t = 0; t < ms; t += 100) {
      if (await evaluate(p, `Array.from(document.querySelectorAll('#messages li[data-dir=in]')).some(l => l.textContent === ${JSON.stringify(text)})`)) return true;
      await sleep(100);
    }
    return false;
  }
  const text = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)}).textContent`);

  // ---------- 1. idle (pairing input) + security panel, every size × scheme
  for (const scheme of SCHEMES) for (const [sz, w, h] of SIZES) {
    const tag = `${sz}-${scheme}`;
    const p = await newPage(w, h, scheme);
    await navigate(p, BASE);
    await waitState(p, 'idle');
    check(!(await evaluate(p, `document.getElementById('pair-view').hidden`)), `${tag}: idle shows the pairing input`);
    check((await text(p, '#badge')) === '网页版', `${tag}: badge visible`);
    check((await evaluate(p, `document.documentElement.lang`)) === 'zh-CN', `${tag}: <html lang=zh-CN> by default`);
    await layoutOk(p, `idle ${tag}`);
    await evaluate(p, `document.getElementById('badge').click()`);
    const hash = await text(p, '#version-hash');
    const want = JSON.parse(readFileSync(new URL('../public/version.json', import.meta.url), 'utf8')).combined;
    check(hash === want, `${tag}: badge panel shows the combined hash`);
    await layoutOk(p, `badge ${tag}`);
    await evaluate(p, `document.getElementById('badge-close').click(); document.getElementById('pair-link').value = 'not a link'; document.getElementById('pair-go').click()`);
    check(!(await evaluate(p, `document.getElementById('pair-error').hidden`)), `${tag}: garbage link → inline error`);
    noProblems(p, `idle ${tag}`);
    await p.dispose();
  }

  // ---------- 2. full flow against the fake relay/host (mobile light + desktop dark)
  for (const [sz, w, h, scheme] of [['mobile', 360, 800, 'light'], ['desktop', 1440, 900, 'dark']]) {
    const tag = `${sz}-${scheme}`;
    const p = await newPage(w, h, scheme);
    await navigate(p, fake.newPairing(BASE));
    await waitState(p, 'awaiting-approval');
    check((await evaluate(p, 'location.href')) === BASE, `${tag}: #p= fragment stripped from the URL`);
    const sas = await text(p, '#sas');
    check(/^\d{6}$/.test(sas) && sas === fake.sas, `${tag}: SAS ${sas} matches the host's (${fake.sas})`);
    check(/^网页 · .+ Chrome$/.test(fake.label || ''), `${tag}: device label "${fake.label}"`);
    await layoutOk(p, `sas ${tag}`);
    await fake.approve();
    await waitState(p, 'ready');
    const dev = await evaluate(p, `new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const tx = r.result.transaction('kv');
      const d = tx.objectStore('kv').get('device'), hst = tx.objectStore('kv').get('host');
      tx.oncomplete = () => res({ ext: d.result.priv.extractable, type: d.result.priv.type, alg: d.result.priv.algorithm.name, host: !!hst.result?.approved, psk: 'psk' in (hst.result || {}) }); }; })`);
    check(dev.ext === false && dev.type === 'private' && dev.alg === 'X25519', `${tag}: device key is a non-extractable X25519 CryptoKey in IndexedDB`);
    check(dev.host && !dev.psk, `${tag}: approved host persisted without the PSK`);
    await typeAndEnter(p, '你好，贾维斯');
    check(await waitIn(p, 'echo: 你好，贾维斯'), `${tag}: Enter sends; host echo rendered`);
    const xss = '<img src=x onerror=alert(1)><b>bold</b>';
    await typeAndEnter(p, xss);
    check(await waitIn(p, 'echo: ' + xss), `${tag}: markup round-trips as text`);
    await fake.say('<script>alert(2)</script>');
    check(await waitIn(p, '<script>alert(2)</script>'), `${tag}: host-initiated message rendered`);
    check((await evaluate(p, `document.querySelectorAll('#messages img, #messages b, #messages script').length`)) === 0, `${tag}: no element injection (textContent only)`);
    check((await evaluate(p, `document.querySelectorAll('#messages li[data-dir=out]').length`)) === 2, `${tag}: outgoing items data-dir=out`);
    await layoutOk(p, `chat ${tag}`);

    fake.setUp(false);
    await waitState(p, 'waiting-host');
    check((await text(p, '#status')) === ZH['st.hostDown'], `${tag}: host down → ${ZH['st.hostDown']}`);
    fake.setUp(true);
    await waitState(p, 'ready');
    await typeAndEnter(p, 'after restart');
    check(await waitIn(p, 'echo: after restart'), `${tag}: host restart → RESUME redone on the same socket`);

    await navigate(p, BASE);
    await waitState(p, 'ready');
    check(true, `${tag}: reload → RESUME (IK) → ready`);

    fake.sendRaw(Uint8Array.of(4, 1, 2, 3));
    await waitState(p, 'error');
    check(true, `${tag}: undecryptable DATA → socket closed, error state`);
    await evaluate(p, `document.getElementById('retry').click()`);
    await waitState(p, 'ready');

    fake.revokeAll();
    await waitState(p, 'revoked');
    await navigate(p, BASE);
    await waitState(p, 'revoked');
    check(true, `${tag}: RESUME of a removed device → revoked`);
    await evaluate(p, `document.getElementById('repair').click()`);
    await waitState(p, 'idle');
    const after = await evaluate(p, `new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const tx = r.result.transaction('kv');
      const d = tx.objectStore('kv').get('device'), hst = tx.objectStore('kv').get('host'); tx.oncomplete = () => res({ dev: !!d.result, host: !!hst.result }); }; })`);
    check(after.dev && !after.host, `${tag}: 重新配对 clears the host, keeps the device key`);

    await p.send('Page.navigate', { url: fake.newPairing(BASE) });   // same-document: only the fragment changes
    await waitState(p, 'awaiting-approval');
    check((await evaluate(p, 'location.href')) === BASE, `${tag}: hashchange pairing link read and stripped`);
    fake.setUp(false);
    await waitState(p, 'error');
    check((await text(p, '#status')) === PAIR_FAIL, `${tag}: host down mid-pairing → ${PAIR_FAIL}`);
    fake.setUp(true);

    await navigate(p, 'about:blank');
    await navigate(p, fake.newPairing(BASE, -10));
    await waitState(p, 'idle');
    check(!(await evaluate(p, `document.getElementById('pair-error').hidden`)), `${tag}: expired link refused before connecting`);
    noProblems(p, `flow ${tag}`);
    await p.dispose();
  }

  // ---------- 3. gallery: every reachable screen, 360 / 1440 × light / dark × zh / en, audited
  const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, '0')).join('');
  const now = Date.now();
  fake.answer('mem_list', () => [
    { t: 'mem_sources', harness: 'claude', sources: [
      { id: 'proj', label: 'CLAUDE.md（项目）', path: '~/shop/CLAUDE.md', n: 3 },
      { id: 'user', label: '~/.claude/CLAUDE.md（全局）', path: '~/.claude/CLAUDE.md', problem: 'not_found' },
      { id: 'auto', label: '自动记忆 · auto memory', path: '~/.claude/projects/shop/memory/', n: 1 }],
      trash: [{ id: 't1', label: 'CLAUDE.md', text: '- 发货前先截图给我看' }] },
    { t: 'mem_items', items: [
      { src: 'proj', file: 'CLAUDE.md', fsha: 'a', iid: '1', kind: 'line', text: '- 回答用中文，先给结论' },
      { src: 'proj', file: 'CLAUDE.md', fsha: 'a', iid: '2', kind: 'line', text: '- 改价格前必须先问我' },
      { src: 'proj', file: 'CLAUDE.md', fsha: 'a', iid: '3', kind: 'line', text: '- Weekly report goes out on Fridays at 17:00' },
      { src: 'auto', file: 'prefs.md', fsha: 'b', iid: '4', kind: 'file', title: '用户偏好', desc: '回答风格', text: '回答要简短，先给结论。' }], more: false },
  ]);
  fake.answer('act_list', () => ({ t: 'act_page', more: false, next: 'p2', items: [
    { k: 'decision', ts: now - 60e3, result: 'deny', by: 'iPhone', tool: 'Bash', text: 'rm -rf build/' },
    { k: 'ask', ts: now - 62e3, tool: 'Bash', cats: ['delete'], text: 'rm -rf build/' },
    { k: 'decision', ts: now - 300e3, result: 'allow', by: 'iPhone', tool: 'Bash', text: 'npm test' },
    { k: 'mem_rm', ts: now - 900e3, by: 'iPhone', label: 'CLAUDE.md', text: '- 发货前先截图给我看' },
    { k: 'task_done', ts: now - 3600e3, title: '每日销量日报', verdict: 'ok', line: '昨天 132 单，正常', readonly: true },
    { k: 'turn_start', ts: now - 4000e3, by: 'iPhone', text: '帮我看看今天的订单' },
  ] }));
  fake.answer('task_list', () => ({ t: 'tasks', more: false, agent: 'claude', paused: false, items: [
    { id: 'daily-report', title: { zh: '每日销量日报', en: 'Daily sales report' }, schedule: '0 9 * * *', tz: 'local', mode: 'research', enabled: true, next: Math.floor(now / 1000) + 3600, tsha: 'x', last: { verdict: 'ok', line: '昨天 132 单，正常' } },
    { id: 'restock', title: { zh: '库存提醒', en: 'Restock check' }, schedule: '30 8 * * 1-5', tz: 'local', mode: 'normal', enabled: false, tsha: 'y' },
  ] }));
  fake.answer('estop', () => [{ t: 'ctl_res', ok: true }, { t: 'estop_state', on: true, by: 'iPhone' }]);
  fake.answer('resume', () => [{ t: 'ctl_res', ok: true }, { t: 'estop_state', on: false }]);

  const COMBOS = [];
  for (const lang of ['zh', 'en']) for (const scheme of SCHEMES) for (const [sz, w, h] of SIZES) COMBOS.push({ lang, scheme, sz, w, h });
  const combos = process.env.AJ_QUICK ? COMBOS.filter((c) => (c.sz === 'mobile' && c.scheme === 'light' && c.lang === 'zh') || (c.sz === 'desktop' && c.scheme === 'dark' && c.lang === 'en')) : COMBOS;
  for (const { lang, scheme, sz, w, h } of combos) {
    const tag = `${w}-${scheme}-${lang}`;
    const L = lang === 'en' ? EN : ZH;
    const ua = sz === 'mobile' ? (scheme === 'light' ? UA.iphone : UA.android) : undefined;
    const p = await newPage(w, h, scheme, { ua });
    const shot = async (name, opts) => { await layoutOk(p, `${name} ${tag}`); await shoot(p, `${name}-${tag}`, opts); };
    await navigate(p, BASE + (lang === 'en' ? '?lang=en' : ''));
    await waitState(p, 'idle');
    check((await text(p, '#status')) === plain(L['st.idle']), `${tag}: status in ${lang}`);
    if (sz === 'mobile') check(!(await evaluate(p, `document.getElementById('a2hs').hidden`)), `${tag}: Add-to-Home-Screen hint shown in a phone browser (${scheme === 'light' ? 'iPhone' : 'Android'})`);
    else check(await evaluate(p, `document.getElementById('a2hs').hidden`), `${tag}: no Add-to-Home-Screen hint on a desktop browser`);
    await shot('01-login', { full: true });
    await evaluate(p, `document.getElementById('menu').open = true`);
    await shot('02-menu');
    await evaluate(p, `document.getElementById('menu-about').click()`);
    await shot('03-security-panel', { full: true });
    await evaluate(p, `document.getElementById('badge-close').click(); window.scrollTo(0, 0)`);
    await evaluate(p, `document.getElementById('pair-link').value = 'not a link'; document.getElementById('pair-go').click()`);
    check((await text(p, '#pair-error')) === plain(L['pair.badLink']), `${tag}: bad link error in ${lang}`);
    await shot('04-pair-bad-link', { full: true });

    await navigate(p, 'about:blank');
    await navigate(p, fake.newPairing(BASE));
    await waitState(p, 'awaiting-approval');
    await shot('05-pair-code');
    await fake.approve();
    await waitState(p, 'ready');
    if (sz === 'mobile') {                                   // the hint is dismissible and stays dismissed
      check(!(await evaluate(p, `document.getElementById('a2hs').hidden`)), `${tag}: hint also on the chat screen until dismissed`);
      await shot('06-chat-a2hs-hint');
      await evaluate(p, `document.getElementById('a2hs-ok').click()`);
      check(await evaluate(p, `document.getElementById('a2hs').hidden && localStorage.getItem('aj.a2hs') === '1'`), `${tag}: 「${plain(L['a2hs.ok'])}」 dismisses the hint and remembers it`);
    }
    await fake.send({ t: 'status', s: 'idle', agent: 'claude' });
    await waitFor(p, `document.getElementById('agent-status').dataset.s === 'idle'`);
    check((await text(p, '#agent-status')).startsWith('Claude Code · '), `${tag}: header shows the Agent name + state`);
    await shot('07-chat-idle');

    await typeAndEnter(p, lang === 'en' ? 'Check today’s orders and draft a restock list' : '看一下今天的订单，拟一份补货清单');
    await sleep(200);
    await fake.send({ t: 'status', s: 'working', agent: 'claude' });
    await fake.send({ t: 'push_key', k: Buffer.from(Uint8Array.of(4, ...crypto.getRandomValues(new Uint8Array(64)))).toString('base64url') });
    await fake.say(lang === 'en' ? 'On it. 132 orders today; 3 items are running low. Drafting the list now…' : '好的。今天 132 单，有 3 个商品库存偏低，正在拟补货清单…');
    await waitFor(p, `document.body.dataset.agent === 'working'`);
    await shot('08-chat-working');

    await fake.send({ t: 'status', s: 'waiting', agent: 'claude' });
    await fake.send({ t: 'ask', id: hex(16), tool: 'Bash', summary: 'cat orders/2026-10-03.csv | wc -l', ttl: 120, batch: 'Bash：cat', batch_max: 20, batch_secs: 600 });
    await fake.send({ t: 'ask', id: hex(16), tool: 'Bash', summary: 'rm -rf exports/old/', ttl: 120, cat: ['delete'], why: 'rm -rf' });
    await waitFor(p, `document.querySelectorAll('#messages li.ask').length === 2`);
    check((await evaluate(p, `[...document.querySelectorAll('#messages li.ask .ask-allow')].map((b) => b.textContent).join('|')`)) === `${L['ask.allow']}|${L['ask.allowOne']}`, `${tag}: approval buttons in ${lang}`);
    await shot('09-chat-approval-cards');
    await evaluate(p, `document.querySelector('#messages li.ask .ask-allow').click()`);
    await sleep(200);
    await fake.send({ t: 'grant', id: hex(16), scope: 'Bash：cat', left: 19 });
    await evaluate(p, `document.getElementById('cmd-toggle').click()`);
    await shot('10-chat-commands-grant');
    await evaluate(p, `document.getElementById('cmd-toggle').click()`);

    await evaluate(p, `document.getElementById('open-mem').click()`);
    await waitFor(p, `document.querySelectorAll('#mem-list .mem-item').length === 4`);
    await shot('11-memory', { full: true });
    await evaluate(p, `document.querySelector('#mem-list .mem-del').click()`);
    await waitFor(p, `!document.getElementById('sheet').hidden`);
    await shot('12-memory-delete-confirm');
    await evaluate(p, `document.getElementById('sheet-no').click()`);
    await evaluate(p, `document.getElementById('open-act') && document.querySelector('#mem-view [data-back]').click()`);
    await evaluate(p, `document.getElementById('open-act').click()`);
    await waitFor(p, `document.querySelectorAll('#act-list li').length === 6`);
    await shot('13-activity', { full: true });
    await evaluate(p, `document.querySelector('#act-view [data-back]').click(); document.getElementById('open-tasks').click()`);
    await waitFor(p, `document.querySelectorAll('#tasks-list li').length === 2`);
    await shot('14-scheduled-tasks', { full: true });
    await evaluate(p, `document.querySelector('#tasks-view [data-back]').click()`);

    await evaluate(p, `document.getElementById('estop').click()`);
    await waitFor(p, `!document.getElementById('sheet').hidden`);
    check((await text(p, '#sheet-yes')) === L['estop.confirmYes'], `${tag}: stop-everything confirm in ${lang}`);
    await shot('15-stop-everything-confirm');
    await evaluate(p, `document.getElementById('sheet-yes').click()`);
    await waitFor(p, `!document.getElementById('estop-banner').hidden`);
    await fake.send({ t: 'status', s: 'stopped', agent: 'claude' });
    await waitFor(p, `document.body.dataset.agent === 'stopped'`);
    check((await evaluate(p, `document.getElementById('msg-input').placeholder`)) === L['chat.placeholderStopped'], `${tag}: composer says everything is stopped`);
    await shot('16-stopped');
    await evaluate(p, `document.getElementById('resume').click()`);
    await waitFor(p, `!document.getElementById('sheet').hidden`);
    await evaluate(p, `document.getElementById('sheet-yes').click()`);
    await waitFor(p, `document.getElementById('estop-banner').hidden`);
    await fake.send({ t: 'status', s: 'idle', agent: 'claude' });

    fake.setUp(false);
    await waitState(p, 'waiting-host');
    check((await text(p, '#status')) === L['st.hostDown'], `${tag}: offline status in ${lang}`);
    await shot('17-computer-offline');
    fake.setUp(true);
    await waitState(p, 'ready');
    fake.sendRaw(Uint8Array.of(4, 1, 2, 3));
    await waitState(p, 'error');
    check((await text(p, '#error-text')) === plain(L['error.dataBad']), `${tag}: disconnect reason in ${lang}`);
    await shot('18-disconnected');
    await evaluate(p, `document.getElementById('retry').click()`);
    await waitState(p, 'ready');
    fake.revokeAll();
    await waitState(p, 'revoked');
    await shot('19-removed');
    noProblems(p, `gallery ${tag}`);
    await p.dispose();
  }

  // ---------- 4. language switch: html[lang], persistence across reloads, ?lang= wins and is saved
  {
    const p = await newPage(360, 800, 'light');
    await navigate(p, BASE);
    await waitState(p, 'idle');
    check((await text(p, '#pair-view h1')) === ZH['pair.title'], 'lang: 中文 by default');
    await evaluate(p, `document.querySelector('#pair-view [data-aj-lang="en"]').click()`);
    check((await evaluate(p, `document.documentElement.lang + '|' + document.title`)) === `en|${EN['meta.title']}`, 'lang: the switch on the pairing screen sets <html lang=en> and the title');
    check((await text(p, '#pair-view h1')) === EN['pair.title'] && (await text(p, '#status')) === EN['st.idle'], 'lang: visible text switches at once (static + status)');
    check((await evaluate(p, `document.getElementById('pair-link').placeholder`)) === EN['pair.placeholder'], 'lang: attributes switch too (placeholder)');
    await navigate(p, BASE);
    await waitState(p, 'idle');
    check((await evaluate(p, `document.documentElement.lang + '|' + localStorage.getItem('aj.lang')`)) === 'en|en', 'lang: English survives a reload (localStorage aj.lang)');
    check((await text(p, '#pair-view h1')) === EN['pair.title'], 'lang: reloaded page renders English');
    await evaluate(p, `document.getElementById('menu').open = true; document.querySelector('#menu [data-aj-lang="zh"]').click()`);
    check((await evaluate(p, `document.documentElement.lang + '|' + localStorage.getItem('aj.lang')`)) === 'zh-CN|zh', 'lang: the switch in the menu goes back to 中文 and saves it');
    await navigate(p, BASE + '?lang=en');
    await waitState(p, 'idle');
    check((await evaluate(p, `document.documentElement.lang + '|' + localStorage.getItem('aj.lang')`)) === 'en|en', 'lang: ?lang=en wins and is saved');
    await navigate(p, 'about:blank');
    await navigate(p, fake.newPairing(BASE + '?lang=en'));
    await waitState(p, 'awaiting-approval');
    check((await evaluate(p, 'location.href')) === BASE + '?lang=en' && (await text(p, '#sas-view h1')) === EN['sas.title'], 'lang: a pairing link keeps ?lang= while its #p= fragment is stripped');
    noProblems(p, 'lang');
    await p.dispose();
  }
  browser.close();
} catch (e) {
  console.error(e); failures.push(String(e));
} finally {
  chrome.kill('SIGTERM');
  await web.stop();
  await fake.stop();
  await sleep(300);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
}
console.log(`\n${failures.length ? 'FAILED' : 'ALL PASS'} — ${failures.length} failure(s); screenshots in ${SHOTS}`);
process.exit(failures.length ? 1 : 0);
