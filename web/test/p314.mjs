// P314: 额度用完自动续跑 on the real page against the local fake host — the chat's thin banner 「额度用完，预计 HH:MM 自动继续」
// (chat / workflow / another account), ✕ = qr_cancel {r, id} → the banner goes, and the automatic turn's page label.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const $ = id => `document.getElementById('${id}')`;
const L = {
  zh: {main: /^额度用完，预计 \d\d:\d\d 自动继续$/, task: /^「周报」额度用完，预计 \d\d:\d\d 自动重跑（另有 1 项）$/,
       sw: /^「周报」额度用完，\d\d:\d\d 改用 Codex 接着跑$/, x: '取消自动继续'},
  en: {main: /^Quota used up · continues automatically at \d\d:\d\d$/, task: /^「周报」 out of quota · reruns automatically at \d\d:\d\d \(\+1 more\)$/,
       sw: /^「周报」 out of quota · continues on Codex at \d\d:\d\d$/, x: 'Cancel the automatic continue'},
};
export const CASES = {
  banner: 'p314 a) 「额度用完，预计 HH:MM 自动继续」 for the chat, the earliest workflow item + how many more, the switch to another account',
  cancel: 'p314 b) ✕ sends qr_cancel with the shown id; the host answer hides the banner (no signature, no card)',
};
export async function runP314({B, web, fake, out = process.env.P314_OUT || 'reports/qa/p314', rec = parityRecorder('web/test/p314.mjs')}) {
  mkdirSync(out, {recursive: true});
  const ok = new Set();
  const step = async (c, f) => { try { await f(); } catch (e) { rec.record(CASES[c], false); throw e; } ok.add(c); };
  for (const [lang, theme] of [['zh', 'light'], ['zh', 'dark'], ['en', 'light'], ['en', 'dark']]) {
    const s = L[lang]; fake.reset();
    const seen = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'qr_cancel') return false;
      seen.push({t: m.t, id: m.id, r: m.r});
      await send({t: 'qr_res', r: m.r, ok: true, items: []});
      return true;
    };
    const now = Math.floor(Date.now() / 1000);
    // inside today (≥ 2 min before midnight), so the text is a plain HH:MM
    const soon = Math.min(now + 1800, Math.floor(new Date().setHours(23, 57, 0, 0) / 1000) - 90);
    const p = await newPage(B, 390, 844, theme, {touch: true, allow: [web.url, fake.relay + '/']});
    const save = async name => writeFileSync(resolve(out, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
      await step('banner', async () => {
        assert.ok(await evaluate(p, `${$('qr-banner')}.hidden`), 'nothing waiting: no banner');
        await fake.send({t: 'quota_resume', items: [{id: 'a1b2c3d4e5f60718', scope: 'main', harness: 'claude', at: soon - 90}]});
        await waitFor(p, `!${$('qr-banner')}.hidden`);
        assert.match(await evaluate(p, `${$('qr-banner-text')}.textContent`), s.main);
        assert.equal(await evaluate(p, `${$('qr-banner-x')}.getAttribute('aria-label')`), s.x);
        await sleep(150); await save('main');
        await fake.send({t: 'quota_resume', items: [
          {id: 'bb00000000000002', scope: 'task', harness: 'claude', at: soon + 600, title: '月报'},
          {id: 'bb00000000000001', scope: 'task', harness: 'claude', at: soon - 90, title: '周报'},
          {id: 'zz', scope: 'task', harness: 'claude', at: soon}]});       // malformed id: ignored
        await waitFor(p, `/周报/.test(${$('qr-banner-text')}.textContent)`);
        assert.match(await evaluate(p, `${$('qr-banner-text')}.textContent`), s.task);
        await fake.send({t: 'quota_resume', items: [{id: 'cc00000000000001', scope: 'task', harness: 'claude', at: soon, title: '周报', to: 'codex'}]});
        await waitFor(p, `/Codex/.test(${$('qr-banner-text')}.textContent)`);
        assert.match(await evaluate(p, `${$('qr-banner-text')}.textContent`), s.sw);
        await sleep(150); await save('switch');
      });
      await step('cancel', async () => {
        await evaluate(p, `${$('qr-banner-x')}.click()`);
        await waitFor(p, `${$('qr-banner')}.hidden`);
        assert.deepEqual(seen.map(m => [m.t, m.id]), [['qr_cancel', 'cc00000000000001']]);
        assert.match(seen[0].r, /^[0-9a-f]{16}$/);
        assert.equal(await evaluate(p, `document.querySelectorAll('.card.ask').length`), 0, 'no approval card involved');
        await fake.send({t: 'quota_resume', items: []});
        await sleep(200);
        assert.ok(await evaluate(p, `${$('qr-banner')}.hidden`));
      });
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      console.log('PASS P314 ' + lang + '-' + theme);
    } finally { fake.st.onApp = null; await p.dispose(); }
  }
  for (const c of Object.keys(CASES)) if (ok.has(c)) rec.record(CASES[c], true);
  console.log('parity results → ' + rec.write());
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
  try { await runP314({B, web, fake}); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
