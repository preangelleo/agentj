#!/usr/bin/env node
// P128: the shared-mode credential approval card speaks ONE language (the page's), and says 查看 for a show, 改 for a change.
// The real web client against the fake host, independent headless Chromium (never :9222), 390×844. For zh and en: a "read"
// card and a "change" card; the visible card text (tags, title, summary, reason) must not contain the other language.
//   node web/test/p128.mjs   → reports/qa/p128/card-<lang>-<kind>.png
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p128/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
// what serve sends (serve.CARD_TEXT / danger.classify_shared), per host language
const CARDS = {
  zh: {
    read: { summary: 'Bash · 查看密码或密钥（内容会显示在电脑屏幕上）。\n具体内容只在电脑上看，凭据不会发到手机。\n本回合内同类高危动作一并批准。', why: '把凭据文件 .env 的内容显示到屏幕上', tag: '查看密码或密钥' },
    change: { summary: 'Bash · 改密码或密钥。\n具体内容只在电脑上看，凭据不会发到手机。\n本回合内同类高危动作一并批准。', why: '写入凭据文件 credentials', tag: '改密码或密钥' },
  },
  en: {
    read: { summary: 'Bash · Shows passwords or keys (on the computer\'s screen).\nCheck the exact details on the computer; credentials never go to the phone.\nAlso approves this kind of high-risk action for the rest of this turn.', why: '把凭据文件 .env 的内容显示到屏幕上', tag: 'Shows passwords or keys' },
    change: { summary: 'Bash · Changes passwords or keys.\nCheck the exact details on the computer; credentials never go to the phone.\nAlso approves this kind of high-risk action for the rest of this turn.', why: '写入凭据文件 credentials', tag: 'Changes passwords or keys' },
  },
};
const WHY_EN = { read: 'Shows credential contents on screen', change: 'Writes a credential file' };
const CJK = /[一-鿿]/;

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const fails = [];
try {
  for (const lang of ['zh', 'en']) {
    for (const kind of ['read', 'change']) {
      fake.reset();
      const p = await newPage(B, 390, 844, 'light', { allow: [web.url, fake.relay + '/'] });
      const base = web.url + (lang === 'en' ? '?lang=en' : '');
      await navigate(p, base);
      await waitState(p, 'idle');
      await navigate(p, 'about:blank');
      await navigate(p, fake.newPairing(base));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await evaluate(p, `(() => { const n = document.getElementById('confirm-no'); if (n && !document.getElementById('confirm').hidden) n.click();
        const a = document.getElementById('a2hs-ok'); if (a && !document.getElementById('a2hs').hidden) a.click(); })()`);
      await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'AgentJ' });
      const c = CARDS[lang][kind];
      await fake.ask({ id: hex(16), tool: 'Bash', summary: c.summary, ttl: 120, cat: ['credentials'], why: c.why, why_en: WHY_EN[kind], cred: kind === 'read' ? 'read' : 'change' });
      await waitFor(p, `document.body.dataset.sheet === '1' && document.getElementById('askTags').textContent.length > 0`);
      await sleep(450);
      const seen = await evaluate(p, `JSON.stringify(['askTags', 'sheetTitle', 'cmd', 'why', 'apprAllowLbl'].map((id) => document.getElementById(id).textContent))`);
      const [tags, title, cmd, why, allow] = JSON.parse(seen);
      const all = [tags, title, cmd, why, allow].join('\n');
      const { data } = await p.send('Page.captureScreenshot', { format: 'png' });
      writeFileSync(join(OUT, `card-${lang}-${kind}.png`), Buffer.from(data, 'base64'));
      const check = (ok, what) => { if (!ok) fails.push(`${lang}/${kind}: ${what} — ${JSON.stringify(all)}`); };
      check(tags.includes(c.tag), 'tag ' + c.tag);
      if (lang === 'en') check(!CJK.test(all), 'no Chinese on an English card');
      else check(!/[A-Za-z]{3,}/.test(all.replace(/\bBash\b/g, '').replace(/文件 [^的\s]+/g, '')), 'no English words on a Chinese card');
      check(!all.includes(' / '), 'no "中文 / English" concatenation');
      await p.dispose();
    }
  }
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
const rec = parityRecorder('web/test/p128.mjs');
rec.record('one-language-credential-card', !fails.length);
rec.write();
if (fails.length) { console.error('P128 FAIL\n' + fails.join('\n')); process.exit(1); }
console.log('P128 cards OK: zh/en × read/change, one language each; screenshots in', OUT);
