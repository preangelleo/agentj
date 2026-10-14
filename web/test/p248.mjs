// P248: actual reconciled Scheduler rows, real phone rendering, local fake relay only.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { reapEnv } from '../../tests/lib/reap.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p248/browser/';
mkdirSync(OUT, { recursive: true });
const home = mkdtempSync(tmpdir() + '/aj-p248-web-');
let web, fake, B;
const priorHome = process.env.HOME;
process.env.HOME = home;
try {
  const py = process.env.AJ_PYTHON || ROOT + 'host/.venv/bin/python';
  const snapshot = lang => JSON.parse(execFileSync(py, [ROOT + 'host/tests/test_p248_run_ledger.py', '--snapshot', lang], {
    env: { ...process.env, HOME: home, XDG_CONFIG_HOME: home + '/config', XDG_DATA_HOME: home + '/data',
      XDG_STATE_HOME: home + '/state', XDG_CACHE_HOME: home + '/cache' }, encoding: 'utf8', timeout: 15000,
  }));
  web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  fake = await startFakeHost();
  B = await launch();
  for (const lang of ['zh', 'en']) {
    const rows = snapshot(lang);
    assert.equal(rows[0].last.status, 'interrupted');
    assert.equal(rows[0].last.report_available, true);
    assert.equal(rows[0].enabled, false);
    fake.reset();
    fake.st.extraCaps = [];
    fake.answer('task_list', () => ({ t: 'tasks', more: false, items: rows, agent: 'claude', paused: false }));
    const p = await newPage(B, 390, 844, 'dark', { allow: [web.url, fake.relay + '/'], touch: true, permissions: [] });
    try {
      await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await sleep(200);
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click(); document.getElementById('setBtn').click()`);
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await evaluate(p, `document.getElementById('open-tasks').click()`);
      await waitFor(p, `document.querySelectorAll('#tasks-list .task-card').length === 1`);
      const actual = await evaluate(p, `({text: document.getElementById('tasks-list').innerText, enabled: document.querySelector('.task-card').dataset.enabled, button: document.querySelector('.task-card button').textContent, overflow: document.documentElement.scrollWidth > innerWidth + 1})`);
      assert.match(actual.text, /无法确认旧执行|old execution may still be active/);
      assert.match(actual.text, /run-[0-9a-f]+\.md/);
      assert.equal(actual.enabled, 'false');
      assert.equal(actual.button, lang === 'zh' ? '启用' : 'Turn on');
      assert.equal(actual.overflow, false);
      assert.deepEqual(p.problems, []);
      assert.deepEqual(p.offsite, []);
      writeFileSync(OUT + lang + '.png', await shoot(p));
      writeFileSync(OUT + lang + '.json', JSON.stringify(actual, null, 2) + '\n');
    } catch (err) {
      console.log('page failure', await evaluate(p, `({ready:document.readyState, path:location.pathname, state:window.__ajState, errors:document.querySelectorAll('script').length})`).catch(() => null));
      throw err;
    } finally { await p.dispose(); }
  }
  console.log('P248 interrupted report + existing Turn on button: zh/en PASS');
} finally {
  if (B) await B.close();
  if (fake) await fake.stop();
  if (web) await web.stop();
  reapEnv('HOME=' + home);  // only descendants carrying this unique fixture HOME, including detached crashpad workers
  process.env.HOME = priorHome;
  rmSync(home, { recursive: true, force: true });
}
