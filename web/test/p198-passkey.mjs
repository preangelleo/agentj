#!/usr/bin/env node
// P198 (F: F20 over F36, Jarvis's decision) — the side that was taken over (Safari → Home Screen app → private tab) gets
// one button, 「用 Face ID 在这里继续」, and comes back with the same passkey; no prompt, nothing else. REAL web client in
// headless Chromium with a CDP virtual platform authenticator (Face ID's stand-in), REAL host CLI, local workerd relay —
// the e2e_p57_passkey.mjs setup. Walks:
//   A pair by QR → save the passkey · B a fresh context, same passkey → Face ID → ready (A is now taken over)
//   A reopened → 「另一边接管了」 + the button (no 重新配对) → tap → ready; still 1 remote; a reload stays ready (the
//     stored taken-over state is gone)
//   B reopened → taken over too (its record came from a passkey restore) → the button, zh and en
//   E paired with 「以后再说」 → the record says it has no passkey → taken over: no button, the old words; a record from
//     before P198 (no word either way): the button, and a tap that finds no passkey says so plainly and stays
// Shots → /tmp/aj-p198/passkey/. Usage: node web/test/p198-passkey.mjs
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, chmodSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { HostProc } from '../../tests/lib/host.mjs';
import { launchBrowser } from '../../tests/lib/browser.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const SHOTS = '/tmp/aj-p198/passkey';
mkdirSync(SHOTS, { recursive: true });
let failed = 0;
const check = (ok, msg) => { if (!ok) failed++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const WEB = web.url.replace('127.0.0.1', 'localhost');
const { startLocalRelay } = await import('../../relay/test/local_relay.mjs');
const relay = await startLocalRelay({});
const FRESH = `${WEB}?relay=${encodeURIComponent(relay.url)}`;
const tmp = mkdtempSync(join(tmpdir(), 'aj-p198-'));
mkdirSync(join(tmp, 'work'));
writeFileSync(join(tmp, 'claude'), `#!/bin/sh\nexec python3 ${join(HERE, '../../host/tests/fakeclaude.py')} "$@"\n`);
chmodSync(join(tmp, 'claude'), 0o700);
const host = new HostProc({ relay: relay.url, web: web.url, env: { AGENTJ_CLAUDE_BIN: join(tmp, 'claude'), AGENTJ_TEST_PASSKEY_ORIGIN: 'loopback' } }).init();
host.run(['agent', 'claude', '--dir', join(tmp, 'work')]);
const browser = await launchBrowser();
const devFile = join(host.stateDir, 'devices.json');
const readDevs = () => JSON.parse(readFileSync(devFile, 'utf8'));
const fragOf = (link) => link.slice(link.indexOf('#'));

/** A page in its own fresh context (or `context`) with a virtual platform authenticator holding `credential` (if any). */
async function phone({ context, credential = null } = {}) {
  const p = await browser.newPage(390, 844, context ? { context } : {});
  await p.send('WebAuthn.enable', { enableUI: false });
  const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal',
    hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  p.auth = authenticatorId;
  if (credential) await p.send('WebAuthn.addCredential', { authenticatorId, credential });
  p.tap = async (sel) => {                            // a real tap: WebAuthn wants user activation
    const r = await p.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.scrollIntoView({ block: 'center' }); const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()`);
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  };
  return p;
}
const visible = (sel) => `(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !!e && !e.hidden && !e.closest('[hidden]'); })()`;
const text = (id) => `document.getElementById(${JSON.stringify(id)}).textContent`;
const synced = async (p) => (await p.send('WebAuthn.getCredentials', { authenticatorId: p.auth })).credentials[0];
// the page's own store module (same origin): read / rewrite this browser's pairing record
const hostRec = `import('/js/store.js').then((s) => s.dbGet('host'))`;
const seedRec = (patch, drop = []) => `import('/js/store.js').then(async (s) => { const h = { ...(await s.dbGet('host')), ...${JSON.stringify(patch)} };
  for (const k of ${JSON.stringify(drop)}) delete h[k]; await s.dbPut('host', h); return true; })`;

async function pair(page, url = WEB) {
  const pr = await host.pair();
  await page.goto(url + fragOf(pr.link));
  await page.waitState('awaiting-approval');
  const sas = (await page.eval(`document.querySelector('#sas').textContent`)).replace(/\D/g, '');
  await pr.waitPrompt();
  pr.typeCode(sas);
  return pr.exited;
}
async function takenOver(p, label) {
  const st = await p.waitState('revoked', 15000).catch(() => p.state());
  const title = await p.eval(text('revoked-title'));
  return { ok: st === 'revoked', title, btn: await p.eval(visible('#takeover-pk')), repair: await p.eval(visible('#repair')),
    lead: await p.eval(`document.getElementById('revoked-lead').dataset.i18n`), label };
}

try {
  await host.serve();

  // ---------------------------------------------------------------- A pairs and saves its passkey; B takes over with it
  const a1 = await phone();
  const ctxA = a1.browserContextId;
  check((await pair(a1)).code === 0, 'A: paired by QR');
  await a1.waitState('ready');
  await a1.waitFor(visible('#confirm'), 10000).catch(() => false);
  await a1.tap('#confirm-yes');
  const idA = Object.keys(readDevs())[0];
  await host.until(() => readDevs()[idA]?.pk, 15000, 'pk stored').catch(() => null);
  check(!!readDevs()[idA]?.pk, 'A: the host record holds the passkey');
  check((await a1.eval(hostRec))?.pk === true, 'A: this browser\'s record says it has one');
  let cred = await synced(a1);
  await a1.close();

  const b1 = await phone({ credential: cred });
  const ctxB = b1.browserContextId;
  await b1.goto(FRESH);
  await b1.waitFor(visible('#pk-restore'), 10000);
  await b1.tap('#pk-restore');
  check(await b1.waitState('ready', 30000).then(() => true, () => false), 'B: Face ID → ready (A is taken over)');
  check((await b1.eval(hostRec))?.pk === true, 'B: a record restored with a passkey says it has one');
  cred = await synced(b1);
  await b1.close();

  // ---------------------------------------------------------------- A reopened: taken over, one button, back with Face ID
  const a2 = await phone({ context: ctxA, credential: cred });
  await a2.goto(FRESH);                              // ?relay=: the loopback relay index exists only with it (see e2e_p57)
  let s = await takenOver(a2);
  check(s.ok && s.title === '另一边接管了' && s.btn && !s.repair && s.lead === 'revoked.takeoverLead',
    `A reopened → 「${s.title}」, button ${s.btn}, 重新配对 ${s.repair}, lead ${s.lead}`);
  check(await a2.eval(text('takeover-pk')) === '用 Face ID 在这里继续', 'A: the button says 「用 Face ID 在这里继续」');
  check(await a2.eval(`document.getElementById('confirm').hidden`), 'A: no prompt or sheet, only the button');
  await a2.shot(join(SHOTS, 'A-taken-over-zh.png'));
  await a2.tap('#takeover-pk');
  const aReady = await a2.waitState('ready', 30000).then(() => true, () => false);
  if (!aReady) console.log('note  A: state=' + await a2.state() + ' view=' + await a2.eval('document.body.dataset.view') + ' pkErr=' + await a2.eval(`document.body.dataset.pkErr || ''`)
    + ' rec=' + JSON.stringify(await a2.eval(hostRec).catch(() => null)) + '\n' + host.log().split('\n').slice(-25).join('\n'));
  check(aReady, 'A: tap → Face ID → ready, no QR, no code');
  const devs = readDevs();
  check(Object.keys(devs).length === 1, `still exactly 1 remote (${Object.keys(devs).length})`);
  check(/"pk_restore".*"result": "ok"/.test(host.log().split('\n').slice(-40).join('\n')), 'host.log: pk_restore ok');
  const recA = await a2.eval(hostRec);
  check(recA?.approved === true && !recA.removed && recA.pk === true, 'A: the stored taken-over state is replaced by an approved pairing');
  await a2.goto(FRESH);
  check(await a2.waitState('ready', 20000).then(() => true, () => false) && await a2.eval(`document.body.dataset.view`) === 'chat',
    'A: the next launch goes straight to the chat, not the taken-over page');
  cred = await synced(a2);
  await a2.close();

  // ---------------------------------------------------------------- B reopened: now B is taken over — same button (zh, en)
  const b2 = await phone({ context: ctxB, credential: cred });
  await b2.goto(FRESH);
  s = await takenOver(b2);
  check(s.ok && s.btn && !s.repair, `B reopened → taken over, button ${s.btn}`);
  await b2.goto(FRESH + '&lang=en');
  s = await takenOver(b2);
  const enBtn = await b2.eval(text('takeover-pk')), enLead = await b2.eval(text('revoked-lead'));
  check(s.ok && s.btn && enBtn === 'Continue here with Face ID' && /Face ID/.test(enLead) && s.title === 'The other app took over',
    `B (en) → 「${s.title}」 / 「${enBtn}」`);
  await b2.shot(join(SHOTS, 'B-taken-over-en.png'));
  await b2.eval(`localStorage.setItem('aj.lang', 'zh')`);
  await b2.close();

  // ---------------------------------------------------------------- E: no passkey here → no button; an old record → plain line
  const e1 = await phone();
  check((await pair(e1)).code === 0, 'E: paired by QR');
  await e1.waitState('ready');
  await e1.waitFor(visible('#confirm'), 10000).catch(() => false);
  await e1.tap('#confirm-no');                        // 「以后再说」
  await sleep(500);
  check((await e1.eval(hostRec))?.pk === false, 'E: the Agent J host offered to save one → the record says it has none');
  await e1.eval(seedRec({ approved: false, removed: 'replaced' }));
  await e1.goto(WEB);
  s = await takenOver(e1);
  check(s.ok && !s.btn && s.lead === 'revoked.replacedLead', `E: taken over without a passkey → no button, lead ${s.lead}`);
  await e1.shot(join(SHOTS, 'E-no-passkey-zh.png'));
  await e1.eval(seedRec({}, ['pk']));                 // a record from before P198: never said
  await e1.goto(WEB);
  s = await takenOver(e1);
  check(s.ok && s.btn, 'E: an older record (unknown) → the button shows');
  await e1.tap('#takeover-pk');                       // this authenticator holds no passkey → the system refuses
  const note = await e1.waitFor(`(() => { const e = document.getElementById('takeover-note'); return !e.hidden && e.textContent; })()`, 70000).catch(() => '');
  check(note.startsWith('没能用 Face ID 验证') && await e1.state() === 'revoked' && await e1.eval(visible('#takeover-pk')),
    `E: no passkey on this phone → 「${note}」, still here, the button stays (pkErr ${await e1.eval('document.body.dataset.pkErr')})`);
  await e1.shot(join(SHOTS, 'E-none-zh.png'));
  check(Object.keys(readDevs()).length === 2, 'E: nothing changed on the Agent J host (A + E)');
  const errs = [e1].flatMap((p) => p.pageErrors());
  check(errs.length === 0, `no page errors ${errs.length ? JSON.stringify(errs.slice(0, 3)) : ''}`);
} catch (e) {
  check(false, 'exception: ' + (e.stack || e).toString().slice(0, 900));
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
  await relay.stop();
  rmSync(tmp, { recursive: true, force: true }); rmSync(host.stateDir, { recursive: true, force: true });
}
console.log(failed ? `FAILED — ${failed} failure(s); shots ${SHOTS}` : `ALL PASS — shots ${SHOTS}`);
process.exit(failed ? 1 : 0);
