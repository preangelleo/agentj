// 0.30.76: the first-run Ego Lite button uses the existing paired chat path.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { evaluate, launch, navigate, newPage, rect, tap, waitFor, waitState } from './browser.mjs';

const snapshot = JSON.parse(readFileSync(new URL('./fixtures/p131-setup.json', import.meta.url)));
snapshot.browser_source = { kind: 'none', backend: 'ego-lite' };
const browser = snapshot.items.find(i => i.id === 'browser');
Object.assign(browser, {
  choice: 'selected', state: 'waiting_local', reason: 'ego_profile_required',
  explain: {
    fact: { zh: '请在主机上运行 agentj browser connect。', en: 'Run agentj browser connect.' },
    effect: { zh: '其他照常。', en: 'Everything else works.' },
    next: ['check'],
  },
});

const web = await startWebServer();
const fake = await startFakeHost();
const B = await launch();
const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
const action = '#setup-ask-ego-install';
const setupOpen = '!document.getElementById("setup-view").hidden';
const draft = '保留这条手写草稿';
const attempts = [];
const marks = [];
let allow = false;
fake.st.prefs = { appearance: { language: 'zh', theme: 'light' } };
fake.st.onApp = async (c, m, send) => {
  if (m.t === 'setup_get') {
    await send({ t: 'setup_card', r: m.r, show: false, ...snapshot });
    return true;
  }
  if (m.t === 'say') {
    attempts.push(m);
    return !allow;
  }
  if (m.t === 'setup_mark') marks.push(m);
  return false;
};

try {
  await navigate(p, fake.newPairing(web.url + '?lang=zh'));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await evaluate(p, `if (!document.getElementById('a2hs').hidden) document.getElementById('a2hs-ok').click();
    const field = document.getElementById('input');
    field.value = ${JSON.stringify(draft)};
    field.dispatchEvent(new Event('input', { bubbles: true }));`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await fake.send({ t: 'setup_card', show: true, ...snapshot });
  await waitFor(p, '!document.getElementById("setup-banner").hidden');
  await evaluate(p, 'document.getElementById("setup-banner-open").click()');
  await waitFor(p, setupOpen + ' && !!document.querySelector(' + JSON.stringify(action) + ')');

  const buttons = await evaluate(p, `Array.from(document.querySelectorAll('#setup-main > .setup-actions button'), b => b.id)`);
  assert.deepEqual(buttons, ['setup-ask-ego-install', 'setup-unused', 'setup-later']);
  assert.match(await evaluate(p, 'document.getElementById("setup-fact").textContent'), /Ego Lite/);
  assert.equal(await evaluate(p, `document.querySelectorAll('#setup-main > .setup-fact').length`), 1);
  assert.equal(await evaluate(p, `document.querySelectorAll('#setup-main > :is(.setup-hint, .setup-step, .setup-effect)').length`), 0);
  await evaluate(p, `if (!document.getElementById('phone-env-dismiss').hidden) document.getElementById('phone-env-dismiss').click()`);
  const point = await rect(p, action);
  await tap(p, point.x, point.y);
  await waitFor(p, 'document.querySelector(' + JSON.stringify(action) + ')?.disabled === true');
  await tap(p, point.x, point.y);
  for (let n = 0; n < 100 && !attempts.length; n++) await new Promise(r => setTimeout(r, 20));
  assert.equal(attempts.length, 1);
  assert.equal(await evaluate(p, setupOpen), true);
  assert.deepEqual(Object.keys(attempts[0]).sort(), ['sid', 't', 'text', 'ts']);
  assert.equal(attempts[0].text, '请帮我装 Ego Lite，并带我完成专用浏览器设置。');

  await fake.send({ t: 'say_res', sid: attempts[0].sid, ok: false, why: 'stopped' });
  await waitFor(p, 'document.querySelector(' + JSON.stringify(action) + ')?.disabled === false');
  assert.equal(await evaluate(p, setupOpen), true);

  allow = true;
  await tap(p, point.x, point.y);
  await waitFor(p, '!(' + setupOpen + ')');
  assert.equal(attempts.length, 2);
  assert.equal(fake.st.turns.at(-1).src.text, attempts[0].text);
  assert.equal(fake.st.turns.at(-1).src.k, 'phone');
  assert.deepEqual(marks, []);
  assert.equal(await evaluate(p, 'document.getElementById("input").value'), draft);
  assert.deepEqual(p.problems.filter(s => !s.includes('favicon')), []);
  assert.deepEqual(p.offsite, []);
  console.log('PASS Ego Lite ordinary owner chat, refusal, single pending click and preserved draft');
} finally {
  await p.dispose();
  await B.close();
  await fake.stop();
  await web.stop();
}
