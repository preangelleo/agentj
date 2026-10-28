// P562 (#337): the owner's WeChat message on the Web App — before (0.25.15: a 「来自微信」 notice page, then a separate
// 「微信分身已回复」 page from the avatar) and after (one main-conversation page labelled 「微信」 with the main answer).
//   P562_SHOTS=<dir> node web/test/p562.mjs        (screenshots → $P562_SHOTS, default reports/qa/p562)
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, evaluate, navigate, waitFor, waitState, shoot} from './browser.mjs';
const OUT = process.env.P562_SHOTS || 'reports/qa/p562';
mkdirSync(OUT, {recursive: true});
const T = {
  zh: {name: '微信', label: '来自微信', ask: '明天东京天气怎么样？', answer: '明天东京晴，最高 18 度，早晚偏凉，记得带件外套。', replied: '微信分身已回复'},
  en: {name: 'WeChat', label: 'From WeChat', ask: 'What is the weather in Tokyo tomorrow?', answer: 'Sunny tomorrow in Tokyo, 18 °C at most; bring a jacket for the evening.', replied: 'WeChat avatar replied'},
};
const web = await startWebServer({port: 0, relayCsp: 'ws://127.0.0.1:*'});
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
async function paired(lang, theme) {
  fake.reset(); fake.setUp?.(true);
  const p = await newPage(B, 390, 844, theme, {touch: true, allow: [web.url, fake.relay + '/']});
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.documentElement.dataset.theme='${theme}'`);
  await fake.send({t: 'status', s: 'idle', agent: 'claude', name: 'Agent J'});
  return p;
}
async function C(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); } catch (e) { failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); }
}
const card = (lang) => ({channel: {source: 'wechat', label: T[lang].label, text: T[lang].ask, cut: false, files: [], paths: []}});
try {
  for (const [lang, theme] of [['zh', 'light'], ['en', 'dark']]) {
    const tag = `${lang}-${theme}-390`;
    await C(`${tag} before: a notice page, then the avatar's separate reply page`, async () => {
      const p = await paired(lang, theme);
      await fake.addTurn({k: 'sys', text: '', via: 'wechat'}, T[lang].label + (lang === 'zh' ? '：' : ': ') + T[lang].ask, 'done', card(lang));
      await fake.addTurn({k: 'sys', via: 'wechat', text: T[lang].replied}, T[lang].answer, 'done');
      await waitFor(p, `document.getElementById('words').innerText.includes(${JSON.stringify(T[lang].answer.slice(0, 6))})`);
      writeFileSync(`${OUT}/before-${tag}.png`, await shoot(p));
    });
    await C(`${tag} after: one main-conversation page labelled ${T[lang].name}`, async () => {
      const p = await paired(lang, theme);
      await fake.addTurn({k: 'wechat', dev: 'wechat:owner', name: T[lang].name, text: T[lang].ask}, T[lang].answer, 'done');
      await waitFor(p, `document.getElementById('words').innerText.includes(${JSON.stringify(T[lang].answer.slice(0, 6))})`);
      const page = await evaluate(p, `document.body.innerText`);
      assert.ok(page.includes(T[lang].name), 'the source label says ' + T[lang].name);
      assert.ok(page.includes(T[lang].ask), 'the owner\'s own words are shown');
      assert.ok(!page.includes(T[lang].replied), 'no avatar line');
      writeFileSync(`${OUT}/after-${tag}.png`, await shoot(p));
    });
  }
} finally {
  await B.close?.(); await fake.close?.(); await web.close?.();
}
console.log(failed ? `P562 phone: ${failed} failed` : 'P562 phone: all passed');
process.exit(failed ? 1 : 0);
