// Paired phone history, actual clipboard, and signed control packets at the Host.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot} from './browser.mjs';

const output = process.env.P0354_OUT || 'reports/qa/p0354-command-target';
const before = process.argv.includes('--before');
const web = await startWebServer(), host = await startFakeHost(), browser = await launch();
const cases = [];
mkdirSync(output, {recursive: true});
try {
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    const zh = lang === 'zh', commands = ['agentj --version', 'agentj doctor', 'pwd', 'agentj status'];
    const block = (index, language = 'sh') => ['```' + language, commands[index], '```'].join('\n');
    const local = zh ? '在 Agent J 主机上运行：' : 'On the Agent J host:';
    const reply = [block(2), local, block(3), zh ? '请在另一台电脑的终端依次运行这两条命令。' : 'Run these two commands in the terminal on the other computer.',
      block(0), block(1), local, block(3), zh ? '在 M1 上运行：' : 'On the M1:', block(2),
      zh ? '不要在 Agent J 主机上运行：' : 'Do not run on the Agent J host:', block(1),
      local, block(2, 'text')].join('\n\n');
    host.reset();
    host.st.runCmds = ['doctor', 'version', 'status'];
    host.st.prefs = {appearance: {theme, language: lang}};
    const requests = [];
    host.st.onApp = async (conn, msg, send) => {
      if (['say','term_open','term_line','cmd_run'].includes(msg.t)) requests.push(msg.t);
      if (msg.t !== 'cmd_run') return false;
      assert.equal(msg.cmd, 'status');
      assert.equal(await host.verifyControl(conn, 'cmd_run', msg, {cmd: msg.cmd}), true);
      await send({t: 'ctl_res', r: msg.r, action: 'cmd_run', ok: true, code: 0, out: 'OK'});
      return true;
    };
    const page = await newPage(browser, width, 844, theme, {touch: true, allow: [web.url, host.relay + '/']});
    try {
      await navigate(page, host.newPairing(web.url + '?lang=' + lang));
      await waitState(page, 'awaiting-approval');
      await host.approve();
      await waitState(page, 'ready');
      await evaluate(page, "if (!document.getElementById('a2hs').hidden) document.getElementById('a2hs-ok').click(); document.querySelector('#phone-env-dismiss')?.click()");
      await evaluate(page, "import('./js/settings.js').then(m => m.applyThemeLocal(" + JSON.stringify(theme) + "))");
      await host.addTurn({k:'phone', dev:host.devs[0].id, text:zh ? '怎么检查这两台电脑？' : 'How do I check both computers?'}, reply);
      await waitFor(page, "document.querySelectorAll('#words .codeblock').length === 8 && document.querySelector('#words .runbtn')");
      await evaluate(page, 'document.fonts.ready.then(() => true)');
      const rows = await evaluate(page, `Array.from(document.querySelectorAll('#words .codeblock'), b => ({
        text: b.querySelector('pre code').innerText, controls: Array.from(b.querySelectorAll('.bar button'), x => x.className)
      }))`);
      const hasRun = rows.map(row => row.controls.some(name => name.includes('runbtn')));
      assert.deepEqual(hasRun, before ? [true,true,false,false,true,true,true,false] : [false,true,false,false,true,false,false,false]);
      assert.deepEqual(rows.map(row => row.text), [2,3,0,1,3,2,1,2].map(index => commands[index]));
      const filename = lang + '-' + theme + '-' + width + '.png';
      writeFileSync(join(output, filename), await shoot(page));
      for (const [index, row] of rows.entries()) {
        const point = await evaluate(page, `(() => {
          const b = document.querySelectorAll('#words .codeblock .copy')[${index}];
          b.scrollIntoView({block:'center'}); const r = b.getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        })()`);
        for (const type of ['mousePressed', 'mouseReleased']) await page.send('Input.dispatchMouseEvent', {type, ...point, button:'left', clickCount:1});
        await waitFor(page, `navigator.clipboard.readText().then(text => text === ${JSON.stringify(row.text)})`);
      }
      assert.deepEqual(requests, []);
      await evaluate(page, "document.querySelectorAll('#words .codeblock')[1].querySelector('.runbtn').click()");
      await waitFor(page, "document.querySelector('#words .runbox')?.innerText.includes('OK')");
      assert.deepEqual(requests, ['cmd_run']);
      cases.push({lang, theme, width, rows, copies:rows.length, executionsAfterCopy:0, signedLocalStatus:1, screenshot:filename});
    } finally { await page.dispose(); }
  }
} finally {
  await browser.close(); await host.stop(); await web.stop();
}
writeFileSync(join(output, 'cases.json'), JSON.stringify(cases, null, 2));
console.log('PASS', cases.length, 'paired browser journeys');
