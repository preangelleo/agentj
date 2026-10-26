#!/usr/bin/env node
// P545 (FEEDBACK #309, PROTOCOL §40) — m.agentj.app/<seat id>: one address, one Home Screen app, one storage and one passkey
// per seat. REAL web client in headless Chromium (own profile, never :9222), REAL host CLI × 2 (two seats = two computers'
// channels), local workerd relay, a CDP virtual platform authenticator for passkeys. Walks:
//   1 one browser: seat A's pairing link (as an up-to-date host prints it: m.agentj.app/<a>/#p=…) → A's own page, paired;
//     seat B's → /<b>/, paired; both tabs stay ready side by side; each page's Home Screen hint names its own address
//   2 isolation: two IndexedDB databases, settings written on A never show on B, the raw keys carry the seat id
//   3 Home Screen: each seat's manifest has its own id / start_url / scope /<seat>/ and name 「Agent J · <name>」; Chromium's
//     app id (what Android installs by) differs per seat and from the root page's
//   4 the root page of that browser lists both seats (「选择要连接的席位」) instead of the system's passkey list
//   5 passkeys: a fresh browser on /<a>/?c=<A's credential> asks for that credential only and comes back as A; a fresh
//     browser on /<a>/ holding only B's passkey is refused (seat.pkOther) and offered B's address
//   6 an old phone paired on the root page keeps working there (no sheet, no redirect); Settings 「改用本席位专属网址」 moves it
//     to /<a>/ without a new pairing, the root page opens /<a>/ from then on; unpaired there, it stays unpaired; a browser
//     that knows exactly one seat goes from the root page straight to it
// Shots → $AJ_P545_SHOTS (default /tmp/aj-p545). Usage: node web/test/p545.mjs
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { HostProc } from '../../tests/lib/host.mjs';
import { launchBrowser } from '../../tests/lib/browser.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const SHOTS = process.env.AJ_P545_SHOTS || '/tmp/aj-p545';
mkdirSync(SHOTS, { recursive: true });
let failed = 0;
const check = (ok, msg) => { if (!ok) failed++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const WEB = web.url.replace('127.0.0.1', 'localhost');           // WebAuthn refuses an IP address as rp id
const { startLocalRelay } = await import('../../relay/test/local_relay.mjs');
const relay = await startLocalRelay({});
const Q = `?relay=${encodeURIComponent(relay.url)}`;
const tmp = mkdtempSync(join(tmpdir(), 'aj-p545-'));
mkdirSync(join(tmp, 'work'));
writeFileSync(join(tmp, 'claude'), `#!/bin/sh\nexec python3 ${join(HERE, '../../host/tests/fakeclaude.py')} "$@"\n`);
chmodSync(join(tmp, 'claude'), 0o700);
const mkHost = (name) => {
  const h = new HostProc({ relay: relay.url, web: web.url, env: { AGENTJ_CLAUDE_BIN: join(tmp, 'claude'), AGENTJ_TEST_PASSKEY_ORIGIN: 'loopback' } }).init();
  h.run(['agent', 'claude', '--dir', join(tmp, 'work')]);
  h.run(['name', name]);
  return h;
};
const hostA = mkHost('Father@TB');
const hostB = mkHost('Mother@Mac');
const devsOf = (h) => JSON.parse(readFileSync(join(h.stateDir, 'devices.json'), 'utf8'));
const browser = await launchBrowser();
const fragOf = (link) => link.slice(link.indexOf('#'));
const seatOf = async (h) => {
  const { seat_id } = await import('node:child_process').then(({ execFileSync }) => JSON.parse(execFileSync('python3', ['-c',
    `import json,sys; sys.path.insert(0, ${JSON.stringify(join(HERE, '../../host'))}); from agentj.seaturl import seat_id; print(json.dumps({"seat_id": seat_id(${JSON.stringify(h.config.channel)})}))`], { encoding: 'utf8' })));
  return seat_id;
};

async function phone({ context, credentials = [], w = 390, h = 844 } = {}) {
  const p = await browser.newPage(w, h, context ? { context } : {});
  await p.send('WebAuthn.enable', { enableUI: false });
  const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal',
    hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  p.auth = authenticatorId;
  for (const c of credentials) await p.send('WebAuthn.addCredential', { authenticatorId, credential: c });
  // what the page asked the system for (allowCredentials of every get()) — the "no list to pick from" evidence
  await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => { const g = navigator.credentials.get.bind(navigator.credentials);
    window.__ajGets = []; navigator.credentials.get = (o) => { window.__ajGets.push((o.publicKey.allowCredentials || []).length); return g(o); }; })();` });
  p.tap = async (sel) => {
    const r = await p.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.scrollIntoView({ block: 'center' }); const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()`);
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  };
  return p;
}
const visible = (sel) => `(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !!e && !e.hidden && !e.closest('[hidden]'); })()`;
const path = (p) => p.eval('location.pathname');
const creds = async (p) => (await p.send('WebAuthn.getCredentials', { authenticatorId: p.auth })).credentials;

async function pairVia(page, h, url) {
  const pr = await h.pair();
  await page.goto(url + fragOf(pr.link));
  await page.waitState('awaiting-approval', 20000);
  const sas = (await page.eval(`document.querySelector('#sas').textContent`)).replace(/\D/g, '');
  await pr.waitPrompt();
  pr.typeCode(sas);
  return pr.exited;
}
/** Answer the sheets that follow a new pairing (the passkey offer: save = yes); → their titles. P545 adds none. */
async function sheets(p, { savePk = true } = {}) {
  const seen = [];
  for (let i = 0; i < 2; i++) {
    if (!(await p.waitFor(visible('#confirm'), i ? 4000 : 8000).then(() => true, () => false))) break;
    seen.push(await p.eval(`document.getElementById('confirm-title').textContent`));
    await p.tap(savePk ? '#confirm-yes' : '#confirm-no');
    await sleep(2800);                                 // and its 「记住了」 notice
  }
  return seen;
}

try {
  await hostA.serve(); await hostB.serve();
  const A = await seatOf(hostA), B = await seatOf(hostB);
  check(/^[a-z2-7]{8}$/.test(A) && /^[a-z2-7]{8}$/.test(B) && A !== B, `seat ids: A=${A} B=${B}`);

  // ---------------------------------------------------------------- 1 one browser, two seats, each on its own page
  const pa = await phone();
  const ctx = pa.browserContextId;
  check((await pairVia(pa, hostA, `${WEB}${A}/${Q}`)).code === 0, 'A: pairing link on A\'s own address → paired');
  check(await path(pa) === `/${A}/`, `A: on its own page (${await path(pa)})`);
  await pa.waitState('ready', 20000);
  const seenA = await sheets(pa);
  check(!seenA.some((x) => /专属网址|own address/.test(x)), `A: no extra sheet after the pairing (${seenA.join(' / ')})`);
  const hintA = await pa.eval(`document.getElementById('a2hs-seat').textContent`);
  check(hintA.startsWith('本席位专属网址：') && hintA.includes(`/${A}`) && !(await pa.eval(`document.getElementById('a2hs-seat').hidden`)), `A: the Home Screen hint names A's own address (${hintA})`);
  if (await pa.eval(visible('#a2hs'))) await pa.shot(join(SHOTS, '01-seatA-home-screen-hint-zh.png'));
  const pb = await phone({ context: ctx });
  // a link of the OTHER seat opened on A's address goes to B's page
  check((await pairVia(pb, hostB, `${WEB}${A}/${Q}`)).code === 0, 'B: second seat paired in the same browser');
  check(await path(pb) === `/${B}/`, `B: B's link opened on A's address went to B's own page (${await path(pb)})`);
  await pb.waitState('ready', 20000);
  await sheets(pb);
  await sleep(4000);
  check(await pa.state() === 'ready' && await pb.state() === 'ready', 'A and B: both tabs stay ready side by side (no takeover between seats)');
  check(Object.keys(devsOf(hostA)).length === 1 && Object.keys(devsOf(hostB)).length === 1, 'each computer has exactly one remote');

  // ---------------------------------------------------------------- 2 storage isolation
  await pa.eval(`(localStorage.setItem('aj.fontScale', '1.4'), true)`);
  check(await pb.eval(`localStorage.getItem('aj.fontScale')`) !== '1.4', 'a setting written on A is not B\'s');
  const raw = await pa.eval(`Object.keys(window.AJSeat.root).filter((k) => k.startsWith('aj.')).sort()`);
  check(raw.includes(`aj.fontScale@${A}`) && !raw.includes('aj.fontScale') && raw.includes(`aj.paired@${A}`) && raw.includes(`aj.paired@${B}`),
    `raw localStorage keys carry the seat id (${raw.filter((k) => /fontScale|paired/.test(k)).join(', ')})`);
  const dbs = await pa.eval(`indexedDB.databases().then((l) => l.map((d) => d.name).sort())`);
  const rootPairing = await pa.eval(`new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onupgradeneeded = () => r.transaction.abort(); r.onerror = (e) => { e.preventDefault(); res('none'); };
    r.onsuccess = () => { const d = r.result; if (!d.objectStoreNames.contains('kv')) return res('none'); const q = d.transaction('kv').objectStore('kv').get('host'); q.onsuccess = () => res(q.result ? 'paired' : 'none'); }; })`);
  check(dbs.includes(`agentjarvis@${A}`) && dbs.includes(`agentjarvis@${B}`) && rootPairing === 'none', `one IndexedDB per seat; the root page holds no pairing (${dbs.join(', ')})`);
  const chA = await pa.eval(`import('/${A}/js/store.js').then((s) => s.dbGet('host')).then((h) => h.channel)`);
  const chB = await pb.eval(`import('/${B}/js/store.js').then((s) => s.dbGet('host')).then((h) => h.channel)`);
  check(chA === hostA.config.channel && chB === hostB.config.channel, 'each page holds its own computer\'s pairing');

  // ---------------------------------------------------------------- 3 Home Screen identity per seat
  await sleep(500);
  const man = async (p) => { const m = await p.send('Page.getAppManifest', {}); const id = await p.send('Page.getAppId', {}).catch(() => ({})); return { url: m.url, json: m.data ? JSON.parse(m.data) : null, appId: id.appId, recommendedId: id.recommendedId }; };
  const mA = await man(pa), mB = await man(pb);
  check(mA.json?.id === `/${A}/` && mA.json?.scope === './' && mB.json?.id === `/${B}/`, `manifest id per seat (A ${mA.json?.id}, B ${mB.json?.id})`);
  check(mA.json?.name === 'Agent J · Father@TB' && mB.json?.name === 'Agent J · Mother@Mac', `icon names: 「${mA.json?.name}」 · 「${mB.json?.name}」`);
  check(!!mA.appId && !!mB.appId && mA.appId !== mB.appId, `Chromium app ids differ (${mA.appId} | ${mB.appId})`);
  const pr = await phone({ context: ctx });
  await pr.goto(WEB + Q);
  await pr.waitFor(`document.body.dataset.view === 'pair'`, 10000);
  const mR = await man(pr);
  check(!!mR.appId && mR.appId !== mA.appId && mR.appId !== mB.appId, `the root page's app id is a third one (${mR.appId})`);
  const appleA = await pa.eval(`document.querySelector('meta[name="apple-mobile-web-app-title"]').content`);
  check(appleA === 'Agent J · Father@TB', `iOS Home Screen title on A: 「${appleA}」`);
  writeFileSync(join(SHOTS, 'manifests.json'), JSON.stringify({ A: mA, B: mB, root: mR }, null, 2));

  // ---------------------------------------------------------------- 4 the root page lists the seats
  await pr.waitFor(visible('#seat-pick'), 10000);
  const items = await pr.eval(`[...document.querySelectorAll('#seat-list a')].map((a) => [a.querySelector('b').textContent, a.getAttribute('href')])`);
  check(items.length === 2 && items.some(([n, h]) => n === 'Agent J - Mother@Mac' && h.endsWith(`/${B}/`)) && items.some(([, h]) => h.endsWith(`/${A}/`)),
    `root: 「选择要连接的席位」 lists both seats (${items.map(([n]) => n).join(' · ')})`);
  check(!(await pr.eval(visible('#pk-restore'))), 'root with two seats: no system passkey list button');
  await pr.shot(join(SHOTS, '02-root-choose-seat-zh.png'));
  await pr.goto(WEB + Q + '&lang=en');
  await pr.waitFor(visible('#seat-pick'), 10000);
  await pr.shot(join(SHOTS, '03-root-choose-seat-en.png'));
  await pr.goto(WEB + '?lang=zh');
  await pr.close();

  // an iPhone on A's address (not installed yet): the 「添加到主屏幕」 hint names this seat's own address
  const ip = await phone();
  await ip.send('Emulation.setUserAgentOverride', { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1' });
  await ip.goto(`${WEB}${A}/${Q}`);
  await ip.waitFor(visible('#a2hs'), 10000).catch(() => {});
  check(await ip.eval(visible('#a2hs-seat')) && (await ip.eval(`document.getElementById('a2hs-seat').textContent`)).includes(`/${A}`), 'iPhone on A\'s address: the Home Screen hint names it');
  await ip.shot(join(SHOTS, '01-seatA-home-screen-hint-iphone-zh.png'));
  await ip.close();

  // Settings → 「本席位专属网址」 on a seat's page
  await pa.eval(`document.getElementById('setBtn')?.click() ?? true`);
  await sleep(800);
  await pa.eval(`document.getElementById('set-seat-row').scrollIntoView({ block: 'center' }), true`);
  check(await pa.eval(visible('#set-seat-row')) && (await pa.eval(`document.getElementById('set-seat-url').textContent`)).endsWith(`/${A}`)
    && !(await pa.eval(visible('#set-seat-move'))), 'A Settings: its own address, no 「改用」 button on a seat\'s page');
  await pa.shot(join(SHOTS, '04-seatA-settings-address-zh.png'));
  const credA = (await creds(pa))[0], credB = (await creds(pb))[0];
  check(!!credA && !!credB, 'both seats saved a passkey');
  const hint = await pa.eval(`localStorage.getItem('aj.seatPk')`);
  check(hint === credA.credentialId.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''), 'A keeps its own credential id (for its Home Screen app)');
  check((mA.json?.start_url || '').includes('#c=') || (await man(pa)).json?.start_url?.includes('#c='), 'A\'s manifest start_url carries its passkey in the fragment (#c=)');
  await pa.close(); await pb.close();

  // ---------------------------------------------------------------- 5 passkeys per seat
  const f1 = await phone({ credentials: [credA, credB] });
  await f1.goto(`${WEB}${A}/${Q}#c=${hint}`);
  await f1.waitFor(visible('#pk-restore'), 15000);
  await f1.tap('#pk-restore');
  const back = await f1.waitState('ready', 30000).then(() => true, () => false);
  const gets = await f1.eval('window.__ajGets');
  check(back && await path(f1) === `/${A}/` && !(await f1.eval('location.hash')), 'fresh browser on /<A>/#c= (the installed app\'s start): Face ID → ready as A, no pairing; the hint left the address bar');
  check(gets.length >= 1 && gets.every((n) => n === 1), `the page asked for exactly one credential (allowCredentials ${JSON.stringify(gets)}) — no list`);
  check(Object.keys(devsOf(hostA)).length === 1, 'still one remote on A (the same phone came back)');
  await f1.goto(WEB + Q);                                                       // this browser knows exactly one seat
  await sleep(1500);
  check(await path(f1) === `/${A}/` && await f1.waitState('ready', 20000).then(() => true, () => false), 'root page in a browser that knows one seat → that seat\'s page, ready');
  await f1.close();
  const f2 = await phone({ credentials: [credB] });
  await f2.goto(`${WEB}${A}/${Q}`);
  await f2.waitFor(visible('#pk-restore'), 15000);
  await f2.tap('#pk-restore');
  await f2.waitFor(visible('#seat-pick-note'), 20000).catch(() => {});
  check(await f2.eval(visible('#seat-pick-note')) && await f2.state() !== 'ready' && Object.keys(devsOf(hostB)).length === 1, 'B\'s passkey on A\'s page: refused (seat.pkOther), no session to B from here');
  const offer = await f2.eval(`[...document.querySelectorAll('#seat-list a')].map((a) => a.getAttribute('href'))`);
  check(offer.length === 1 && offer[0].endsWith(`/${B}/`), 'and B\'s own address is offered instead');
  await f2.eval(`document.getElementById('seat-pick').scrollIntoView({ block: 'start' }), true`);
  await sleep(300);
  await f2.shot(join(SHOTS, '05-seatA-other-seat-passkey-zh.png'));
  await f2.close();

  // ---------------------------------------------------------------- 6 an old root-page phone keeps working, then moves
  const o = await phone();
  const pr2 = await hostA.pair();
  await o.goto(WEB + Q);
  await o.waitFor(`document.body.dataset.view === 'pair' && document.getElementById('status').dataset.state === 'idle'`, 10000);
  await sleep(800);                                                              // main() has wired the field
  await o.type('#pair-link', pr2.link.replace('127.0.0.1', 'localhost'));      // pasted on the root page: paired there, as before
  await o.click('#pair-go');
  await o.waitState('awaiting-approval', 20000);
  const sas = (await o.eval(`document.querySelector('#sas').textContent`)).replace(/\D/g, '');
  await pr2.waitPrompt(); pr2.typeCode(sas);
  check((await pr2.exited).code === 0 && await o.waitState('ready', 20000).then(() => true, () => false) && await path(o) === '/', 'old flow: pasted on the root page → paired on the root page');
  const seenO = await sheets(o, { savePk: false });
  check(!seenO.some((x) => /专属网址|own address/.test(x)) && await o.eval(`document.getElementById('a2hs-seat').hidden`), `root: nothing new after the pairing — no sheet, no seat line (${seenO.join(' / ')})`);
  const before = Object.keys(devsOf(hostA)).length;
  await o.goto(WEB + Q);                                                         // an old user reopening: unchanged
  check(await o.waitState('ready', 20000).then(() => true, () => false) && await path(o) === '/', 'root reopened: still the root page, ready (no forced move)');
  await o.eval(`document.getElementById('setBtn')?.click() ?? true`);
  await sleep(800);
  await o.eval(`document.getElementById('set-seat-move').scrollIntoView({ block: 'center' }), true`);
  check(await o.eval(visible('#set-seat-move')), 'root Settings: 「改用本席位专属网址」');
  await o.shot(join(SHOTS, '07-root-settings-move-zh.png'));
  await o.tap('#set-seat-move');
  await sleep(1500);
  check(await o.waitState('ready', 25000).then(() => true, () => false) && await path(o) === `/${A}/`, 'moved: A\'s own page, ready, no new pairing');
  check(Object.keys(devsOf(hostA)).length === before, `still ${before} remote(s) on A after the move`);
  const rootRec = await o.eval(`new Promise((res) => { const r = indexedDB.open('agentjarvis', 1); r.onsuccess = () => { const q = r.result.transaction('kv').objectStore('kv').get('host'); q.onsuccess = () => res(q.result && { approved: q.result.approved, movedTo: q.result.movedTo, channel: q.result.channel }); }; })`);
  check(rootRec?.approved && rootRec.movedTo === A && rootRec.channel === hostA.config.channel, 'the root page still has its record (readable), marked movedTo');
  await o.goto(WEB + Q);
  await sleep(1500);
  check(await path(o) === `/${A}/` && await o.waitState('ready', 20000).then(() => true, () => false), 'root reopened after the move → A\'s page, ready');
  // unpaired on the seat page: it stays unpaired (the root's moved copy is gone, nothing comes back on a reload)
  await o.click('#unpair');
  await o.waitFor(visible('#confirm'), 8000);
  await o.tap('#confirm-yes');
  await o.waitFor(`document.body.dataset.view === 'pair'`, 10000).catch(() => {});
  await o.goto(`${WEB}${A}/${Q}`);
  await sleep(2500);
  const rootAfter = await o.eval(`new Promise((res) => { const r = indexedDB.open('agentjarvis', 1); r.onsuccess = () => { const q = r.result.transaction('kv').objectStore('kv').get('host'); q.onsuccess = () => res(q.result || null); }; })`);
  check(await o.state() !== 'ready' && await o.eval(`document.body.dataset.view`) === 'pair' && rootAfter === null, 'unpaired on the seat page → reload stays unpaired; the root\'s moved copy is deleted');
  const errs = o.pageErrors().filter((e) => !/favicon|ERR_ABORTED|net::/.test(e));
  check(errs.length === 0, `no page errors (${errs.slice(0, 3).join(' | ')})`);
  await o.close();
} catch (e) {
  failed++;
  console.log('FAIL  ' + (e && e.stack || e));
} finally {
  await browser.close().catch(() => {});
  await hostA.stop().catch(() => {}); await hostB.stop().catch(() => {});
  await relay.stop?.().catch?.(() => {});
  await web.stop();
}
console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
