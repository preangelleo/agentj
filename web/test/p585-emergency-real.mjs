// #585 「紧急修复」做彻底 — real host (agentj serve) + local Noise relay + real Chromium page, stand-in harness subprocesses.
// Reproduces the stuck sessions of 11-07 and taps the real buttons:
//   A  Codex / Claude turn that never ends (fakecodex / fakeclaude HANG — Stella, Olares)   → 一键修复 recovers, the probe answers
//   B  「全部停下」 already on + stuck (Stella)                                           → 接管 says why + 「恢复」 → goes on by itself
//   C  an approval card nobody can answer (lost during compaction / a sub-thread's card)   → 一键修复 withdraws it and recovers
//   D  single buttons: 停止 · 重启 Agent · 换个新对话 · 重启服务 (not a service here: says so)
// Phone 390×844 and desktop 1440×900. Before / after screenshots → .jarvis/out/p585-emergency/ (AJ_P585_SHOTS).
// No real model, account, customer machine or the owner's systemd units are used (the restart manager is never called).
import { mkdtempSync, mkdirSync, writeFileSync, chmodSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { HostProc } from '../../tests/lib/host.mjs';
import { launchBrowser } from '../../tests/lib/browser.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const SHOTS = process.env.AJ_P585_SHOTS || join(HERE, '../../../.jarvis/out/p585-emergency');
mkdirSync(SHOTS, { recursive: true });
const ONLY = (process.env.AJ_P585_ONLY || '').split(',').filter(Boolean);
let failed = 0; const results = [];
const check = (ok, msg) => { if (!ok) failed++; results.push({ ok: !!ok, msg }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const WEB = web.url.replace('127.0.0.1', 'localhost').replace(/\/$/, '');
const { startLocalRelay } = await import('../../relay/test/local_relay.mjs');
const relay = await startLocalRelay({});
const Q = `?relay=${encodeURIComponent(relay.url)}&lang=zh`;
const browser = await launchBrowser();

function mkHost(kind) {
  const tmp = mkdtempSync(join(tmpdir(), `aj-p585-${kind}-`));
  const work = join(tmp, 'work'); mkdirSync(work);
  const fake = { codex: 'fakecodex.py', claude: 'fakeclaude.py' }[kind];
  writeFileSync(join(tmp, kind), `#!/bin/sh\nexec python3 ${join(HERE, '../../host/tests', fake)} "$@"\n`); chmodSync(join(tmp, kind), 0o700);
  const log = join(work, '.fake.jsonl');
  const env = kind === 'codex' ? { AGENTJ_CODEX_BIN: join(tmp, kind), FAKE_CX_LOG: log } : { AGENTJ_CLAUDE_BIN: join(tmp, kind), FAKE_CLAUDE_LOG: log };
  const h = new HostProc({ relay: relay.url, web: web.url, env: { ...env, AGENTJ_TEST_PASSKEY_ORIGIN: 'loopback', AGENTJ_SERVICE_NAME: 'p585e2e-never-a-unit' } }).init();   // the owner's agentj.service is never looked up
  h.run(['agent', kind, '--dir', work]);
  h.kind = kind; h.log = log; h.work = work;
  return h;
}
async function phone(w) {
  const h = w === 390 ? 844 : 900;
  const p = await browser.newPage(w, h);
  await p.send('WebAuthn.enable', { enableUI: false });
  await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  return p;
}
const visible = (sel) => `(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !!e && !e.hidden && !e.closest('[hidden]') && e.getClientRects().length > 0; })()`;
async function sheets(p) {
  for (let i = 0; i < 3; i++) {
    if (!(await p.waitFor(visible('#confirm'), i ? 3000 : 8000).then(() => true, () => false))) break;
    await p.click('#confirm-yes'); await sleep(2500);
  }
  await p.eval(`document.getElementById('a2hs-ok')?.click(); document.getElementById('phone-env-dismiss')?.click(); true`);
}
async function pair(p, h) {
  const pr = await h.pair();
  await p.goto(WEB + '/' + Q + pr.link.slice(pr.link.indexOf('#')));
  await p.waitState('awaiting-approval', 20000);
  const sas = (await p.eval(`document.querySelector('#sas').textContent`)).replace(/\D/g, '');
  await pr.waitPrompt(); pr.typeCode(sas);
  const r = await pr.exited;
  await p.waitState('ready', 25000);
  await sheets(p);
  return r.code === 0;
}
const agentStatus = (h) => [...h.events].reverse().find((e) => e.ev === 'agent_status')?.s;
async function say(p, text) {
  await p.eval(`(() => { const i = document.getElementById('input'); i.focus(); i.value = ${JSON.stringify(text)}; i.dispatchEvent(new Event('input', {bubbles:true})); return true; })()`);
  await sleep(150); await p.click('#send');
}
async function toChat(p) {
  await p.eval(`(() => { const b = [...document.querySelectorAll('[id$="-chat"]')].find((x) => x.offsetParent); if (b) { b.click(); return true; }
    document.getElementById('setClose')?.click(); document.querySelector('#settings .sheet-x, #set-close')?.click(); return true; })()`);
  await sleep(800);
}
async function toRescue(p) {
  if (!(await p.eval(visible('#set-s-rescue')))) { await p.click('#setBtn'); await sleep(600); }
  await p.eval(`document.querySelector('[data-set-jump="set-s-rescue"]')?.click(); true`); await sleep(500);
}
const shot = async (p, name, sel) => {
  if (sel) await p.eval(`document.querySelector(${JSON.stringify(sel)})?.scrollIntoView({block:'start'}); true`);
  await sleep(300); await p.shot(join(SHOTS, name + '.png'));
};
const state = (p, id) => p.eval(`document.getElementById(${JSON.stringify(id)})?.dataset.state`);
async function tap(p, id, confirm = true) {
  await p.eval(`document.getElementById(${JSON.stringify(id)}).scrollIntoView({block:'center'}); true`);
  await p.click('#' + id);
  if (confirm) { await p.waitFor(visible('#confirm'), 5000); await p.click('#confirm-yes'); }
}
const waitEnd = (p, id, ms = 150000) => p.waitFor(`['ok','fail'].includes(document.getElementById(${JSON.stringify(id)})?.dataset.state)`, ms);
const cardText = (p, id) => p.eval(`document.getElementById(${JSON.stringify(id)})?.innerText || ''`);
const chatHas = (p, s) => p.eval(`document.body.innerText.includes(${JSON.stringify(s)})`);
const fakeLog = (h) => existsSync(h.log) ? readFileSync(h.log, 'utf8') : '';
const probeSeen = (h) => fakeLog(h).includes('紧急修复') || fakeLog(h).includes('\\u7d27\\u6025\\u4fee\\u590d');

async function hang(h, p) {
  for (let i = 0; i < 3; i++) {             // the composer may still be settling right after pairing: resend until received
    const n0 = h.events.filter((e) => e.ev === 'msg').length;
    await sheets(p); await say(p, 'HANG');
    if (await h.until(() => h.events.filter((e) => e.ev === 'msg').length > n0, 10000, 'msg').then(() => true, () => false)) break;
  }
  await h.until(() => agentStatus(h) === 'working', 30000, 'agent working');
  await sleep(1500);
}

async function scenarioA(kind, w) {
  const h = mkHost(kind); await h.serve();
  const p = await phone(w);
  try {
    check(await pair(p, h), `${kind} ${w}: paired`);
    await hang(h, p);
    await shot(p, `A-${kind}-${w}-1-before-chat`);
    await toRescue(p); await shot(p, `A-${kind}-${w}-2-before-rescue`, '#set-s-rescue');
    const t0 = Date.now();
    await tap(p, 'rs-repair-go');
    await waitEnd(p, 'rs-repair');
    const txt = await cardText(p, 'rs-repair');
    await shot(p, `A-${kind}-${w}-3-after-rescue`, '#rs-repair');
    check(await state(p, 'rs-repair') === 'ok', `${kind} ${w}: 一键修复 ok in ${Math.round((Date.now() - t0) / 1000)} s — ${txt.replace(/\s+/g, ' ').slice(0, 160)}`);
    check(/停止 ✓/.test(txt) && /Agent 已经回话/.test(txt), `${kind} ${w}: the card shows the step taken and the proof (Agent answered)`);
    await toChat(p); await sleep(800);
    check(probeSeen(h) || await chatHas(p, '连通测试'), `${kind} ${w}: a real probe line reached the ${kind} process (its answer echoes it)`);
    check(await chatHas(p, 'ECHO') || await chatHas(p, '我在'), `${kind} ${w}: the Agent's answer is in the chat`);
    await shot(p, `A-${kind}-${w}-4-after-chat`);
    // the conversation goes on
    await say(p, 'after repair'); await p.waitFor(`document.body.innerText.includes('ECHO: after repair')`, 30000).then(() => check(true, `${kind} ${w}: next message answered`), () => check(false, `${kind} ${w}: next message answered`));
  } finally { await p.close(); await h.stop(); }
}

async function scenarioB(w) {   // Stella: Stop everything on, still stuck; takeover used to fail silently three times
  const h = mkHost('codex'); await h.serve();
  const p = await phone(w);
  try {
    check(await pair(p, h), `B ${w}: paired`);
    await hang(h, p);
    await p.click('#setBtn'); await sleep(500);
    await p.eval(`document.getElementById('set-estop').scrollIntoView({block:'center'}); document.getElementById('set-estop').click(); true`);
    await p.waitFor(visible('#confirm'), 5000); await p.click('#confirm-yes');
    await h.until(() => h.events.find((e) => e.ev === 'estop' && e.on), 15000, 'estop on');
    await sleep(800);
    await toRescue(p); await shot(p, `B-codex-${w}-1-before-estop-on`, '#rs-reset');
    await tap(p, 'rs-takeover-go');
    await waitEnd(p, 'rs-takeover', 60000);
    const t1 = await cardText(p, 'rs-takeover');
    await shot(p, `B-codex-${w}-2-takeover-says-why`, '#rs-takeover');
    check(await state(p, 'rs-takeover') === 'fail' && /全部停下/.test(t1), `B ${w}: takeover says the real reason (Stop everything) — ${t1.replace(/\s+/g, ' ').slice(0, 120)}`);
    check(await p.eval(visible('#rs-takeover-resume')), `B ${w}: a one-tap 「恢复」 button is right there`);
    await p.click('#rs-takeover-resume');
    await p.waitFor(visible('#confirm'), 5000); await p.click('#confirm-yes');
    await h.until(() => h.events.find((e) => e.ev === 'estop' && e.on === false), 30000, 'estop off');
    await p.waitFor(`document.getElementById('rs-takeover')?.dataset.state === 'ok'`, 120000).then(() => {}, () => {});
    const t2 = await cardText(p, 'rs-takeover');
    await shot(p, `B-codex-${w}-3-after-resume-takeover`, '#rs-takeover');
    check(await state(p, 'rs-takeover') === 'ok' && /Agent 已经回话/.test(t2), `B ${w}: after 「恢复」 the takeover went on by itself and the Agent answered — ${t2.replace(/\s+/g, ' ').slice(0, 140)}`);
  } finally { await p.close(); await h.stop(); }
}

async function scenarioB2(w) {   // Stella with 一键修复: Stop everything on + stuck → stops cleanly → 「恢复」 → the ladder goes on by itself
  const h = mkHost('codex'); await h.serve();
  const p = await phone(w);
  try {
    check(await pair(p, h), `B2 ${w}: paired`);
    await hang(h, p);
    await p.click('#setBtn'); await sleep(500);
    await p.eval(`document.getElementById('set-estop').scrollIntoView({block:'center'}); document.getElementById('set-estop').click(); true`);
    await p.waitFor(visible('#confirm'), 5000); await p.click('#confirm-yes');
    await h.until(() => h.events.find((e) => e.ev === 'estop' && e.on), 15000, 'estop on');
    await sleep(800); await toRescue(p);
    await tap(p, 'rs-repair-go');
    await waitEnd(p, 'rs-repair', 60000);
    const t1 = await cardText(p, 'rs-repair');
    await shot(p, `B2-codex-${w}-1-repair-asks-resume`, '#rs-repair');
    check(await state(p, 'rs-repair') === 'fail' && /全部停下/.test(t1) && await p.eval(visible('#rs-repair-resume')),
      `B2 ${w}: with Stop everything on, 一键修复 stops cleanly and asks for 「恢复」 (never lifts it itself) — ${t1.replace(/\s+/g, ' ').slice(0, 140)}`);
    await p.click('#rs-repair-resume');
    await p.waitFor(visible('#confirm'), 5000); await p.click('#confirm-yes');
    await p.waitFor(`document.getElementById('rs-repair')?.dataset.state === 'ok'`, 150000).then(() => {}, () => {});
    const t2 = await cardText(p, 'rs-repair');
    await shot(p, `B2-codex-${w}-2-after-resume-repaired`, '#rs-repair');
    check(await state(p, 'rs-repair') === 'ok' && /Agent 已经回话/.test(t2), `B2 ${w}: after 「恢复」 一键修复 went on by itself and the Agent answered — ${t2.replace(/\s+/g, ' ').slice(0, 140)}`);
  } finally { await p.close(); await h.stop(); }
}

async function scenarioC(w) {   // an approval card that never gets an answer, the turn waits on it
  const h = mkHost('codex'); await h.serve();
  const p = await phone(w);
  try {
    check(await pair(p, h), `C ${w}: paired`);
    await say(p, 'RUN: touch p585-card.txt');
    await h.until(() => h.events.find((e) => e.ev === 'ask' || e.ev === 'approval' || (e.ev === 'agent_status' && e.s === 'waiting')), 30000, 'card').catch(() => null);
    await sleep(1500);
    await shot(p, `C-codex-${w}-1-before-card-waiting`);
    await toRescue(p);
    await tap(p, 'rs-repair-go');
    await waitEnd(p, 'rs-repair');
    const txt = await cardText(p, 'rs-repair');
    await shot(p, `C-codex-${w}-2-after-rescue`, '#rs-repair');
    check(await state(p, 'rs-repair') === 'ok' && /撤下/.test(txt), `C ${w}: 一键修复 withdrew the waiting card and the Agent answered — ${txt.replace(/\s+/g, ' ').slice(0, 160)}`);
    check(!existsSync(join(h.work, 'p585-card.txt')), `C ${w}: the unanswered command did not run (withdrawn = deny)`);
  } finally { await p.close(); await h.stop(); }
}

async function scenarioD(kind, w) {   // single buttons
  const h = mkHost(kind); await h.serve();
  const p = await phone(w);
  try {
    check(await pair(p, h), `D ${kind} ${w}: paired`);
    for (const [id, label, re] of [['rs-stop', '停止', /确认都不在了|已停下/], ['rs-session', '重启 Agent', /Agent 已经回话/], ['rs-newchat', '换个新对话', /Agent 已经回话/]]) {
      await toChat(p); await hang(h, p); await toRescue(p);
      if (id === 'rs-stop') await shot(p, `D-${kind}-${w}-${id}-before`, '#' + id);
      await tap(p, id + '-go');
      await waitEnd(p, id);
      const txt = await cardText(p, id);
      await shot(p, `D-${kind}-${w}-${id}-after`, '#' + id);
      check(await state(p, id) === 'ok' && re.test(txt), `D ${kind} ${w}: 「${label}」 ok with proof — ${txt.replace(/\s+/g, ' ').slice(0, 150)}`);
    }
    await tap(p, 'rs-restart-go');
    await waitEnd(p, 'rs-restart', 60000);
    const txt = await cardText(p, 'rs-restart');
    await shot(p, `D-${kind}-${w}-rs-restart-after`, '#rs-restart');
    check(await state(p, 'rs-restart') === 'fail' && /不是作为后台服务运行/.test(txt) && await p.eval(visible('#rs-restart-next')),
      `D ${kind} ${w}: 「重启服务」 on a host that is not a service says so and offers the next step — ${txt.replace(/\s+/g, ' ').slice(0, 120)}`);
  } finally { await p.close(); await h.stop(); }
}

try {
  const run = (k) => !ONLY.length || ONLY.includes(k);
  for (const w of [390, 1440]) {
    if (run('A')) { await scenarioA('codex', w); await scenarioA('claude', w); }
    if (run('B')) { await scenarioB(w); await scenarioB2(w); }
    if (run('C')) await scenarioC(w);
    if (run('D')) { await scenarioD('codex', w); if (w === 390) await scenarioD('claude', w); }
  }
} catch (e) { failed++; console.log('FAIL  crashed: ' + (e && e.stack || e)); }
finally {
  writeFileSync(join(SHOTS, 'real-e2e.json'), JSON.stringify({ when: new Date().toISOString(), failed, results }, null, 2));
  await browser.close(); await relay.stop?.(); await web.stop();
}
console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
