// Exercise the same paired history renderer and clipboard controls used by a phone.
import test from 'node:test';
import assert from 'node:assert/strict';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate} from './browser.mjs';

test('named shell commands stay visible and copyable on a paired 390px phone', async () => {
  const commands = ['agentj --version', 'agentj doctor'];
  const reply = commands.map(cmd => ['```sh', cmd, '```'].join('\n')).join('\n\n');
  const server = await startWebServer();
  const host = await startFakeHost();
  const browser = await launch();
  try {
    for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
      host.reset();
      host.st.prefs = {appearance: {language: lang, theme}};
      host.st.runCmds = ['doctor', 'version'];
      const executions = [];
      host.st.onApp = async (_client, packet) => {
        if (['cmd_run', 'term_open', 'term_line', 'say'].includes(packet.t)) executions.push(packet.t);
        return false;
      };
      const page = await newPage(browser, 390, 844, theme, {touch: true, allow: [server.url, host.relay + '/']});
      try {
        await navigate(page, host.newPairing(server.url + '?lang=' + lang));
        await waitState(page, 'awaiting-approval');
        await host.approve();
        await waitState(page, 'ready');
        await evaluate(page, "document.querySelector('#phone-env-dismiss')?.click()");
        await host.addTurn({k: 'phone', dev: host.devs[0].id, text: 'Check version and status'}, reply);
        await waitFor(page, "document.querySelectorAll('#words .codeblock').length === 2");
        for (const [index, command] of commands.entries()) {
          const bounds = await evaluate(page, `(() => {
            const block = document.querySelectorAll('#words .codeblock')[${index}];
            const code = block.querySelector('pre code'), copy = block.querySelector('.copy');
            const button = block.querySelector('.bar .runbtn'), rect = copy.getBoundingClientRect();
            return {text: code.innerText, visible: code.checkVisibility(), canCopy: copy.checkVisibility(),
              label: button?.getAttribute('aria-label'), x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
          })()`);
          assert.equal(bounds.text, command);
          assert.ok(bounds.visible && bounds.canCopy, lang + ' ' + theme + ' ' + command);
          assert.match(bounds.label || '', /Agent J/);
          for (const type of ['mousePressed', 'mouseReleased']) {
            await page.send('Input.dispatchMouseEvent', {type, x: bounds.x, y: bounds.y, button: 'left', clickCount: 1});
          }
          await waitFor(page, `navigator.clipboard.readText().then(s => s === ${JSON.stringify(command)})`);
        }
        assert.deepEqual(executions, []);
      } finally {
        await page.dispose();
      }
    }
  } finally {
    await browser.close();
    await host.stop();
    await server.stop();
  }
});
