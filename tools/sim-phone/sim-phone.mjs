#!/usr/bin/env node
// sim-phone — a simulated phone for test environments without a real phone.
//
// It is a real (headless) Chromium running the real Agent Jarvis web client. It never bypasses pairing: it shows the
// 6-digit safety code like a phone does, and a human (or the test) still types those 6 digits and the approval
// passphrase into the host (`jarvis pair` / `jarvis admin`). Node >= 22, no npm dependencies.
//
// Usage:
//   node sim-phone.mjs --link <pairing link> [--web <url>] [--profile <dir>] [--send "<text>"] [--approve | --deny] [--wait <s>]
//   node sim-phone.mjs --profile <dir> [--send "<text>"] [--approve | --deny] [--wait <s>]     (an already paired profile)
//
//   --link <url>     the pairing link: `jarvis pair --no-qr` prints it; the `jarvis admin` page shows it under 显示链接
//   --web <url>      the web client to open (default: the link's own origin, else https://alpha-web.agentjarvis.net)
//   --profile <dir>  keep the browser profile (the device key and pairing) in <dir>, so later runs resume as the same phone
//   --send <text>    after pairing / resuming, send this message and print the replies
//   --approve        answer the next approval card with 批准 (allow)
//   --deny           answer the next approval card with 拒绝 (deny)
//   --wait <s>       how long to wait for a reply / an approval card (default 60; pairing itself waits up to 180 s)
//
// Output (stdout, one event per line):
//   CODE 123456          the safety code the page shows — type it (and the passphrase) into the host
//   PAIRED               the host approved this phone (or a saved profile resumed: RESUMED)
//   SENT <text>          the message left the phone
//   REPLY <line>         one line of a reply from the computer (Agent or `jarvis send`)
//   ASK <tool>: <line>   an approval card appeared (first line of what the Agent wants to run)
//   ANSWERED allow|deny  this tool pressed 批准 / 拒绝
//   ASK-RESULT <result>  what the host decided: allow | deny | timeout | gone
//   AGENT <status>       the Agent status line changed (idle | working | waiting | down)
// Errors: one line on stderr, prefixed "sim-phone:".
//
// Exit codes:
//   0  done (paired / resumed; the reply arrived; the card was answered)
//   1  usage error (bad arguments, Node < 22)
//   2  no Chromium found or it did not start
//   3  pairing refused or failed (wrong code, expired / invalid link, revoked device, page error)
//   4  timed out waiting for the host to approve the pairing
//   5  --send: no reply within --wait seconds
//   6  --approve / --deny: no approval card within --wait seconds
//   7  not paired: no --link and the profile holds no paired computer (or it could not reconnect)
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync, rmSync, mkdirSync, unlinkSync, realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { join, resolve, delimiter } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const DEFAULT_WEB = 'https://alpha-web.agentjarvis.net';
const PAIR_WAIT_S = 180;
const EXIT = { ok: 0, usage: 1, chromium: 2, pairFailed: 3, pairTimeout: 4, noReply: 5, noCard: 6, notPaired: 7 };

class Exit extends Error { constructor(code, msg) { super(msg); this.code = code; } }
const out = (line) => process.stdout.write(line + '\n');
const oneLine = (s) => String(s).replace(/[\r\n]+/g, ' ').trim();

const USAGE = `usage: node sim-phone.mjs --link <pairing link> [--web <url>] [--profile <dir>] [--send "<text>"] [--approve|--deny] [--wait <s>]
       node sim-phone.mjs --profile <dir> [--send "<text>"] [--approve|--deny] [--wait <s>]
For test environments without a real phone only; it never bypasses pairing (see README.md).`;

export function parseArgs(argv) {
  const a = { wait: 60 };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const val = () => { if (i + 1 >= argv.length) throw new Exit(EXIT.usage, `${k} needs a value`); return argv[++i]; };
    if (k === '--link') a.link = val();
    else if (k === '--web') a.web = val();
    else if (k === '--profile') a.profile = val();
    else if (k === '--send') a.send = val();
    else if (k === '--wait') a.wait = Number(val());
    else if (k === '--approve') a.answer = a.answer && a.answer !== 'allow' ? 'both' : 'allow';
    else if (k === '--deny') a.answer = a.answer && a.answer !== 'deny' ? 'both' : 'deny';
    else if (k === '-h' || k === '--help') a.help = true;
    else throw new Exit(EXIT.usage, `unknown argument ${k}`);
  }
  if (a.help) return a;
  if (a.answer === 'both') throw new Exit(EXIT.usage, '--approve and --deny are exclusive');
  if (!Number.isFinite(a.wait) || a.wait <= 0 || a.wait > 3600) throw new Exit(EXIT.usage, '--wait must be 1–3600 seconds');
  if (a.link !== undefined && !a.link.includes('#p=')) throw new Exit(EXIT.usage, '--link must be a pairing link (it contains #p=)');
  if (a.link === undefined && a.profile === undefined) throw new Exit(EXIT.usage, 'give --link, or --profile of an already paired phone');
  if (a.send !== undefined && !a.send.trim()) throw new Exit(EXIT.usage, '--send needs some text');
  let web = a.web;
  if (!web && a.link) { try { const u = new URL(a.link); if (/^https?:$/.test(u.protocol)) web = u.origin + u.pathname; } catch {} }
  web = web || DEFAULT_WEB;
  let u;
  try { u = new URL(web); } catch { throw new Exit(EXIT.usage, '--web is not a URL'); }
  if (!/^https?:$/.test(u.protocol)) throw new Exit(EXIT.usage, '--web must be http(s)');
  u.hash = '';
  a.web = u.toString();
  return a;
}

// ---------------------------------------------------------------- Chromium
export function findChromium(env = process.env) {
  if (env.CHROMIUM) return existsSync(env.CHROMIUM) ? env.CHROMIUM : null;
  if (existsSync('/usr/bin/chromium')) return '/usr/bin/chromium';
  for (const name of ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable']) {
    for (const dir of (env.PATH || '').split(delimiter)) {
      if (dir && existsSync(join(dir, name))) return join(dir, name);
    }
  }
  const mac = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  return existsSync(mac) ? mac : null;
}

function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
  };
  const ready = new Promise((r, j) => { ws.onopen = r; ws.onerror = () => j(new Error('CDP connection failed')); });
  return {
    ready, close: () => { try { ws.close(); } catch {} },
    send: (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); }),
  };
}

async function launch(bin, profileDir) {
  const persistent = !!profileDir;
  const profile = persistent ? resolve(profileDir) : mkdtempSync(join(tmpdir(), 'sim-phone-'));
  mkdirSync(profile, { recursive: true });
  const portFile = join(profile, 'DevToolsActivePort');
  if (existsSync(portFile)) { try { unlinkSync(portFile); } catch {} }   // a stale one from an earlier run
  const flags = ['--headless=new', `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--disable-background-networking',
    '--disable-component-update', '--disable-sync', '--disable-extensions', '--mute-audio', '--password-store=basic'];
  if (process.getuid?.() === 0) flags.push('--no-sandbox');   // root in a container: Chromium refuses its sandbox
  const chrome = spawn(bin, [...flags, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  let err = '';
  chrome.stderr.on('data', (d) => { err += d; if (err.length > 8000) err = err.slice(-4000); });
  let port;
  for (let i = 0; i < 150 && !port && chrome.exitCode === null; i++) {
    if (existsSync(portFile)) port = Number(readFileSync(portFile, 'utf8').split('\n')[0]) || undefined;
    if (!port) await sleep(100);
  }
  const cleanup = async () => {
    chrome.kill('SIGTERM');
    for (let i = 0; i < 30 && chrome.exitCode === null && chrome.signalCode === null; i++) await sleep(100);
    try { chrome.kill('SIGKILL'); } catch {}
    if (!persistent) rmSync(profile, { recursive: true, force: true });
  };
  if (!port) { await cleanup(); throw new Exit(EXIT.chromium, 'Chromium did not start' + (/profile|SingletonLock|in use/i.test(err) ? ' (is the profile in use by another sim-phone?)' : '')); }
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const browser = cdp(version.webSocketDebuggerUrl); await browser.ready;
  // The default browser context: its storage (IndexedDB = the device key and the paired computer) lives in the profile.
  const { targetId } = await browser.send('Target.createTarget', { url: 'about:blank' });
  const p = cdp(`ws://127.0.0.1:${port}/devtools/page/${targetId}`); await p.ready;
  await p.send('Runtime.enable'); await p.send('Page.enable');
  await p.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  const page = {
    goto: (url) => p.send('Page.navigate', { url }),
    eval: async (expr) => {
      const r = await p.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error('page: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
      return r.result.value;
    },
  };
  return { page, close: async () => { p.close(); browser.close(); await cleanup(); } };
}

// ---------------------------------------------------------------- the phone
// Everything below only reads the page and presses the page's own buttons — the same things a person's thumb does.
const SNAPSHOT = `(() => {
  const q = (s) => document.querySelector(s);
  const st = q('#status');
  const asks = [...document.querySelectorAll('#messages li.ask')].map((li) => ({
    tool: li.querySelector('.ask-title')?.textContent || '', summary: li.querySelector('.ask-summary')?.textContent || '',
    result: li.dataset.result || '', open: !!li.querySelector('.ask-allow:not([disabled])') }));
  const replies = [...document.querySelectorAll('#messages li[data-dir=in]:not(.ask)')].map((li) => li.textContent);
  const ag = q('#agent-status');
  const pe = q('#pair-error');
  return { state: st?.dataset.state || '', status: st?.textContent || '', pairError: pe && !pe.hidden ? pe.textContent : '', sas: (q('#sas')?.textContent || '').replace(/\\D/g, ''),
    sasShown: !!q('#sas-view') && !q('#sas-view').hidden, asks, replies,
    agent: ag && !ag.hidden ? ag.dataset.s || '' : '' };
})()`;

const BOOT = `(async () => {
  const dbs = indexedDB.databases ? await indexedDB.databases() : [];
  if (!dbs.some((d) => d.name === 'agentjarvis')) return { device: false, paired: false };
  return await new Promise((res) => {
    const r = indexedDB.open('agentjarvis');
    r.onerror = () => res(null);
    r.onsuccess = () => {
      const db = r.result;
      if (!db.objectStoreNames.contains('kv')) { db.close(); return res({ device: false, paired: false }); }
      const tx = db.transaction('kv', 'readonly'), st = tx.objectStore('kv');
      const d = st.get('device'), h = st.get('host');
      tx.oncomplete = () => { db.close(); res({ device: !!d.result, paired: !!(h.result && h.result.approved) }); };
      tx.onerror = tx.onabort = () => { db.close(); res(null); };
    };
  });
})()`;

async function snapshot(page) {
  try { return await page.eval(SNAPSHOT); } catch { return null; }      // mid-navigation
}

async function waitFor(page, pred, ms) {
  const t0 = Date.now();
  let s = null;
  while (Date.now() - t0 < ms) {
    s = await snapshot(page);
    if (s) { const v = pred(s); if (v) return v; }
    await sleep(100);
  }
  return null;
}

async function run(a) {
  const bin = findChromium();
  if (!bin) throw new Exit(EXIT.chromium, 'no Chromium / Chrome found (set CHROMIUM=/path/to/chromium)');
  const { page, close } = await launch(bin, a.profile);
  try {
    await page.goto(a.web);
    // the client is up when it settled on a state (idle = not paired; connecting / waiting-host / ready = a saved pairing)
    // The client has booted once its device key is in its storage (the first thing it does); then it either shows 未配对 or
    // resumes the saved pairing. Read-only peek, and only at a database the client already created (never create it).
    let boot = null;
    for (let t0 = Date.now(); Date.now() - t0 < 20000 && !boot?.device; await sleep(150)) {
      const s = await snapshot(page);
      if (s && s.state === 'error' && /无法使用/.test(s.status)) throw new Exit(EXIT.pairFailed, 'the web client cannot run in this browser');
      boot = await page.eval(BOOT).catch(() => null);
    }
    if (!boot?.device) throw new Exit(EXIT.pairFailed, `the web client did not start at ${a.web}`);
    await sleep(500);                                   // let main() finish (it reads the saved pairing right after the key)

    if (a.link) {
      // The in-app 扫码 hands the QR's text to the client's startPairing(); the paste box + 开始配对 is the same call with
      // the same text — so we "scan" by pasting the link there (no new client path, no shortcut past the handshake).
      await page.eval(`(() => { const t = document.querySelector('#pair-link'); t.value = ${JSON.stringify(a.link)};
        t.dispatchEvent(new Event('input', { bubbles: true })); document.querySelector('#pair-go').click(); return true; })()`);
      const shown = await waitFor(page, (s) => (s.state === 'awaiting-approval' && /^\d{6}$/.test(s.sas)) ? s
        : s.pairError || ['error', 'revoked'].includes(s.state) ? { fail: s } : null, 60000);
      if (!shown) throw new Exit(EXIT.pairTimeout, 'no safety code: the computer did not answer (is `jarvis serve` running, is the link fresh?)');
      if (shown.fail) throw new Exit(EXIT.pairFailed, `pairing did not start: ${oneLine(shown.fail.pairError || shown.fail.status)}`);
      out(`CODE ${shown.sas}`);
      const done = await waitFor(page, (s) => s.state === 'ready' ? s : ['error', 'revoked', 'idle'].includes(s.state) ? { fail: s } : null, PAIR_WAIT_S * 1000);
      if (!done) throw new Exit(EXIT.pairTimeout, `the computer did not approve within ${PAIR_WAIT_S} s`);
      if (done.fail) throw new Exit(EXIT.pairFailed, `pairing refused: ${oneLine(done.fail.status)}`);
      out('PAIRED');
    } else {
      if (!boot.paired) throw new Exit(EXIT.notPaired, 'this profile has no paired computer — run once with --link');
      const done = await waitFor(page, (s) => s.state === 'ready' ? s : s.state === 'revoked' ? { fail: s } : null, Math.max(30, a.wait) * 1000);
      if (!done) throw new Exit(EXIT.notPaired, 'could not reconnect to the paired computer (is `jarvis serve` running?)');
      if (done.fail) throw new Exit(EXIT.pairFailed, 'this phone was removed from the computer (revoked)');
      out('RESUMED');
    }

    if (a.send === undefined && !a.answer) return EXIT.ok;
    const base = await snapshot(page);
    let seenReplies = base.replies.length, seenAsks = base.asks.length;
    const askResults = new Map();
    let lastAgent = base.agent, answered = false, gotReply = false, sawWork = false, quietSince = 0;
    if (a.send !== undefined) {
      await page.eval(`(() => { const t = document.querySelector('#msg-input'); t.focus(); t.value = ${JSON.stringify(a.send)};
        t.dispatchEvent(new Event('input', { bubbles: true })); document.querySelector('#send').click(); return true; })()`);
      out(`SENT ${oneLine(a.send)}`);
    }
    const deadline = Date.now() + a.wait * 1000;
    while (Date.now() < deadline) {
      const s = await snapshot(page);
      if (!s) { await sleep(100); continue; }
      if (s.state === 'revoked') throw new Exit(EXIT.pairFailed, 'this phone was removed from the computer (revoked)');
      if (s.agent !== lastAgent) { lastAgent = s.agent; if (s.agent) out(`AGENT ${s.agent}`); }
      if (s.agent === 'working' || s.agent === 'waiting') sawWork = true;
      for (const r of s.replies.slice(seenReplies)) { for (const line of r.split('\n')) out(`REPLY ${line}`); gotReply = true; quietSince = Date.now(); }
      seenReplies = s.replies.length;
      for (let i = seenAsks; i < s.asks.length; i++) {
        const k = s.asks[i];
        out(`ASK ${oneLine(k.tool.replace(/^Agent 想要执行：/, ''))}: ${oneLine(k.summary.split('\n')[0])}`);
        if (a.answer && !answered && k.open) {
          await page.eval(`(() => { const li = document.querySelectorAll('#messages li.ask')[${i}];
            li.querySelector(${JSON.stringify(a.answer === 'allow' ? '.ask-allow' : '.ask-deny')}).click(); return true; })()`);
          answered = true;
          out(`ANSWERED ${a.answer}`);
        }
      }
      seenAsks = s.asks.length;
      s.asks.forEach((k, i) => { if (k.result && !askResults.has(i)) { askResults.set(i, k.result); out(`ASK-RESULT ${k.result}`); } });
      // done when: what was asked for happened and the Agent is idle again (or there is no Agent status at all) for a moment
      const wantReply = a.send !== undefined;
      const needAnswer = !!a.answer;
      const settled = (!s.agent || s.agent === 'idle' || s.agent === 'down') && (sawWork || !s.agent || gotReply)
        && s.asks.every((k) => !k.open) && Date.now() - quietSince > 1200;
      if ((!wantReply || gotReply) && (!needAnswer || answered) && settled) return EXIT.ok;
      await sleep(150);
    }
    if (a.answer && !answered) throw new Exit(EXIT.noCard, `no approval card within ${a.wait} s`);
    if (a.send !== undefined && !gotReply) throw new Exit(EXIT.noReply, `no reply within ${a.wait} s`);
    return EXIT.ok;                                    // got what was asked; the Agent was still busy at the deadline
  } finally {
    await close();
  }
}

async function main() {
  const major = Number(process.versions.node.split('.')[0]);
  if (major < 22 || typeof WebSocket === 'undefined') { process.stderr.write('sim-phone: needs Node >= 22 (global WebSocket)\n'); return EXIT.usage; }
  let a;
  try { a = parseArgs(process.argv.slice(2)); } catch (e) {
    process.stderr.write(`sim-phone: ${e.message}\n${USAGE}\n`); return e.code ?? EXIT.usage;
  }
  if (a.help) { out(USAGE); return EXIT.ok; }
  try { return await run(a); } catch (e) {
    process.stderr.write(`sim-phone: ${oneLine(e.message)}\n`);
    return e instanceof Exit ? e.code : EXIT.pairFailed;
  }
}

const isMain = (() => { try { return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } })();
if (isMain) {
  main().then((c) => process.exit(c));
}
