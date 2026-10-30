#!/usr/bin/env node
// P570c #1 (董事长 10-29 18:2x 「迁移卡改成普通确认按钮，不扫 Face ID」): the move card on the real page, as serve sends it
// (serve._ask_msg for sysmigrate._card: tool "agentj tasks migrate", no passkey_required, cat []).
// Summaries = sysmigrate.card_text(...) via $AJ_P570C_CARDS (JSON {zh-2, zh-12, en-2, en-12}); asserts: no Face ID button,
// the plain allow / deny pair, the cost sentence kept. Shots zh/en × light/dark × 2 / 12 tasks × 390 → agentjarvis/reports/qa/p570c/.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P570C_OUT || (process.env.AJ_SHOTS ? process.env.AJ_SHOTS + '/' : ROOT + 'reports/qa/p570c/');
const CARDS = process.env.AJ_P570C_CARDS ? JSON.parse(readFileSync(process.env.AJ_P570C_CARDS, 'utf8')) : JSON.parse(execFileSync(process.env.AJ_PYTHON || 'python3', ['-c', `
import sys,json
sys.path.insert(0,'agentjarvis/host')
from agentj.sysmigrate import card_text
print(json.dumps({f'{lang}-{n}':card_text([('每日简报、邮件整理、每周数据汇总' if lang=='zh' else 'Daily brief, inbox tidy-up, weekly data summary')+str(i) for i in range(n)],lang) for lang in ('zh','en') for n in (2,12)},ensure_ascii=False))
`], { cwd: ROOT, encoding: 'utf8' }));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
try {
  for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const n of [2, 12]) {
    const tag = `card-${lang}-${scheme}-${n}`;
    try {
      fake.reset();
      fake.st.prefs = { appearance: { language: lang, theme: scheme } };
      const p = await newPage(B, 390, 844, scheme, { allow: [web.url, fake.relay + '/'], touch: true });
      await p.send('Page.addScriptToEvaluateOnNewDocument', { source: "try { localStorage.setItem('aj.a2hs', '1') } catch {}" });
      await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'waiting', kind: 'permission', agent: 'claude', name: 'Agent J' });
      await evaluate(p, `document.documentElement.dataset.theme='${scheme}';for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click()`);
      await fake.send({ t: 'ask', id: (n === 2 ? 'a' : 'b').repeat(32), tool: 'agentj tasks migrate', summary: CARDS[`${lang}-${n}`], ttl: 300, cat: [], why: '' });
      await waitFor(p, `!!document.getElementById('apprAllow') && document.getElementById('apprAllow').getClientRects().length>0`);
      await sleep(300);
      const g = await evaluate(p, `(()=>{const v=e=>!!e&&!e.hidden&&e.getClientRects().length>0;const a=document.getElementById('apprAllow'),d=document.getElementById('apprDeny'),pk=document.getElementById('apprPk');
        return {allow:a.textContent.trim(),deny:d&&d.textContent.trim(),allowV:v(a),denyV:v(d),pk:v(pk),text:document.getElementById('sheet').innerText}})()`);
      assert.ok(g.allowV && g.denyV, 'the plain allow / deny pair is shown');
      assert.equal(g.pk, false, 'no Face ID / passkey block on this card');
      assert.doesNotMatch(g.allow, /Face ID|通行密钥|passkey/i, 'the allow button is a plain confirm');
      assert.ok(g.text.includes(lang === 'zh' ? '花钱、删数据等' : 'spending, deleting data'), 'the cost sentence is kept');
      assert.ok(g.text.includes(lang === 'zh' ? 'Agent J 主机自己的系统定时' : "Agent J host's own scheduler"));
      console.log(`ok ${tag}: [${g.allow}] [${g.deny}]`);
      writeFileSync(`${OUT}${tag}-390.png`, await shoot(p, '', { full: true }));
      await evaluate(p, `[...document.querySelectorAll('#sheet button, #sheet a')].find(b=>/展开全部|Show all/.test(b.textContent))?.click()`);
      await sleep(300);
      const shown = await evaluate(p, `document.getElementById('sheet').innerText`);
      assert.ok(shown.includes(lang === 'zh' ? '花钱、删数据等' : 'spending, deleting data'), 'expanded: the cost sentence is on screen');
      writeFileSync(`${OUT}${tag}-open-390.png`, await shoot(p, '', { full: true }));
      await p.dispose();
    } catch (e) { failed++; console.log(`FAIL ${tag}: ${e.message}`); }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
console.log(failed ? `p570c_card: ${failed} FAILED` : 'p570c_card: all passed');
process.exit(failed ? 1 : 0);
