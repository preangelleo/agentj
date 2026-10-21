// P424 (#145, owner 10-19 17:47): after the Agent J host's serve was restarted under load, the 「…没响应，救援守护在线」 banner
// stayed, could not be closed, and messages did not go out. The real cause: serve's new relay socket replaced the rescue
// watchdog's, the relay told the phone `{"t":"host","up":true}`, and the phone ignored it ("duplicate up") — it sat on the
// dead watchdog session until a frame failed. Now: `up` on a ready session → RESUME at once → the normal `ready` takes the
// banner away; the banner has × and Esc (for that rescue spell only); a message sent while the watchdog holds the line waits
// in the offline queue (「Agent J 恢复后自动发送」) and goes out by itself once Agent J is back, with one line saying so.
// Real page + local fake host, zh + en.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { parityRecorder } from '../../parity/lib.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const $ = (id) => `document.getElementById('${id}')`;
const L = {
  zh: { close: '先关掉这条提示', line: 'Agent J 恢复后自动发送', msg: '恢复了吗？', resent: /已补发 1 条排队的消息/, cmdLater: 'Agent J 恢复后再试这条命令' },
  en: { close: 'Hide this notice for now', line: 'Sends when Agent J is back', msg: 'Are you back?', resent: /sent 1 queued message/, cmdLater: 'Try this command again once Agent J is back' },
};
const line = (p) => evaluate(p, `(() => { const b = ${$('outbox')}, l = b.querySelector('.obl'); return b.hidden ? null : l ? l.textContent : ''; })()`);

export async function runP424Rescue({ B, web, fake, out = 'reports/qa/p424/rescue', rec = parityRecorder('web/test/p424_rescue.mjs') }) {
  mkdirSync(out, { recursive: true });
  const results = {};
  for (const lang of ['zh', 'en']) {
    const s = L[lang]; fake.reset();
    const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
      // ---- the watchdog holds the line (ready {mode:"rescue"} + rescue {on})
      fake.st.readyMode = 'rescue'; fake.kick(); await waitState(p, 'ready', 20000);
      const since = Date.now() - 60000;
      await fake.send({ t: 'rescue', on: true, why: 'serve_stall', since, actions: ['restart'] });
      await waitFor(p, `!${$('rescue-banner')}.hidden`);
      assert.equal(await evaluate(p, `${$('rescue-banner-x')}.getAttribute('aria-label')`), s.close);
      assert.ok(await evaluate(p, `${$('rescue-banner-x')}.getBoundingClientRect().height>=44`), '× is a 44 px target');
      writeFileSync(resolve(out, `${lang}-banner.png`), await shoot(p, ''));
      // × closes it; the watchdog's repeat of the same spell does not bring it back
      await evaluate(p, `${$('rescue-banner-x')}.click()`);
      await waitFor(p, `${$('rescue-banner')}.hidden`);
      await fake.send({ t: 'rescue', on: true, why: 'serve_stall', since, actions: ['restart'] });
      await sleep(200);
      assert.equal(await evaluate(p, `${$('rescue-banner')}.hidden`), true, 'closed for this spell');
      // a new spell shows it again; Esc closes it
      await fake.send({ t: 'rescue', on: true, why: 'serve_down', since: since + 1000, actions: ['restart'] });
      await waitFor(p, `!${$('rescue-banner')}.hidden`);
      await evaluate(p, `document.activeElement && document.activeElement.blur()`);
      await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
      await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
      await waitFor(p, `${$('rescue-banner')}.hidden`);
      // ---- a message sent now waits (the watchdog answers no say)
      await fake.send({ t: 'rescue', on: true, why: 'serve_down', since: since + 2000, actions: ['restart'] });
      await waitFor(p, `!${$('rescue-banner')}.hidden`);
      // ---- security (#145): a built-in command does not wait — a /clear that lands after the spell would wipe a live session.
      // It stays in the field, nothing queues, the line says to try again.
      const typeIn = (v) => evaluate(p, `(() => { const i = ${$('input')}; i.value = ${JSON.stringify(v)}; i.dispatchEvent(new Event('input')); })()`);
      for (const cmd of ['/clear', '/new', '/stop', '/compact', '/model opus', '\uff0fclear']) {
        await evaluate(p, `${$('toast')}.textContent = ''`);
        await typeIn(cmd);
        await evaluate(p, `${$('send')}.click()`);
        await waitFor(p, `${$('toast')}.textContent === ${JSON.stringify(s.cmdLater)}`, 3000);
        assert.equal(await evaluate(p, `document.querySelectorAll('#outbox .obi').length`), 0, cmd + ' did not queue');
        assert.equal(await evaluate(p, `${$('input')}.value`), cmd, cmd + ' stays in the field');
      }
      writeFileSync(resolve(out, `${lang}-cmd-refused.png`), await shoot(p, ''));
      await typeIn('');
      await evaluate(p, `(() => { const i = ${$('input')}; i.value = ${JSON.stringify(s.msg)}; i.dispatchEvent(new Event('input')); })()`);
      assert.equal(await evaluate(p, `${$('send')}.disabled`), false, 'Send stays usable');
      await evaluate(p, `${$('send')}.click()`);
      await waitFor(p, `document.querySelectorAll('#outbox .obi').length === 1`);
      assert.equal(await line(p), s.line, 'queued with the rescue line');
      assert.equal(fake.st.says.length, 0, 'nothing went to the watchdog');
      writeFileSync(resolve(out, `${lang}-queued.png`), await shoot(p, ''));
      // ---- serve is back: its socket replaces the watchdog's, the relay says up:true on the open session
      fake.st.readyMode = null;
      fake.swapHost();
      await waitFor(p, `${$('rescue-banner')}.hidden && window.__ajState === 'ready'`, 8000);
      await waitFor(p, `document.querySelectorAll('#outbox .obi').length === 0`, 8000);
      assert.deepEqual(fake.st.says.map((m) => m.text), [s.msg], 'the queued message went out by itself, once');
      assert.deepEqual(fake.st.slashes, [], 'no command ran after the spell');
      await waitFor(p, `${$('toast')}.textContent && ${JSON.stringify(s.resent.source)} && new RegExp(${JSON.stringify(s.resent.source)}).test(${$('toast')}.textContent)`, 4000);
      writeFileSync(resolve(out, `${lang}-back.png`), await shoot(p, ''));
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      assert.deepEqual(problems, []); assert.deepEqual(p.offsite, []);
      results[lang] = 'pass'; console.log('PASS P424 #145 ' + lang);
    } finally { fake.st.readyMode = null; await p.dispose(); }
  }
  writeFileSync(resolve(out, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  rec.record('p424-rescue-recover', Object.keys(results).length === 2);
  console.log('parity results → ' + rec.write());
  return results;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP424Rescue({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
