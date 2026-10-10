#!/usr/bin/env node
// 0.18 long tasks (P113, ADR-A193, PROTOCOL §20) · the opening card and 「我的 Agent 会什么」 against the fake host, in an
// independent headless Chromium (never :9222). node web/test/p113.mjs → parity cases + reports/qa/p113/*.png
// Cases: zh/en × 360/390 — the card shows goal, deliverables, plan, the owner's choices (≥ 56 px touch targets, real taps),
// what still needs setup (no secret field anywhere), optional items folded, what 「确认并开始」 confirms; the submit is signed
// by this device: card answer (agentj.preflight.v1 over the exact picks), brief (agentj.brief.v1) and the schedule's task_on
// control — each verified here in Node like the host does; nothing overflows horizontally; 「返回修改」 is a signed cancel;
// a refusal (changed brief) closes the card with one line; the capability sheet filters ready / needs setup and expands.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, rect, tap } from './browser.mjs';
import { preflightBytes, briefBytes, controlMessage, unb64u } from '../../protocol/wire.js';

const OUT = fileURLToPath(new URL('../../../reports/qa/p113/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const rec = parityRecorder('web/test/p113.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const subtle = webcrypto.subtle;
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function paired(lang, w) {
  fake.reset();
  const p = await newPage(B, w, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  return p;
}
function card(lang) {
  const id = hex(16);
  const zh = lang === 'zh';
  const pf = { schema_version: 1, card_id: id, task_id: 'competitor-brief', revision: 1, brief_digest: 'b'.repeat(64), registry_revision: 3,
    nonce: hex(16), expires_at: new Date(Date.now() + 1800e3).toISOString().replace(/\.\d+Z$/, 'Z'), status: 'needs_input',
    required: [
      { id: 'competitors', capability_id: 'ceo-competitor-brief', why: { zh: '要关注哪三家竞品', en: 'Which three competitors' }, how: { zh: '选三家', en: 'Pick three' },
        reuse: { zh: '以后每周沿用', en: 'Reused every week' }, status: 'missing', action_kind: 'choose' },
      { id: 'cap-phone-delivery', capability_id: 'phone-delivery', why: { zh: '需要：主人的已配对手机', en: "Needed: the owner's paired phone" },
        how: { zh: '我会自动检查；没通过会告诉你下一步', en: 'I check it automatically and tell you the next step if it fails' },
        reuse: { zh: '配一次，以后同类任务自动复用', en: 'Set up once, reused by similar tasks' }, status: 'unverified', action_kind: 'verify' }],
    optional: [{ id: 'cap-cred-research-api-key', capability_id: 'cred-research-api-key', why: { zh: '可选：调研服务，没有也能完成', en: 'Optional: research service; works without it' },
      how: { zh: '用手机上的专用密钥卡填一次，Agent 看不到值', en: "Fill in once on the phone's secret card; the Agent never sees the value" },
      reuse: { zh: '同服务任务可复用', en: 'Reusable for this service' }, status: 'missing', action_kind: 'secret_card' }] };
  const brief = { goal: zh ? '每周一 09:00 生成三家竞品的公开资料简报，存在电脑上并推到你手机。' : 'Every Monday 09:00, a public-sources brief on three competitors, saved on the computer and sent to your phone.',
    acceptance: [zh ? '三家各有至少两条可追溯来源' : 'Each competitor has two traceable sources'], red_lines: [zh ? '不付费、不外发' : 'No payments, nothing sent outside'],
    deliverables: [{ path: 'reports/weekly.md', format: 'markdown', audience: 'owner' }],
    plan: { kind: 'weekly', schedule_task_ref: 'competitor-brief/task.json', timezone: 'Asia/Tokyo', human_enable_required: true },
    workflow: 'competitor-brief', title: { zh: '每周竞品简报', en: 'Weekly competitor brief' }, schedule: '0 9 * * 1', tz: 'Asia/Tokyo',
    scope_digest: 'd'.repeat(64), confirm: true };
  return { t: 'lt_card', kind: 'capability_preflight', id, card: pf, brief, ttl: 1800,
    asks: { competitors: { type: 'multi', options: ['Atlas', 'Beacon', 'Cedar', 'Delta'] } }, enable: { id: 'competitor-brief', tsha: 'e'.repeat(64) } };
}
const answers = () => fake.log.filter((m) => m.t === 'lt_answer');
async function verify(sig, bytes) {
  const dev = fake.devs[0];
  const key = await subtle.importKey('raw', unb64u(dev.sk), { name: 'Ed25519' }, false, ['verify']);
  return subtle.verify({ name: 'Ed25519' }, key, unb64u(sig), bytes);
}
async function layout(p) {
  return ev(p, `(() => { const box = document.querySelector('#lt-box') || document.querySelector('#lt-caps-box');
    const small = [...document.querySelectorAll('.lt:not([hidden]) button, .lt:not([hidden]) summary')].filter((b) => b.offsetParent && b.getBoundingClientRect().height < 56).map((b) => b.className + ':' + b.textContent);
    return { overflow: box.scrollWidth > box.clientWidth + 1 || document.documentElement.scrollWidth > innerWidth + 1, small,
      pw: document.querySelectorAll('.lt input[type=password]').length }; })()`);
}
async function tapSel(p, sel) {
  await ev(p, `document.querySelector(${JSON.stringify(sel)}).scrollIntoView({ block: 'center' })`);
  await sleep(120);
  const r = await rect(p, sel);
  await tap(p, r.x, r.y);
  await sleep(80);
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}

try {
  for (const lang of ['zh', 'en']) {
    for (const w of [360, 390]) {
      await C(`p113-card-${lang}-${w}`, async () => {
        const p = await paired(lang, w);
        try {
          const m = card(lang);
          await fake.send(m);
          await waitFor(p, `!!document.getElementById('lt') && !document.getElementById('lt').hidden`);
          assert.equal(await ev(p, `document.getElementById('lt-title').textContent`), lang === 'zh' ? '每周竞品简报' : 'Weekly competitor brief');
          const text = await ev(p, `document.getElementById('lt-box').textContent`);
          for (const s of lang === 'zh' ? ['要关注哪三家竞品', '还要配置的', '可选项（1）', '启用定时：每周一 09:00（Asia/Tokyo）', '这不是付款'] :
            ['Which three competitors', 'Still to set up', 'Optional (1)', 'Turn on the schedule: Mondays 09:00 (Asia/Tokyo)', 'does not approve paying'])
            assert.ok(text.includes(s), s);
          let L = await layout(p);
          assert.equal(L.pw, 0, 'no secret field on the card');
          assert.equal(L.overflow, false, 'no horizontal overflow');
          assert.deepEqual(L.small, [], 'every touch target ≥ 56px');
          assert.equal(await ev(p, `document.getElementById('lt-go').disabled`), true, 'choices first');
          writeFileSync(OUT + `card-${lang}-${w}.png`, await shoot(p, 'unused'));
          for (const o of ['Atlas', 'Beacon', 'Cedar']) await tapSel(p, `.lt-opt:nth-of-type(${['Atlas', 'Beacon', 'Cedar', 'Delta'].indexOf(o) + 1})`);
          assert.deepEqual(await ev(p, `[...document.querySelectorAll('#lt .lt-opt[aria-pressed=true]')].map((b) => b.textContent)`), ['Atlas', 'Beacon', 'Cedar']);
          assert.equal(await ev(p, `document.getElementById('lt-go').disabled`), false);
          await ev(p, `document.querySelector('.lt-full').open = true; document.querySelector('.lt-opt-sec').open = true; true`);
          L = await layout(p);
          assert.equal(L.overflow, false);
          writeFileSync(OUT + `card-picked-${lang}-${w}.png`, await shoot(p, 'unused'));
          await tapSel(p, '#lt-go');
          for (let i = 0; i < 100 && !answers().length; i++) await sleep(50);
          const a = answers()[0];
          assert.ok(a, 'sent');
          assert.equal(a.action, 'submit');
          assert.deepEqual(a.picks, { competitors: ['Atlas', 'Beacon', 'Cedar'] });
          const dev = fake.devs[0].id;
          assert.ok(await verify(a.sig, preflightBytes(fake.channel, dev, m.card, 'submit', a.picks)), 'card signature over the exact picks');
          assert.ok(!(await verify(a.sig, preflightBytes(fake.channel, dev, m.card, 'submit', { competitors: ['Atlas'] }))), 'bound to the picks');
          assert.ok(await verify(a.bsig, briefBytes(fake.channel, dev, m.card, m.brief.scope_digest, 'submit')), 'brief signature');
          assert.ok(await verify(a.en.sig, await controlMessage(fake.channel, dev, 'task_on', a.en.n, a.en.ts, m.enable)), 'schedule enable = task_on control');
          await fake.send({ t: 'lt_done', id: m.id, status: 'ready', missing: [], enabled: true });
          await fake.send({ t: 'lt_res', id: m.id, ok: true, status: 'ready', enabled: true });
          await waitFor(p, `document.getElementById('lt').hidden`);
          assert.equal(p.problems.length, 0, p.problems.join('\n'));
        } finally { await p.dispose(); }
      });
    }
  }

  await C('p113-card-back-is-signed-cancel', async () => {
    const p = await paired('zh', 390);
    try {
      const m = card('zh');
      await fake.send(m);
      await waitFor(p, `!!document.getElementById('lt') && !document.getElementById('lt').hidden`);
      await tapSel(p, '#lt-back');
      for (let i = 0; i < 100 && !answers().length; i++) await sleep(50);
      const a = answers()[0];
      assert.equal(a.action, 'cancel');
      assert.ok(!('picks' in a) && !('bsig' in a) && !('en' in a), 'cancel signs nothing else');
      assert.ok(await verify(a.sig, preflightBytes(fake.channel, fake.devs[0].id, m.card, 'cancel', null)));
      await fake.send({ t: 'lt_done', id: m.id, status: 'cancelled' });
      await waitFor(p, `document.getElementById('lt').hidden`);
    } finally { await p.dispose(); }
  });

  await C('p113-card-refused-changed', async () => {
    const p = await paired('en', 360);
    try {
      const m = card('en');
      await fake.send(m);
      await waitFor(p, `!!document.getElementById('lt') && !document.getElementById('lt').hidden`);
      await fake.send({ t: 'lt_res', id: m.id, ok: false, why: 'changed' });
      await waitFor(p, `document.getElementById('lt').hidden`);
      await waitFor(p, `document.getElementById('toast').textContent.includes('The brief changed')`);
    } finally { await p.dispose(); }
  });

  for (const lang of ['zh', 'en']) {
    await C(`p113-caps-${lang}`, async () => {
      const p = await paired(lang, 360);
      try {
        const now = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
        const items = [
          { id: 'web-public', kind: 'browser', title: { zh: '公开网页读取', en: 'Public web reading' }, status: 'ready', provides: ['web.read'], verified: now, expires: now },
          { id: 'ceo-competitor-brief', kind: 'ceo', title: { zh: '每周竞品简报', en: 'Weekly competitor brief' }, status: 'ready', provides: ['workflow.competitor-brief'], verified: now, expires: now },
          { id: 'cred-research-api-key', kind: 'credential', title: { zh: '凭据 RESEARCH_API_KEY', en: 'Credential RESEARCH_API_KEY' }, status: 'missing', provides: ['credential.x'], verified: null, expires: null },
          { id: 'mcp-sources', kind: 'mcp', title: { zh: '连接器 sources', en: 'Connector sources' }, status: 'unverified', provides: ['mcp.sources'], verified: null, expires: null }];
        await fake.send({ t: 'lt_caps', kind: 'capability_snapshot', revision: 4, items });
        await waitFor(p, `!!document.getElementById('lt-caps') && !document.getElementById('lt-caps').hidden`);
        assert.equal(await ev(p, `document.querySelectorAll('#lt-caps .lt-cap').length`), 4);
        const L = await layout(p);
        assert.equal(L.overflow, false);
        assert.deepEqual(L.small, []);
        writeFileSync(OUT + `caps-${lang}-360.png`, await shoot(p, 'unused'));
        await tapSel(p, '#lt-caps .lt-filter .lt-opt:nth-of-type(3)');
        assert.equal(await ev(p, `document.querySelectorAll('#lt-caps .lt-cap').length`), 2, 'needs setup only');
        await tapSel(p, '#lt-caps .lt-filter .lt-opt:nth-of-type(2)');
        assert.equal(await ev(p, `document.querySelectorAll('#lt-caps .lt-cap').length`), 2, 'ready only');
        await tapSel(p, '#lt-caps .lt-cap summary');
        assert.equal(await ev(p, `document.querySelector('#lt-caps .lt-cap').open`), true, 'a tap explains the item');
        writeFileSync(OUT + `caps-open-${lang}-360.png`, await shoot(p, 'unused'));
        await tapSel(p, '#lt-caps .lt-go');
        assert.equal(await ev(p, `document.getElementById('lt-caps').hidden`), true);
        assert.equal(p.problems.length, 0, p.problems.join('\n'));
      } finally { await p.dispose(); }
    });
  }
} finally {
  rec.write();
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `✗ ${failed} failed` : '✓ all P113 long-task card cases passed');
process.exit(failed ? 1 : 0);
