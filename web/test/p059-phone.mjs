// R-0059: Android watcher cursor follows completed visible replies, not silent/empty turns.
// Real paired client + local Noise fake host; before screenshots can use P059_BEFORE_PUBLIC.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const before = process.env.P059_BEFORE_PUBLIC;
const out = join(root, 'web/reports/qa/p059', before ? 'before' : 'after');
mkdirSync(out, { recursive: true });
const web = await startWebServer(before ? { publicDir: before } : {}), fake = await startFakeHost(), B = await launch();
const native = p => evaluate(p, 'window.agentjNative.snapshot().latest');
const pageCount = p => evaluate(p, "document.getElementById('pg').textContent");
try {
  for (const width of [390, 1440]) for (const theme of ['dark', 'light']) {
    fake.reset();
    const p = await newPage(B, width, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=zh'));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, "document.getElementById('a2hs-ok')?.click()");
      assert.equal(await native(p), 0, 'empty history has no notification cursor');
      assert.equal(await pageCount(p), '0 / 0');
      // A desktop turn anchored on the computer's own input: a reply page, so the cursor and the page count both move.
      const desk = await fake.addTurn({ k: 'host', name: 'Desktop', text: '整理今天的报告' }, '报告已整理好。');
      await waitFor(p, `window.agentjNative.snapshot().latest===${desk.id}`);
      await waitFor(p, `document.getElementById('pg').textContent==='1 / 1'`);
      // Since 0.30.31 (p0331) a background completion lives in 后台动态, not on a reply page: the page count stays,
      // but it is still a visible finished reply (the host pushes for task/loop too), so the cursor moves.
      const visible = await fake.addTurn({ k: 'agent', via: 'background_done' }, '后台工作已完成。');
      await waitFor(p, `window.agentjNative.snapshot().latest===${visible.id}`);
      assert.equal(await pageCount(p), '1 / 1', 'background completion is activity, not a reply page');
      // Both explicit silent metadata and legacy markers must not advance the watcher.
      const quiet = await fake.addTurn({ k: 'agent', via: 'hook' }, '内部检查', 'open');
      await fake.updateTurn(quiet.id, { end: 'done', silent: true });
      await fake.addTurn({ k: 'agent', via: 'hook' }, '〔静默消息〕例行检查完成');
      const blank = await fake.addTurn({ k: 'agent' }, '   ');
      // Await an actual history sync, rather than asserting before the frames land. The quiet turns change no visible
      // DOM, so the barrier is time; the before run (P059_BEFORE_PUBLIC) is its positive control: the old cursor does
      // reach the last quiet turn within the same wait.
      await sleep(400);
      assert.equal(await pageCount(p), '1 / 1', 'quiet turns add no reply page');
      writeFileSync(join(out, `${width}-${theme}-silent.png`), await shoot(p, ''));
      if (before) {
        assert.notEqual(await native(p), visible.id, 'old watcher advances incorrectly on quiet turns');
        assert.equal(await native(p), blank.id, 'old watcher follows the last completed turn, even a blank one');
      } else assert.equal(await native(p), visible.id, 'silent and empty completions cannot trigger a native notification');
      // An unanchored desktop reply (desktop_text with no input page) is a reply page again.
      const next = await fake.addTurn({ k: 'agent' }, '新报告已完成。');
      await waitFor(p, `window.agentjNative.snapshot().latest===${next.id}`);
      await waitFor(p, `document.getElementById('pg').textContent==='2 / 2'`);
      writeFileSync(join(out, `${width}-${theme}-reply.png`), await shoot(p, ''));
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      console.log(`PASS R-0059 ${width}/${theme}`);
    } finally { await p.dispose(); }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
