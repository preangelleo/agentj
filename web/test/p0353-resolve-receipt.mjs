// Exercise the real Host → encrypted history → upgrade receipt in each supported language.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { evaluate, launch, navigate, newPage, shoot, waitFor, waitState } from './browser.mjs';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';

const host = process.env.P0353_HOST || fileURLToPath(new URL('../../host', import.meta.url));
const generator = fileURLToPath(new URL('./p0353-receipt-host.py', import.meta.url));
const legacy = process.argv.includes('--legacy');
const cards = JSON.parse(execFileSync(process.env.PYTHON || 'python3', legacy ? [generator, '--legacy'] : [generator], {
  env: {...process.env, PYTHONPATH: host}, encoding: 'utf8',
}));
const out = process.env.P0353_OUT || 'reports/qa/p0353-resolve-receipt' + (legacy ? '-legacy' : '');
mkdirSync(out, {recursive: true});
const web = await startWebServer(), fake = await startFakeHost(), browser = await launch();
const cases = [];
try {
  for (const lang of ['zh', 'en']) for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    fake.reset();
    const requests = [];
    fake.st.runCmds = ['reconnect'];
    fake.st.onApp = async (conn, msg, send) => {
      if (msg.t !== 'cmd_run') return false;
      requests.push({cmd: msg.cmd, signed: await fake.verifyControl(conn, 'cmd_run', msg, {cmd: msg.cmd})});
      await send({t: 'ctl_res', r: msg.r, action: 'cmd_run', ok: false, why: 'no_passkey'});
      return true;
    };
    fake.st.prefs = {appearance: {theme, language: lang}};
    const p = await newPage(browser, width, 844, theme, {allow: [web.url, fake.relay + '/'], touch: true});
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await evaluate(p, "document.querySelector('#phone-env-dismiss')?.click()");
      const turn = {...cards[lang], id: 1};
      fake.st.turns.push(turn);
      await fake.send({t: 'hist_turn', epoch: fake.st.epoch, turn});
      await waitFor(p, "document.querySelector('#words')?.innerText.includes('st_xxxxx')");
      const info = await evaluate(p, `({
        paragraphs: Array.from(document.querySelectorAll('#words p'), node => node.innerText),
        guide: document.querySelector('#words a[href="https://agentj.app/docs/install/#upgrade-failed"]')?.href,
        link: (() => {
          const a = document.querySelector('#cmdx a[href="https://agentj.app/docs/install/#upgrade-failed"]');
          return a && {label: a.textContent, visible: a.getClientRects().length > 0, rel: a.rel, target: a.target};
        })(),
        repair: document.querySelector('#cmdx [data-repair=reconnect]')?.textContent,
      })`);
      cases.push({lang, width, theme, ...info});
      writeFileSync(join(out, lang + '-' + width + '-' + theme + '.png'), await shoot(p));
      await evaluate(p, "document.querySelector('#cmdx [data-repair=reconnect]').click()");
      await waitFor(p, "import('./js/cmdrun.js').then(m => m._debug().R?.why === 'no_passkey')");
      assert.deepEqual(requests, [{cmd: 'reconnect', signed: true}]);
    } finally {
      await p.dispose();
    }
  }
} finally {
  await browser.close();
  await fake.stop();
  await web.stop();
}
writeFileSync(join(out, 'rendered.json'), JSON.stringify(cases, null, 2));
for (const row of cases) {
  const text = row.paragraphs.join('\n');
  assert.equal(row.repair, row.lang === 'en' ? 'Repair connection' : '修复连接');
  if (legacy) {
    assert.deepEqual(row.link, {label: row.lang === 'en' ? 'How to repair a host update' : '查看升级修复步骤',
      visible: true, rel: 'noopener noreferrer', target: '_blank'});
    continue;
  }
  assert.equal(row.link, null, 'current receipts already contain the link');
  assert.ok(text.includes('0.30.50') && text.includes('0.30.52'), 'source and requested version');
  assert.match(text, /no logs|不含日志/i);
  assert.doesNotMatch(text, /请让我运行|run `?agentj doctor/i);
  assert.ok(row.paragraphs.slice(1).some(p => p.includes('/update')), 'recovery has a separate paragraph');
  if (row.lang === 'en') assert.match(row.paragraphs[1], /^After\b/);
  assert.equal(row.guide, 'https://agentj.app/docs/install/#upgrade-failed');
  assert.equal(row.repair, row.lang === 'en' ? 'Repair connection' : '修复连接');
}
console.log('PASS ' + cases.length + ' resolver-failure receipts');
