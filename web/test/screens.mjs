#!/usr/bin/env node
// Web client: screenshots + layout + full client flow against a local fake relay/host, in an INDEPENDENT headless Chromium.
// Never touches the shared CDP on :9222 — own chromium on a random debugging port, temp profile, one isolated browser
// context per run; public/ is served by test/serve.mjs (Worker headers, connect-src ws://127.0.0.1:*).
// Run: node web/test/screens.mjs      Output: /tmp/aj-a2/web-shots/*.png
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, existsSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const SHOTS = process.env.AJ_SHOTS || '/tmp/aj-a2/web-shots';
const CHROMIUM = process.env.CHROMIUM || '/usr/bin/chromium';
const SIZES = [['mobile', 360, 800], ['desktop', 1440, 900]];
const SCHEMES = ['light', 'dark'];
const PAIR_FAIL = '未获批准或已过期，请在电脑上重新运行 jarvis pair';

mkdirSync(SHOTS, { recursive: true });
const failures = [];
const check = (ok, msg) => { if (!ok) failures.push(msg); console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const profile = mkdtempSync(join(tmpdir(), 'aj-a2-web-chrome-'));
const chrome = spawn(CHROMIUM, ['--headless=new', `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--disable-background-networking',
  '--disable-component-update', '--disable-sync', '--disable-extensions', '--mute-audio', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
let chromeErr = '';
chrome.stderr.on('data', (d) => { chromeErr += d; });

async function devtoolsPort() {
  const f = join(profile, 'DevToolsActivePort');
  for (let i = 0; i < 100; i++) { if (existsSync(f)) { const [p] = readFileSync(f, 'utf8').split('\n'); if (p) return Number(p); } await sleep(100); }
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

try {
  const port = await devtoolsPort();
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const browser = cdp(version.webSocketDebuggerUrl); await browser.ready;

  async function newPage(w, h, scheme) {
    const { browserContextId } = await browser.send('Target.createBrowserContext', { disposeOnDetach: true });
    const { targetId } = await browser.send('Target.createTarget', { url: 'about:blank', browserContextId });
    const p = cdp(`ws://127.0.0.1:${port}/devtools/page/${targetId}`); await p.ready;
    p.problems = [];
    p.on((m) => {
      if (m.method === 'Log.entryAdded' && (m.params.entry.level === 'error' || /Content Security Policy/i.test(m.params.entry.text))) p.problems.push(`log: ${m.params.entry.text}`);
      if (m.method === 'Runtime.exceptionThrown') p.problems.push(`exception: ${m.params.exceptionDetails.text} ${m.params.exceptionDetails.exception?.description || ''}`);
      if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) p.problems.push(`console.${m.params.type}: ${m.params.args.map((a) => a.value ?? a.description).join(' ')}`);
    });
    await p.send('Page.enable'); await p.send('Runtime.enable'); await p.send('Log.enable');
    await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
    await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
    p.dispose = async () => { p.close(); await browser.send('Target.closeTarget', { targetId }).catch(() => {}); await browser.send('Target.disposeBrowserContext', { browserContextId }).catch(() => {}); };
    return p;
  }
  const evaluate = async (p, expr) => { const r = await p.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };
  async function navigate(p, url) { const load = p.waitFor('Page.loadEventFired'); await p.send('Page.navigate', { url }); await load; await sleep(200); }
  async function waitState(p, want, ms = 10000) {
    let s;
    for (let t = 0; t < ms; t += 100) { s = await evaluate(p, 'window.__ajState'); if (s === want) return true; await sleep(100); }
    throw new Error(`state ${s} != ${want} (status: ${await evaluate(p, `document.getElementById('status').textContent + ' / ' + document.getElementById('error-text').textContent`)}; problems: ${p.problems.join(' | ')})`);
  }
  async function shoot(p, name) {
    const { cssContentSize: s } = await p.send('Page.getLayoutMetrics');
    const { data } = await p.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: s.width, height: s.height, scale: 1 } });
    const file = join(SHOTS, `${name}.png`);
    writeFileSync(file, Buffer.from(data, 'base64'));
    console.log(`      shot ${file}`);
  }
  async function layoutOk(p, label) {
    const r = await evaluate(p, `({ sw: document.documentElement.scrollWidth, iw: innerWidth,
      small: Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent).map(b => b.getBoundingClientRect())
        .filter(b => b.height < 44 || b.width < 44).length })`);
    check(r.sw <= r.iw, `${label}: no horizontal overflow (scrollWidth ${r.sw} <= ${r.iw})`);
    check(r.small === 0, `${label}: every visible button ≥ 44×44 px (${r.small} smaller)`);
  }
  const noProblems = (p, label) => check(p.problems.length === 0, `${label}: zero console errors / CSP violations${p.problems.length ? ' — ' + p.problems.join(' | ') : ''}`);
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

  // ---------- 1. idle (pairing input) + badge panel, every size × scheme
  for (const scheme of SCHEMES) for (const [sz, w, h] of SIZES) {
    const tag = `${sz}-${scheme}`;
    const p = await newPage(w, h, scheme);
    await navigate(p, BASE);
    await waitState(p, 'idle');
    check(!(await evaluate(p, `document.getElementById('pair-view').hidden`)), `${tag}: idle shows the pairing input`);
    check((await evaluate(p, `document.getElementById('badge').textContent`)) === '网页版 · 改了会被发现（建设中）', `${tag}: badge visible`);
    await layoutOk(p, `idle ${tag}`);
    await shoot(p, `idle-${tag}`);
    await evaluate(p, `document.getElementById('badge').click()`);
    const hash = await evaluate(p, `document.getElementById('version-hash').textContent`);
    const want = JSON.parse(readFileSync(new URL('../public/version.json', import.meta.url), 'utf8')).combined;
    check(hash === want, `${tag}: badge panel shows the combined hash`);
    await layoutOk(p, `badge ${tag}`);
    await shoot(p, `badge-${tag}`);
    await evaluate(p, `document.getElementById('pair-link').value = 'not a link'; document.getElementById('pair-go').click()`);
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
    const sas = await evaluate(p, `document.getElementById('sas').textContent`);
    check(/^\d{6}$/.test(sas) && sas === fake.sas, `${tag}: SAS ${sas} matches the host's (${fake.sas})`);
    check(/^网页 · .+ Chrome$/.test(fake.label || ''), `${tag}: device label "${fake.label}"`);
    await layoutOk(p, `sas ${tag}`);
    await shoot(p, `sas-${tag}`);
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
    await shoot(p, `chat-${tag}`);

    fake.setUp(false);
    await waitState(p, 'waiting-host');
    check((await evaluate(p, `document.getElementById('status').textContent`)) === '电脑离线', `${tag}: host down → 电脑离线`);
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
    await shoot(p, `revoked-${tag}`);
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
    check((await evaluate(p, `document.getElementById('status').textContent`)) === PAIR_FAIL, `${tag}: host down mid-pairing → ${PAIR_FAIL}`);
    fake.setUp(true);
    await shoot(p, `pair-failed-${tag}`);

    await navigate(p, 'about:blank');
    await navigate(p, fake.newPairing(BASE, -10));
    await waitState(p, 'idle');
    check(!(await evaluate(p, `document.getElementById('pair-error').hidden`)), `${tag}: expired link refused before connecting`);
    noProblems(p, `flow ${tag}`);
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
