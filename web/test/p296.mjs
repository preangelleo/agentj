#!/usr/bin/env node
// P296: an `agentj mail send` card offers each new recipient as an UNTICKED box 「以后发给 X 不用再确认（加入白名单）」; a plain
// hold sends a plain allow (no allow_add); ticking one sends allow_add = [that address], signed with the allow (fakehost checks
// the signature with wire.mailAllowMessage, the same bytes as host approvals.allow_add_line). A Bash card shows no boxes.
// The real web client against the fake host, independent headless Chromium (never :9222), 390×844, zh and en.
//   node web/test/p296.mjs   → reports/qa/p296/card-<lang>.png
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p296/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const A = 'zs@qq.example', B2 = 'ls@163.example';
const SUMMARY = `发邮件给：张三 <${A}>、${B2}\n标题：周报\n正文：hello\n发给外人：邮箱、电话、密钥等会打码`;

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const Br = await launch();
const fails = [];
const check = (ok, what) => { if (!ok) fails.push(what); };
try {
  for (const lang of ['zh', 'en']) {
    fake.reset();
    const p = await newPage(Br, 390, 844, 'light', { allow: [web.url, fake.relay + '/'] });
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
    const box = `document.getElementById('apprMailAllow')`;
    const hold = async () => {
      const n = fake.st.answers.length;
      await evaluate(p, `document.getElementById('apprAllow').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'touch'}))`);
      await waitFor(p, `document.body.dataset.sheet==='0'`, 3000);
      for (let i = 0; i < 40 && fake.st.answers.length === n; i++) await sleep(50);
      return fake.st.answers[fake.st.answers.length - 1];
    };
    // 1. a plain hold: boxes shown unticked, the answer carries no allow_add
    let id = hex(16);
    await fake.ask({ id, tool: 'agentj mail send', summary: SUMMARY, ttl: 120, allow_offer: [A, B2] });
    await waitFor(p, `document.body.dataset.sheet === '1' && !${box}.hidden && !document.getElementById('apprBtns').hidden`);
    await sleep(300);
    const seen = JSON.parse(await evaluate(p, `JSON.stringify([...${box}.querySelectorAll('label')].map((l) => [l.textContent, l.querySelector('input').checked]))`));
    check(seen.length === 2, `${lang}: two boxes (${JSON.stringify(seen)})`);
    check(seen.every(([, on]) => on === false), `${lang}: unticked by default`);
    check(seen[0] && seen[0][0].includes(A) && seen[1][0].includes(B2), `${lang}: one per address, in order`);
    check(lang === 'zh' ? seen[0][0].includes('不用再确认') : seen[0][0].includes("Don't ask again"), `${lang}: label text`);
    const { data } = await p.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(OUT, `card-${lang}.png`), Buffer.from(data, 'base64'));
    let ans = await hold();
    check(ans && ans.id === id && ans.ok === true && !('allow_add' in ans), `${lang}: plain allow has no allow_add (${JSON.stringify(ans)})`);
    check(fake.st.sigOk[fake.st.sigOk.length - 1] === true, `${lang}: plain allow signature verifies`);
    // 2. tick the second → allow_add = [B2], signature over the extra line verifies
    id = hex(16);
    await fake.ask({ id, tool: 'agentj mail send', summary: SUMMARY, ttl: 120, allow_offer: [A, B2] });
    await waitFor(p, `document.body.dataset.sheet === '1' && !${box}.hidden && ${box}.dataset.id === '${id}'`);
    await sleep(300);
    await evaluate(p, `${box}.querySelectorAll('input')[1].click()`);
    ans = await hold();
    check(ans && ans.id === id && JSON.stringify(ans.allow_add) === JSON.stringify([B2]), `${lang}: ticked → allow_add [B2] (${JSON.stringify(ans)})`);
    check(fake.st.sigOk[fake.st.sigOk.length - 1] === true, `${lang}: allow_add signature verifies`);
    // 3. a Bash card: no boxes
    await fake.ask({ id: hex(16), tool: 'Bash', summary: 'ls -la', ttl: 120 });
    await waitFor(p, `document.body.dataset.sheet === '1' && document.getElementById('cmd').textContent === 'ls -la'`);
    check(await evaluate(p, `${box}.hidden`) === true, `${lang}: no boxes on a Bash card`);
    await p.dispose();
  }
} finally {
  await Br.close(); await fake.stop(); await web.stop();
}
const rec = parityRecorder('web/test/p296.mjs');
rec.record('mail-card-allowlist-boxes', !fails.length);
rec.write();
if (fails.length) { console.error('P296 FAIL\n' + fails.join('\n')); process.exit(1); }
console.log('P296 mail card OK: zh/en, unticked boxes, plain allow, signed allow_add, none on Bash; screenshots in', OUT);
