// P424 (#87, owner 10-17 「我经常会连续收到你两条一模一样的消息」): the same reply on two pages — the second an input-less
// 「后台任务完成」 page (「这一页没有你的输入」) carrying the page before it word for word, then a quiet 〔不回群〕 turn. The host no
// longer writes that echo (shared.py / serve.py, test_p424_dup_reply.py); history already on disk still has it, so the phone
// folds it too: no second page, no page-number jump, no new-message flash. A different reply right after still gets its page;
// the same words on a page WITH the owner's input stay a page. Real page + local fake host, zh + en, light + dark.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { parityRecorder } from '../../parity/lib.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const $ = (id) => `document.getElementById('${id}')`;
const pg = (p) => evaluate(p, `${$('pg')}.textContent`);
const L = {
  zh: { ask: '旧函数清理了吗？', ans: '旧函数清理完了，测试全绿。', next: '后台测试也跑完了：报告已生成。' },
  en: { ask: 'Are the old functions gone?', ans: 'The old functions are gone; tests are green.', next: 'The background test finished: the report is ready.' },
};

export async function runP424Dup({ B, web, fake, out = 'reports/qa/p424/dup', rec = parityRecorder('web/test/p424_dup.mjs') }) {
  mkdirSync(out, { recursive: true });
  const results = {};
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
    const s = L[lang]; fake.reset();
    const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
      const dev = await evaluate(p, `import('./js/api.js').then(api=>api.myDeviceId())`);
      await fake.addTurn({ k: 'phone', dev, text: s.ask }, s.ans);
      await waitFor(p, `${$('pg')}.textContent==='1 / 1'`);
      // the echo: an input-less background-task page with the very same reply, then the quiet turn
      await fake.addTurn({ k: 'agent', text: '', via: 'task' }, s.ans);
      await fake.addTurn({ k: 'agent', text: '' }, '〔不回群〕');
      await sleep(400);
      assert.equal(await pg(p), '1 / 1', 'the echo page and the quiet turn add no page');
      assert.equal(await evaluate(p, `${$('pgCorner')}.dataset.unread`), '0', 'no new-message flash for the echo');
      assert.equal(await evaluate(p, `${$('om')}.dataset.header`), '0', 'still the page with the owner\'s input, not 「这一页没有你的输入」');
      assert.equal(await evaluate(p, `${$('words')}.textContent.trim()`), s.ans);
      writeFileSync(resolve(out, `${lang}-${theme}-no-echo.png`), await shoot(p, ''));
      // a different reply right after: its own page as before
      await fake.addTurn({ k: 'agent', text: '', via: 'task' }, s.next);
      await waitFor(p, `${$('pg')}.textContent==='2 / 2'`);
      assert.equal(await evaluate(p, `${$('om')}.dataset.header`), '1');
      // the same words on a page WITH the owner's input stay a page (only input-less echoes fold)
      await fake.addTurn({ k: 'phone', dev, text: s.ask }, s.next);
      await waitFor(p, `${$('pg')}.textContent==='3 / 3'`);
      // a reload (head fetch) folds the stored echo the same way
      await navigate(p, web.url + '?lang=' + lang); await waitState(p, 'ready');
      await waitFor(p, `${$('pg')}.textContent==='3 / 3'`);
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      results[lang + '-' + theme] = 'pass'; console.log('PASS P424 #87 ' + lang + '-' + theme);
    } finally { await p.dispose(); }
  }
  writeFileSync(resolve(out, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  rec.record('p424-echo-page', Object.keys(results).length === 4);
  console.log('parity results → ' + rec.write());
  return results;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
  try { await runP424Dup({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
