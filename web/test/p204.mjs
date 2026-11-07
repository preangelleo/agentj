#!/usr/bin/env node
// P204 (0.21) · spend & speed (PROTOCOL §29), the /learn proposal card (§30), lock-screen alert kinds (§9). Real page + local
// fake host, own headless Chromium (never :9222).
//   use-line         a finished page carries `use`: one folded line in the reply header (「12 s · ≈$0.04」), a tap opens the
//                    details (time / tokens / speed / model / cost), a second tap folds it; an open page and a page without
//                    `use` show nothing; a subscription page shows its quota share and never a dollar sign
//   phase            while working the header says 「执行工具中 · Bash」 / 「生成中」; idle → gone
//   load-today       the load card shows today's totals from `res.use`
//   push-kinds       Settings → 锁屏提醒 on → three switches; turning one off sends {t:"push_pref", kinds} and survives a reload
//   learn-card       a /learn proposal: tag 「技能提案 · 你确认后才写入」, no 低风险, + / − lines coloured
//   audit            no console errors / off-origin requests
//   shots            390 / 1440 × zh / en × light / dark → reports/qa/p204/screens/
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + (process.env.AJ_P204_OUT || 'reports/qa/p204/screens/');
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P204_ONLY || '').split(',').filter(Boolean);
const rec = parityRecorder('web/test/p204.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const hex = (n) => randomBytes(n).toString('hex');
const ev = (p, s) => evaluate(p, s);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
const text = (p, id) => ev(p, `(document.getElementById(${JSON.stringify(id)}) || {}).textContent || ''`);
const shown = (id) => `(() => { const e = document.getElementById(${JSON.stringify(id)}); return !!e && !e.hidden && e.getClientRects().length > 0; })()`;
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };

const USE_API = { s: 12.3, h: 'claude', b: 'api', m: 'claude-opus-5-5', i: 5200, o: 1240, cr: 15000, cw: 800, usd: 0.0412, est: true, tps: 151.2, tool_s: 4.1 };
const USE_SUB = { s: 48, h: 'claude', b: 'sub', m: 'claude-opus-5-5', i: 30, o: 900, cr: 40000, cw: 0, q: 0.4 };
const LEARN = {
  zh: '技能提案：weekly-report（新建，文档型）\n每周一按固定格式写周报\n写入 ~/.claude/skills/weekly-report\n[新] SKILL.md +5 -0 · sha256 3f2a9c1d0b7e\n\n--- /dev/null\n+++ b/SKILL.md\n@@ -0,0 +1,5 @@\n+---\n+name: weekly-report\n+description: 写周报\n+---\n+按这个格式写周报。',
  en: 'Skill proposal: weekly-report (change, document skill)\nWrites to ~/.claude/skills/weekly-report — nothing is deleted\n[~] SKILL.md +1 -1 · sha256 3f2a9c1d0b7e\n\n--- a/SKILL.md\n+++ b/SKILL.md\n@@ -3,3 +3,3 @@\n description: weekly report\n----\n-Old format.\n+New format.',
};

async function paired({ w = 390, h = 844, scheme = 'light', lang = 'zh' } = {}) {
  fake.reset();
  await fake.addTurn({ k: 'phone', dev: 'X', name: 'iPhone', text: lang === 'en' ? 'Sum up the logs' : '帮我总结一下日志' },
    lang === 'en' ? 'Done: no errors since 09:12.' : '总结好了：09:12 以后没有错误。', 'done');
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `(() => { const n = document.getElementById('confirm-no'); if (n && !document.getElementById('confirm').hidden) n.click();
    const a = document.getElementById('a2hs-ok'); if (a && !document.getElementById('a2hs').hidden) a.click(); })()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await sleep(150);
  return p;
}
async function C(name, fn) {
  if (ONLY.length && !ONLY.includes(name)) return;
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
async function withUse(p, use, end = 'done') {
  const t = await fake.addTurn({ k: 'phone', dev: 'X', name: 'iPhone', text: '再查一次' }, '查过了，一切正常。', end);
  if (use) await fake.updateTurn(t.id, { use });
  await sleep(250);
  return t;
}

try {
  await C('use-line', async () => {
    const p = await paired();
    try {
      await withUse(p, USE_API);
      await waitFor(p, shown('rmUse'));
      assert.equal((await text(p, 'rmUse')).trim(), '· 12 s · ≈$0.04');
      assert.equal(await ev(p, shown('useBox')), false, 'folded by default');
      await click(p, '#rmUse');
      await waitFor(p, shown('useBox'));
      const box = await text(p, 'useBox');
      for (const w of ['耗时', '12 s（其中执行工具 4.1 s）', '输入 5.2k · 输出 1.2k · 缓存 15.8k', '151 token/秒', 'claude-opus-5-5', '≈$0.04（按价目表估算）', '不外发'])
        assert.ok(box.includes(w), `${w} in ${box}`);
      assert.equal(await ev(p, `document.getElementById('rmUse').getAttribute('aria-expanded')`), 'true');
      await click(p, '#rmUse');
      assert.equal(await ev(p, shown('useBox')), false);
      await withUse(p, USE_SUB);
      const sub = (await text(p, 'rmUse')).trim();
      assert.equal(sub, '· 48 s · 额度 0.4%');
      await click(p, '#rmUse');
      assert.ok(!(await text(p, 'useBox')).includes('$'), 'a subscription never shows dollars');
      await click(p, '#rmUse');
      await withUse(p, { s: 33, h: 'claude' });
      assert.equal((await text(p, 'rmUse')).trim(), '· 33 s', 'no numbers reported → the time only');
      await withUse(p, null);
      assert.equal(await ev(p, shown('rmUse')), false, 'no use → nothing');
      await withUse(p, USE_API, 'open');
      assert.equal(await ev(p, shown('rmUse')), false, 'an open page has no figures yet');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('phase', async () => {
    const p = await paired();
    try {
      await fake.addTurn({ k: 'phone', dev: 'X', text: '跑一下测试' }, '', 'open');
      await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J', ph: 'tool', tool: 'Bash' });
      await waitFor(p, shown('rmPh'));
      assert.equal((await text(p, 'rmPh')).trim(), '· 执行工具中 · Bash');
      await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J', ph: 'gen' });
      await waitFor(p, `document.getElementById('rmPh').textContent.includes('生成中')`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await waitFor(p, `document.getElementById('rmPh').hidden`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J', ph: 'tool', tool: 'Bash' });
      await sleep(150);
      assert.equal(await ev(p, shown('rmPh')), false, 'a phase only counts while working');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('load-today', async () => {
    const p = await paired();
    try {
      fake.st.res = { cpu: 0.2, mem: 0.5, load: 0.3, use: { n: 3, s: 61, i: 12000, o: 3400, cr: 50000, cw: 0, usd: 0.21, est: true, q: 0, sub: 0, no_usd: 0 } };
      await ev(p, `document.getElementById('loadHit').click()`);
      await waitFor(p, `!document.getElementById('loadDlg').hidden && document.getElementById('loadUseV').textContent.includes('轮')`);
      const v = await text(p, 'loadUseV');
      assert.ok(v.includes('3 轮') && v.includes('≈$0.21') && v.includes('输出 3.4k'), v);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('push-kinds', async () => {
    const p = await paired();
    try {
      await click(p, '#setBtn');
      await ev(p, `import('./js/push.js').then((m) => m.pushUi('on'))`);
      await waitFor(p, `!document.getElementById('set-push-kinds').hidden`);
      const sw = (k) => `document.querySelector('[data-push-kind="${k}"]')`;
      assert.deepEqual(JSON.parse(await ev(p, `JSON.stringify(['ask', 'reply', 'security'].map((k) => document.querySelector('[data-push-kind="' + k + '"]').getAttribute('aria-checked')))`)), ['true', 'true', 'true']);
      const before = fake.log.length;
      await ev(p, `${sw('security')}.click()`);
      await waitFor(p, `${sw('security')}.getAttribute('aria-checked') === 'false'`);
      await sleep(200);
      const prefs = fake.log.slice(before).filter((m) => m.t === 'push_pref');
      assert.deepEqual(prefs.at(-1), { t: 'push_pref', kinds: ['ask', 'reply'] });
      assert.equal(await ev(p, `localStorage.getItem('aj.pushKinds')`), '["ask","reply"]');
      assert.ok((await text(p, 'set-push-kinds')).includes('不带任何内容'));
      await ev(p, `import('./js/push.js').then((m) => m.pushUi('offer'))`);
      await waitFor(p, `document.getElementById('set-push-kinds').hidden`);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('learn-card', async () => {
    for (const lang of ['zh', 'en']) {
      const p = await paired({ lang });
      try {
        await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'Agent J' });
        await fake.ask({ id: hex(16), tool: 'agentj learn', summary: LEARN[lang], ttl: 600, cat: [], why: '', learn: 'proposal' });
        await waitFor(p, `document.body.dataset.sheet === '1' && document.getElementById('askTags').textContent.length > 0`);
        await sleep(300);
        const tags = await text(p, 'askTags');
        assert.ok(tags.includes(lang === 'en' ? 'Skill proposal' : '技能提案'), tags);
        assert.ok(!/低风险|Low risk/.test(tags), 'no low-risk tag');
        const n = JSON.parse(await ev(p, `JSON.stringify({add: document.querySelectorAll('#cmd .dl-add').length, del: document.querySelectorAll('#cmd .dl-del').length,
          text: document.getElementById('cmd').textContent})`));
        assert.ok(n.add >= 1 && n.text === LEARN[lang], JSON.stringify(n));
        if (lang === 'en') assert.equal(n.del, 2, 'the --- header is not a removed line; ---- (a removed "---") is');
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });

  await C('shots', async () => {
    for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) {
      const p = await paired({ w, h, scheme, lang });
      try {
        const tag = `${lang}-${scheme}-${w}`;
        await withUse(p, USE_API);
        await click(p, '#rmUse');
        await sleep(150);
        writeFileSync(OUT + `use-${tag}.png`, await shoot(p, ''));
        await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J', ph: 'tool', tool: 'Bash' });
        await fake.addTurn({ k: 'phone', dev: 'X', text: lang === 'en' ? 'Run the tests' : '跑一下测试' }, '', 'open');
        await sleep(600);
        writeFileSync(OUT + `phase-${tag}.png`, await shoot(p, ''));
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
        fake.st.res = { cpu: 0.2, mem: 0.5, load: 0.3, use: { n: 7, s: 300, i: 82000, o: 9100, cr: 410000, cw: 2000, usd: 1.37, est: true, q: 2.5, sub: 2, no_usd: 0 } };
        await ev(p, `document.getElementById('loadHit').click()`);
        await sleep(500);
        writeFileSync(OUT + `load-${tag}.png`, await shoot(p, ''));
        await ev(p, `document.getElementById('loadClose').click()`);
        await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'Agent J' });
        await fake.ask({ id: hex(16), tool: 'agentj learn', summary: LEARN[lang], ttl: 600, cat: [], why: '', learn: 'proposal' });
        await sleep(700);
        writeFileSync(OUT + `learn-${tag}.png`, await shoot(p, ''));
        noProblems(p);
      } finally { await p.dispose(); }
      const q = await paired({ w, h, scheme, lang });
      try {
        await click(q, '#setBtn');
        await ev(q, `import('./js/push.js').then((m) => m.pushUi('on'))`);
        await sleep(200);
        await ev(q, `document.getElementById('set-push-kinds').scrollIntoView({block: 'center'})`);
        await sleep(200);
        writeFileSync(OUT + `push-${lang}-${scheme}-${w}.png`, await shoot(q, ''));
      } finally { await q.dispose(); }
    }
  });
} finally {
  rec.write();
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p204: ${failed} failed` : 'p204: all passed');
process.exit(failed ? 1 : 0);
